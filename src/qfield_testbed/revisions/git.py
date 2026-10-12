"""Git plumbing over the shared cache: bare mirrors, detached checkouts per sha, ref resolution.

One bare mirror per repository (``cache/git/<repo>.git``) is fetched on demand; every revision that needs
sources gets its own detached worktree under ``cache/checkouts/<repo>/<sha>`` so deployments and bisect
candidates never step on each other.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from qfield_testbed.errors import MaterializationUnavailableError, TestbedError
from qfield_testbed.paths import Paths
from qfield_testbed.runtimes.shell import Runner

log = logging.getLogger(__name__)

_SHA = re.compile(r"^[0-9a-f]{40}$")


def repo_slug(url: str) -> str:
    """``https://github.com/opengisch/QFieldCloud`` → ``opengisch-QFieldCloud`` (cache directory name)."""
    tail = url.rstrip("/").removesuffix(".git")
    parts = [p for p in re.split(r"[/:]", tail) if p]
    return "-".join(parts[-2:])


class Mirror:
    def __init__(self, runner: Runner, paths: Paths, url: str) -> None:
        self.runner = runner
        self.url = url
        self.path = paths.git_mirrors / f"{repo_slug(url)}.git"
        self.checkouts = paths.checkouts / repo_slug(url)
        self._fetched = False

    def _git(self, *args: str, check: bool = True) -> str:
        return self.runner.run(["git", "-C", str(self.path), *args], check=check).stdout

    def ensure(self) -> None:
        """Clone the mirror if missing, fetch it once per process."""
        if not self.path.exists():
            log.info("cloning mirror of %s", self.url)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.runner.run(["git", "clone", "--mirror", "--quiet", self.url, str(self.path)], timeout=1800)
            self._fetched = True
        elif not self._fetched:
            log.info("fetching %s", self.url)
            self.runner.run(["git", "-C", str(self.path), "fetch", "--quiet", "--prune", "--tags"], timeout=600)
            self._fetched = True

    def rev_parse(self, ref: str) -> str:
        """Resolve a ref to a full sha; a sha not yet known triggers a fetch before failing."""
        self.ensure()
        result = self.runner.run(
            ["git", "-C", str(self.path), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False
        )
        sha = result.stdout.strip()
        if result.returncode != 0 or not _SHA.match(sha):
            raise TestbedError(f"unknown ref '{ref}' in {self.url}")
        return sha

    def tags(self, pattern: str = "*") -> list[str]:
        self.ensure()
        out = self._git("tag", "--list", pattern, "--sort=creatordate")
        return [line.strip() for line in out.splitlines() if line.strip()]

    def rev_list_first_parent(self, good: str, bad: str) -> list[str]:
        """Commits after ``good`` up to and including ``bad`` along the first-parent line, oldest first."""
        self.ensure()
        out = self._git("rev-list", "--first-parent", "--reverse", f"{good}..{bad}")
        return [line.strip() for line in out.splitlines() if line.strip()]

    def subject(self, sha: str) -> str:
        return self._git("log", "-1", "--format=%s", sha, check=False).strip()

    def date(self, sha: str) -> str:
        return self._git("log", "-1", "--format=%cs", sha, check=False).strip()

    def checkout(self, sha: str) -> Path:
        """A detached worktree of ``sha`` in the cache (created once, reused forever)."""
        target = self.checkouts / sha
        if (target / ".git").exists():
            return target
        self.ensure()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():  # half-created earlier
            self.runner.run(["git", "-C", str(self.path), "worktree", "remove", "--force", str(target)], check=False)
        log.info("checking out %s", sha[:12])
        result = self.runner.run(
            ["git", "-C", str(self.path), "worktree", "add", "--detach", "--quiet", str(target), sha], check=False
        )
        if result.returncode != 0:
            raise MaterializationUnavailableError(f"cannot check out {sha[:12]}: {result.stderr.strip()}")
        return target

    def drop_checkout(self, sha: str) -> None:
        self.runner.run(
            ["git", "-C", str(self.path), "worktree", "remove", "--force", str(self.checkouts / sha)], check=False
        )


def worktree_head(runner: Runner, path: Path) -> tuple[str, bool]:
    """``(sha, dirty)`` of a user checkout; refuses paths that are not git worktrees."""
    if not path.is_dir():
        raise TestbedError(f"worktree path does not exist: {path}")
    head = runner.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=False)
    if head.returncode != 0:
        raise TestbedError(f"not a git worktree: {path}")
    status = runner.run(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"], check=False)
    return head.stdout.strip(), bool(status.stdout.strip())


def ensure_submodules(runner: Runner, path: Path) -> None:
    if (path / ".gitmodules").is_file():
        runner.run(["git", "-C", str(path), "submodule", "update", "--init", "--recursive", "--quiet"], timeout=600)
