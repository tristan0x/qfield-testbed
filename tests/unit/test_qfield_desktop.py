from __future__ import annotations

import os
from pathlib import Path

import pytest

from qfield_testbed.components import REGISTRY, register
from qfield_testbed.components.qfield_desktop import QFieldDesktop
from qfield_testbed.deployment import Deployment, Testbed
from qfield_testbed.errors import TestbedError
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec, GitSource
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


def _desktop(testbed: Testbed) -> QFieldDesktop:
    testbed.paths.ensure()
    spec = DeploymentSpec.from_yaml(
        "name: d\nqfieldcloud: {version: release:v26.28}\nqfield-desktop: {version: release:v4.3.5}\n"
    )
    component = Deployment.probe(testbed, spec, "qfield-desktop", spec.components()["qfield-desktop"])
    assert isinstance(component, QFieldDesktop)
    return component


def test_check_host_needs_gh(testbed: Testbed, runner: FakeRunner) -> None:
    assert _desktop(testbed).check_host() == []
    runner.available.discard("gh")
    assert _desktop(testbed).check_host() == ["gh missing (needed to download the release AppImage)"]


def test_resolve_refuses_other_sources(testbed: Testbed) -> None:
    component = _desktop(testbed)
    assert component.resolve().label == "v4.3.5"
    component.spec = component.spec.model_copy(update={"version": GitSource(ref="master")})
    with pytest.raises(TestbedError, match="unsupported version source for qfield-desktop: git:master"):
        component.resolve()


def test_materialize_downloads_the_release_appimage(testbed: Testbed, runner: FakeRunner) -> None:
    component = _desktop(testbed)
    target = testbed.paths.artifacts / "qfield-desktop" / "v4.3.5"
    target.mkdir(parents=True)
    (target / "qfield-v4.3.5-linux-x64.AppImage").write_bytes(b"\x7fELF")  # what `gh release download` leaves
    appimage = component.materialize(component.resolve())
    assert appimage == target / "qfield-linux-x64.AppImage"
    assert os.access(appimage, os.X_OK)
    download = next(cmd for cmd in runner.commands() if cmd.startswith("gh release download"))
    assert "v4.3.5 --repo opengisch/QField --pattern qfield-*-linux-x64.AppImage" in download
    assert testbed.cache.load().entries["qfield-desktop/appimage/v4.3.5"].users == {"d"}


def test_wire_needs_cloud_and_keeps_other_settings(testbed: Testbed) -> None:
    component = _desktop(testbed)
    with pytest.raises(TestbedError, match="qfieldcloud component must be running and provisioned first"):
        component.wire()
    component.ctx.state.component("qfieldcloud").facts["url"] = "http://127.0.0.1:30000/"
    component.ctx.state.credentials = Credentials(username="testbed", password="pw")
    conf = component.backend().config_home / "OPENGIS.ch" / "QField.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("[General]\ntheme=dark\n")
    component.wire()
    text = conf.read_text()
    assert "theme=dark" in text
    assert "url=http://127.0.0.1:30000/" in text
    assert component.describe()["settings_file"] == str(conf)
    assert component.describe()["appimage"] is None


def test_desktop_repo_defaults_to_the_configured_repository(testbed: Testbed) -> None:
    assert _desktop(testbed).repo == "opengisch/QField"
    testbed.config.repositories.qfield = "me/QField"
    assert _desktop(testbed).repo == "me/QField"
