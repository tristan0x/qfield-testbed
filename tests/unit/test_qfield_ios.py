from __future__ import annotations

import json
from pathlib import Path

import pytest

from qfield_testbed.components import REGISTRY, register
from qfield_testbed.components.qfield_ios import QFieldIos
from qfield_testbed.config import GlobalConfig, IosHost
from qfield_testbed.deployment import Deployment, Testbed
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError, UnsupportedComponentError
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import BuildSource, DeploymentSpec, ReleaseSource
from qfield_testbed.state import Credentials
from tests.unit.test_deployment import FakeQgis

SPEC = "name: i\nqfieldcloud: {version: release:v26.28}\nqfield-ios:\n  version: build:/Users/me/QField/build-sim\n  host: mac\n  device: iPhone 16\n"
SSH = [
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "me@mac.local",
    "--",
]


@pytest.fixture
def cloud_stub():
    class FakeCloud(FakeQgis):
        name = "qfieldcloud"

        def describe(self) -> dict[str, object]:
            return {"url": "http://127.0.0.1:30000/"}

    saved = REGISTRY.get("qfieldcloud")
    register(FakeCloud)
    yield
    if saved is not None:
        REGISTRY["qfieldcloud"] = saved


@pytest.mark.usefixtures("cloud_stub")
def test_unconfigured_host_is_refused(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["docker", "info"])
    with pytest.raises(UnsupportedComponentError, match="not configured"):
        testbed.add(DeploymentSpec.from_yaml(SPEC))


@pytest.mark.usefixtures("cloud_stub")
def test_unreachable_host_is_refused(config: GlobalConfig, runner: FakeRunner) -> None:
    config.ios_hosts["mac"] = IosHost(ssh="me@mac.local")
    runner.reply(["docker", "info"])
    runner.reply([*SSH, "true"], returncode=255)
    with pytest.raises(UnsupportedComponentError, match="unreachable"):
        Testbed(config, runner).add(DeploymentSpec.from_yaml(SPEC))


@pytest.mark.usefixtures("cloud_stub")
def test_simulator_lifecycle(config: GlobalConfig, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    config.ios_hosts["mac"] = IosHost(ssh="me@mac.local")
    runner.reply(["docker", "info"])
    runner.reply([*SSH, "git -C /Users/me/QField/build-sim rev-parse HEAD"], stdout="f" * 40 + "\n")
    runner.reply(
        [*SSH, "find /Users/me/QField/build-sim -maxdepth 6 -name QField.app -path '*iphonesimulator*'"],
        stdout="/Users/me/QField/build-sim/Release-iphonesimulator/QField.app\n",
    )
    runner.reply(
        [*SSH, "xcrun simctl list -j devices available"],
        stdout=json.dumps({"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-2": []}}),
    )
    runner.reply(
        [*SSH, "xcrun simctl list -j devicetypes"],
        stdout=json.dumps(
            {"devicetypes": [{"name": "iPhone 16", "identifier": "com.apple.CoreSimulator.SimDeviceType.iPhone-16"}]}
        ),
    )
    runner.reply(
        [*SSH, "xcrun simctl list -j runtimes available"],
        stdout=json.dumps(
            {"runtimes": [{"identifier": "com.apple.CoreSimulator.SimRuntime.iOS-18-2", "isAvailable": True}]}
        ),
    )
    runner.reply(
        [
            *SSH,
            "xcrun simctl create qft-i com.apple.CoreSimulator.SimDeviceType.iPhone-16 com.apple.CoreSimulator.SimRuntime.iOS-18-2",
        ],
        stdout="UDID-1\n",
    )
    runner.reply(
        [*SSH, "xcrun simctl get_app_container UDID-1 ch.opengis.qfield data"],
        stdout="/Users/me/Library/Developer/CoreSimulator/Devices/UDID-1/data/Containers/Data/Application/APP\n",
    )
    monkeypatch.setattr("qfield_testbed.runtimes.ssh.Ssh.reverse_tunnel", lambda *_: 4242)
    monkeypatch.setattr("qfield_testbed.components.qfield_ios.process_alive", lambda pid: pid == 4242)
    monkeypatch.setattr("qfield_testbed.components.qfield_ios.stop_process", lambda pid: None)

    testbed = Testbed(config, runner)
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC))
    deployment.state.credentials = Credentials(username="testbed", password="pw")
    deployment.save()
    deployment.start()

    info = deployment.describe()["components"]["qfield-ios"]
    assert info["udid"] == "UDID-1"
    assert info["app"].endswith("QField.app")
    assert info["qfieldcloud_url"] == f"http://localhost:{deployment.ports.port('app')}/"
    assert info["settings_file"].endswith("ch.opengis.qfield.plist")
    commands = runner.commands()
    assert any("xcrun simctl bootstatus UDID-1 -b" in cmd for cmd in commands)
    assert any(
        "xcrun simctl install UDID-1 /Users/me/QField/build-sim/Release-iphonesimulator/QField.app" in cmd
        for cmd in commands
    )
    assert any("plutil -replace QFieldCloud.url -string http://localhost:" in cmd for cmd in commands)
    assert deployment.env()["QFT_REVISION_QFIELD_IOS"].startswith("build@ffffffffffff")

    deployment.stop()
    assert any("xcrun simctl shutdown UDID-1" in cmd for cmd in runner.commands())
    deployment.remove()
    assert any("xcrun simctl delete UDID-1" in cmd for cmd in runner.commands())


# --- component steps, one at a time ----------------------------------------------------------------------

