from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from qfield_testbed.components import REGISTRY, Component, register
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import (
    HealthTimeoutError,
    TestbedError,
    TimeoutExceededError,
    UnknownDeploymentError,
    UnsupportedComponentError,
    WiringError,
)
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
def fake_qgis() -> Iterator[type[FakeQgis]]:
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


@pytest.mark.parametrize(
    ("step", "status"),
    [("start", Status.FAILED), ("health", Status.FAILED), ("wire", Status.WIRING_FAILED)],
)
def test_start_failure_records_the_component_status(
    testbed: Testbed, fake_qgis: type[FakeQgis], monkeypatch: pytest.MonkeyPatch, step: str, status: Status
) -> None:
    def boom(self: FakeQgis) -> None:
        raise RuntimeError(f"{step} broke")

    monkeypatch.setattr(fake_qgis, step, boom)
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC))
    with pytest.raises(RuntimeError, match=f"{step} broke"):
        deployment.start()
    reloaded = testbed.deployment("demo")
    assert reloaded.state.status is Status.FAILED
    assert reloaded.state.components["qgis"].status is status


def _cloud_stub() -> type[Component]:
    class FakeCloud(FakeQgis):
        name = "qfieldcloud"

    return FakeCloud


# --- readiness: --timeout -------------------------------------------------------------------------------------


class Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr("qfield_testbed.deployment.time.monotonic", clock)
    return clock


class TimedQgis(FakeQgis):
    """Records the health timeout it gets; materializing takes `build_s` seconds of the test clock."""

    health_timeouts: ClassVar[list[float | None]] = []
    build_s: ClassVar[float] = 0.0
    clock: ClassVar[Clock | None] = None

    def materialize(self, revision: Revision) -> Path:
        if self.clock:
            self.clock.now += self.build_s
        return super().materialize(revision)

    def health(self, timeout: float = 180) -> None:
        self.health_timeouts.append(timeout)


@pytest.fixture
def timed_qgis(fake_qgis: type[FakeQgis], clock: Clock) -> type[TimedQgis]:
    register(TimedQgis)
    TimedQgis.health_timeouts, TimedQgis.build_s, TimedQgis.clock = [], 0.0, clock
    return TimedQgis


def test_without_timeout_each_health_check_keeps_its_own(testbed: Testbed, timed_qgis: type[TimedQgis]) -> None:
    testbed.add(DeploymentSpec.from_yaml(SPEC)).start()
    assert timed_qgis.health_timeouts == [180]


def test_timeout_gives_health_the_remaining_budget(testbed: Testbed, timed_qgis: type[TimedQgis]) -> None:
    timed_qgis.build_s = 100
    testbed.add(DeploymentSpec.from_yaml(SPEC)).start(timeout=600)
    assert timed_qgis.health_timeouts == [500]


def test_timeout_spent_by_a_build_fails_before_the_next_step(testbed: Testbed, timed_qgis: type[TimedQgis]) -> None:
    timed_qgis.build_s = 700
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC))
    with pytest.raises(TimeoutExceededError) as raised:
        deployment.start(timeout=600)
    assert raised.value.details == {"component": "qgis", "step": "render", "elapsed_s": 700.0}
    assert timed_qgis.health_timeouts == []
    assert testbed.deployment("demo").state.status is Status.FAILED


class FailingQgis(FakeQgis):
    """Fails at `failing_step` with `error`; has logs to attach."""

    error: ClassVar[TestbedError] = HealthTimeoutError("http://x/ not healthy", details={"probe": "http://x/"})
    failing_step: ClassVar[str] = "health"

    def health(self, timeout: float = 180) -> None:
        if self.failing_step == "health":
            raise self.error

    def wire(self) -> None:
        if self.failing_step == "wire":
            raise self.error

    def logs(self, **_: Any) -> str | None:
        return "line 1\nboom\n"


@pytest.fixture
def failing_qgis(fake_qgis: type[FakeQgis]) -> type[FailingQgis]:
    register(FailingQgis)
    return FailingQgis


def test_health_failure_names_component_step_and_logs(testbed: Testbed, failing_qgis: type[FailingQgis]) -> None:
    failing_qgis.failing_step = "health"
    failing_qgis.error = HealthTimeoutError("http://x/ not healthy", details={"probe": "http://x/"})
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC))
    with pytest.raises(HealthTimeoutError) as raised:
        deployment.start()
    details = raised.value.details
    assert (details["component"], details["step"], details["probe"]) == ("qgis", "health", "http://x/")
    assert details["log_tail"] == "line 1\nboom\n"
    assert str(raised.value) == "qgis: http://x/ not healthy"
    assert testbed.deployment("demo").state.component("qgis").status is Status.FAILED


def test_wiring_failure_names_its_step(testbed: Testbed, failing_qgis: type[FailingQgis]) -> None:
    failing_qgis.failing_step = "wire"
    failing_qgis.error = WiringError("qgis is up but wiring failed")
    deployment = testbed.add(DeploymentSpec.from_yaml(SPEC))
    with pytest.raises(WiringError) as raised:
        deployment.start()
    assert raised.value.details["step"] == "wire"
    assert str(raised.value) == "qgis is up but wiring failed"  # already named: no prefix added
    assert testbed.deployment("demo").state.component("qgis").status is Status.WIRING_FAILED
