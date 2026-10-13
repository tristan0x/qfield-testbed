from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from qfield_testbed.config import GlobalConfig
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError
from qfield_testbed.paths import Paths
from qfield_testbed.revisions import github
from qfield_testbed.revisions.git import Mirror, ensure_submodules, repo_slug, resolve_code_source, worktree_head
from qfield_testbed.runtimes.shell import FakeRunner, SubprocessRunner
from qfield_testbed.spec import GitSource, ReleaseSource, WorktreeSource

SHA = "1a2b3c4d5e6f" + "0" * 28

# --- github -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["opengisch/QField", "https://github.com/opengisch/QField", "https://github.com/opengisch/QField.git/"],
)
def test_owner_repo(value: str) -> None:
    assert github.owner_repo(value) == "opengisch/QField"


def test_owner_repo_rejects_bare_names() -> None:
    with pytest.raises(TestbedError, match="expected owner/name or a GitHub URL, got 'QField'"):
        github.owner_repo("QField")


def test_release_download_returns_first_matching_file(runner: FakeRunner, tmp_path: Path) -> None:
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "qfield-v4-arm64.apk").write_text("b")
    (dest / "qfield-v4-x86_64.apk").write_text("a")
    (dest / "notes.txt").write_text("")
    path = github.release_download(runner, "https://github.com/opengisch/QField", "v4", "qfield-*.apk", dest)
    assert path == dest / "qfield-v4-arm64.apk"
    assert runner.commands() == [
        f"gh release download v4 --repo opengisch/QField --pattern qfield-*.apk --dir {dest} --clobber"
    ]


def test_release_download_failure(runner: FakeRunner, tmp_path: Path) -> None:
    runner.reply(["gh", "release"], returncode=1, stderr="first\nrelease not found\n")
    with pytest.raises(
        MaterializationUnavailableError, match=r"no asset '\*.apk' in release v9 of o/r: \['release not found'\]"
    ):
        github.release_download(runner, "o/r", "v9", "*.apk", tmp_path / "new")
    assert (tmp_path / "new").is_dir()


def test_release_download_nothing_downloaded(runner: FakeRunner, tmp_path: Path) -> None:
    with pytest.raises(MaterializationUnavailableError, match=r"release v9 of o/r: asset '\*.apk' not found"):
        github.release_download(runner, "o/r", "v9", "*.apk", tmp_path)


def test_workflow_run_id(runner: FakeRunner) -> None:
    runner.reply(["gh", "api"], stdout=json.dumps({"workflow_runs": [{"id": 7}, {"id": 3}]}))
    assert github.workflow_run_id(runner, "o/r", "android.yml", SHA) == 7
    assert runner.commands() == [
        f"gh api repos/o/r/actions/workflows/android.yml/runs -f head_sha={SHA} -f status=success -f per_page=5"
    ]


@pytest.mark.parametrize(("returncode", "stdout"), [(0, '{"workflow_runs": []}'), (0, ""), (1, '{"x": 1}')])
def test_workflow_run_id_none(runner: FakeRunner, returncode: int, stdout: str) -> None:
    runner.reply(["gh", "api"], stdout=stdout, returncode=returncode)
    assert github.workflow_run_id(runner, "o/r", "android.yml", SHA) is None


def test_artifact_download(runner: FakeRunner, tmp_path: Path) -> None:
    dest = tmp_path / "artifact"
    assert github.artifact_download(runner, "o/r", 77, "qfield-*", dest) == dest
    assert dest.is_dir()
    assert runner.commands() == [f"gh run download 77 --repo o/r --pattern qfield-* --dir {dest}"]


def test_artifact_download_expired(runner: FakeRunner, tmp_path: Path) -> None:
    runner.reply(["gh", "run"], returncode=1, stderr="no valid artifacts found\n")
    with pytest.raises(
        MaterializationUnavailableError,
        match=r"artifact 'qfield-\*' of run 77 in o/r unavailable \(expired\?\): no valid artifacts found",
    ):
        github.artifact_download(runner, "o/r", 77, "qfield-*", tmp_path)


# --- git: pure helpers and FakeRunner ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "slug"),
    [
        ("https://github.com/opengisch/QFieldCloud", "opengisch-QFieldCloud"),
        ("https://github.com/opengisch/QFieldCloud.git/", "opengisch-QFieldCloud"),
        ("git@github.com:opengisch/qfieldsync.git", "opengisch-qfieldsync"),
    ],
)
def test_repo_slug(url: str, slug: str) -> None:
    assert repo_slug(url) == slug


@pytest.fixture
def paths(config: GlobalConfig) -> Paths:
    return Paths.from_config(config)


@pytest.fixture
def mirror(runner: FakeRunner, paths: Paths) -> Mirror:
    return Mirror(runner, paths, "https://github.com/o/r")


def test_mirror_ensure_clones_once(runner: FakeRunner, paths: Paths, mirror: Mirror) -> None:
    assert mirror.path == paths.git_mirrors / "o-r.git"
    mirror.ensure()
    mirror.path.mkdir(parents=True)  # what the clone would have created
    mirror.ensure()
    assert runner.commands() == [f"git clone --mirror --quiet https://github.com/o/r {mirror.path}"]
    assert mirror.path.parent.is_dir()


def test_mirror_ensure_fetches_existing_once(runner: FakeRunner, mirror: Mirror) -> None:
    mirror.path.mkdir(parents=True)
    mirror.ensure()
    mirror.ensure()
    assert runner.commands() == [f"git -C {mirror.path} fetch --quiet --prune --tags"]


