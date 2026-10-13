"""Persistent state: the registry of deployments (slots) and each deployment's own state file.

Both are small JSON documents guarded by a file lock so concurrent ``qft`` invocations (two terminals, a
bisect session plus a manual `show`) never corrupt them.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

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


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: int
    created_at: datetime = Field(default_factory=_now)
    session: str | None = Field(default=None, description="Bisect session owning this deployment, if any")


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

    def register(self, spec: DeploymentSpec, *, prober: PortProber, session: str | None = None) -> DeploymentState:
        """Allocate a slot, write the spec and the initial state; the deployment dir is created here."""
        with self.locked() as data:
            if spec.name in data.deployments:
                raise DeploymentExistsError(spec.name)
            used = (entry.slot for entry in data.deployments.values())
            slot = find_free_slot(used, port_base=self.port_base, max_slots=self.max_slots, prober=prober)
            if slot is None:
                raise NoFreeSlotError(self.max_slots)
            data.deployments[spec.name] = RegistryEntry(slot=slot, session=session)
            directory = self.paths.deployment_dir(spec.name)
            directory.mkdir(parents=True, exist_ok=False)
            (directory / "spec.yaml").write_text(spec.to_yaml())
            state = DeploymentState(name=spec.name, slot=slot)
            save_state(directory, state)
            return state

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
