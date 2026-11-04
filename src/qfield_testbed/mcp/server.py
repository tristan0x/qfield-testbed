"""`qft mcp serve`: an MCP server over stdio whose tools are thin wrappers of `qfield_testbed.service`.

The server acts as one agent session for its whole life, so ownership guards, TTLs and quotas apply as they do to an
agent using the CLI. A failure answers with an error result whose structured content is the CLI's `error` object
(same `code`, `hint`, `docs` and `details`). stdout carries the protocol only: everything else qft or the commands
it runs would print goes to stderr.
"""

from __future__ import annotations

import functools
import inspect
import io
import json
import logging
import os
import secrets
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from qfield_testbed import __version__, service
from qfield_testbed.bisect.algorithm import Verdict
from qfield_testbed.bisect.scenario import BisectScenario
from qfield_testbed.caller import CALLER, set_agent_session
from qfield_testbed.cli._console import OUTPUT
from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import DeploymentInfo, DeploymentSummary
from qfield_testbed.durations import parse_duration
from qfield_testbed.errors import TestbedError, UsageError, error_object
from qfield_testbed.mcp import prompts, resources
from qfield_testbed.mcp.jobs import Job, JobList, JobLogs, JobManager, JobRef
from qfield_testbed.runtimes.shell import ProcessResult, send_uncaptured_output_to_stderr
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.suggest.propose import Proposal

log = logging.getLogger(__name__)

POLL_S = 0.5

INSTRUCTIONS = """\
qfield-testbed builds sandboxed deployments of the QField stack on this machine: QFieldCloud, QGIS with QFieldSync,
QField on an emulator or the desktop. Typical flow: testbed_doctor (what this host can run) → spec_suggest (a spec
putting a local checkout live) → deployment_ensure (create or reuse, then start; the first start can take minutes)
→ deployment_show / deployment_logs → deployment_remove when done. A long call (builds, starts) that outlasts the
call ceiling answers with a `job`: follow it with job_status until it succeeds or fails. Deployments you create belong to this server's
agent session and expire 24 h after their last use. Errors carry a stable `code` and a `hint`: follow the hint;
PREREQ_MISSING and NEEDS_HUMAN mean a person has to act. Never pass force=true unless the user asked for it.
"""

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
READ_ONLY_REMOTE = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
STARTS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
CHANGES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
DESTROYS = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False)
STEPS = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=True)
CREATES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)


class EnsureResult(service.EnsureData):
    warnings: list[str] = Field(default_factory=list)


def error_result(error: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=json.dumps({"error": error}, default=str))],
        structured_content={"error": error},
    )


