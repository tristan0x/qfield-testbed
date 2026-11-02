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
