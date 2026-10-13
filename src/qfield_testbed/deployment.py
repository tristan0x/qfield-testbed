"""The deployment orchestrator: brings components up in order, tears them down, swaps one revision.

:class:`Testbed` is the entry point (config, paths, registry, cache, runner). :class:`Deployment` wraps one
registered deployment and drives its components through the lifecycle defined in :mod:`.components.base`.
"""

from __future__ import annotations

import logging
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from qfield_testbed.cache import Cache
from qfield_testbed.components import REGISTRY, Component, ComponentContext
from qfield_testbed.config import GlobalConfig
from qfield_testbed.errors import TestbedError, UnknownDeploymentError, UnsupportedComponentError
from qfield_testbed.paths import Paths
from qfield_testbed.ports import PortBlock, make_prober
from qfield_testbed.runtimes import docker
from qfield_testbed.runtimes.shell import Runner, SubprocessRunner
from qfield_testbed.spec import DeploymentSpec, VersionSource
from qfield_testbed.state import (
    Credentials,
    DeploymentState,
    Registry,
    Status,
    load_spec,
    load_state,
    save_state,
)

log = logging.getLogger(__name__)


class Testbed:
    def __init__(self, config: GlobalConfig | None = None, runner: Runner | None = None) -> None:
        self.config = config or GlobalConfig.load()
        self.runner = runner or SubprocessRunner()
        self.paths = Paths.from_config(self.config)
        self.registry = Registry(self.paths, port_base=self.config.port_base, max_slots=self.config.max_slots)
        self.cache = Cache(self.paths)

    # --- deployments ------------------------------------------------------------------------------------

    def add(self, spec: DeploymentSpec, *, session: str | None = None) -> Deployment:
        """Register a deployment: validate components for this host, allocate a slot, generate credentials."""
        self.paths.ensure()
        self._check_components(spec)
        prober = make_prober(docker.published_host_ports(self.runner))
        state = self.registry.register(spec, prober=prober, session=session)
        if spec.qfieldcloud:
            password = spec.qfieldcloud.test_account.password
            if password == "auto":  # noqa: S105 - sentinel, not a secret
                password = secrets.token_urlsafe(12)
            state.credentials = Credentials(username=spec.qfieldcloud.test_account.username, password=password)
        for name in spec.components():
            state.component(name)
        save_state(self.paths.deployment_dir(spec.name), state)
        log.info(
            "registered deployment '%s' on slot %d (ports %d-%d)",
            spec.name,
            state.slot,
            self.registry.port_block(state.slot).base,
            self.registry.port_block(state.slot).base + 99,
        )
        return Deployment(self, spec.name)

    def deployment(self, name: str) -> Deployment:
        if name not in self.registry.load().deployments:
            raise UnknownDeploymentError(name)
        return Deployment(self, name)

    def deployments(self) -> list[Deployment]:
        return [Deployment(self, name) for name in self.registry.names()]

    def _check_components(self, spec: DeploymentSpec) -> None:
        problems: list[str] = []
        for name, component_spec in spec.components().items():
            cls = REGISTRY.get(name)
            if cls is None:
                problems.append(f"{name}: not supported by this version of qfield-testbed")
                continue
            probe = Deployment.probe(self, spec, name, component_spec)
            problems.extend(f"{name}: {problem}" for problem in probe.check_host())
        if problems:
            raise UnsupportedComponentError("cannot run this spec on this host:\n  - " + "\n  - ".join(problems))


@dataclass
class Summary:
    name: str
    slot: int
    status: Status
    components: dict[str, Status]
    ports: PortBlock
    session: str | None