@pytest.mark.parametrize(("returncode", "stdout"), [(1, ""), (0, "not-a-sha\n")])
def test_mirror_rev_parse_unknown(runner: FakeRunner, mirror: Mirror, returncode: int, stdout: str) -> None:
    runner.reply_when(lambda args: "rev-parse" in args, returncode=returncode, stdout=stdout)
    with pytest.raises(TestbedError, match=r"unknown ref 'nope' in https://github\.com/o/r"):
        mirror.rev_parse("nope")
    assert runner.commands()[-1] == f"git -C {mirror.path} rev-parse --verify --quiet nope^{{commit}}"


def test_mirror_checkout_reuses_existing(runner: FakeRunner, mirror: Mirror) -> None:
    (mirror.checkouts / SHA / ".git").mkdir(parents=True)
    assert mirror.checkout(SHA) == mirror.checkouts / SHA
    assert runner.calls == []


def test_mirror_checkout_replaces_half_created(runner: FakeRunner, mirror: Mirror) -> None:
    mirror.path.mkdir(parents=True)
    target = mirror.checkouts / SHA
    target.mkdir(parents=True)
    assert mirror.checkout(SHA) == target
    git = f"git -C {mirror.path}"
    assert runner.commands() == [
        f"{git} fetch --quiet --prune --tags",
        f"{git} worktree remove --force {target}",
        f"{git} worktree prune",
        f"{git} worktree add --detach --quiet {target} {SHA}",
    ]


def test_mirror_checkout_failure(runner: FakeRunner, mirror: Mirror) -> None:
    runner.reply_when(lambda args: "add" in args, returncode=128, stderr="fatal: invalid reference\n")
    with pytest.raises(
        MaterializationUnavailableError, match="cannot check out 1a2b3c4d5e6f: fatal: invalid reference"
    ):
        mirror.checkout(SHA)


def test_worktree_head_rejects_missing_and_non_git(runner: FakeRunner, tmp_path: Path) -> None:
    with pytest.raises(TestbedError, match="worktree path does not exist"):
        worktree_head(runner, tmp_path / "missing")
    runner.reply(["git"], returncode=128)
    with pytest.raises(TestbedError, match=f"not a git worktree: {tmp_path}"):
        worktree_head(runner, tmp_path)


def test_ensure_submodules_only_with_gitmodules(runner: FakeRunner, tmp_path: Path) -> None:
    ensure_submodules(runner, tmp_path)
    assert runner.calls == []
    (tmp_path / ".gitmodules").write_text("")
    ensure_submodules(runner, tmp_path)
    assert runner.commands() == [f"git -C {tmp_path} submodule update --init --recursive --quiet"]


# --- git: a real local repository --------------------------------------------------------------------------


# a developer's global config may sign commits and tags; fixtures must not depend on keys
NO_SIGNING = ("-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *("-c", "user.name=t", "-c", "user.email=t@example.com"), *NO_SIGNING, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    """A three-commit repository with a tag on the first commit and a side branch merged into the third."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch=master")
    (repo / "f").write_text("1")
    git(repo, "add", "f")
    git(repo, "commit", "--quiet", "-m", "first")
    git(repo, "tag", "v1")
    git(repo, "checkout", "--quiet", "-b", "side")
    (repo / "g").write_text("side")
    git(repo, "add", "g")
    git(repo, "commit", "--quiet", "-m", "side work")
    git(repo, "checkout", "--quiet", "master")
    (repo / "f").write_text("2")
    git(repo, "commit", "--quiet", "-am", "second")
    git(repo, "merge", "--quiet", "--no-ff", "-m", "merge side", "side")
    return repo


def test_mirror_against_a_real_repository(upstream: Path, paths: Paths) -> None:
    mirror = Mirror(SubprocessRunner(), paths, str(upstream))
    first = git(upstream, "rev-parse", "v1")
    head = git(upstream, "rev-parse", "master")
    assert mirror.rev_parse("v1") == first
    assert mirror.rev_parse(head[:8]) == head
    assert mirror.tags() == ["v1"]
    assert mirror.tags("x*") == []
    line = mirror.rev_list_first_parent(first, head)
    assert [mirror.subject(sha) for sha in line] == ["second", "merge side"]
    assert len(mirror.date(head)) == len("2026-01-01")
    assert mirror.subject("0" * 40) == ""

    checkout = mirror.checkout(first)
    assert (checkout / "f").read_text() == "1"
    assert not (checkout / "g").exists()
    assert mirror.checkout(first) == checkout

    # a second Mirror object (a new process) fetches the existing mirror before resolving
    git(upstream, "tag", "v2")
    assert Mirror(SubprocessRunner(), paths, str(upstream)).tags() == ["v1", "v2"]


def test_resolve_code_source(upstream: Path, paths: Paths) -> None:
    runner = SubprocessRunner()
    mirror = Mirror(runner, paths, str(upstream))
    first = git(upstream, "rev-parse", "v1")
    head = git(upstream, "rev-parse", "master")

    release = resolve_code_source(runner, mirror, ReleaseSource(tag="v1"))
    assert (release.label, release.sha) == ("v1", first)

    by_sha = resolve_code_source(runner, mirror, GitSource(ref=head[:7]))
    assert (by_sha.label, by_sha.sha) == (head[:12], head)

    by_tag = resolve_code_source(runner, mirror, GitSource(ref="v1"))
    assert (by_tag.label, by_tag.sha) == ("v1", first)

    by_branch = resolve_code_source(runner, mirror, GitSource(ref="master"))
    assert (by_branch.label, by_branch.sha) == (f"master@{head[:12]}", head)

    clean = resolve_code_source(runner, mirror, WorktreeSource(path=upstream))
    assert (clean.label, clean.sha, clean.dirty) == (f"worktree@{head[:12]}", head, False)
    (upstream / "f").write_text("edited")
    (upstream / "untracked").write_text("ignored")
    assert resolve_code_source(runner, mirror, WorktreeSource(path=upstream)).dirty is True
