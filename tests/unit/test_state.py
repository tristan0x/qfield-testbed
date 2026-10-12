from __future__ import annotations

import pytest

from qfield_testbed.config import GlobalConfig
from qfield_testbed.errors import DeploymentExistsError, NoFreeSlotError, UnknownDeploymentError
from qfield_testbed.paths import Paths
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import Registry, Status, load_spec, load_state, save_state


def _spec(name: str) -> DeploymentSpec:
    return DeploymentSpec.from_yaml(f"name: {name}\nqgis: {{version: host}}\n")


@pytest.fixture
def registry(config: GlobalConfig) -> Registry:
    paths = Paths.from_config(config)
    paths.ensure()
    return Registry(paths, port_base=config.port_base, max_slots=config.max_slots)


def test_register_allocates_increasing_slots_and_writes_files(registry: Registry) -> None:
    first = registry.register(_spec("a"), prober=lambda _p: True)
    second = registry.register(_spec("b"), prober=lambda _p: True)
    assert (first.slot, second.slot) == (0, 1)
    directory = registry.paths.deployment_dir("a")
    assert load_spec(directory).name == "a"
    assert load_state(directory).status is Status.CREATED
    assert registry.names() == ["a", "b"]


def test_register_reuses_freed_slot(registry: Registry) -> None:
    registry.register(_spec("a"), prober=lambda _p: True)
    registry.register(_spec("b"), prober=lambda _p: True)
    registry.unregister("a")
    assert registry.register(_spec("c"), prober=lambda _p: True).slot == 0


def test_duplicate_name_rejected(registry: Registry) -> None:
    registry.register(_spec("a"), prober=lambda _p: True)
    with pytest.raises(DeploymentExistsError):
        registry.register(_spec("a"), prober=lambda _p: True)


def test_no_free_slot(registry: Registry) -> None:
    with pytest.raises(NoFreeSlotError):
        registry.register(_spec("a"), prober=lambda _p: False)


def test_unregister_unknown(registry: Registry) -> None:
    with pytest.raises(UnknownDeploymentError):
        registry.unregister("ghost")


def test_state_roundtrip(registry: Registry) -> None:
    state = registry.register(_spec("a"), prober=lambda _p: True)
    state.component("qgis").facts["profile"] = "/x"
    state.status = Status.RUNNING
    directory = registry.paths.deployment_dir("a")
    save_state(directory, state)
    loaded = load_state(directory)
    assert loaded.status is Status.RUNNING
    assert loaded.component("qgis").facts == {"profile": "/x"}
    assert loaded.updated_at >= loaded.created_at
