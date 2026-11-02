"""`qft gc`: remove what nobody uses any more — expired deployments, and `qft-*` resources nothing owns.

Planning and applying are separate so that `--dry-run` shows exactly what would go. A deployment in use (its
lock held, e.g. during a start) is skipped, never interrupted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from filelock import Timeout

from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import TestbedError

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class GcPlan:
    expired: list[str] = field(default_factory=list)  # deployments past their TTL (or of the selected session)


@dataclass
class GcReport:
    removed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # what was left, and why
    failed: dict[str, str] = field(default_factory=dict)


def plan_gc(
    testbed: Testbed,
    *,
    expired: bool = True,
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
    return plan


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
    return report
