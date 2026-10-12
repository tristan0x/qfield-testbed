"""GitHub releases and Actions artifacts through the authenticated ``gh`` CLI."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from qfield_testbed.errors import MaterializationUnavailableError, TestbedError
from qfield_testbed.runtimes.shell import Runner

log = logging.getLogger(__name__)


def owner_repo(url_or_slug: str) -> str:
    """``https://github.com/opengisch/QField`` or ``opengisch/QField`` → ``opengisch/QField``."""
    tail = url_or_slug.rstrip("/").removesuffix(".git")
    parts = tail.split("/")
    if len(parts) < 2:  # noqa: PLR2004
        raise TestbedError(f"expected owner/name or a GitHub URL, got '{url_or_slug}'")
    return "/".join(parts[-2:])


def release_download(runner: Runner, repo: str, tag: str, pattern: str, dest: Path) -> Path:
    """Download the release asset matching ``pattern`` into ``dest`` and return its path."""
    dest.mkdir(parents=True, exist_ok=True)
    result = runner.run(
        [
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            owner_repo(repo),
            "--pattern",
            pattern,
            "--dir",
            str(dest),
            "--clobber",
        ],
        check=False,
        timeout=1800,
    )
    if result.returncode != 0:
        raise MaterializationUnavailableError(
            f"no asset '{pattern}' in release {tag} of {repo}: {result.stderr.strip().splitlines()[-1:]}"
        )
    files = sorted(dest.glob(pattern))
    if not files:
        raise MaterializationUnavailableError(f"release {tag} of {repo}: asset '{pattern}' not found after download")
    return files[0]


def release_exists(runner: Runner, repo: str, tag: str) -> bool:
    return runner.run(["gh", "release", "view", tag, "--repo", owner_repo(repo)], check=False).returncode == 0


def workflow_run_id(runner: Runner, repo: str, workflow: str, sha: str) -> int | None:
    """Id of the latest successful run of ``workflow`` for ``sha``, or None."""
    result = runner.run(
        [
            "gh",
            "api",
            f"repos/{owner_repo(repo)}/actions/workflows/{workflow}/runs",
            "-f",
            f"head_sha={sha}",
            "-f",
            "status=success",
            "-f",
            "per_page=5",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    runs = json.loads(result.stdout or "{}").get("workflow_runs", [])
    return int(runs[0]["id"]) if runs else None


def artifact_download(runner: Runner, repo: str, run_id: int, name_pattern: str, dest: Path) -> Path:
    """Download the artifact whose name matches ``name_pattern`` and return its directory."""
    dest.mkdir(parents=True, exist_ok=True)
    result = runner.run(
        [
            "gh",
            "run",
            "download",
            str(run_id),
            "--repo",
            owner_repo(repo),
            "--pattern",
            name_pattern,
            "--dir",
            str(dest),
        ],
        check=False,
        timeout=1800,
    )
    if result.returncode != 0:
        raise MaterializationUnavailableError(
            f"artifact '{name_pattern}' of run {run_id} in {repo} unavailable (expired?): {result.stderr.strip()[-200:]}"
        )
    return dest
