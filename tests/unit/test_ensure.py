"""`qft deployment ensure`: the same spec converges on one ready deployment; a different one is refused."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import AlreadyExistsError
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import Status
from tests.unit.test_cli_deployment import CliCloud, CliQgis, fake_components, invoke, isolate  # noqa: F401

SPEC = "name: demo\nqfieldcloud: {version: git:master}\nqgis: {version: host}\n"


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    return isolate(tmp_path, monkeypatch, runner)


@pytest.fixture
def master_at(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    head = ["a" * 40]

    def resolve(self: CliCloud) -> Revision:
        return Revision(source=self.spec.version, label="master", sha=head[0])  # type: ignore[union-attr]

    monkeypatch.setattr(CliCloud, "resolve", resolve)
    return head


def _ensure(testbed: Testbed, text: str = SPEC, **options: object):  # type: ignore[no-untyped-def]
    return testbed.ensure(DeploymentSpec.from_yaml(text), **options)  # type: ignore[arg-type]


@pytest.mark.usefixtures("fake_components", "master_at")
def test_ensure_creates_then_reuses(testbed: Testbed) -> None:
    first = _ensure(testbed)
    assert (first.created, first.started, first.info.status) == (True, True, Status.RUNNING)
    starts = CliQgis.calls.count("start qgis")
    again = _ensure(testbed)
    assert (again.created, again.started, again.spec_hash) == (False, False, first.spec_hash)
    assert CliQgis.calls.count("start qgis") == starts  # running: not restarted
    testbed.deployment("demo").stop()
    assert _ensure(testbed).started is True


@pytest.mark.usefixtures("fake_components")
def test_another_spec_is_refused_with_a_diff_unless_replaced(testbed: Testbed, master_at: list[str]) -> None:
    first = _ensure(testbed)
    master_at[0] = "b" * 40  # master moved on: another deployment
    with pytest.raises(AlreadyExistsError) as refused:
        _ensure(testbed)
    assert "+    ref: " + "b" * 40 in refused.value.details["diff"]
    assert refused.value.details["existing_hash"] == first.spec_hash
    replaced = _ensure(testbed, replace=True)
    assert replaced.created is True
    assert replaced.spec_hash != first.spec_hash


@pytest.mark.usefixtures("fake_components", "master_at")
def test_auto_name_follows_the_identity(testbed: Testbed) -> None:
    one = _ensure(testbed, auto_name=True)
    assert one.info.name.startswith("demo-")
    assert len(one.info.name) == len("demo-") + 6
    assert _ensure(testbed, auto_name=True).info.name == one.info.name
    other = _ensure(testbed, SPEC.replace("{version: host}", "{version: docker:3.44}"), auto_name=True)
    assert other.info.name != one.info.name


@pytest.mark.usefixtures("fake_components", "master_at")
def test_concurrent_ensures_converge(testbed: Testbed) -> None:
    results = []
    threads = [threading.Thread(target=lambda: results.append(_ensure(testbed))) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(r.created for r in results) == [False, False, True]
    assert testbed.registry.names() == ["demo"]


@pytest.mark.usefixtures("fake_components", "master_at")
def test_ensure_command(testbed: Testbed, tmp_path: Path) -> None:
    spec_file = tmp_path / "demo.yaml"
    spec_file.write_text(SPEC)
    first = json.loads(invoke(["--json", "deployment", "ensure", str(spec_file), "--timeout", "10m"]).stdout)["data"]
    assert (first["name"], first["created"], first["status"]) == ("demo", True, "running")
    again = invoke(["deployment", "ensure", "-"], stdin=SPEC)
    assert "deployment demo reused" in again.output
    changed = invoke(["deployment", "ensure", "-", "--set", "qfieldcloud.worker_replicas=2"], stdin=SPEC)
    assert isinstance(changed.exception, AlreadyExistsError)
    assert testbed.registry.load().deployments["demo"].spec_hash == first["spec_hash"]
