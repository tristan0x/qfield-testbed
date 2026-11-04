"""The operations of qft, once, for every adapter: the CLI (`qft --json`) and the MCP server (`qft mcp serve`).

Each function returns the `data` of the machine contract as a typed model and raises the typed errors of
`errors.py`. Adapters only parse their inputs, ask for confirmations when they can, and render the result: the CLI
prints it or wraps it in the JSON envelope, the MCP server returns it as structured content. Keeping the logic here
is what keeps both adapters' semantics and error codes identical (docs/adr/0001-agent-interface.md).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from qfield_testbed.bisect.algorithm import Verdict
from qfield_testbed.bisect.runner import Outcome, ScriptTest
from qfield_testbed.bisect.scenario import BisectScenario
from qfield_testbed.bisect.session import BisectSession, SessionStatus
from qfield_testbed.cache import dir_size
from qfield_testbed.caller import CALLER
from qfield_testbed.deployment import DeploymentInfo, DeploymentSummary, Testbed, parse_ttl
from qfield_testbed.doctor import CAPABILITIES, Capability, Check, HostFacts, capabilities, host_facts, run_checks
from qfield_testbed.durations import parse_duration
from qfield_testbed.errors import (
    NeedsHumanError,
    NotFoundError,
    PrerequisiteMissingError,
    RefusedError,
    TimeoutExceededError,
    UsageError,
)
from qfield_testbed.gc import GcPlan, GcReport, apply_gc, plan_gc
from qfield_testbed.runtimes.shell import ProcessResult
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import OWNERS
from qfield_testbed.suggest.detect import detect
from qfield_testbed.suggest.propose import Proposal, propose

# --- results ---------------------------------------------------------------------------------------------------


class DoctorData(BaseModel):
    checks: list[Check]
    capabilities: dict[str, Capability]
    host: HostFacts


class SpecValidation(BaseModel):
    name: str
    components: list[str]


class EnsureData(DeploymentInfo):
    created: bool
    started: bool
    spec_hash: str


class DeploymentList(BaseModel):
    deployments: list[DeploymentSummary]


class Removed(BaseModel):
    name: str
    removed: bool


class Logs(BaseModel):
    components: dict[str, str]  # component → its last lines


class Environment(BaseModel):
    variables: dict[str, str]


class GcData(BaseModel):
    dry_run: bool
    plan: GcPlan
    report: GcReport | None = None


class CacheEntry(BaseModel):
    key: str
    path: Path
    size_bytes: int
    last_used: datetime
    users: list[str]
    images: list[str]


class CacheList(BaseModel):
    cache_dir: Path
    entries: list[CacheEntry]
    total_bytes: int


class PrunedEntry(BaseModel):
    key: str
    size_bytes: int | None  # None when its files were already gone


class CachePrune(BaseModel):
    dry_run: bool
    removed: list[PrunedEntry]


class FirstBad(BaseModel):
    index: int
    label: str
    sha: str | None
    subject: str
    date: str
    url: str | None


class VerdictEntry(BaseModel):
    index: int
    label: str
    subject: str
    verdict: Verdict | None
    exit_code: int | None


class BisectSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    session: str
    status: SessionStatus
    component: str
    good: str
    bad: str
    deployment: str
    candidates: int
    tested: int
    skipped: int
    remaining_steps: int
    current: int | None
    next: int | None
    first_bad: FirstBad | None
    ambiguous: list[str]
    verdicts: list[VerdictEntry]


class BisectNext(BaseModel):
    prepared: int | None  # the candidate now in the deployment
    outcome: Literal["prepared", "already-prepared", "skipped", "done"]
    session: BisectSummary


def _testbed(testbed: Testbed | None) -> Testbed:
    return testbed if testbed is not None else Testbed()


def seconds(duration: str | None) -> float | None:
    return parse_duration(duration).total_seconds() if duration else None


# --- host and specs --------------------------------------------------------------------------------------------


def doctor(testbed: Testbed | None = None) -> DoctorData:
    testbed = _testbed(testbed)
    checks = run_checks(testbed)
    return DoctorData(checks=checks, capabilities=capabilities(checks), host=host_facts(testbed))


def check_capabilities(require: list[str]) -> None:
    unknown = sorted(set(require) - set(CAPABILITIES))
    if unknown:
        raise UsageError(
            f"unknown capability: {', '.join(unknown)}",
            hint="one of: " + ", ".join(CAPABILITIES),
            docs="getting-started",
        )


def require(report: DoctorData, keys: list[str]) -> None:
    """Fail with what to fix unless the host can run every capability in ``keys``."""
    missing = {key: report.capabilities[key] for key in keys if not report.capabilities[key].can_run}
    if missing:
        hints = list(dict.fromkeys(hint for capability in missing.values() for hint in capability.fix_hints))
        raise PrerequisiteMissingError(
            f"this host cannot run: {', '.join(missing)}",
            hint="; ".join(hints) or None,
            details={"missing": {key: capability.missing for key, capability in missing.items()}},
        )


def spec_suggest(path: Path, needs: list[str], testbed: Testbed | None = None) -> Proposal:
    testbed = _testbed(testbed)
    return propose(testbed, detect(testbed.runner, path), set(needs))


def spec_validate(spec: DeploymentSpec) -> SpecValidation:
    return SpecValidation(name=spec.name, components=list(spec.components()))


# --- deployments -----------------------------------------------------------------------------------------------


def deployment_ensure(
    spec: DeploymentSpec,
    *,
    auto_name: bool = False,
    replace: bool = False,
    force: bool = False,
    timeout: str | None = None,
    ttl: str | None = None,
    testbed: Testbed | None = None,
) -> tuple[EnsureData, list[str]]:
    """The deployment of ``spec``, created and started or reused, and the warnings to show."""
    result = _testbed(testbed).ensure(
        spec,
        auto_name=auto_name,
        replace=replace,
        force=force,
        timeout=seconds(timeout),
        ttl=parse_ttl(ttl) if ttl is not None else "default",
    )
    data = EnsureData(**dict(result.info), created=result.created, started=result.started, spec_hash=result.spec_hash)
    return data, result.warnings


def deployment_list(
    *, agent_session: str | None = None, owner: str | None = None, testbed: Testbed | None = None
) -> DeploymentList:
    if owner is not None and owner not in OWNERS:
        raise UsageError(f"unknown owner '{owner}'", hint="one of: " + ", ".join(OWNERS))
    summaries = [
        summary
        for summary in (deployment.summary() for deployment in _testbed(testbed).deployments())
        if (agent_session is None or summary.agent_session == agent_session)
        and (owner is None or summary.owner == owner)
    ]
    return DeploymentList(deployments=summaries)


def deployment_show(name: str, *, touch: bool = True, testbed: Testbed | None = None) -> DeploymentInfo:
    return _testbed(testbed).deployment(name, touch=touch).info()


def deployment_start(
    name: str, *, component: str | None = None, timeout: str | None = None, testbed: Testbed | None = None
) -> DeploymentInfo:
    deployment = _testbed(testbed).deployment(name, touch=True)
    deployment.start(component, timeout=seconds(timeout))
    return deployment.info()


def deployment_stop(
    name: str, *, component: str | None = None, force: bool = False, testbed: Testbed | None = None
) -> DeploymentInfo:
    testbed = _testbed(testbed)
    testbed.authorize(name, "stop", force=force)
    deployment = testbed.deployment(name)
    deployment.stop(component)
    return deployment.info()


def deployment_restart(
    name: str,
    *,
    component: str | None = None,
    timeout: str | None = None,
    force: bool = False,
    testbed: Testbed | None = None,
) -> DeploymentInfo:
    testbed = _testbed(testbed)
    testbed.authorize(name, "restart", force=force)
    deployment = testbed.deployment(name, touch=True)
    deployment.stop(component)
    deployment.start(component, timeout=seconds(timeout))
    return deployment.info()


def deployment_keep(name: str, ttl: str, *, force: bool = False, testbed: Testbed | None = None) -> DeploymentSummary:
    testbed = _testbed(testbed)
    testbed.authorize(name, "keep", force=force)
    deployment = testbed.deployment(name)
    testbed.registry.set_ttl(name, parse_ttl(ttl))
    return deployment.summary()


def removal_check(name: str, *, force: bool = False, testbed: Testbed | None = None) -> str | None:
    """Fail unless the caller may remove ``name``; the bisect session it belongs to, if any, to warn about."""
    testbed = _testbed(testbed)
    deployment = testbed.deployment(name)
    testbed.authorize(name, "remove", force=force)
    return deployment.summary().session


def deployment_remove(name: str, *, force: bool = False, testbed: Testbed | None = None) -> Removed:
    """Remove ``name`` (the caller has obtained any confirmation)."""
    testbed = _testbed(testbed)
    removal_check(name, force=force, testbed=testbed)
    testbed.deployment(name).remove()
    return Removed(name=name, removed=True)


def deployment_logs(
    name: str,
    *,
    component: str | None = None,
    service: str | None = None,
    tail: int | None = 200,
    since: str | None = None,
    capture: bool = True,
    follow: bool = False,
    testbed: Testbed | None = None,
) -> Logs:
    """The last lines of each component; with ``capture=False`` they are printed instead (the CLI's human mode)."""
    if follow and capture:
        raise UsageError("--follow streams forever and cannot answer with one JSON document", hint="drop --follow")
    deployment = _testbed(testbed).deployment(name, touch=True)
    captured: dict[str, str] = {}
    for comp in deployment.components(component):
        text = comp.logs(follow=follow, tail=tail, since=since, service=service, capture=capture)
        if text is not None:
            captured[comp.name] = text
    return Logs(components=captured)


def deployment_run(
    name: str,
    cmd: list[str],
    *,
    timeout: str | None = None,
    tail_lines: int = 200,
    capture: bool = True,
    testbed: Testbed | None = None,
) -> ProcessResult:
    """Run ``cmd`` with the deployment's environment; a command that outlives ``timeout`` is killed (TIMEOUT)."""
    if not cmd:
        raise UsageError("usage: qft deployment run NAME [--timeout 10m] -- CMD [ARGS...]")
    result = (
        _testbed(testbed)
        .deployment(name, touch=True)
        .run(cmd, timeout=seconds(timeout), capture=capture, tail_lines=tail_lines)
    )
    if result.timed_out:
        raise TimeoutExceededError(
            f"`{shlex.join(cmd)}` did not finish within {timeout}; it was killed",
            hint="raise --timeout, or look at the output tails in details",
            details=asdict(result),
        )
    return result


def deployment_env(name: str, *, testbed: Testbed | None = None) -> Environment:
    return Environment(variables=dict(sorted(_testbed(testbed).deployment(name, touch=True).env().items())))


# --- clean-up --------------------------------------------------------------------------------------------------


def gc_plan(
    *,
    expired: bool = False,
    orphaned: bool = False,
    agent_session: str | None = None,
    force: bool = False,
    testbed: Testbed | None = None,
) -> GcPlan:
    """What `gc` would remove; neither ``expired`` nor ``orphaned`` nor a session means both."""
    if agent_session and CALLER.agent_session not in {None, agent_session} and not force:
        raise RefusedError(
            f"agent session '{CALLER.agent_session}' may not clean up agent session '{agent_session}'",
            hint="pass --force",
            docs="isolation",
        )
    if not (expired or orphaned or agent_session):
        expired = orphaned = True
    return plan_gc(_testbed(testbed), expired=expired, orphaned=orphaned, agent_session=agent_session)


def gc_apply(plan: GcPlan, *, testbed: Testbed | None = None) -> GcData:
    return GcData(dry_run=False, plan=plan, report=apply_gc(_testbed(testbed), plan))


def cache_list(testbed: Testbed | None = None) -> CacheList:
    testbed = _testbed(testbed)
    entries = [
        CacheEntry(
            key=key,
            path=entry.path,
            size_bytes=dir_size(entry.path) if entry.path.exists() else 0,
            last_used=entry.last_used,
            users=sorted(entry.users),
            images=entry.images,
        )
        for key, entry in sorted(testbed.cache.load().entries.items())
    ]
    return CacheList(
        cache_dir=testbed.paths.cache, entries=entries, total_bytes=sum(entry.size_bytes for entry in entries)
    )


def cache_prune(
    *,
    older_than: str | None = None,
    docker_build_cache: bool = False,
    dry_run: bool = False,
    testbed: Testbed | None = None,
) -> CachePrune:
    testbed = _testbed(testbed)
    age = parse_duration(older_than) if older_than else timedelta(days=testbed.config.cache.prune_after_days)
    candidates = testbed.cache.prunable(older_than=age, live_users=set(testbed.registry.names()))
    removed: list[PrunedEntry] = []
    for key, entry in sorted(candidates.items()):
        removed.append(PrunedEntry(key=key, size_bytes=dir_size(entry.path) if entry.path.exists() else None))
        if not dry_run:
            testbed.cache.drop(key)
            for image in entry.images:
                testbed.runner.run(["docker", "image", "rm", image], check=False)
    if docker_build_cache and not dry_run:
        testbed.runner.run(["docker", "builder", "prune", "-f"], check=False, capture=False)
    return CachePrune(dry_run=dry_run, removed=removed)


# --- bisect ----------------------------------------------------------------------------------------------------


def bisect_session(name: str | None, testbed: Testbed | None = None) -> BisectSession:
    """The session ``name``, or the only one when ``name`` is None."""
    testbed = _testbed(testbed)
    if name is None:
        names = BisectSession.names(testbed)
        if len(names) != 1:
            if not names:
                raise NotFoundError("no bisect session yet", hint="`qft bisect start SCENARIO.yaml`", docs="bisect")
            raise UsageError("several sessions exist; name one: " + ", ".join(names))
        name = names[0]
    return BisectSession(testbed, name)


def bisect_summary(session: BisectSession) -> BisectSummary:
    return BisectSummary.model_validate(session.summary())


def bisect_start(
    scenario: BisectScenario, *, session: str | None = None, testbed: Testbed | None = None
) -> BisectSession:
    return BisectSession.create(_testbed(testbed), scenario, session)


def bisect_next(session: BisectSession) -> BisectNext:
    """Bring the next candidate into the session's deployment, unless one is already waiting for its verdict."""
    if session.state.current is not None:
        return BisectNext(prepared=session.state.current, outcome="already-prepared", session=bisect_summary(session))
    index = session.search().next_index()
    if index is None:
        return BisectNext(prepared=None, outcome="done", session=bisect_summary(session))
    if session.prepare(index) is None:
        return BisectNext(prepared=None, outcome="skipped", session=bisect_summary(session))
    return BisectNext(prepared=index, outcome="prepared", session=bisect_summary(session))


def bisect_run(
    session: BisectSession,
    *,
    prompt: Callable[[str], str] | None = None,
    before: Callable[[BisectSession, int], None] | None = None,
    after: Callable[[BisectSession, int], None] | None = None,
) -> BisectSummary:
    """Swap, test and record candidates until the first bad one is known; ``prompt`` answers a manual test."""
    if session.state.scenario.test.manual and prompt is None:
        # checked before the first swap: the prompt would otherwise come after minutes of building and starting
        raise NeedsHumanError(
            "the scenario's test is manual and nobody can answer (no terminal, --json or --non-interactive)",
            hint="run `qft bisect run` in a terminal, or use `qft bisect next` and `qft bisect good|bad|skip`",
            docs="bisect",
        )
    test = session.state.scenario.test
    if test.script is not None:
        ScriptTest(test.script, test.args, test.timeout).check()
    while (index := session.search().next_index()) is not None:
        if before is not None:
            before(session, index)
        if not session.step(prompt):
            break
        if after is not None:
            after(session, index)
    return bisect_summary(session)


def bisect_verdict(session: BisectSession, verdict: Verdict) -> int:
    """Record ``verdict`` for the prepared candidate; its index."""
    index = session.state.current
    if index is None:
        raise UsageError("no candidate is prepared", hint="run `qft bisect next` first")
    session.record(index, Outcome(verdict, None, 0.0, f"manual verdict: {verdict}\n"))
    return index
