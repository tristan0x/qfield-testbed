"""Every command answers `--json` with one envelope whose `data` is documented in docs/json-output.md."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.main import get_command

from qfield_testbed.cli import app
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_cli_bisect import MANUAL, SCRIPT, _scenario, _script
from tests.unit.test_cli_bisect import testbed as bisect_testbed  # noqa: F401 - fixture
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401

SPEC = "name: demo\nqgis: {version: host}\n"


def _leaf_commands(group: Any, prefix: str = "") -> set[str]:
    commands = getattr(group, "commands", None)
    if commands is None:
        return {prefix.strip()}
    leaves: set[str] = set()
    for name, command in commands.items():
        leaves |= _leaf_commands(command, f"{prefix} {name}")
    return leaves


def _json(*args: str) -> Any:
    """Run ``qft --json ARGS``: stdout must be exactly one successful envelope; return its data."""
    result = invoke(["--json", *args])
    assert result.exit_code == 0, (args, result.output)
    envelope = json.loads(result.stdout)
    assert envelope["schema_version"] == 1
    assert envelope["ok"] is True, envelope
    return envelope["data"]


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_: subprocess.CompletedProcess(cmd, 0))


@pytest.mark.usefixtures("fake_components", "launched")
def test_every_command_answers_in_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    testbed = isolate(tmp_path, monkeypatch, runner)
    spec_file = tmp_path / "demo.yaml"
    spec_file.write_text(SPEC)

    doctor = _json("doctor")
    assert set(doctor) == {"checks", "capabilities", "host"}
    assert doctor["host"]["slots"] == {"used": 0, "max": 5}
    assert set(_json("config", "show")) >= {"home", "port_base", "max_slots"}
    assert _json("config", "path")["exists"] is True
    assert "name:" in _json("spec", "example")["yaml"]
    assert _json("spec", "validate", str(spec_file)) == {"name": "demo", "components": ["qgis"]}
    assert _json("spec", "schema")["schema"]["title"] == "DeploymentSpec"

    added = _json("deployment", "add", str(spec_file))
    assert (added["name"], added["status"]) == ("demo", "created")
    assert "password" not in json.dumps(_json("deployment", "list"))
    [listed] = _json("deployment", "list")["deployments"]
    assert (listed["name"], listed["port_base"], listed["port_last"]) == ("demo", 30000, 30099)
    assert _json("deployment", "start", "demo")["status"] == "running"
    shown = _json("deployment", "show", "demo")
    assert shown["components"]["qgis"]["profile"] == "testbed"
    assert _json("deployment", "env", "demo")["variables"]["QFT_DEPLOYMENT"] == "demo"
    assert _json("deployment", "logs", "demo")["components"] == {"qgis": "qgis says hi\n"}
    assert _json("deployment", "run", "demo", "--", "true")["exit_code"] == 0
    assert _json("deployment", "launch", "demo", "qgis")["exit_code"] == 0
    assert _json("deployment", "stop", "demo")["status"] == "stopped"
    assert _json("deployment", "restart", "demo")["status"] == "running"
    assert _json("deployment", "remove", "demo", "--yes") == {"name": "demo", "removed": True}

    cache = _json("cache", "list")
    assert set(cache) == {"cache_dir", "entries", "total_bytes"}
    assert _json("cache", "prune", "--dry-run") == {"dry_run": True, "removed": []}
    assert testbed.deployments() == []


@pytest.mark.usefixtures("fake_components", "bisect_testbed")
def test_every_bisect_command_answers_in_json(tmp_path: Path) -> None:
    scenario = tmp_path / "m.yaml"
    scenario.write_text(_scenario("m", MANUAL))
    assert "bisect" in _json("bisect", "example")["yaml"]
    assert _json("bisect", "start", str(scenario))["session"] == "m"
    prepared = _json("bisect", "next")
    assert (prepared["prepared"], prepared["outcome"]) == (1, "prepared")
    assert _json("bisect", "next")["outcome"] == "already-prepared"
    assert _json("bisect", "bad")["tested"] == 1
    _json("bisect", "next")
    assert _json("bisect", "good")["first_bad"]["label"] == "c2"
    assert _json("bisect", "next")["outcome"] == "done"
    assert _json("bisect", "status")["status"] == "done"
    [listed] = _json("bisect", "list")["sessions"]
    assert listed["session"] == "m"
    outcomes = _json("bisect", "log")["outcomes"]
    assert [row["verdict"] for row in outcomes] == ["good", "bad"]
    assert "manual verdict" in _json("bisect", "log", "--index", "1")["log"]
    assert _json("bisect", "reset")["tested"] == 0
    _json("bisect", "next")
    assert _json("bisect", "skip")["skipped"] == 1
    assert _json("bisect", "remove", "m", "--yes") == {"session": "m", "removed": True}

    _script(tmp_path, {0: 0, 1: 1, 2: 1})
    scripted = tmp_path / "s.yaml"
    scripted.write_text(_scenario("s", SCRIPT))
    _json("bisect", "start", str(scripted))
    assert _json("bisect", "run", "s")["first_bad"]["label"] == "c2"


def test_no_command_is_left_without_json() -> None:
    """A new command must get a JSON case above."""
    leaves = _leaf_commands(get_command(app))
    covered = {
        "doctor",
        "config show",
        "config path",
        "spec example",
        "spec validate",
        "spec schema",
        *(
            f"deployment {c}"
            for c in ("add", "list", "start", "show", "env", "logs", "run", "launch", "stop", "restart", "remove")
        ),
        "cache list",
        "cache prune",
        *(
            f"bisect {c}"
            for c in ("example", "start", "next", "bad", "good", "skip", "status", "list", "log", "reset", "run")
        ),
        "bisect remove",
    }
    assert leaves == covered
