"""The console script as users run it, in a subprocess: in-process tests cannot see what `main()` exits with."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from qfield_testbed import __version__


def _qft(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "config.yaml"
    config.write_text("port_base: 30000\nmax_slots: 5\n")
    env = {
        **os.environ,
        "QFT_CONFIG": str(config),
        "QFT_HOME": str(tmp_path / "home"),
        "QFT_CACHE_DIR": str(tmp_path / "cache"),
    }
    return subprocess.run(
        [sys.executable, "-m", "qfield_testbed", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (["deployment", "show"], 2, "Missing argument 'name'"),
        (["spec", "validate", "missing.yaml"], 2, "cannot read spec"),
        (["deployment", "show", "nothing"], 5, "No deployment named 'nothing'"),
        (["deployment", "remove", "nothing", "--force"], 5, "hint: run `qft deployment list`"),
        (["--version"], 0, "qfield-testbed"),
    ],
    ids=["usage-error", "spec-error", "not-found", "hint", "version"],
)
def test_qft_exit_status(tmp_path: Path, args: list[str], code: int, message: str) -> None:
    result = _qft(tmp_path, *args)
    assert result.returncode == code
    assert message in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def _envelope(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    document = json.loads(result.stdout)  # exactly one JSON document on stdout, nothing else
    assert document["schema_version"] == 1
    assert "Traceback" not in result.stderr
    return dict(document)


@pytest.mark.parametrize(
    ("args", "env"),
    [(["--json", "deployment", "show", "nothing"], {}), (["deployment", "show", "nothing"], {"QFT_OUTPUT": "json"})],
    ids=["flag", "environment"],
)
def test_json_errors_are_one_envelope_on_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], env: dict[str, str]
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = _qft(tmp_path, *args)
    assert result.returncode == 5
    envelope = _envelope(result)
    assert envelope["ok"] is False
    assert envelope["error"] == {
        "code": "NOT_FOUND",
        "message": "No deployment named 'nothing'.",
        "hint": "run `qft deployment list`",
        "docs": None,
        "details": {},
    }


def test_json_usage_error(tmp_path: Path) -> None:
    result = _qft(tmp_path, "--json", "deployment", "show")
    assert result.returncode == 2
    error = _envelope(result)["error"]
    assert isinstance(error, dict)
    assert error["code"] == "USAGE"
    assert "Missing argument" in error["message"]


def test_json_version(tmp_path: Path) -> None:
    result = _qft(tmp_path, "--json", "--version")
    assert result.returncode == 0
    assert _envelope(result)["data"] == {"version": __version__}


def test_json_command_without_data_still_answers(tmp_path: Path) -> None:
    result = _qft(tmp_path, "--json", "-vv", "config", "path")
    assert result.returncode == 0
    envelope = _envelope(result)
    assert (envelope["ok"], envelope["data"]) == (True, None)
    assert "config.yaml" in result.stderr  # human output moved to stderr
