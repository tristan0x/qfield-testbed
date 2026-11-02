"""Persistent state: the registry of deployments (slots) and each deployment's own state file.

Both are small JSON documents guarded by a file lock so concurrent ``qft`` invocations (two terminals, a
bisect session plus a manual `show`) never corrupt them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, model_validator

from qfield_testbed.errors import DeploymentExistsError, NoFreeSlotError, UnknownDeploymentError
from qfield_testbed.paths import Paths
from qfield_testbed.ports import PortBlock, PortProber, find_free_slot
from qfield_testbed.revisions import Revision
from qfield_testbed.spec import DeploymentSpec


def _now() -> datetime:
    return datetime.now(tz=UTC)


class Status(StrEnum):
    CREATED = "created"  # registered, nothing materialized yet
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    WIRING_FAILED = "wiring failed"  # up and healthy, but clients could not be pointed at it


class ComponentState(BaseModel):
    """What a component recorded about itself: its revision, whether it was initialized, and runtime facts."""

    model_config = ConfigDict(extra="forbid")

    revision: Revision | None = None
    initialized: bool = False
    status: Status = Status.CREATED
    facts: dict[str, Any] = Field(default_factory=dict, description="Raw facts recorded by the component")
    summary: dict[str, Any] = Field(default_factory=dict, description="What `describe()` returned; shown by `show`")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    token: str | None = None


class DeploymentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    slot: int
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    status: Status = Status.CREATED
    credentials: Credentials | None = None
    components: dict[str, ComponentState] = Field(default_factory=dict)

    def component(self, name: str) -> ComponentState:
        return self.components.setdefault(name, ComponentState())


Owner = Literal["human", "agent", "bisect"]


class RegistryEntry(BaseModel):
    """A deployment in the registry: its slot, who owns it and when it expires (see docs/isolation.md)."""

    model_config = ConfigDict(extra="forbid")

    slot: int
    # looked up at call time, so tests can move the clock
    created_at: datetime = Field(default_factory=lambda: _now())  # noqa: PLW0108
    session: str | None = Field(default=None, description="Bisect session owning this deployment, if any")
    owner: Owner = "human"
    agent_session: str | None = Field(default=None, description="Agent session that created it (QFT_AGENT_SESSION)")
    last_used_at: datetime = Field(default_factory=lambda: _now())  # noqa: PLW0108
    ttl_s: float | None = Field(default=None, description="Removable by `qft gc` this long after its last use")

    @model_validator(mode="before")
    @classmethod
    def _from_older_registries(cls, data: Any) -> Any:
        """Entries written before owners existed: bisect-owned when a session is set, human otherwise."""
        if isinstance(data, dict) and "owner" not in data:
            data = {**data, "owner": "bisect" if data.get("session") else "human"}
            if "created_at" in data:
                data.setdefault("last_used_at", data["created_at"])
        return data

    @property
    def expires_at(self) -> datetime | None:
        """Sliding expiry: every use pushes it back by the TTL."""
        return self.last_used_at + timedelta(seconds=self.ttl_s) if self.ttl_s is not None else None


class RegistryData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployments: dict[str, RegistryEntry] = Field(default_factory=dict)


class Registry:
    """Index of deployments and their slots; the single source of truth for slot allocation."""

    def __init__(self, paths: Paths, *, port_base: int, max_slots: int) -> None:
        self.paths = paths
        self.port_base = port_base
        self.max_slots = max_slots

    @contextmanager
    def locked(self) -> Iterator[RegistryData]:
        """Load, yield for mutation, save — all under the registry lock."""
        self.paths.home.mkdir(parents=True, exist_ok=True)
        with FileLock(self.paths.registry_lock, timeout=30):
            data = self.load()
            yield data
            self.paths.registry_file.write_text(data.model_dump_json(indent=2) + "\n")

    def load(self) -> RegistryData:
        if not self.paths.registry_file.is_file():
            return RegistryData()
        return RegistryData.model_validate_json(self.paths.registry_file.read_text())

    def names(self) -> list[str]:
        return sorted(self.load().deployments)

    def register(
        self,
        spec: DeploymentSpec,
        *,
        prober: PortProber,
        session: str | None = None,
        agent_session: str | None = None,
        ttl_s: float | None = None,
    ) -> DeploymentState:
        """Allocate a slot, write the spec and the initial state; the deployment dir is created here."""
        with self.locked() as data:
            if spec.name in data.deployments:
                raise DeploymentExistsError(spec.name)
            used = (entry.slot for entry in data.deployments.values())
            slot = find_free_slot(used, port_base=self.port_base, max_slots=self.max_slots, prober=prober)
            if slot is None:
                raise NoFreeSlotError(self.max_slots)
            owner: Owner = "bisect" if session else "agent" if agent_session else "human"
            data.deployments[spec.name] = RegistryEntry(
                slot=slot, session=session, owner=owner, agent_session=agent_session, ttl_s=ttl_s
            )
            directory = self.paths.deployment_dir(spec.name)
            directory.mkdir(parents=True, exist_ok=False)
            (directory / "spec.yaml").write_text(spec.to_yaml())
            state = DeploymentState(name=spec.name, slot=slot)
            save_state(directory, state)
            return state

    def touch(self, name: str) -> None:
        """Record a use of ``name`` now: its TTL starts over."""
        with self.locked() as data:
            if name not in data.deployments:
                raise UnknownDeploymentError(name)
            data.deployments[name].last_used_at = _now()

    def set_ttl(self, name: str, ttl_s: float | None) -> None:
        """Replace the TTL of ``name`` (None: never expires); counts as a use."""
        with self.locked() as data:
            if name not in data.deployments:
                raise UnknownDeploymentError(name)
            data.deployments[name].ttl_s = ttl_s
            data.deployments[name].last_used_at = _now()

    def unregister(self, name: str) -> None:
        with self.locked() as data:
            if name not in data.deployments:
                raise UnknownDeploymentError(name)
            del data.deployments[name]

    def port_block(self, slot: int) -> PortBlock:
        return PortBlock.for_slot(self.port_base, slot)


def state_file(directory: Path) -> Path:
    return directory / "state.json"


def load_state(directory: Path) -> DeploymentState:
    file = state_file(directory)
    if not file.is_file():
        raise UnknownDeploymentError(directory.name)
    return DeploymentState.model_validate_json(file.read_text())


def save_state(directory: Path, state: DeploymentState) -> None:
    state.updated_at = _now()
    tmp = state_file(directory).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.model_dump(mode="json"), indent=2) + "\n")
    tmp.replace(state_file(directory))


def load_spec(directory: Path) -> DeploymentSpec:
    return DeploymentSpec.from_file(directory / "spec.yaml")
