"""`qft spec suggest`: detecting the project of a checkout, then proposing a spec that puts it live."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from qfield_testbed.runtimes.shell import SubprocessRunner
from qfield_testbed.suggest.detect import detect, repository_name

FIXTURES = Path(__file__).parents[1] / "fixtures" / "repos"


def _checkout(tmp_path: Path, fixture: str, remote: str | None = None, name: str | None = None) -> Path:
    """A git checkout of a fixture project, with ``remote`` as origin."""
    root = tmp_path / (name or fixture)
    shutil.copytree(FIXTURES / fixture, root)
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fixture"], check=True)
    if remote:
        subprocess.run([*git, "remote", "add", "origin", remote], check=True)
    return root


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("git@github.com:opengisch/QFieldCloud.git", "QFieldCloud"),
        ("https://github.com/someone/qfieldsync/", "qfieldsync"),
        ("https://github.com/opengisch/QField", "QField"),
    ],
)
def test_repository_name(url: str, name: str) -> None:
    assert repository_name(url) == name


@pytest.mark.parametrize(
    ("fixture", "remote", "project"),
    [
        ("qfieldsync", "git@github.com:opengisch/qfieldsync.git", "qfieldsync"),
        ("qfieldsync", "https://github.com/a-fork/QFieldSync", "qfieldsync"),
        ("qfieldcloud", None, "qfieldcloud"),
        ("qfield", "https://github.com/opengisch/QField.git", "qfield"),
        ("qgis-plugin", "https://github.com/opengisch/qgis-swiss-locator", "qgis-plugin"),
    ],
    ids=["qfieldsync", "fork", "no-remote", "qfield", "qgis-plugin"],
)
def test_detects_the_project(tmp_path: Path, fixture: str, remote: str | None, project: str) -> None:
    root = _checkout(tmp_path, fixture, remote)
    detection = detect(SubprocessRunner(), root / "qfieldsync" if fixture == "qfieldsync" else root)
    assert detection.project == project
    assert detection.root == root.resolve()
    assert detection.evidence


def test_a_worktree_is_its_project(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "qfieldsync", "git@github.com:opengisch/qfieldsync.git")
    worktree = tmp_path / "qfieldsync-fix-806"
    subprocess.run(["git", "-C", str(root), "worktree", "add", "-q", "-b", "fix", str(worktree)], check=True)
    detection = detect(SubprocessRunner(), worktree)
    assert (detection.project, detection.root) == ("qfieldsync", worktree.resolve())


def test_the_files_win_over_the_remote(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "qgis-plugin", "git@github.com:me/QField.git")
    detection = detect(SubprocessRunner(), root)
    assert detection.project == "qgis-plugin"
    assert detection.plugin_dir == (root / "swiss_locator").resolve()
    assert any("going by the files" in line for line in detection.evidence)


def test_an_unrelated_directory_is_unknown(tmp_path: Path) -> None:
    assert detect(SubprocessRunner(), tmp_path).project == "unknown"
