"""`qft gc`: expired deployments go, used ones stay, busy ones are skipped; orphans are matched, never guessed."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import RefusedError
from qfield_testbed.gc import apply_gc, plan_gc
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401

T0 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)


def _spec(name: str) -> DeploymentSpec:
    return DeploymentSpec.from_yaml(f"name: {name}\nqgis: {{version: host}}\n")


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    monkeypatch.setattr("qfield_testbed.state._now", lambda: T0)
    return isolate(tmp_path, monkeypatch, runner)


@pytest.fixture
def three(testbed: Testbed) -> Testbed:
    """`old` (agent s1, 1h TTL), `fresh` (agent s1, 1d TTL), `mine` (a person's, no TTL)."""
    testbed.add(_spec("old"), agent_session="s1", ttl=3600)
    testbed.add(_spec("fresh"), agent_session="s1", ttl=86400)
    testbed.add(_spec("mine"))
    return testbed


@pytest.mark.usefixtures("fake_components")
def test_only_expired_deployments_are_planned(three: Testbed) -> None:
    assert plan_gc(three, now=T0 + timedelta(minutes=30)).expired == []
    assert plan_gc(three, now=T0 + timedelta(hours=2)).expired == ["old"]
    assert plan_gc(three, now=T0 + timedelta(days=30)).expired == ["fresh", "old"]


@pytest.mark.usefixtures("fake_components")
def test_a_session_can_be_cleaned_whole(three: Testbed) -> None:
    assert plan_gc(three, expired=False, agent_session="s1", now=T0).expired == ["fresh", "old"]
    assert plan_gc(three, expired=False, agent_session="nobody", now=T0).expired == []


@pytest.mark.usefixtures("fake_components")
def test_apply_removes_and_skips_busy_deployments(three: Testbed) -> None:
    plan = plan_gc(three, now=T0 + timedelta(days=30))
    with three.deployment("fresh").locked():
        report = apply_gc(three, plan)
    assert report.removed == ["old"]
    assert report.skipped == {"fresh": "in use"}
    assert sorted(three.registry.names()) == ["fresh", "mine"]


@pytest.mark.usefixtures("fake_components")
def test_gc_command(three: Testbed, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qfield_testbed.gc._now", lambda: T0 + timedelta(hours=2))
    dry = json.loads(invoke(["--json", "gc", "--dry-run"]).stdout)["data"]
    assert dry["plan"]["expired"] == ["old"]
    assert sorted(three.registry.names()) == ["fresh", "mine", "old"]
    assert isinstance(invoke(["gc"]).exception, RefusedError)  # needs --yes without a terminal
    done = json.loads(invoke(["--json", "gc", "--yes"]).stdout)["data"]
    assert done["report"]["removed"] == ["old"]
    assert "Nothing to clean up" in invoke(["gc", "--yes"]).output
    other = invoke(["--agent-session", "s2", "gc", "--agent-session", "s1", "--yes"])
    assert isinstance(other.exception, RefusedError)
    assert invoke(["--agent-session", "s2", "gc", "--agent-session", "s1", "--yes", "--force"]).exit_code == 0
    assert three.registry.names() == ["mine"]


# --- orphans ------------------------------------------------------------------------------------------------


def _lost(testbed: Testbed, name: str) -> None:
    """A deployment this home created, whose registry entry is gone (a crash, a hand edit) — its resources remain."""
    testbed.add(_spec(name))
    testbed.registry.unregister(name)


def _docker_inventory(runner: FakeRunner, *, projects: str, labelled: str, networks: str) -> None:
    runner.reply_when(lambda a: a[:3] == ("docker", "container", "ls") and "com.docker" in " ".join(a), stdout=projects)
    runner.reply_when(lambda a: a[:3] == ("docker", "volume", "ls"), stdout="")
    runner.reply_when(
        lambda a: a[:3] == ("docker", "container", "ls") and "qft.deployment" in " ".join(a), stdout=labelled
    )
    runner.reply_when(lambda a: a[:3] == ("docker", "network", "ls"), stdout=networks)


@pytest.mark.usefixtures("fake_components")
def test_orphans_are_matched_against_registered_names(testbed: Testbed, runner: FakeRunner) -> None:
    testbed.add(_spec("demo"))
    testbed.add(_spec("demo-2"))
    _lost(testbed, "gone")
    _docker_inventory(
        runner,
        projects="qft-demo-qfc\nqft-demo-2-tb\nqft-gone-qfc\nother-project\n",
        labelled="demo\ngone\n",
        networks="qft-demo\nqft-demo-2\nqft-gone\nqft-demo-qfc_default\n",
    )
    (testbed.paths.deployment_dir("ghost") / "qgis").mkdir(parents=True)
    plan = plan_gc(testbed, expired=False, orphaned=True)
    assert plan.orphaned_projects == ["qft-gone-qfc"]
    assert plan.orphaned_containers == ["gone"]
    assert plan.orphaned_networks == ["qft-gone"]
    assert sorted(plan.orphaned_directories) == ["ghost", "gone"]
    assert plan.stale_entries == []


@pytest.mark.usefixtures("fake_components")
def test_apply_removes_orphans_and_stale_entries(testbed: Testbed, runner: FakeRunner) -> None:
    testbed.add(_spec("demo"))
    testbed.add(_spec("lost"))
    _lost(testbed, "gone")
    (testbed.paths.deployment_dir("lost") / "state.json").unlink()
    _docker_inventory(runner, projects="qft-gone-qfc\n", labelled="", networks="qft-gone\n")
    runner.reply_when(lambda a: a[:3] == ("docker", "network", "inspect"), returncode=0)
    report = apply_gc(testbed, plan_gc(testbed, expired=False, orphaned=True))
    assert "compose project qft-gone-qfc" in report.removed
    assert "registry entry lost" in report.removed
    assert testbed.registry.names() == ["demo"]
    assert "docker compose -p qft-gone-qfc down -v --remove-orphans" in runner.commands()
    assert all("qft-demo" not in command for command in runner.commands() if " rm" in command or "down" in command)


@pytest.mark.usefixtures("fake_components")
def test_dry_run_changes_nothing(testbed: Testbed, runner: FakeRunner) -> None:
    _lost(testbed, "gone")
    _docker_inventory(runner, projects="qft-gone-qfc\n", labelled="", networks="")
    data = json.loads(invoke(["--json", "gc", "--orphaned", "--dry-run"]).stdout)["data"]
    assert data["plan"]["orphaned_projects"] == ["qft-gone-qfc"]
    assert not any("down" in command for command in runner.commands())


@pytest.mark.usefixtures("fake_components")
def test_gc_on_start_removes_expired_deployments_quietly(three: Testbed, monkeypatch: pytest.MonkeyPatch) -> None:
    config = three.paths.home.parent / "config.yaml"
    config.write_text(config.read_text() + "gc_on_start: true\n")
    monkeypatch.setattr("qfield_testbed.gc._now", lambda: T0 + timedelta(hours=2))
    assert invoke(["deployment", "list"]).exit_code == 0
    assert sorted(three.registry.names()) == ["fresh", "mine"]


@pytest.mark.usefixtures("fake_components")
def test_resources_of_other_homes_are_never_orphans(testbed: Testbed, runner: FakeRunner) -> None:
    """Docker is shared by every QFT_HOME: what this home never created belongs to someone else."""
    _docker_inventory(
        runner, projects="qft-fix-753-qfc\nqft-pre-753-qfc\n", labelled="fix-753\n", networks="qft-fix-753\n"
    )
    plan = plan_gc(testbed, expired=False, orphaned=True)
    assert plan.empty()


@pytest.mark.usefixtures("fake_components")
def test_a_clean_removal_is_forgotten(testbed: Testbed, runner: FakeRunner) -> None:
    testbed.add(_spec("done"))
    testbed.deployment("done").remove()
    _docker_inventory(runner, projects="qft-done-qfc\n", labelled="", networks="")
    assert plan_gc(testbed, expired=False, orphaned=True).orphaned_projects == []
    assert "done" not in testbed.registry.load().created_here


def test_older_registries_count_their_deployments_as_created_here(testbed: Testbed) -> None:
    testbed.paths.home.mkdir(parents=True, exist_ok=True)
    testbed.registry.paths.registry_file.write_text(
        '{"deployments": {"old": {"slot": 0, "created_at": "2026-10-01T10:00:00Z"}}}'
    )
    assert testbed.registry.load().created_here == ["old"]
