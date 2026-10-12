from __future__ import annotations

import json

import pytest

from qfield_testbed.components import REGISTRY, register
from qfield_testbed.config import GlobalConfig, IosHost
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import UnsupportedComponentError
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
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


def test_unconfigured_host_is_refused(testbed: Testbed, runner: FakeRunner, cloud_stub) -> None:
    runner.reply(["docker", "info"])
    with pytest.raises(UnsupportedComponentError, match="not configured"):
        testbed.add(DeploymentSpec.from_yaml(SPEC))


def test_unreachable_host_is_refused(config: GlobalConfig, runner: FakeRunner, cloud_stub) -> None:
    config.ios_hosts["mac"] = IosHost(ssh="me@mac.local")
    runner.reply(["docker", "info"])
    runner.reply([*SSH, "true"], returncode=255)
    with pytest.raises(UnsupportedComponentError, match="unreachable"):
        Testbed(config, runner).add(DeploymentSpec.from_yaml(SPEC))


def test_simulator_lifecycle(
    config: GlobalConfig, runner: FakeRunner, cloud_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr("qfield_testbed.runtimes.ssh.Ssh.reverse_tunnel", lambda self, remote, local: 4242)
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
