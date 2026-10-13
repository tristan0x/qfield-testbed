from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from qfield_testbed.cli.cache import parse_duration
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import TestbedError
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    return isolate(tmp_path, monkeypatch, runner)


def _entry(testbed: Testbed, key: str, *, size: int, user: str | None = None, age_days: int = 0) -> Path:
    path = testbed.paths.cache / key.replace("/", "_")
    if size:
        path.mkdir(parents=True)
        (path / "blob").write_bytes(b"x" * size)
    testbed.cache.touch(key, path, user=user, images=[f"qft-{key.replace('/', '-')}:1"])
    with testbed.cache.locked() as index:
        index.entries[key].last_used = datetime.now(tz=UTC) - timedelta(days=age_days)
    return path


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30d", timedelta(days=30)), ("12h", timedelta(hours=12)), ("90m", timedelta(minutes=90))],
)
def test_parse_duration(value: str, expected: timedelta) -> None:
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["30", "d", "1w", "-1d", "1.5h"])
def test_parse_duration_rejects(value: str) -> None:
    with pytest.raises(TestbedError, match=f"invalid duration '{value}'"):
        parse_duration(value)


def test_list_empty(testbed: Testbed) -> None:
    result = invoke(["cache", "list"])
    assert result.exit_code == 0
    assert f"Cache is empty ({testbed.paths.cache})" in result.output


def test_list_shows_sizes_users_and_total(testbed: Testbed) -> None:
    _entry(testbed, "qgis/host/a", size=2048, user="demo")
    _entry(testbed, "qfieldcloud/git/b", size=0, age_days=40)  # its directory is gone
    result = invoke(["cache", "list"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines[0].split() == ["KEY", "SIZE", "LAST", "USED", "USED", "BY", "IMAGES"]
    gone = next(line for line in lines if line.startswith("qfieldcloud/git/b"))
    assert "0.0 B" in gone
    assert (datetime.now(tz=UTC) - timedelta(days=40)).strftime("%Y-%m-%d") in gone
    used = next(line for line in lines if line.startswith("qgis/host/a"))
    assert "2.0 KiB" in used
    assert "demo" in used
    assert "qft-qgis-host-a:1" in used
    assert f"2 entries, 2.0 KiB on disk in {testbed.paths.cache}" in result.output


def test_prune_nothing_by_default(testbed: Testbed, runner: FakeRunner) -> None:
    _entry(testbed, "qgis/host/a", size=1, age_days=29)  # younger than the 30 days of the config default
    result = invoke(["cache", "prune"])
    assert result.exit_code == 0
    assert "Nothing to prune." in result.output
    assert "cache pruned" in result.output
    assert "qgis/host/a" in testbed.cache.load().entries
    assert not any("image rm" in command for command in runner.commands())


@pytest.mark.usefixtures("fake_components")
def test_prune_keeps_entries_of_live_deployments(testbed: Testbed, runner: FakeRunner) -> None:
    testbed.add(DeploymentSpec.from_yaml("name: demo\nqgis: {version: host}\n"))
    kept = _entry(testbed, "qgis/host/live", size=1, user="demo", age_days=100)
    dropped = _entry(testbed, "qgis/host/old", size=3, user="gone", age_days=100)
    _entry(testbed, "qgis/host/missing", size=0, age_days=100)
    runner.reply(["docker", "image", "ls"], stdout="qft-x:1\nqft-y:<none>\n")

    result = invoke(["cache", "prune", "--older-than", "1d", "--docker-build-cache"])
    assert result.exit_code == 0, result.output
    assert "removing qgis/host/old (3.0 B)" in result.output
    assert "removing qgis/host/missing (gone)" in result.output
    assert "live" not in result.output
    assert "cache pruned" in result.output
    assert "1 qft-* images present" in result.output
    assert sorted(testbed.cache.load().entries) == ["qgis/host/live"]
    assert kept.exists()
    assert not dropped.exists()
    commands = runner.commands()
    assert "docker image rm qft-qgis-host-old:1" in commands
    assert "docker image rm qft-qgis-host-missing:1" in commands
    assert "docker image rm qft-qgis-host-live:1" not in commands
    assert "docker builder prune -f" in commands


def test_prune_dry_run_changes_nothing(testbed: Testbed, runner: FakeRunner) -> None:
    path = _entry(testbed, "qgis/host/old", size=1, age_days=100)
    result = invoke(["cache", "prune", "-n", "--docker-build-cache"])
    assert result.exit_code == 0
    assert "would remove qgis/host/old (1.0 B)" in result.output
    assert "cache pruned" not in result.output
    assert path.exists()
    assert "qgis/host/old" in testbed.cache.load().entries
    commands = runner.commands()
    assert not any("image rm" in command or "builder prune" in command for command in commands)


@pytest.mark.usefixtures("testbed")
def test_prune_rejects_bad_duration() -> None:
    result = invoke(["cache", "prune", "--older-than", "soon"])
    assert result.exit_code == 1
    assert "invalid duration 'soon'" in str(result.exception)