class JobFailedError(Exception):
    """A job that ended in failure within the call: answered with its error object."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message", ""))
        self.error = error


def _error(function: Callable[..., Any], exc: Exception) -> CallToolResult:
    if isinstance(exc, JobFailedError):
        return error_result(exc.error)
    if isinstance(exc, TestbedError):
        return error_result(
            error_object(exc.code, exc.message or str(exc), hint=exc.hint, docs=exc.docs, details=exc.details)
        )
    log.error("tool %s failed", function.__name__, exc_info=exc)
    return error_result(error_object("ERROR", f"{type(exc).__name__}: {exc}"))


def contract[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    """Failures as error results carrying the CLI's `error` object, instead of the SDK's bare message."""
    if inspect.iscoroutinefunction(function):

        @functools.wraps(function)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            try:
                return await function(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - every failure becomes an error result
                return _error(function, exc)

        return cast("Callable[P, R]", async_wrapper)

    @functools.wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return function(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - every failure becomes an error result
            return cast("R", _error(function, exc))  # the SDK sends a CallToolResult as is, whatever the annotation

    return wrapper


class Pending[T](BaseModel):
    """The result of a long tool: `result` when it finished within the call ceiling, else the `job` to follow."""

    job: JobRef | None = None
    result: T | None = None


def call_ceiling() -> float:
    """Seconds a tool call may be held open (`mcp_call_ceiling`, `QFT_MCP_CALL_CEILING`: e.g. 50s, 2m or 30)."""
    value = GlobalConfig.load().mcp_call_ceiling.strip()
    return float(value) if value.replace(".", "", 1).isdigit() else parse_duration(value).total_seconds()


async def follow[T](
    jobs: JobManager,
    ctx: Context,
    tool: str,
    function: Callable[[], Any],
    *,
    result_type: type[T],
    deployment: str | None,
    wait: bool,
) -> Pending[T]:
    """Run ``function`` as a job; answer with its result if it ends within the ceiling, else with the job."""
    job = jobs.start(tool, function, deployment=deployment)
    deadline = time.monotonic() + (call_ceiling() if wait else 0)
    reported = -1
    while True:
        left = max(0.0, deadline - time.monotonic())
        done = await anyio.to_thread.run_sync(jobs.wait, job.job_id, min(POLL_S, left))
        if job.progress.steps_done != reported:
            reported = job.progress.steps_done
            try:
                await ctx.report_progress(reported, None, job.progress.step)
            except Exception:
                log.debug("progress not sent", exc_info=True)
        if done:
            break
        if time.monotonic() >= deadline:
            return Pending[result_type](job=job.ref())  # type: ignore[valid-type]
    if job.status == "failed":
        raise JobFailedError(job.error or error_object("ERROR", "the job failed"))
    return Pending[result_type](result=job.result)  # type: ignore[valid-type]


def _spec(spec_yaml: str | None, spec_path: str | None, name: str | None = None) -> DeploymentSpec:
    if (spec_yaml is None) == (spec_path is None):
        raise UsageError("give the spec either as `spec_yaml` (its text) or as `spec_path` (a file), not both")
    overrides = {"name": name} if name and name != "auto" else None
    if spec_yaml is not None:
        return DeploymentSpec.from_yaml(spec_yaml, overrides=overrides)
    return DeploymentSpec.from_file(Path(spec_path or "").expanduser(), overrides=overrides)


def build_server(*, allow_run: bool = False) -> MCPServer:  # noqa: C901, PLR0915 - one declaration per tool
    mcp = MCPServer("qfield-testbed", version=__version__, instructions=INSTRUCTIONS)
    jobs = JobManager()

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def testbed_doctor(require: list[str] | None = None) -> service.DoctorData:
        """Check the host (Docker, KVM, gh, QGIS, display…) and tell which components and version sources it can run.

        `require` (e.g. ["qfield-android", "source:build"]) fails with PREREQ_MISSING when one is missing.
        """
        keys = require or []
        service.check_capabilities(keys)
        report = service.doctor()
        service.require(report, keys)
        return report

    @mcp.tool(annotations=READ_ONLY_REMOTE)
    @contract
    def spec_suggest(path: str = ".", needs: list[str] | None = None) -> Proposal:
        """Propose a deployment spec putting the QFieldCloud, QFieldSync, QField or QGIS plugin checkout at `path` live.

        `needs`: android, sync, worker, tls, desktop, master, release. Pass the returned `spec_yaml` to deployment_ensure.
        """
        return service.spec_suggest(Path(path).expanduser(), needs or [])

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def spec_validate(spec_yaml: str | None = None, spec_path: str | None = None) -> service.SpecValidation:
        """Validate a deployment spec (its YAML text or a file); INVALID_SPEC lists each problem with path and line."""
        return service.spec_validate(_spec(spec_yaml, spec_path))

    @mcp.tool(annotations=STARTS)
    @contract
    async def deployment_ensure(  # noqa: PLR0917 - MCP passes arguments by name
        ctx: Context,
        spec_yaml: str | None = None,
        spec_path: str | None = None,
        name: str | None = None,
        replace: bool = False,
        timeout: str | None = "30m",
        ttl: str | None = None,
        force: bool = False,
        wait: bool = True,
    ) -> Pending[EnsureResult]:
        """Make sure a deployment of this spec exists and is ready: reuse it, or create and start it.

        `name`: override the spec's name, `auto` to derive it from the spec. ALREADY_EXISTS means the name holds
        another spec (see `details.diff`): rename it, or `replace=true` for a deployment of yours. `timeout` bounds
        the start (e.g. 20m); `ttl` how long it is kept after its last use (e.g. 8h, 3d). A first start can take
        minutes: when it outlasts the call, the answer is a `job` to follow with job_status (`wait=false`: at once).
        """
        spec = _spec(spec_yaml, spec_path, name)

        def run() -> EnsureResult:
            data, warnings = service.deployment_ensure(
                spec, auto_name=name == "auto", replace=replace, force=force, timeout=timeout, ttl=ttl
            )
            return EnsureResult(**dict(data), warnings=warnings)

        deployment = None if name == "auto" else spec.name
        return await follow(
            jobs, ctx, "deployment_ensure", run, result_type=EnsureResult, deployment=deployment, wait=wait
        )

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def deployment_list(agent_session: str | None = None, owner: str | None = None) -> service.DeploymentList:
        """List deployments with status, owner and expiry; filter on an agent session or an owner (human, agent, bisect)."""
        return service.deployment_list(agent_session=agent_session, owner=owner)

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def deployment_show(name: str) -> DeploymentInfo:
        """URLs, ports, test account, revisions and status of a deployment."""
        return service.deployment_show(name)

    @mcp.tool(annotations=STARTS)
    @contract
    async def deployment_start(
        ctx: Context, name: str, component: str | None = None, timeout: str | None = "30m", wait: bool = True
    ) -> Pending[DeploymentInfo]:
        """Materialize and start the components of a deployment (or one `component`), then wire them together."""

        def run() -> DeploymentInfo:
            return service.deployment_start(name, component=component, timeout=timeout)

        return await follow(jobs, ctx, "deployment_start", run, result_type=DeploymentInfo, deployment=name, wait=wait)

    @mcp.tool(annotations=CHANGES)
    @contract
    def deployment_stop(name: str, component: str | None = None, force: bool = False) -> DeploymentInfo:
        """Stop the components of a deployment; data and containers are kept for the next start."""
        return service.deployment_stop(name, component=component, force=force)

    @mcp.tool(annotations=STARTS)
    @contract
    async def deployment_restart(  # noqa: PLR0917 - MCP passes arguments by name
        ctx: Context,
        name: str,
        component: str | None = None,
        timeout: str | None = "30m",
        force: bool = False,
        wait: bool = True,
    ) -> Pending[DeploymentInfo]:
        """Stop then start a deployment (or one `component`), e.g. to reload a worktree."""

        def run() -> DeploymentInfo:
            return service.deployment_restart(name, component=component, timeout=timeout, force=force)

        return await follow(
            jobs, ctx, "deployment_restart", run, result_type=DeploymentInfo, deployment=name, wait=wait
        )

    @mcp.tool(annotations=CHANGES)
    @contract
    def deployment_keep(name: str, ttl: str, force: bool = False) -> DeploymentSummary:
        """Keep a deployment longer: its TTL counted from now (e.g. 8h, 3d, or none for no expiry)."""
        return service.deployment_keep(name, ttl, force=force)

    @mcp.tool(annotations=DESTROYS)
    @contract
    def deployment_remove(name: str, force: bool = False) -> service.Removed:
        """Tear down the containers, volumes and files of a deployment and free its slot (the shared cache is kept)."""
        return service.deployment_remove(name, force=force)

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def deployment_logs(
        name: str,
        component: str | None = None,
        service_name: str | None = None,
        tail: int = 200,
        since: str | None = None,
    ) -> service.Logs:
        """The last log lines of each component (or one `component` / compose `service_name`, e.g. worker_wrapper)."""
        return service.deployment_logs(name, component=component, service=service_name, tail=tail, since=since)

    if allow_run:

        @mcp.tool(annotations=STEPS)
        @contract
        def deployment_run(name: str, command: list[str], timeout: str = "10m", tail_lines: int = 200) -> ProcessResult:
            """Run a command ON THIS HOST with the deployment's QFT_* environment (URLs, credentials, launchers).

            The output tails are returned; a command that outlives `timeout` is killed (TIMEOUT).
            """
            return service.deployment_run(name, command, timeout=timeout, tail_lines=tail_lines)

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def scenario_validate(scenario_path: str) -> service.ScenarioValidation:
        """Check a bisect scenario file without starting it; `problems` lists what `bisect_run` would refuse."""
        return service.scenario_validate(BisectScenario.from_file(Path(scenario_path).expanduser()))

    @mcp.tool(annotations=CREATES)
    @contract
    def bisect_start(scenario_path: str, session: str | None = None) -> service.BisectSummary:
        """Create a bisect session from a scenario file: resolve the candidates and register its deployment."""
        scenario = BisectScenario.from_file(Path(scenario_path).expanduser())
        return service.bisect_summary(service.bisect_start(scenario, session=session))

    @mcp.tool(annotations=STEPS)
    @contract
    async def bisect_step(
        ctx: Context,
        session: str | None = None,
        verdict: Literal["good", "bad", "skip"] | None = None,
        wait: bool = True,
    ) -> Pending[service.BisectNext]:
        """Record `verdict` for the prepared candidate (if given), then prepare the next one in the deployment.

        Test the prepared candidate against the deployment, then call again with its verdict; `outcome: done`
        ends the search (see `session.first_bad`). Preparing builds and starts: it may answer with a `job`.
        """
        current = service.bisect_session(session)
        if verdict is not None:
            service.bisect_verdict(current, Verdict(verdict))

        def run() -> service.BisectNext:
            return service.bisect_next(current)

        return await follow(
            jobs,
            ctx,
            "bisect_step",
            run,
            result_type=service.BisectNext,
            deployment=current.state.deployment_name,
            wait=wait,
        )

    @mcp.tool(annotations=STEPS)
    @contract
    async def bisect_run(ctx: Context, session: str | None = None, wait: bool = True) -> Pending[service.BisectSummary]:
        """Run the whole automatic search of a session with a scripted test: swap, test, record until the first bad.

        This takes one build and start per step: it usually answers with a `job` to follow with job_status. A
        manual test cannot run here (NEEDS_HUMAN): use bisect_step instead.
        """
        current = service.bisect_session(session)
        if current.state.scenario.test.manual:
            service.bisect_run(current)  # raises NEEDS_HUMAN before any swap

        def run() -> service.BisectSummary:
            return service.bisect_run(current, prompt=lambda _message: "")

        return await follow(
            jobs,
            ctx,
            "bisect_run",
            run,
            result_type=service.BisectSummary,
            deployment=current.state.deployment_name,
            wait=wait,
        )

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def job_status(job_id: str) -> Job:
        """Status, progress, and once ended the result (as the tool would have answered) or the error of a job."""
        return jobs.get(job_id)

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def job_logs(job_id: str, tail: int = 100) -> JobLogs:
        """The last log lines of a job (what qft logged while running it)."""
        return jobs.logs(job_id, tail)

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def job_list() -> JobList:
        """The jobs of this server, running or ended."""
        return jobs.list()

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def bisect_status(session: str | None = None) -> service.BisectSummary:
        """Candidates, verdicts, the first bad revision once known, and what comes next."""
        return service.bisect_summary(service.bisect_session(session))

    @mcp.tool(annotations=READ_ONLY)
    @contract
    def cache_list() -> service.CacheList:
        """Cached materializations (checkouts, downloads, images), their size and the deployments using them."""
        return service.cache_list()

    @mcp.tool(annotations=DESTROYS)
    @contract
    def cache_prune(
        older_than: str | None = None, docker_build_cache: bool = False, dry_run: bool = True
    ) -> service.CachePrune:
        """Remove cache entries no deployment uses and not used recently (dry run unless dry_run=false)."""
        return service.cache_prune(older_than=older_than, docker_build_cache=docker_build_cache, dry_run=dry_run)

    @mcp.tool(annotations=DESTROYS)
    @contract
    def gc(
        expired: bool = False,
        orphaned: bool = False,
        agent_session: str | None = None,
        dry_run: bool = True,
        force: bool = False,
    ) -> service.GcData:
        """Remove expired deployments and leftovers no deployment owns (dry run unless dry_run=false)."""
        plan = service.gc_plan(expired=expired, orphaned=orphaned, agent_session=agent_session, force=force)
        if dry_run or plan.empty():
            return service.GcData(dry_run=dry_run, plan=plan)
        return service.gc_apply(plan)

    resources.register(mcp)
    prompts.register(mcp)
    return mcp


def prepare_process(agent_session: str | None) -> str:
    """Make this process an MCP server: one agent session, no prompts, nothing but the protocol on stdout."""
    session = agent_session or CALLER.agent_session or f"mcp-{secrets.token_hex(4)}"
    set_agent_session(session)
    OUTPUT.non_interactive = True
    send_uncaptured_output_to_stderr(enabled=True)
    return session


def serve(*, allow_run: bool = False, agent_session: str | None = None) -> None:
    session = prepare_process(agent_session)
    server = build_server(allow_run=allow_run)
    # the protocol keeps the real stdout; file descriptor 1 and sys.stdout now lead to stderr, so that neither qft
    # nor a program it starts can write a stray line into the JSON-RPC stream
    protocol = io.TextIOWrapper(os.fdopen(os.dup(sys.stdout.fileno()), "wb"), encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    if logging.getLogger().getEffectiveLevel() > logging.INFO:
        logging.getLogger().setLevel(logging.INFO)  # job_logs shows what each job logged
    log.info("qft mcp serve: agent session %s%s", session, ", deployment_run enabled" if allow_run else "")

    async def run() -> None:
        async with stdio_server(stdout=anyio.wrap_file(protocol)) as (read_stream, write_stream):
            lowlevel = server._lowlevel_server  # noqa: SLF001 - run_stdio_async() cannot take another stdout
            await lowlevel.run(read_stream, write_stream, lowlevel.create_initialization_options())

    anyio.run(run)