APP = "/Users/me/QField/build-sim/Release-iphonesimulator/QField.app"


def _ios(config: GlobalConfig, runner: FakeRunner) -> QFieldIos:
    config.ios_hosts["mac"] = IosHost(ssh="me@mac.local")
    testbed = Testbed(config, runner)
    testbed.paths.ensure()
    spec = DeploymentSpec.from_yaml(SPEC)
    component = Deployment.probe(testbed, spec, "qfield-ios", spec.components()["qfield-ios"])
    assert isinstance(component, QFieldIos)
    return component


def test_check_host_problems(config: GlobalConfig, runner: FakeRunner) -> None:
    component = _ios(config, runner)
    runner.reply([*SSH, "xcrun simctl list -j devices available"], returncode=1)
    assert component.check_host() == [
        "`xcrun simctl` fails on me@mac.local: is Xcode installed and selected (xcode-select)?"
    ]
    runner.available.discard("ssh")
    assert component.check_host() == ["ssh client missing"]
    config.ios_hosts.clear()
    (problem,) = component.check_host()
    assert "iOS host 'mac' is not configured" in problem
    assert problem.endswith("configured: none")


def test_resolve_without_git_and_unsupported_source(config: GlobalConfig, runner: FakeRunner) -> None:
    component = _ios(config, runner)
    runner.reply([*SSH, "git -C /Users/me/QField/build-sim rev-parse HEAD"], returncode=128)
    revision = component.resolve()
    assert (revision.label, revision.sha, revision.dirty) == ("build@build-sim", None, False)
    assert not any("status --porcelain" in cmd for cmd in runner.commands())
    component.spec = component.spec.model_copy(update={"version": ReleaseSource(tag="v4.3.5")})
    with pytest.raises(TestbedError, match=r"unsupported version source for qfield-ios: release:v4.3.5"):
        component.resolve()


def test_resolve_dirty_checkout(config: GlobalConfig, runner: FakeRunner) -> None:
    component = _ios(config, runner)
    runner.reply([*SSH, "git -C /Users/me/QField/build-sim rev-parse HEAD"], stdout="e" * 40 + "\n")
    runner.reply(
        [*SSH, "git -C /Users/me/QField/build-sim status --porcelain --untracked-files=no"], stdout=" M src/x.cpp\n"
    )
    assert str(component.resolve()) == "build@eeeeeeeeeeee+dirty"


def test_materialize_falls_back_to_any_app_then_fails(config: GlobalConfig, runner: FakeRunner) -> None:
    component = _ios(config, runner)
    revision = Revision(source=BuildSource(path=Path("/Users/me/QField/build-sim")), label="build@x")
    with pytest.raises(MaterializationUnavailableError, match=r"no QField.app under /Users/me/QField/build-sim"):
        component.materialize(revision)
    runner.reply(
        [*SSH, "find /Users/me/QField/build-sim -maxdepth 6 -name QField.app"],
        stdout="/Users/me/QField/build-sim/b/QField.app\n/Users/me/QField/build-sim/a/QField.app\n",
    )
    assert component.materialize(revision) == Path("/Users/me/QField/build-sim/a/QField.app")


def test_start_reuses_a_live_tunnel(config: GlobalConfig, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    component = _ios(config, runner)
    runner.reply(
        [*SSH, "xcrun simctl list -j devices available"],
        stdout=json.dumps(
            {
                "devices": {
                    "com.apple.CoreSimulator.SimRuntime.iOS-18-2": [{"udid": "U2", "name": "qft-i", "state": "Booted"}]
                }
            }
        ),
    )
    monkeypatch.setattr("qfield_testbed.components.qfield_ios.process_alive", lambda pid: pid == 99)

    def no_new_tunnel(*_: object) -> int:
        raise AssertionError("a live tunnel must be reused")

    monkeypatch.setattr("qfield_testbed.runtimes.ssh.Ssh.reverse_tunnel", no_new_tunnel)
    component.state.facts.update({"app": APP, "tunnel_pid": 99})
    component.start()
    assert component.state.facts["udid"] == "U2"
    assert component.state.facts["tunnel_pid"] == 99
    assert component.state.facts["runtime"] == "com.apple.CoreSimulator.SimRuntime.iOS-18-2"
    assert not any("simctl create" in cmd for cmd in runner.commands())


def test_wire_skips_or_logs_manual_steps(
    config: GlobalConfig, runner: FakeRunner, caplog: pytest.LogCaptureFixture
) -> None:
    component = _ios(config, runner)
    component.ctx.state.credentials = Credentials(username="testbed", password="pw")
    component.wire()  # not started: no simulator to configure
    assert runner.calls == []
    component.state.facts.update({"udid": "U1", "qfieldcloud_url": "http://localhost:30000/"})
    component.wire()  # the app has no data container: nothing written
    assert component.state.facts["settings_file"] is None
    assert "http://localhost:30000/" in caplog.text


def test_stop_reset_remove_without_simulator(
    config: GlobalConfig, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    stopped: list[int | None] = []
    monkeypatch.setattr("qfield_testbed.components.qfield_ios.stop_process", stopped.append)
    component = _ios(config, runner)
    component.state.facts["tunnel_pid"] = 7
    component.reset()
    assert stopped == [7]
    assert "tunnel_pid" not in component.state.facts
    assert runner.calls == []
    # the Mac is gone from the config: removal still succeeds and only warns
    component.state.facts["udid"] = "U1"
    config.ios_hosts.clear()
    component.remove()
    assert "could not clean the simulator: iOS host 'mac' is not configured" in caplog.text
