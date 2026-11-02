from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


# --- ownership and TTL ----------------------------------------------------------------------------------------


def test_registries_written_before_owners_load_unchanged(registry: Registry) -> None:
    registry.paths.registry_file.write_text(
        '{"deployments": {'
        '"old": {"slot": 0, "created_at": "2026-10-01T10:00:00Z", "session": null},'
        '"bs-x": {"slot": 1, "created_at": "2026-10-02T10:00:00Z", "session": "x"}}}'
    )
    entries = registry.load().deployments
    assert (entries["old"].owner, entries["bs-x"].owner) == ("human", "bisect")
    assert entries["old"].last_used_at == entries["old"].created_at
    assert entries["old"].expires_at is None


def test_owner_follows_who_registers(registry: Registry) -> None:
    registry.register(_spec("a"), prober=lambda _p: True)
    registry.register(_spec("b"), prober=lambda _p: True, agent_session="s1", ttl_s=3600)
    registry.register(_spec("c"), prober=lambda _p: True, session="hunt")
    entries = registry.load().deployments
    assert [entries[n].owner for n in "abc"] == ["human", "agent", "bisect"]
    assert entries["b"].agent_session == "s1"


def test_ttl_slides_with_each_use(registry: Registry, monkeypatch: pytest.MonkeyPatch) -> None:
    start = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    now = [start]
    monkeypatch.setattr("qfield_testbed.state._now", lambda: now[0])
    registry.register(_spec("a"), prober=lambda _p: True, agent_session="s1", ttl_s=3600)
    assert registry.load().deployments["a"].expires_at == start + timedelta(hours=1)
    now[0] = start + timedelta(minutes=50)
    registry.touch("a")
    assert registry.load().deployments["a"].expires_at == start + timedelta(minutes=110)
    registry.set_ttl("a", None)
    assert registry.load().deployments["a"].expires_at is None
    with pytest.raises(UnknownDeploymentError):
        registry.touch("nope")
