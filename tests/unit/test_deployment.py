from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import pytest

from qfield_testbed.components import REGISTRY, Component, register
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import UnknownDeploymentError, UnsupportedComponentError
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec, HostSource
from qfield_testbed.state import Status


class FakeQgis(Component):
    """A stand-in `qgis` component recording the lifecycle calls it receives."""

    name = "qgis"
    calls: ClassVar[list[str]] = []

    def resolve(self) -> Revision:
        self.calls.append("resolve")
        return Revision(source=HostSource(), label="host-4.2.3")

    def materialize(self, revision: Revision) -> Path:
        self.calls.append("materialize")
        return self.ctx.paths.cache / revision.key

    def render(self, revision: Revision, materialized: Path) -> None:
        self.calls.append("render")
        (self.directory / "launcher").write_text("#!/bin/sh\n")

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def remove(self) -> None:
        self.calls.append("remove")

    def describe(self) -> dict[str, Any]:
        return {"profile": str(self.directory / "profiles" / "testbed")}


@pytest.fixture
def fake_qgis() -> type[FakeQgis]:
    saved = dict(REGISTRY)
    REGISTRY.clear()
    register(FakeQgis)
    FakeQgis.calls = []
    yield FakeQgis
    REGISTRY.clear()
    REGISTRY.update(saved)


SPEC = "name: demo\nqgis: {version: host}\n"
SPEC_WITH_CLOUD = "name: demo\nqfieldcloud: {version: release:v26.28}\nqgis: {version: host}\n"


def test_add_registers_and_generates_credentials(
    testbed: Testbed, fake_qgis: type[FakeQgis], runner: FakeRunner
) -> None:
    runner.reply(["docker", "ps"], stdout="0.0.0.0:30003->5432/tcp\n")  # busy port inside slot 0's block
    register(_cloud_stub())
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC_WITH_CLOUD))
    assert deployment.state.slot == 1
    assert deployment.state.credentials is not None
    assert deployment.state.credentials.username == "testbed"
    assert len(deployment.state.credentials.password) >= 12
    assert [d.name for d in testbed.deployments()] == ["demo"]


def test_add_refuses_unsupported_component(testbed: Testbed, fake_qgis: type[FakeQgis]) -> None:
    with pytest.raises(UnsupportedComponentError, match="qfieldcloud: not supported"):
        testbed.add(DeploymentSpec.from_yaml(SPEC_WITH_CLOUD))
    assert testbed.deployments() == []


def test_lifecycle(testbed: Testbed, fake_qgis: type[FakeQgis]) -> None:
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC))
    deployment.start()
    assert fake_qgis.calls == ["resolve", "materialize", "render", "start"]
    assert deployment.state.status is Status.RUNNING
    info = deployment.describe()
    assert info["components"]["qgis"]["revision"] == "host-4.2.3"
    assert info["components"]["qgis"]["profile"].endswith("profiles/testbed")
    env = deployment.env()
    assert env["QFT_DEPLOYMENT"] == "demo"
    assert env["QFT_REVISION_QGIS"] == "host-4.2.3"
    assert env["QFT_QGIS_PROFILE"].endswith("profiles/testbed")

    reloaded = testbed.deployment("demo")
    assert reloaded.state.component("qgis").status is Status.RUNNING

    reloaded.stop()
    assert reloaded.state.status is Status.STOPPED
    reloaded.remove()
    assert fake_qgis.calls[-2:] == ["stop", "remove"]
    assert not reloaded.directory.exists()
    with pytest.raises(UnknownDeploymentError):
        testbed.deployment("demo")


def test_start_failure_marks_deployment_failed(
    testbed: Testbed, fake_qgis: type[FakeQgis], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: FakeQgis) -> None:
        raise RuntimeError("no display")

    monkeypatch.setattr(fake_qgis, "start", boom)
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC))
    with pytest.raises(RuntimeError):
        deployment.start()
    assert testbed.deployment("demo").state.status is Status.FAILED


def _cloud_stub() -> type[Component]:
    class FakeCloud(FakeQgis):
        name = "qfieldcloud"

    return FakeCloud
