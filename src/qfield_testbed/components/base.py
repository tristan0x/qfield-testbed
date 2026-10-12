"""The component lifecycle every product implements.

``resolve → materialize → render → start → health → wire`` brings a component up; ``stop`` and ``remove``
take it down. ``describe`` exposes URLs, ports and paths for ``qft deployment show`` and the ``QFT_*``
environment handed to bisect scripts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from qfield_testbed.state import ComponentState

if TYPE_CHECKING:
    from qfield_testbed.cache import Cache
    from qfield_testbed.config import GlobalConfig
    from qfield_testbed.paths import Paths
    from qfield_testbed.ports import PortBlock
    from qfield_testbed.revisions import Revision
    from qfield_testbed.runtimes.shell import Runner
    from qfield_testbed.spec import ComponentSpec, DeploymentSpec
    from qfield_testbed.state import DeploymentState


@dataclass
class ComponentContext:
    """Everything a component needs to know about the deployment it belongs to."""

    spec: DeploymentSpec
    state: DeploymentState
    directory: Path
    ports: PortBlock
    paths: Paths
    config: GlobalConfig
    cache: Cache
    runner: Runner

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def network(self) -> str:
        """Docker network shared by every container of the deployment."""
        return f"qft-{self.spec.name}"

    def facts(self, component: str) -> dict[str, Any]:
        """Facts another component recorded (e.g. QFieldCloud's URL, needed by every client)."""
        return self.state.components.get(component, ComponentState()).facts


class Component(ABC):
    name: ClassVar[str]
    """Glossary name of the component, also the key in the spec (e.g. ``qfield-android``)."""

    def __init__(self, ctx: ComponentContext, spec: ComponentSpec) -> None:
        self.ctx = ctx
        self.spec = spec

    @property
    def state(self) -> ComponentState:
        return self.ctx.state.component(self.name)

    @property
    def directory(self) -> Path:
        """Private directory of the component inside the deployment directory."""
        path = self.ctx.directory / self.name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def check_host(self) -> list[str]:
        """Problems that make this component unusable on this host (tools missing, no KVM…); empty = fine."""
        return []

    @abstractmethod
    def resolve(self) -> Revision:
        """Turn the version source into an immutable revision (no side effects beyond fetching metadata)."""

    @abstractmethod
    def materialize(self, revision: Revision) -> Path:
        """Fetch or build the revision into the shared cache; return its path. Idempotent."""

    @abstractmethod
    def render(self, revision: Revision, materialized: Path) -> None:
        """Write the deployment-specific files (compose, env, profile…) under :attr:`directory`."""

    @abstractmethod
    def start(self) -> None: ...

    def health(self, timeout: float = 180) -> None:  # noqa: B027 - optional hook
        """Block until ready or raise :class:`~qfield_testbed.errors.HealthTimeoutError`."""

    def wire(self) -> None:  # noqa: B027 - optional hook
        """Point the component at the deployment's QFieldCloud with the test account."""

    @abstractmethod
    def stop(self) -> None: ...

    def reset(self) -> None:  # noqa: B027 - optional hook
        """Wipe persistent data (volumes, profile state) so the next start is pristine; used by bisect swaps."""

    @abstractmethod
    def remove(self) -> None:
        """Tear down everything the component created outside the cache (containers, volumes, networks)."""

    def describe(self) -> dict[str, Any]:
        """Facts worth showing; defaults to what was recorded in the state."""
        return dict(self.state.facts)

    def env(self) -> dict[str, str]:
        """``QFT_*`` variables exported to `deployment run` and bisect scripts."""
        prefix = f"QFT_{self.name.upper().replace('-', '_')}"
        variables = {f"{prefix}_{key.upper()}": str(value) for key, value in self.describe().items()}
        if self.state.revision:
            variables[f"QFT_REVISION_{self.name.upper().replace('-', '_')}"] = str(self.state.revision)
        return variables
