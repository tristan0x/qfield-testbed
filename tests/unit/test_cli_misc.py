from __future__ import annotations

import io
import logging
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from qfield_testbed import __version__
from qfield_testbed.cli import main
from qfield_testbed.cli._console import styled_status
from qfield_testbed.deployment import Testbed
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import EXAMPLE_FULL, EXAMPLE_MINIMAL, DeploymentSpec
from qfield_testbed.state import Status
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    return isolate(tmp_path, monkeypatch, runner)


# --- root command -------------------------------------------------------------------------------------------


def test_version() -> None:
    result = invoke(["--version"])
    assert result.exit_code == 0
    assert result.output == f"qfield-testbed {__version__}\n"


@pytest.mark.usefixtures("testbed")
@pytest.mark.parametrize(("flags", "level"), [([], logging.WARNING), (["-v"], logging.INFO), (["-vv"], logging.DEBUG)])
def test_verbosity_sets_the_log_level(monkeypatch: pytest.MonkeyPatch, flags: list[str], level: int) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: seen.append(kwargs))
    result = invoke([*flags, "config", "path"])
    assert result.exit_code == 0
    [kwargs] = seen
    assert kwargs["level"] == level


def test_no_arguments_shows_help() -> None:
    result = invoke([])
    assert "deployment" in result.output
    assert "bisect" in result.output


# --- main(): exceptions → exit codes and messages on stderr ---------------------------------------------------


def _main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["qft", *args])
    try:
        main()
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def test_main_reports_testbed_errors_with_their_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code = _main(monkeypatch, "spec", "validate", str(tmp_path / "missing.yaml"))
    assert code == 2  # SpecError
    captured = capsys.readouterr()
    assert captured.err.startswith("error: cannot read spec")
    assert "Traceback" not in captured.err


@pytest.mark.usefixtures("terminal")
@pytest.mark.usefixtures("fake_components")
def test_main_reports_aborts(
    testbed: Testbed, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    testbed.add(DeploymentSpec.from_yaml("name: demo\nqgis: {version: host}\n"))
    monkeypatch.setattr(sys, "stdin", io.StringIO("n\n"))
    assert _main(monkeypatch, "deployment", "remove", "demo") == 130
    assert "error: aborted" in capsys.readouterr().err
    assert [d.name for d in testbed.deployments()] == ["demo"]


def test_main_reports_interrupts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def interrupted(**_: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("qfield_testbed.cli.app", interrupted)
    assert _main(monkeypatch, "doctor") == 130
    assert "error: interrupted" in capsys.readouterr().err


@pytest.mark.parametrize("code", [0, 3])
def test_main_propagates_explicit_exits(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    import typer  # noqa: PLC0415

    def exiting(**_: Any) -> None:
        raise typer.Exit(code)

    monkeypatch.setattr("qfield_testbed.cli.app", exiting)
    assert _main(monkeypatch, "doctor") == code


def test_python_dash_m(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["qft", "--version"])
    runpy.run_module("qfield_testbed", run_name="__main__", alter_sys=True)
    assert capsys.readouterr().out == f"qfield-testbed {__version__}\n"


# --- config, spec, console ------------------------------------------------------------------------------------


def test_config_show_reflects_file_and_environment(testbed: Testbed) -> None:
    result = invoke(["config", "show"])
    assert result.exit_code == 0
    shown = yaml.safe_load(result.output)
    assert shown["home"] == str(testbed.paths.home)
    assert shown["cache_dir"] == str(testbed.paths.cache)
    assert shown["port_base"] == 30000
    assert shown["cache"]["prune_after_days"] == 30


@pytest.mark.usefixtures("testbed")
def test_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = invoke(["config", "path"])
    assert existing.output == f"{tmp_path / 'config.yaml'}\n"
    monkeypatch.setenv("QFT_CONFIG", str(tmp_path / "nope.yaml"))
    missing = invoke(["config", "path"])
    assert missing.output == f"{tmp_path / 'nope.yaml'}  (not created yet; defaults apply)\n"


@pytest.mark.parametrize(("flags", "expected"), [([], EXAMPLE_FULL), (["--minimal"], EXAMPLE_MINIMAL)])
def test_spec_example(flags: list[str], expected: str) -> None:
    result = invoke(["spec", "example", *flags])
    assert result.exit_code == 0
    assert result.output == expected
    DeploymentSpec.from_yaml(result.output)


def test_spec_validate(tmp_path: Path) -> None:
    file = tmp_path / "spec.yaml"
    file.write_text(EXAMPLE_MINIMAL)
    result = invoke(["spec", "validate", str(file)])
    assert result.exit_code == 0
    assert "valid spec 'smoke' with components: qfieldcloud, qgis, qfieldsync" in result.output


@pytest.mark.parametrize(
    ("status", "style"),
    [
        (Status.CREATED, "dim"),
        (Status.STOPPED, "yellow"),
        (Status.STARTING, "cyan"),
        (Status.RUNNING, "green"),
        (Status.FAILED, "red"),
        (Status.WIRING_FAILED, "red"),
    ],
)
def test_styled_status(status: Status, style: str) -> None:
    assert styled_status(status) == f"[{style}]{status.value}[/]"


# --- doctor -----------------------------------------------------------------------------------------------------


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep each table row on one line."""
    monkeypatch.setenv("COLUMNS", "200")


@pytest.mark.usefixtures("wide")
def test_doctor_without_ios_hosts(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["docker", "info"], returncode=1)
    result = invoke(["doctor"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert next(line for line in lines if "docker  " in line).lstrip().startswith("✗")
    assert "none configured (ios_hosts in config)" in result.output
    assert f"home: {testbed.paths.home}" in result.output
    assert f"cache: {testbed.paths.cache}" in result.output
    assert "config: " in result.output


@pytest.mark.usefixtures("wide")
def test_doctor_checks_ios_hosts(tmp_path: Path, testbed: Testbed, runner: FakeRunner) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(config.read_text() + "ios_hosts:\n  mini: {ssh: me@mini.local}\n  old: {ssh: me@old.local}\n")
    runner.reply_when(lambda args: args[0] == "ssh" and "me@old.local" in args, returncode=255)
    result = invoke(["doctor"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    mini = next(line for line in lines if "ios host 'mini'" in line)
    assert mini.lstrip().startswith("✓")
    assert "me@mini.local" in mini
    old = next(line for line in lines if "ios host 'old'" in line)
    assert old.lstrip().startswith("✗")
    assert "me@old.local: unreachable or no Xcode" in old
    assert "none configured" not in result.output
    assert testbed.config.ios_hosts == {}  # loaded before the file changed: the CLI reads it afresh


@pytest.mark.parametrize(("returned", "expected"), [(7, 7), (0, 0), (None, 0)])
def test_main_exits_with_the_returned_code(
    monkeypatch: pytest.MonkeyPatch, returned: int | None, expected: int
) -> None:
    monkeypatch.setattr("qfield_testbed.cli.app", lambda **_: returned)
    assert _main(monkeypatch, "doctor") == expected
