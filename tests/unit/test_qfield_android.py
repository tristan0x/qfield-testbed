from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qfield_testbed.components import REGISTRY, register
from qfield_testbed.components.qfield_android import QFieldAndroid
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import UnsupportedComponentError
from qfield_testbed.provision.android_settings import merge_qfieldcloud_section
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import Credentials
from tests.unit.test_deployment import FakeQgis

SHA = "9b93f81fc6b13d44b6430afcca8bed0a4d007090"


def test_merge_qfieldcloud_section_keeps_other_keys() -> None:
    existing = "[General]\nfoo=bar\n\n[QFieldCloud]\nurl=https://app.qfield.cloud\nprovider=x\n"
    out = merge_qfieldcloud_section(existing, "http://10.0.2.2:8000/", "testbed")
    assert "foo=bar" in out
    assert "provider=x" in out
    assert "url=http://10.0.2.2:8000/" in out
    assert "username=testbed" in out
    assert "urls=http://10.0.2.2:8000/" in out
    fresh = merge_qfieldcloud_section("", "http://10.0.2.2:8000/", "u")
    assert fresh.startswith("[QFieldCloud]")


@pytest.fixture
def cloud_stub():
    class FakeCloud(FakeQgis):
        name = "qfieldcloud"

        def describe(self) -> dict[str, object]:
            return {"url": "http://127.0.0.1:30000/", "url_internal": "http://app:8000/"}

    saved = REGISTRY.get("qfieldcloud")
    register(FakeCloud)
    yield
    if saved is not None:
        REGISTRY["qfieldcloud"] = saved


@pytest.fixture
def android_runner(runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    kvm = tmp_path / "kvm"
    kvm.write_text("")
    monkeypatch.setattr("qfield_testbed.components.qfield_android.KVM_DEVICE", kvm)
    runner.reply(["docker", "info"])
    runner.reply(["docker", "network", "inspect"], returncode=1)  # network not created yet
    runner.reply(["gh", "api", "repos/opengisch/QField/commits/v4.3.5"], stdout=SHA + "\n")
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "wait-for-device", "shell", "getprop"], stdout="1\n")
    runner.reply(
        ["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "dumpsys window | grep -i mCurrentFocus"],
        stdout="mCurrentFocus=Window{Launcher}\n",
    )
    runner.reply(
        [
            "docker",
            "exec",
            "qft-a-tb-android-1",
            "adb",
            "shell",
            "dumpsys package ch.opengis.qfield | grep versionName",
        ],
        stdout="    versionName=4.3.5\n",
    )
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "id"], stdout="uid=0(root)\n")
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "su root sh -c 'find"], stdout="")
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "su root sh -c 'stat"], stdout="u0_a150\n")
    return runner


def test_release_apk_is_cached_installed_and_seeded(
    testbed: Testbed, android_runner: FakeRunner, cloud_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qfield_testbed.provision.android_settings as mod  # noqa: PLC0415

    monkeypatch.setattr(
        mod, "seed_qfield_settings", lambda adb, url, username: mod.DEFAULT_CONF if adb.root() else None
    )
    testbed.paths.ensure()
    # the APK is already in the cache: no gh download
    apk_dir = testbed.paths.artifacts / "qfield" / "v4.3.5"
    apk_dir.mkdir(parents=True)
    (apk_dir / "qfield-android-x64.apk").write_bytes(b"PK\x03\x04fake")
    spec = DeploymentSpec.from_yaml(
        'name: a\nqfieldcloud: {version: release:v26.28}\nqfield-android:\n  version: release:v4.3.5\n  device: "Nexus 5"\n'
    )
    deployment = testbed.add(spec)
    deployment.state.credentials = Credentials(username="testbed", password="pw")
    deployment.save()
    deployment.start()

    component = deployment.components("qfield-android")[0]
    assert isinstance(component, QFieldAndroid)
    compose = yaml.safe_load((component.directory / "compose.yml").read_text())
    android = compose["services"]["android"]
    assert android["image"] == "budtmo/docker-android:emulator_14.0"
    assert android["environment"]["EMULATOR_DEVICE"] == "Nexus 5"
    assert android["devices"] == ["/dev/kvm"]
    assert f"127.0.0.1:{deployment.ports.port('novnc')}:6080" in android["ports"]
    assert compose["services"]["qfc-proxy"]["network_mode"] == "service:android"
    assert "TCP:app:8000" in compose["services"]["qfc-proxy"]["command"]
    assert compose["networks"]["default"] == {"name": "qft-a", "external": True}

    commands = android_runner.commands()
    assert any(cmd.startswith("docker network create qft-a") for cmd in commands)
    assert any("compose -p qft-a-tb" in cmd and " up -d" in cmd for cmd in commands)
    assert any(cmd.startswith("docker cp ") and cmd.endswith("qft-a-tb-android-1:/tmp/qfield.apk") for cmd in commands)
    assert any("adb install -r -g /tmp/qfield.apk" in cmd for cmd in commands)
    assert not any(cmd.startswith("gh release download") for cmd in commands)

    info = deployment.describe()["components"]["qfield-android"]
    assert info["qfieldcloud_url"] == "http://10.0.2.2:8000/"
    assert info["novnc_url"].startswith(f"http://127.0.0.1:{deployment.ports.port('novnc')}/")
    assert info["adb"] == "docker exec qft-a-tb-android-1 adb"
    assert info["installed_version"] == "4.3.5"
    assert info["settings_file"].endswith("OPENGIS.ch/QField.conf")
    env = deployment.env()
    assert env["QFT_QFIELD_ANDROID_ADB"] == "docker exec qft-a-tb-android-1 adb"
    assert env["QFT_QFIELD_ANDROID_QFIELDCLOUD_URL"] == "http://10.0.2.2:8000/"
    assert (deployment.directory / "bin" / "android").is_file()

    # a second start does not reinstall the same APK
    before = len([c for c in android_runner.commands() if "adb install" in c])
    deployment.start()
    assert len([c for c in android_runner.commands() if "adb install" in c]) == before


def test_android_requires_kvm(
    testbed: Testbed, runner: FakeRunner, cloud_stub, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.reply(["docker", "info"])
    monkeypatch.setattr("qfield_testbed.components.qfield_android.KVM_DEVICE", Path("/nonexistent/kvm"))
    with pytest.raises(UnsupportedComponentError, match="kvm"):
        testbed.add(
            DeploymentSpec.from_yaml(
                "name: a\nqfieldcloud: {version: release:v26.28}\nqfield-android: {version: release:v4.3.5}\n"
            )
        )
