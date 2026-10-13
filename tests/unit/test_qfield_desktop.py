from __future__ import annotations

import os
from pathlib import Path

import pytest

from qfield_testbed.components import REGISTRY, register
from qfield_testbed.deployment import Testbed
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import Credentials
from tests.unit.test_deployment import FakeQgis


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
def test_desktop_launcher_and_settings(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["docker", "info"])
    testbed.paths.ensure()
    appimage_dir = testbed.paths.artifacts / "qfield-desktop" / "v4.3.5"
    appimage_dir.mkdir(parents=True)
    (appimage_dir / "qfield-linux-x64.AppImage").write_bytes(b"\x7fELF fake")
    spec = DeploymentSpec.from_yaml(
        "name: d\nqfieldcloud: {version: release:v26.28}\nqfield-desktop: {version: release:v4.3.5}\n"
    )
    deployment = testbed.add(spec)
    deployment.state.credentials = Credentials(username="testbed", password="pw")
    deployment.save()
    deployment.start()

    info = deployment.describe()["components"]["qfield-desktop"]
    launcher = Path(info["launcher"])
    assert os.access(launcher, os.X_OK)
    text = launcher.read_text()
    assert "XDG_CONFIG_HOME=" in text
    assert "APPIMAGE_EXTRACT_AND_RUN='1'" in text
    assert text.rstrip().endswith('qfield-linux-x64.AppImage\' "$@"')
    conf = Path(info["settings_file"])
    assert conf.name == "QField.conf"
    assert conf.parent.name == "OPENGIS.ch"
    assert "url=http://127.0.0.1:30000/" in conf.read_text()
    assert "username=testbed" in conf.read_text()
    assert not any(cmd.startswith("gh release download") for cmd in runner.commands())
    assert deployment.launcher("qfield-desktop") == launcher
