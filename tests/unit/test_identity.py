"""Spec identity: equal for the same deployment, different as soon as what would run differs."""

from __future__ import annotations

from pathlib import Path

import pytest

from qfield_testbed.deployment import Testbed
from qfield_testbed.identity import normalize, spec_diff, spec_hash
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from tests.unit.test_cli_deployment import CliCloud, fake_components, isolate  # noqa: F401

SPEC = "name: demo\nqfieldcloud: {version: git:master}\nqgis: {version: host}\n"


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    return isolate(tmp_path, monkeypatch, runner)


def _hash(testbed: Testbed, text: str) -> str:
    return spec_hash(normalize(testbed, DeploymentSpec.from_yaml(text)))


@pytest.fixture
def master_at(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Where `git:<ref>` points: the test moves it."""
    head = ["a" * 40]

    def resolve(self: CliCloud) -> Revision:
        return Revision(source=self.spec.version, label="master", sha=head[0])  # type: ignore[union-attr]

    monkeypatch.setattr(CliCloud, "resolve", resolve)
    return head


@pytest.mark.usefixtures("fake_components", "master_at")
def test_same_deployment_same_hash(testbed: Testbed) -> None:
    reordered = "qgis: {version: host}\nname: other\ndescription: renamed\nqfieldcloud:\n  version: git:master\n"
    assert _hash(testbed, SPEC) == _hash(testbed, reordered)
    explicit_defaults = SPEC.replace("{version: git:master}", "{version: git:master, worker_replicas: 1}")
    assert _hash(testbed, SPEC) == _hash(testbed, explicit_defaults)


@pytest.mark.usefixtures("fake_components")
def test_a_moving_ref_changes_the_identity(testbed: Testbed, master_at: list[str]) -> None:
    before = normalize(testbed, DeploymentSpec.from_yaml(SPEC))
    master_at[0] = "b" * 40
    after = normalize(testbed, DeploymentSpec.from_yaml(SPEC))
    assert spec_hash(before) != spec_hash(after)
    diff = spec_diff(before, after)
    assert "-    ref: " + "a" * 40 in diff
    assert "+    ref: " + "b" * 40 in diff


@pytest.mark.usefixtures("fake_components", "master_at")
def test_options_change_the_identity(testbed: Testbed) -> None:
    assert _hash(testbed, SPEC) != _hash(testbed, SPEC.replace("{version: host}", "{version: docker:3.44}"))
