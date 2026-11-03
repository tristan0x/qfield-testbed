"""`qft gc`: remove what nobody uses any more — expired deployments, and `qft-*` resources nothing owns.

Planning and applying are separate so that `--dry-run` shows exactly what would go. A deployment in use (its
lock held, e.g. during a start) is skipped, never interrupted.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime

from filelock import Timeout

from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import TestbedError
from qfield_testbed.runtimes import docker
from qfield_testbed.state import state_file

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


# what a deployment named <n> creates in Docker, besides containers labelled qft.deployment=<n>
PREFIX = "qft-"
COMPOSE_SUFFIXES = ("-qfc", "-tb")  # QFieldCloud, Android emulator


@dataclass
class GcPlan:
    expired: list[str] = field(default_factory=list)  # deployments past their TTL (or of the selected session)
    orphaned_projects: list[str] = field(default_factory=list)  # compose projects
    orphaned_containers: list[str] = field(default_factory=list)  # deployment names on labelled containers
    orphaned_networks: list[str] = field(default_factory=list)
    orphaned_directories: list[str] = field(default_factory=list)  # deployment directories without an entry
    stale_entries: list[str] = field(default_factory=list)  # registry entries whose directory is gone

    def empty(self) -> bool:
        return not any(vars(self).values())


@dataclass
class GcReport:
    removed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # what was left, and why
    failed: dict[str, str] = field(default_factory=dict)


def plan_gc(
    testbed: Testbed,
    *,
    expired: bool = True,
    orphaned: bool = False,
    agent_session: str | None = None,
    now: datetime | None = None,
) -> GcPlan:
    """What `qft gc` would remove: expired deployments, and every deployment of ``agent_session`` if given."""
    now = now or _now()
    plan = GcPlan()
    for name, entry in sorted(testbed.registry.load().deployments.items()):
        of_session = agent_session is not None and entry.owner == "agent" and entry.agent_session == agent_session
        past_ttl = expired and entry.expires_at is not None and entry.expires_at <= now
        if of_session or past_ttl:
            plan.expired.append(name)
    if orphaned:
        _plan_orphans(testbed, plan)
    return plan


def _resources(names: set[str]) -> tuple[set[str], set[str]]:
    """The compose projects and networks of deployments named ``names``: matched by name, never parsed."""
    projects = {f"{PREFIX}{name}{suffix}" for name in names for suffix in COMPOSE_SUFFIXES}
    return projects, {f"{PREFIX}{name}" for name in names}


def _plan_orphans(testbed: Testbed, plan: GcPlan) -> None:
    runner = testbed.runner
    data = testbed.registry.load()
    names = set(data.deployments)
    # Docker is shared by every QFT_HOME of the host: only what this home created and lost track of is an orphan
    lost = set(data.created_here) - names
    projects, networks = _resources(lost)
    plan.orphaned_projects = sorted(docker.compose_projects(runner, PREFIX) & projects)
    plan.orphaned_containers = sorted(docker.labelled_deployments(runner) & lost)
    plan.orphaned_networks = sorted(docker.networks(runner, PREFIX) & networks)
    deployments_dir = testbed.paths.deployments_dir
    on_disk = {p.name for p in deployments_dir.iterdir() if p.is_dir()} if deployments_dir.is_dir() else set()
    plan.orphaned_directories = sorted(on_disk - names)
    plan.stale_entries = sorted(n for n in names if not state_file(testbed.paths.deployment_dir(n)).is_file())


def apply_gc(testbed: Testbed, plan: GcPlan) -> GcReport:
    report = GcReport()
    for name in plan.expired:
        try:
            testbed.deployment(name).remove(lock_timeout=0)
        except Timeout:
            report.skipped[name] = "in use"
        except TestbedError as exc:
            report.failed[name] = exc.message or str(exc)
        else:
            log.info("removed deployment '%s'", name)
            report.removed.append(name)
    _apply_orphans(testbed, plan, report)
    return report


def _apply_orphans(testbed: Testbed, plan: GcPlan, report: GcReport) -> None:
    runner = testbed.runner
    # registered since the plan was made (an `add` running meanwhile): not an orphan any more
    names = set(testbed.registry.names())
    projects, networks = _resources(names)
    for project in plan.orphaned_projects:
        if project not in projects:
            docker.compose_down_project(runner, project)
            report.removed.append(f"compose project {project}")
    for name in plan.orphaned_containers:
        if name not in names:
            docker.remove_labelled(runner, {docker.DEPLOYMENT_LABEL: name})
            report.removed.append(f"containers of {name}")
    for network in plan.orphaned_networks:
        if network not in networks:
            docker.remove_network(runner, network)
            report.removed.append(f"network {network}")
    for name in plan.orphaned_directories:
        if name not in names:
            shutil.rmtree(testbed.paths.deployment_dir(name), ignore_errors=True)
            testbed.cache.release(name)
            report.removed.append(f"directory of {name}")
    for name in plan.stale_entries:
        if not state_file(testbed.paths.deployment_dir(name)).is_file():
            with contextlib.suppress(TestbedError):
                testbed.registry.unregister(name)
            testbed.cache.release(name)
            report.removed.append(f"registry entry {name}")