class Deployment:
    def __init__(self, testbed: Testbed, name: str) -> None:
        self.testbed = testbed
        self.name = name
        self.directory = testbed.paths.deployment_dir(name)
        self.spec = load_spec(self.directory)
        self.state = load_state(self.directory)
        self.ports = testbed.registry.port_block(self.state.slot)

    # --- components -------------------------------------------------------------------------------------

    def context(self) -> ComponentContext:
        return ComponentContext(
            spec=self.spec,
            state=self.state,
            directory=self.directory,
            ports=self.ports,
            paths=self.testbed.paths,
            config=self.testbed.config,
            cache=self.testbed.cache,
            runner=self.testbed.runner,
        )

    @staticmethod
    def probe(testbed: Testbed, spec: DeploymentSpec, name: str, component_spec: Any) -> Component:
        """Instantiate a component before the deployment exists (host checks at `add` time)."""
        ctx = ComponentContext(
            spec=spec,
            state=DeploymentState(name=spec.name, slot=-1),
            directory=testbed.paths.deployment_dir(spec.name),
            ports=PortBlock(base=0),
            paths=testbed.paths,
            config=testbed.config,
            cache=testbed.cache,
            runner=testbed.runner,
        )
        return REGISTRY[name](ctx, component_spec)

    def components(self, only: str | None = None) -> list[Component]:
        ctx = self.context()
        selected: list[Component] = []
        for name, component_spec in self.spec.components().items():
            if only and name != only:
                continue
            cls = REGISTRY.get(name)
            if cls is None:
                raise UnsupportedComponentError(f"component '{name}' is not supported by this version")
            selected.append(cls(ctx, component_spec))
        if only and not selected:
            raise TestbedError(f"deployment '{self.name}' has no component '{only}'")
        return selected

    @contextmanager
    def locked(self) -> Iterator[None]:
        with FileLock(self.directory / "deployment.lock", timeout=5):
            yield

    def save(self) -> None:
        save_state(self.directory, self.state)

    # --- lifecycle --------------------------------------------------------------------------------------

    def start(self, only: str | None = None) -> None:
        """Resolve, materialize, render, start, wait for health and wire each component in order."""
        with self.locked():
            self.state.status = Status.STARTING
            self.save()
            try:
                for component in self.components(only):
                    self._bring_up(component)
            except Exception:
                self.state.status = Status.FAILED
                self.save()
                raise
            self.state.status = Status.RUNNING
            self.save()

    def _bring_up(self, component: Component) -> None:
        log.info("[%s] resolving %s", component.name, getattr(component.spec, "version", "?"))
        revision = component.resolve()
        component.state.revision = revision
        component.state.status = Status.STARTING
        self.save()
        log.info("[%s] materializing %s", component.name, revision)
        materialized = component.materialize(revision)
        component.render(revision, materialized)
        log.info("[%s] starting", component.name)
        try:
            component.start()
            component.health()
        except Exception:
            component.state.status = Status.FAILED
            self.save()
            raise
        try:
            component.wire()
        except Exception:
            component.state.status = Status.WIRING_FAILED
            self.save()
            raise
        component.state.summary = component.describe()
        component.state.facts.update(component.state.summary)
        component.state.status = Status.RUNNING
        component.state.initialized = True
        self.save()

    def stop(self, only: str | None = None) -> None:
        with self.locked():
            for component in reversed(self.components(only)):
                log.info("[%s] stopping", component.name)
                component.stop()
                component.state.status = Status.STOPPED
            if only is None:
                self.state.status = Status.STOPPED
            self.save()

    def remove(self) -> None:
        """Tear everything down and forget the deployment; cached materializations are kept."""
        with self.locked():
            for component in reversed(self.components()):
                if component.state.status is not Status.CREATED:
                    log.info("[%s] removing", component.name)
                    component.remove()
        docker.remove_network(self.testbed.runner, self.context().network)
        self.testbed.cache.release(self.name)
        self.testbed.registry.unregister(self.name)
        shutil.rmtree(self.directory, ignore_errors=True)

    def swap(self, component_name: str, version: VersionSource) -> None:
        """Replace one component's version source and bring it up again (the bisect primitive)."""
        with self.locked():
            self.spec = self.spec.model_copy(deep=True)
            component_spec = self.spec.component(component_name)
            if component_spec is None:
                raise TestbedError(f"deployment '{self.name}' has no component '{component_name}'")
            # each component spec narrows `version` to its own sources; the caller passes one valid for this component
            component_spec.version = version  # pyright: ignore[reportAttributeAccessIssue]
            (self.directory / "spec.yaml").write_text(self.spec.to_yaml())
            component = self.components(component_name)[0]
            component.stop()
            component.reset()
            self._bring_up(component)
            # dependents are re-wired so they see the new server (e.g. QFieldSync after a QFieldCloud swap)
            for dependent in self.components():
                if dependent.name != component_name and dependent.state.status is Status.RUNNING:
                    dependent.wire()
            self.save()

    # --- introspection ----------------------------------------------------------------------------------

    def summary(self) -> Summary:
        entry = self.testbed.registry.load().deployments[self.name]
        return Summary(
            name=self.name,
            slot=self.state.slot,
            status=self.state.status,
            components={name: self.state.component(name).status for name in self.spec.components()},
            ports=self.ports,
            session=entry.session,
        )

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "name": self.name,
            "status": self.state.status,
            "slot": self.state.slot,
            "directory": str(self.directory),
            "network": self.context().network,
            "ports": self.ports.all_ports(),
            "components": {},
        }
        if self.state.credentials:
            info["test_account"] = self.state.credentials.model_dump()
        for name in self.spec.components():
            component_state = self.state.component(name)
            info["components"][name] = {
                "status": component_state.status,
                "version": str(getattr(self.spec.component(name), "version", "?")),
                "revision": str(component_state.revision) if component_state.revision else None,
                **component_state.summary,
            }
        return info

    def env(self) -> dict[str, str]:
        """The ``QFT_*`` environment for `deployment run` and bisect scripts."""
        variables = {
            "QFT_DEPLOYMENT": self.name,
            "QFT_DEPLOYMENT_DIR": str(self.directory),
            "QFT_NETWORK": self.context().network,
        }
        if self.state.credentials:
            variables["QFT_QFIELDCLOUD_USERNAME"] = self.state.credentials.username
            variables["QFT_QFIELDCLOUD_PASSWORD"] = self.state.credentials.password
            if self.state.credentials.token:
                variables["QFT_QFIELDCLOUD_TOKEN"] = self.state.credentials.token
        for component in self.components():
            variables.update(component.env())
        return variables

    def launcher(self, target: str) -> Path | None:
        path = self.directory / "bin" / target
        return path if path.is_file() else None
