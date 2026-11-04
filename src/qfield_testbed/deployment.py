"""The deployment orchestrator: brings components up in order, tears them down, swaps one revision.

:class:`Testbed` is the entry point (config, paths, registry, cache, runner). :class:`Deployment` wraps one
registered deployment and drives its components through the lifecycle defined in :mod:`.components.base`.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from qfield_testbed.cache import Cache
from qfield_testbed.caller import CALLER
from qfield_testbed.components import REGISTRY, Component, ComponentContext
from qfield_testbed.config import GlobalConfig
from qfield_testbed.durations import parse_duration
from qfield_testbed.errors import (
    AlreadyExistsError,
    NotFoundError,
    RefusedError,
    ResourceExhaustedError,
    TestbedError,
    TimeoutExceededError,
    UnknownDeploymentError,
    UnsupportedComponentError,
)
from qfield_testbed.identity import normalize, spec_diff, spec_hash
from qfield_testbed.paths import Paths
from qfield_testbed.ports import BLOCK_SIZE, PortBlock, make_prober
from qfield_testbed.runtimes import docker
from qfield_testbed.runtimes.shell import ProcessResult, Runner, SubprocessRunner, run_process
from qfield_testbed.spec import DeploymentSpec, HostSource, VersionSource
from qfield_testbed.state import (
    Credentials,
    DeploymentState,
    Owner,
    Registry,
    RegistryEntry,
    Status,
    load_spec,
    load_state,
    save_state,
)

log = logging.getLogger(__name__)

# who wants to hear about each step of bringing a component up, as (component, step): the MCP server's jobs
STEP_SINK: ContextVar[Callable[[str, str], None] | None] = ContextVar("STEP_SINK", default=None)


class Testbed:
    def __init__(self, config: GlobalConfig | None = None, runner: Runner | None = None) -> None:
        self.config = config or GlobalConfig.load()
        self.runner = runner or SubprocessRunner()
        self.paths = Paths.from_config(self.config)
        self.registry = Registry(self.paths, port_base=self.config.port_base, max_slots=self.config.max_slots)
        self.cache = Cache(self.paths)

    # --- deployments ------------------------------------------------------------------------------------

    def add(
        self,
        spec: DeploymentSpec,
        *,
        session: str | None = None,
        agent_session: str | None = None,
        ttl: float | Literal["default"] | None = "default",
    ) -> Deployment:
        """Register a deployment: validate components for this host, allocate a slot, generate credentials.

        ``session`` is the owning bisect session, ``agent_session`` the agent session creating it. ``ttl`` (seconds,
        None for none) defaults to `agent_ttl` from the config for agents and to no expiry otherwise.
        """
        self.paths.ensure()
        self._check_components(spec)
        prober = make_prober(docker.published_host_ports(self.runner))
        if agent_session and not session:
            agents = sum(1 for entry in self.registry.load().deployments.values() if entry.owner == "agent")
            if agents >= self.config.max_agent_deployments:
                raise ResourceExhaustedError(
                    f"{agents} agent deployments exist already (max_agent_deployments: "
                    f"{self.config.max_agent_deployments})",
                    hint="remove some (`qft gc`, `qft deployment list --owner agent`) or raise max_agent_deployments",
                    docs="isolation",
                )
        if ttl == "default":
            ttl = parse_ttl(self.config.agent_ttl) if agent_session and not session else None
        state = self.registry.register(spec, prober=prober, session=session, agent_session=agent_session, ttl_s=ttl)
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

    def ensure(
        self,
        spec: DeploymentSpec,
        *,
        auto_name: bool = False,
        replace: bool = False,
        force: bool = False,
        timeout: float | None = None,
        ttl: float | Literal["default"] | None = "default",
    ) -> EnsureResult:
        """Make sure a deployment of ``spec`` exists and is ready: reuse it when its identity matches, else create it.

        A deployment with the same name but another identity is refused with a diff, unless ``replace``.
        ``auto_name`` names it after its identity, so that equal specs share one deployment.
        """
        identity = normalize(self, spec)
        digest = spec_hash(identity)
        if auto_name:
            spec = spec.model_copy(update={"name": f"{spec.name[:13].rstrip('-')}-{digest[:6]}"})
        name = spec.name
        locks = self.paths.home / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        # concurrent `ensure` of one name: the second waits for the first, then reuses what it made
        with FileLock(locks / f"{name}.lock", timeout=ENSURE_LOCK_TIMEOUT_S):
            entry = self.registry.load().deployments.get(name)
            if entry is not None and entry.spec_hash != digest:
                if not replace:
                    raise AlreadyExistsError(
                        f"deployment '{name}' exists with another spec",
                        hint="pass --replace to recreate it, or --name auto to keep both",
                        details={
                            "existing_hash": entry.spec_hash,
                            "requested_hash": digest,
                            "diff": spec_diff(self._identity(name), identity),
                        },
                    )
                self.authorize(name, "replace", force=force)
                self.deployment(name).remove()
                entry = None
            warnings: list[str] = []
            if entry is None:
                deployment = self.add(spec, agent_session=CALLER.agent_session, ttl=ttl)
                self.registry.set_spec_hash(name, digest)
                (deployment.directory / IDENTITY_FILE).write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
                deployment.state.host_qgis = _host_qgis(self, spec)
                deployment.save()
            else:
                deployment = self.deployment(name, touch=True)
                current = _host_qgis(self, spec)
                if deployment.state.host_qgis and current and current != deployment.state.host_qgis:
                    warnings.append(
                        f"the host QGIS changed since '{name}' was created ({deployment.state.host_qgis} → {current})"
                    )
            started = deployment.state.status is not Status.RUNNING
            if started:
                deployment.start(timeout=timeout)
            return EnsureResult(deployment.info(), entry is None, started, digest, warnings)

    def _identity(self, name: str) -> dict[str, Any]:
        file = self.paths.deployment_dir(name) / IDENTITY_FILE
        return json.loads(file.read_text()) if file.is_file() else {}

    def authorize(self, name: str, action: str, *, force: bool = False) -> None:
        """Refuse an agent session to ``action`` a deployment it does not own, unless ``force``; people may."""
        caller = CALLER.agent_session
        if caller is None or force:
            return
        entry = self.registry.load().deployments.get(name)
        if entry is None or (entry.owner == "agent" and entry.agent_session == caller):
            return
        whose = {
            "human": "a person",
            "bisect": f"bisect session '{entry.session}'",
            "agent": f"agent session '{entry.agent_session}'",
        }[entry.owner]
        raise RefusedError(
            f"agent session '{caller}' may not {action} deployment '{name}': it belongs to {whose}",
            hint="act on your own deployments (`qft deployment list --agent-session …`), or pass --force",
            docs="isolation",
            details={"owner": entry.owner, "agent_session": entry.agent_session, "bisect_session": entry.session},
        )

    def deployment(self, name: str, *, touch: bool = False) -> Deployment:
        """The deployment ``name``; ``touch`` records a use, which restarts its TTL."""
        if name not in self.registry.load().deployments:
            raise UnknownDeploymentError(name)
        if touch:
            self.registry.touch(name)
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
class Deadline:
    """The time budget of a start: what is left of ``timeout`` seconds (None: no bound), measured from ``started``."""

    timeout: float | None = None
    started: float = field(default_factory=lambda: time.monotonic())  # noqa: PLW0108 - looked up at call time, so tests can move the clock

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 1)

    def remaining(self, component: str, step: str) -> float | None:
        """Seconds left before ``step`` of ``component``; a timeout error naming them once none is left."""
        if self.timeout is None:
            return None
        left = self.timeout - (time.monotonic() - self.started)
        if left <= 0:
            raise TimeoutExceededError(
                f"{component} was not ready within {self.timeout:.0f}s (before its {step} step)",
                hint="raise --timeout; a first start builds or pulls images and can take long",
                details={"component": component, "step": step, "elapsed_s": self.elapsed()},
            )
        return left


LOG_TAIL_LINES = 30
IDENTITY_FILE = "identity.json"  # the normalized spec a deployment was ensured from, for diffs
ENSURE_LOCK_TIMEOUT_S = 3 * 3600  # a first start may build images for a long time


@dataclass
class EnsureResult:
    info: DeploymentInfo
    created: bool
    started: bool
    spec_hash: str
    warnings: list[str]


def _host_qgis(testbed: Testbed, spec: DeploymentSpec) -> str | None:
    """The version of the host QGIS a spec uses (`qgis: host`), or None."""
    if spec.qgis is None or not isinstance(spec.qgis.version, HostSource):
        return None
    try:
        return Deployment.probe(testbed, spec, "qgis", spec.qgis).resolve().label
    except TestbedError:
        return None


def parse_ttl(value: str) -> float | None:
    """A TTL from the config or the command line: a duration (`8h`, `3d`) or `none`."""
    return None if value.lower() == "none" else parse_duration(value).total_seconds()


def _ownership(entry: RegistryEntry) -> dict[str, Any]:
    return {
        "owner": entry.owner,
        "agent_session": entry.agent_session,
        "created_at": entry.created_at,
        "last_used_at": entry.last_used_at,
        "ttl_s": entry.ttl_s,
        "expires_at": entry.expires_at,
    }


@contextmanager
def _step(component: Component, step: str, deadline: Deadline) -> Iterator[float | None]:
    """Run one step of bringing ``component`` up: within the deadline, and failures say where they happened.

    Yields the seconds left (None without a timeout). A :class:`TestbedError` gets the component, the step, the
    time spent and the component's last log lines in its ``details``, and the component name in its message.
    """
    remaining = deadline.remaining(component.name, step)
    if (sink := STEP_SINK.get()) is not None:
        try:
            sink(component.name, step)
        except Exception:
            log.debug("step sink failed", exc_info=True)
    try:
        yield remaining
    except TestbedError as exc:
        exc.details.setdefault("component", component.name)
        exc.details.setdefault("step", step)
        exc.details.setdefault("elapsed_s", deadline.elapsed())
        if "log_tail" not in exc.details and (tail := _log_tail(component)):
            exc.details["log_tail"] = tail
        if not exc.message.startswith(component.name):
            exc.message = f"{component.name}: {exc.message}"
            exc.args = (exc.message, *exc.args[1:])
        raise


def _log_tail(component: Component) -> str | None:
    try:
        return component.logs(tail=LOG_TAIL_LINES, capture=True) or None
    except Exception:
        log.debug("[%s] no logs to attach", component.name, exc_info=True)
        return None


class DeploymentSummary(BaseModel):
    """One line of `qft deployment list`; never carries credentials."""

    name: str
    slot: int
    status: Status
    port_base: int
    port_last: int
    components: dict[str, Status]
    session: str | None = Field(description="Bisect session owning this deployment, if any")
    owner: Owner
    agent_session: str | None
    created_at: datetime
    last_used_at: datetime
    ttl_s: float | None
    expires_at: datetime | None


class ComponentInfo(BaseModel):
    """A component as `qft deployment show` reports it; the facts it described about itself are extra fields."""

    model_config = ConfigDict(extra="allow")

    status: Status
    version: str
    revision: str | None
    source: str  # the spec's `version` (`release:v26.29`, `worktree:…`), whatever the component reports


class DeploymentInfo(BaseModel):
    """Everything needed to talk to a deployment: URLs, ports, credentials, revisions and status."""

    name: str
    status: Status
    slot: int
    directory: Path
    network: str
    ports: dict[str, int]
    test_account: Credentials | None
    components: dict[str, ComponentInfo]
    owner: Owner
    agent_session: str | None
    created_at: datetime
    last_used_at: datetime
    ttl_s: float | None
    expires_at: datetime | None


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
            raise NotFoundError(f"deployment '{self.name}' has no component '{only}'")
        return selected

    @contextmanager
    def locked(self, timeout: float = 5) -> Iterator[None]:
        """Hold this deployment's lock (a start holds it throughout); raises `filelock.Timeout` when busy."""
        with FileLock(self.directory / "deployment.lock", timeout=timeout):
            yield

    def save(self) -> None:
        save_state(self.directory, self.state)

    # --- lifecycle --------------------------------------------------------------------------------------

    def start(self, only: str | None = None, *, timeout: float | None = None) -> None:
        """Resolve, materialize, render, start, wait for health and wire each component in order.

        Returns once every component is ready. ``timeout`` bounds the whole start: it is checked between steps
        and given to the health checks; a build in progress is not interrupted.
        """
        deadline = Deadline(timeout)
        with self.locked():
            self.state.status = Status.STARTING
            self.save()
            try:
                for component in self.components(only):
                    self._bring_up(component, deadline)
            except Exception:
                self.state.status = Status.FAILED
                self.save()
                raise
            self.state.status = Status.RUNNING
            self.save()

    def _bring_up(self, component: Component, deadline: Deadline | None = None) -> None:
        deadline = deadline or Deadline()
        with _step(component, "resolve", deadline):
            log.info("[%s] resolving %s", component.name, getattr(component.spec, "version", "?"))
            revision = component.resolve()
        component.state.revision = revision
        component.state.status = Status.STARTING
        self.save()
        with _step(component, "materialize", deadline):
            log.info("[%s] materializing %s", component.name, revision)
            materialized = component.materialize(revision)
        with _step(component, "render", deadline):
            component.render(revision, materialized)
        try:
            with _step(component, "start", deadline):
                log.info("[%s] starting", component.name)
                component.start()
            with _step(component, "health", deadline) as remaining:
                if remaining is None:
                    component.health()
                else:
                    component.health(timeout=remaining)
        except Exception:
            component.state.status = Status.FAILED
            self.save()
            raise
        try:
            with _step(component, "wire", deadline):
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

    def remove(self, *, lock_timeout: float = 5) -> None:
        """Tear everything down and forget the deployment; cached materializations are kept."""
        with self.locked(lock_timeout):
            for component in reversed(self.components()):
                if component.state.status is not Status.CREATED:
                    log.info("[%s] removing", component.name)
                    component.remove()
        docker.remove_network(self.testbed.runner, self.context().network)
        self.testbed.cache.release(self.name)
        self.testbed.registry.unregister(self.name)
        self.testbed.registry.forget(self.name)
        shutil.rmtree(self.directory, ignore_errors=True)

    def swap(self, component_name: str, version: VersionSource) -> None:
        """Replace one component's version source and bring it up again (the bisect primitive)."""
        with self.locked():
            self.spec = self.spec.model_copy(deep=True)
            component_spec = self.spec.component(component_name)
            if component_spec is None:
                raise NotFoundError(f"deployment '{self.name}' has no component '{component_name}'")
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

    def summary(self) -> DeploymentSummary:
        entry = self.testbed.registry.load().deployments[self.name]
        return DeploymentSummary(
            name=self.name,
            slot=self.state.slot,
            status=self.state.status,
            port_base=self.ports.base,
            port_last=self.ports.base + BLOCK_SIZE - 1,
            components={name: self.state.component(name).status for name in self.spec.components()},
            session=entry.session,
            **_ownership(entry),
        )

    def info(self) -> DeploymentInfo:
        components: dict[str, ComponentInfo] = {}
        for name in self.spec.components():
            component_state = self.state.component(name)
            # the component's own facts win, as `version` (the QGIS version of `qgis: host`, for example)
            source = str(getattr(self.spec.component(name), "version", "?"))
            components[name] = ComponentInfo.model_validate(
                {
                    "status": component_state.status,
                    "version": source,
                    "revision": str(component_state.revision) if component_state.revision else None,
                    **component_state.summary,
                    "source": source,
                }
            )
        return DeploymentInfo(
            name=self.name,
            status=self.state.status,
            slot=self.state.slot,
            directory=self.directory,
            network=self.context().network,
            ports=self.ports.all_ports(),
            test_account=self.state.credentials,
            components=components,
            **_ownership(self.testbed.registry.load().deployments[self.name]),
        )

    def describe(self) -> dict[str, Any]:
        """:meth:`info` as plain data."""
        return self.info().model_dump()

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

    def run(
        self, cmd: list[str], *, timeout: float | None = None, capture: bool = False, tail_lines: int = 200
    ) -> ProcessResult:
        """Run a user command with this deployment's ``QFT_*`` environment (`qft deployment run`)."""
        return run_process(
            cmd, env={**os.environ, **self.env()}, timeout=timeout, capture=capture, tail_lines=tail_lines
        )

    def launcher(self, target: str) -> Path | None:
        path = self.directory / "bin" / target
        return path if path.is_file() else None
