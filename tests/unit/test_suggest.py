"""`qft spec suggest`: detecting the project of a checkout, then proposing a spec that puts it live."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from qfield_testbed.deployment import Testbed
from qfield_testbed.doctor import CAPABILITIES, Capability
from qfield_testbed.errors import PrerequisiteMissingError, UsageError
from qfield_testbed.runtimes.shell import FakeRunner, SubprocessRunner
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.suggest.detect import detect, repository_name
from qfield_testbed.suggest.propose import deployment_name, propose
from tests.unit.test_cli_deployment import invoke, isolate

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


def test_a_specific_layout_wins_over_the_remote(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "qfieldcloud", "git@github.com:me/qfieldsync.git")
    detection = detect(SubprocessRunner(), root)
    assert detection.project == "qfieldcloud"
    assert any("going by the files" in line for line in detection.evidence)


def test_a_generic_plugin_layout_yields_to_the_remote(tmp_path: Path) -> None:
    """QFieldSync is a QGIS plugin too: a qfieldsync remote must not turn it into "some plugin"."""
    root = _checkout(tmp_path, "qgis-plugin", "git@github.com:me/qfieldsync.git")
    assert detect(SubprocessRunner(), root).project == "qfieldsync"


def test_a_plugin_is_found_one_level_down(tmp_path: Path) -> None:
    root = _checkout(tmp_path, "qgis-plugin", "https://github.com/opengisch/qgis-swiss-locator")
    assert detect(SubprocessRunner(), root).plugin_dir == (root / "swiss_locator").resolve()


def test_an_unrelated_directory_is_unknown(tmp_path: Path) -> None:
    assert detect(SubprocessRunner(), tmp_path).project == "unknown"


# --- proposals ------------------------------------------------------------------------------------------------


def _matrix(*missing: str) -> dict[str, Capability]:
    return {
        key: Capability(False, [f"{key} check"], [f"fix {key}"]) if key in missing else Capability(True, [], [])
        for key in CAPABILITIES
    }


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    (tmp_path / "qft").mkdir()
    testbed = isolate(tmp_path / "qft", monkeypatch, runner)
    tags = {
        "QFieldCloud": "v26.1 v26.28 v26.29 v27.0rc1",
        "qfieldsync": "v4.22.3 v4.23.3 v4.24.0-beta",
        "QField": "v4.3.5 v4.4.0",
    }
    for repo, names in tags.items():
        lines = "".join(f"{'0' * 40}\trefs/tags/{name}\n" for name in names.split())
        runner.reply_when(
            lambda a, repo=repo: a[:3] == ("git", "ls-remote", "--tags") and a[-1].endswith(repo), stdout=lines
        )
    return testbed


def _propose(testbed: Testbed, tmp_path: Path, fixture: str, *needs: str, matrix: dict[str, Capability] | None = None):  # type: ignore[no-untyped-def]
    root = _checkout(tmp_path, fixture, f"https://github.com/opengisch/{fixture}")
    return root, propose(testbed, detect(SubprocessRunner(), root), set(needs), matrix or _matrix())


def test_qfieldsync_checkout_runs_live_against_the_latest_release(testbed: Testbed, tmp_path: Path) -> None:
    root, proposal = _propose(testbed, tmp_path, "qfieldsync")
    assert proposal.spec == {
        "name": "qfieldsync",
        "qfieldsync": {"version": f"worktree:{root.resolve()}"},
        "qgis": {"version": "host"},
        "qfieldcloud": {"version": "release:v26.29"},  # newest stable, not the rc
    }
    assert DeploymentSpec.from_yaml(proposal.spec_yaml).name == "qfieldsync"


def test_qfieldcloud_checkout_with_needs(testbed: Testbed, tmp_path: Path) -> None:
    _, proposal = _propose(testbed, tmp_path, "qfieldcloud", "tls", "android")
    assert proposal.spec["qfieldcloud"]["tls"] is True
    assert proposal.spec["qfieldsync"] == {"version": "release:v4.23.3"}
    assert proposal.spec["qfield-android"] == {"version": "release:v4.4.0"}


def test_master_instead_of_releases(testbed: Testbed, tmp_path: Path) -> None:
    _, proposal = _propose(testbed, tmp_path, "qfieldsync", "master")
    assert proposal.spec["qfieldcloud"] == {"version": "git:master"}


def test_qfield_checkout_builds_or_falls_back_to_the_ci_artifact(testbed: Testbed, tmp_path: Path) -> None:
    root, built = _propose(testbed, tmp_path, "qfield")
    assert built.spec["qfield-android"] == {"version": f"build:{root.resolve()}"}
    runner = testbed.runner
    assert isinstance(runner, FakeRunner)
    runner.reply_when(lambda a: "--abbrev-ref" in a, stdout="main\n")
    _, artifact = _propose(testbed, tmp_path / "second", "qfield", matrix=_matrix("source:build"))
    assert artifact.spec["qfield-android"] == {"version": "artifact:main"}
    assert any("CI artifact" in note for note in artifact.notes)


def test_release_instead_of_building_the_qfield_checkout(testbed: Testbed, tmp_path: Path) -> None:
    # UC3: "the latest QField release, don't build mine" had no answer in qft; the agent went to `gh`
    _, proposal = _propose(testbed, tmp_path, "qfield", "release")
    assert proposal.spec["qfield-android"] == {"version": "release:v4.4.0"}
    assert proposal.spec["qfieldcloud"] == {"version": "release:v26.29"}
    _, ignored = _propose(testbed, tmp_path / "sync", "qfieldsync", "release")
    assert any("only changes a QField checkout" in note for note in ignored.notes)


def test_no_kvm_no_android(testbed: Testbed, tmp_path: Path) -> None:
    with pytest.raises(PrerequisiteMissingError) as missing:
        _propose(testbed, tmp_path, "qfieldsync", "android", matrix=_matrix("qfield-android"))
    assert missing.value.hint == "fix qfield-android"
    with pytest.raises(PrerequisiteMissingError):
        _propose(testbed, tmp_path / "qfield", "qfield", matrix=_matrix("qfield-android"))


def test_without_host_qgis_the_docker_image_is_proposed(testbed: Testbed, tmp_path: Path) -> None:
    _, proposal = _propose(testbed, tmp_path, "qfieldsync", matrix=_matrix("qgis:host", "qfieldsync"))
    assert proposal.spec["qgis"] == {"version": "docker:ltr"}
    assert proposal.notes


def test_plugin_checkout_and_sync(testbed: Testbed, tmp_path: Path) -> None:
    root, alone = _propose(testbed, tmp_path, "qgis-plugin")
    assert alone.spec["qgis"]["plugins"] == [{"path": str((root / "swiss_locator").resolve())}]
    assert "qfieldcloud" not in alone.spec
    _, synced = _propose(testbed, tmp_path / "s", "qgis-plugin", "sync")
    assert {"qfieldcloud", "qfieldsync"} <= set(synced.spec)


def test_no_release_reachable_falls_back_to_master(testbed: Testbed, tmp_path: Path, runner: FakeRunner) -> None:
    runner.responses.clear()
    _, proposal = _propose(testbed, tmp_path, "qfieldsync")
    assert proposal.spec["qfieldcloud"] == {"version": "git:master"}
    assert any("no release" in note for note in proposal.notes)


@pytest.mark.parametrize(
    ("folder", "name"), [("qfieldsync-fix-cloud-rm-806", "qfieldsync-fix-cloud"), ("2026_Work", "d-2026-work")]
)
def test_deployment_names(folder: str, name: str) -> None:
    assert deployment_name(Path(folder)) == name


def test_suggest_command(testbed: Testbed, tmp_path: Path) -> None:
    root = _checkout(tmp_path, "qfieldsync", "git@github.com:opengisch/qfieldsync.git")
    written = tmp_path / "spec.yaml"
    result = invoke(["spec", "suggest", "--path", str(root), "--output", str(written)])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# qfieldsync/metadata.txt is the QFieldSync plugin")
    assert DeploymentSpec.from_file(written).name == "qfieldsync"
    as_json = json.loads(invoke(["--json", "spec", "suggest", "--path", str(root)]).stdout)["data"]
    assert as_json["project"] == "qfieldsync"
    assert isinstance(invoke(["spec", "suggest", "--path", str(tmp_path / "qft")]).exception, UsageError)
    nested = tmp_path / "qft-claude-x" / "spec.yaml"  # the directory is created
    assert invoke(["spec", "suggest", "--path", str(root), "--output", str(nested)]).exit_code == 0
    assert nested.is_file()
    (tmp_path / "a-file").write_text("")
    unwritable = invoke(["spec", "suggest", "--path", str(root), "--output", str(tmp_path / "a-file" / "s.yaml")])
    assert isinstance(unwritable.exception, UsageError)
    assert "cannot write the spec" in str(unwritable.exception)
    assert "unknown need" in str(invoke(["spec", "suggest", "--path", str(root), "--need", "jetpack"]).exception)
