from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner, Result

from qfield_testbed import deployment as deployment_module
from qfield_testbed.cli import app
from qfield_testbed.components import REGISTRY, Component, register
from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError, UnknownDeploymentError
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import Status

# --- shared helpers (imported by the other test_cli_* modules) ----------------------------------------------


class CliQgis(Component):
    """A `qgis` stand-in: records its lifecycle, writes a launcher and can show logs."""

    name = "qgis"
    calls: ClassVar[list[str]] = []

    def resolve(self) -> Revision:
        return Revision(source=self.spec.version, label="host-4.2.3")  # type: ignore[union-attr]

    def materialize(self, revision: Revision) -> Path:
        return self.ctx.paths.cache / revision.key

    def render(self, revision: Revision, materialized: Path) -> None:
        launcher = self.ctx.directory / "bin" / "qgis"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/sh\n")

    def start(self) -> None:
        self.calls.append("start qgis")

    def stop(self) -> None:
        self.calls.append("stop qgis")

    def remove(self) -> None:
        self.calls.append("remove qgis")

    def describe(self) -> dict[str, Any]:
        return {"profile": "testbed", "missing": None}

    def logs(self, *, follow: bool) -> None:
        self.calls.append(f"logs qgis follow={follow}")


class CliCloud(CliQgis):
    """A `qfieldcloud` stand-in whose revision follows its version ref; revisions starting with `dead` cannot be built."""

    name = "qfieldcloud"

    def resolve(self) -> Revision:
        version = self.spec.version  # type: ignore[union-attr]
        ref = str(getattr(version, "ref", None) or getattr(version, "tag", None) or version)
        return Revision(source=version, label=ref[:12], sha=ref)

    def materialize(self, revision: Revision) -> Path:
        if revision.label.startswith("dead"):
            raise MaterializationUnavailableError(f"cannot build {revision.label}")
        return super().materialize(revision)

    def render(self, revision: Revision, materialized: Path) -> None:
        pass

    def start(self) -> None:
        self.calls.append("start qfieldcloud")

    def stop(self) -> None:
        self.calls.append("stop qfieldcloud")

    def remove(self) -> None:
        self.calls.append("remove qfieldcloud")

    def describe(self) -> dict[str, Any]:
        revision = self.state.revision
        return {"url": "http://127.0.0.1:1/", "version": revision.label if revision else None}


@pytest.fixture
def fake_components() -> Iterator[None]:
    saved = dict(REGISTRY)
    REGISTRY.clear()
    register(CliQgis)
    register(CliCloud)
    CliQgis.calls = []
    yield
    REGISTRY.clear()
    REGISTRY.update(saved)


def isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    """Point the CLI's `Testbed()` at a temporary home/cache/config and at ``runner``; return an equivalent testbed."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("port_base: 30000\nmax_slots: 5\n")
    monkeypatch.setenv("QFT_CONFIG", str(config_file))
    monkeypatch.setenv("QFT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QFT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(deployment_module, "SubprocessRunner", lambda: runner)
    return Testbed(GlobalConfig.load(), runner)


def invoke(args: Sequence[str], stdin: str | None = None) -> Result:
    return CliRunner().invoke(app, list(args), input=stdin)


# --- tests ----------------------------------------------------------------------------------------------------

SPEC = "name: demo\nqgis: {version: host}\n"
SPEC_WITH_CLOUD = "name: demo\nqfieldcloud: {version: release:v26.28}\nqgis: {version: host}\n"


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    return isolate(tmp_path, monkeypatch, runner)


@pytest.fixture
def spec_file(tmp_path: Path) -> Path:
    file = tmp_path / "spec.yaml"
    file.write_text(SPEC)
    return file


@pytest.fixture
def added(testbed: Testbed, spec_file: Path) -> Testbed:
    assert invoke(["deployment", "add", str(spec_file)]).exit_code == 0
    return testbed


@pytest.mark.usefixtures("fake_components")
def test_add_registers_and_suggests_start(testbed: Testbed, spec_file: Path) -> None:
    result = invoke(["deployment", "add", str(spec_file)])
    assert result.exit_code == 0, result.output
    assert "deployment demo registered on slot 0 (ports 30000–30099)" in result.output
    assert "Next: qft deployment start demo" in result.output
    assert testbed.deployment("demo").state.status is Status.CREATED


@pytest.mark.usefixtures("fake_components")
def test_add_from_stdin_with_overrides_and_start(testbed: Testbed) -> None:
    result = invoke(
        ["deployment", "add", "-", "--name", "other", "--set", "qfieldcloud.version=release:v26.30", "--start"],
        stdin=SPEC_WITH_CLOUD,
    )
    assert result.exit_code == 0, result.output
    assert "deployment other is running" in result.output
    assert "test account: testbed / " in result.output
    assert "version: v26.30" in result.output
    assert "missing" not in result.output  # None facts are hidden
    deployment = testbed.deployment("other")
    assert deployment.state.status is Status.RUNNING
    assert CliQgis.calls == ["start qfieldcloud", "start qgis"]


@pytest.mark.usefixtures("fake_components", "testbed")
@pytest.mark.parametrize("bad", ["novalue", "=value"])
def test_add_rejects_malformed_set(spec_file: Path, bad: str) -> None:
    result = invoke(["deployment", "add", str(spec_file), "--set", bad])
    assert result.exit_code == 1
    assert isinstance(result.exception, TestbedError)
    assert f"--set expects key=value, got '{bad}'" in str(result.exception)


@pytest.mark.usefixtures("fake_components", "testbed")
def test_list_without_deployments() -> None:
    result = invoke(["deployment", "list"])
    assert result.exit_code == 0
    assert "No deployment yet" in result.output


@pytest.mark.usefixtures("fake_components", "added")
def test_list_shows_slot_ports_and_status() -> None:
    result = invoke(["deployment", "list"])
    assert result.exit_code == 0
    assert "NAME" in result.output
    line = next(line for line in result.output.splitlines() if line.startswith("demo"))
    assert "30000–30099" in line
    assert "created" in line
    assert "qgis created" in line


@pytest.mark.usefixtures("fake_components", "added")
def test_show_text_and_json(testbed: Testbed) -> None:
    text = invoke(["deployment", "show", "demo"])
    assert text.exit_code == 0
    assert "demo  created  slot 0  network qft-demo" in text.output
    assert f"directory: {testbed.paths.deployment_dir('demo')}" in text.output
    assert "qgis  created" in text.output
    as_json = invoke(["deployment", "show", "demo", "--json"])
    assert as_json.exit_code == 0
    info = json.loads(as_json.output)
    assert info["name"] == "demo"
    assert info["components"]["qgis"]["version"] == "host"


@pytest.mark.usefixtures("fake_components", "testbed")
def test_show_unknown_deployment() -> None:
    result = invoke(["deployment", "show", "nope"])
    assert result.exit_code == 1
    assert isinstance(result.exception, UnknownDeploymentError)


@pytest.mark.usefixtures("fake_components", "added")
def test_start_stop_restart(testbed: Testbed) -> None:
    started = invoke(["deployment", "start", "demo"])
    assert started.exit_code == 0, started.output
    assert "deployment demo is running" in started.output
    assert "profile: testbed" in started.output
    assert testbed.deployment("demo").state.status is Status.RUNNING

    stopped = invoke(["deployment", "stop", "demo", "--component", "qgis"])
    assert stopped.exit_code == 0
    assert "deployment demo stopped" in stopped.output
    assert testbed.deployment("demo").state.component("qgis").status is Status.STOPPED

    restarted = invoke(["deployment", "restart", "demo"])
    assert restarted.exit_code == 0
    assert "deployment demo restarted" in restarted.output
    assert CliQgis.calls == ["start qgis", "stop qgis", "stop qgis", "start qgis"]
    assert testbed.deployment("demo").state.status is Status.RUNNING


@pytest.mark.usefixtures("terminal")
@pytest.mark.usefixtures("fake_components", "added")
def test_remove_asks_for_confirmation(testbed: Testbed) -> None:
    declined = invoke(["deployment", "remove", "demo"], stdin="n\n")
    assert declined.exit_code == 1
    assert "Remove deployment 'demo' and all its data?" in declined.output
    assert "Aborted" in declined.output
    assert [d.name for d in testbed.deployments()] == ["demo"]

    accepted = invoke(["deployment", "remove", "demo"], stdin="y\n")
    assert accepted.exit_code == 0
    assert "deployment demo removed" in accepted.output
    assert testbed.deployments() == []
    assert not testbed.paths.deployment_dir("demo").exists()


@pytest.mark.usefixtures("fake_components")
def test_remove_warns_about_bisect_session(testbed: Testbed) -> None:
    testbed.add(DeploymentSpec.from_yaml(SPEC), session="hunt")
    result = invoke(["deployment", "remove", "demo"], stdin="n\n")
    assert result.exit_code == 1
    assert "belongs to bisect session 'hunt'" in result.stderr
    forced = invoke(["deployment", "remove", "demo", "--force"])
    assert forced.exit_code == 0
    assert "bisect session" not in forced.stderr
    assert testbed.deployments() == []


@pytest.mark.usefixtures("fake_components")
def test_logs_follows_components_that_have_logs(testbed: Testbed) -> None:
    REGISTRY["qfieldcloud"] = type("NoLogsCloud", (CliCloud,), {"logs": None})
    testbed.add(DeploymentSpec.from_yaml(SPEC_WITH_CLOUD))
    result = invoke(["deployment", "logs", "demo", "-f"])
    assert result.exit_code == 0
    assert CliQgis.calls == ["logs qgis follow=True"]
    only = invoke(["deployment", "logs", "demo", "--component", "qgis"])
    assert only.exit_code == 0
    assert CliQgis.calls[-1] == "logs qgis follow=False"


@pytest.fixture
def subprocess_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict[str, str] | None]]:
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_run(cmd: list[str], *, env: dict[str, str] | None = None, check: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append((cmd, env))
        return subprocess.CompletedProcess(cmd, 3)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


@pytest.mark.usefixtures("fake_components", "added")
def test_launch(testbed: Testbed, subprocess_calls: list[tuple[list[str], dict[str, str] | None]]) -> None:
    before_start = invoke(["deployment", "launch", "demo", "qgis"])
    assert before_start.exit_code == 1
    assert "no launcher 'qgis' in deployment 'demo' (start it first)" in str(before_start.exception)

    assert invoke(["deployment", "start", "demo"]).exit_code == 0
    unknown = invoke(["deployment", "launch", "demo", "android"])
    assert "no launcher 'android' in deployment 'demo'; available: qgis" in str(unknown.exception)

    launched = invoke(["deployment", "launch", "demo", "qgis"])
    assert launched.exit_code == 0
    launcher = testbed.paths.deployment_dir("demo") / "bin" / "qgis"
    assert f"launching {launcher}" in launched.output
    assert "(Qt platform: session" in " ".join(launched.output.split())
    assert subprocess_calls == [([str(launcher)], None)]


@pytest.mark.usefixtures("fake_components", "added")
def test_run_passes_the_environment_and_exit_code(
    subprocess_calls: list[tuple[list[str], dict[str, str] | None]],
) -> None:
    result = invoke(["deployment", "run", "demo", "--", "sh", "-c", "exit 3"])
    assert result.exit_code == 3
    [(cmd, env)] = subprocess_calls
    assert cmd == ["sh", "-c", "exit 3"]
    assert env is not None
    assert env["QFT_DEPLOYMENT"] == "demo"
    assert "PATH" in env  # merged over the current environment


@pytest.mark.usefixtures("fake_components", "added")
def test_run_without_command(subprocess_calls: list[tuple[list[str], dict[str, str] | None]]) -> None:
    result = invoke(["deployment", "run", "demo", "--"])
    assert result.exit_code == 1
    assert "usage: qft deployment run NAME -- CMD" in str(result.exception)
    assert subprocess_calls == []


@pytest.mark.usefixtures("fake_components")
def test_env_is_shell_quoted(testbed: Testbed) -> None:
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC_WITH_CLOUD))
    assert deployment.state.credentials is not None
    deployment.state.credentials.password = "it's"
    deployment.save()
    result = invoke(["deployment", "env", "demo"])
    assert result.exit_code == 0
    lines = result.output.splitlines()
    assert lines == sorted(lines)
    assert "export QFT_DEPLOYMENT='demo'" in lines
    assert "export QFT_QFIELDCLOUD_PASSWORD='it'\\''s'" in lines
    evaluated = subprocess.run(
        ["sh", "-c", result.output + 'printf %s "$QFT_QFIELDCLOUD_PASSWORD"'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert evaluated.stdout == "it's"


@pytest.mark.usefixtures("fake_components", "added")
def test_remove_without_a_terminal_names_the_way_out(testbed: Testbed) -> None:
    refused = invoke(["deployment", "remove", "demo"], stdin="")
    assert refused.exit_code == 1
    assert str(refused.exception) == (
        "refusing to remove 'demo' without confirmation: stdin is not a terminal; pass --force"
    )
    assert [d.name for d in testbed.deployments()] == ["demo"]
    assert invoke(["deployment", "remove", "demo", "--force"]).exit_code == 0
    assert testbed.deployments() == []
