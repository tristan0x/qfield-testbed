"""`qft gc` — remove expired deployments and leftovers nothing owns."""

from __future__ import annotations

from typing import Annotated

import typer

from qfield_testbed.caller import CALLER
from qfield_testbed.cli._console import confirm, console, emit, ok
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import RefusedError
from qfield_testbed.gc import GcPlan, GcReport, apply_gc, plan_gc


def gc(
    *,
    expired: Annotated[bool, typer.Option("--expired", help="Deployments past their TTL")] = False,
    agent_session: Annotated[
        str | None, typer.Option("--agent-session", help="Every deployment of this agent session, expired or not")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Show what would be removed")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="From an agent session: clean another session's deployments")
    ] = False,
) -> None:
    """Remove expired deployments (and, with --agent-session, all of one agent session)."""
    if agent_session and CALLER.agent_session not in {None, agent_session} and not force:
        raise RefusedError(
            f"agent session '{CALLER.agent_session}' may not clean up agent session '{agent_session}'",
            hint="pass --force",
            docs="isolation",
        )
    if not expired and agent_session is None:
        expired = True
    testbed = Testbed()
    plan = plan_gc(testbed, expired=expired, agent_session=agent_session)
    if dry_run or not plan.expired:
        emit({"dry_run": dry_run, "plan": plan}, human=lambda: _print_plan(plan, dry_run=dry_run))
        return
    _print_plan(plan, dry_run=True)
    if not yes and not confirm(f"Remove {len(plan.expired)} deployment(s)?", action="garbage-collect"):
        raise typer.Abort
    report = apply_gc(testbed, plan)
    emit({"dry_run": False, "plan": plan, "report": report}, human=lambda: _print_report(report))


def _print_plan(plan: GcPlan, *, dry_run: bool) -> None:
    if not plan.expired:
        console.print("Nothing to clean up.")
        return
    verb = "would remove" if dry_run else "to remove"
    for name in plan.expired:
        console.print(f"{verb} deployment {name}")


def _print_report(report: GcReport) -> None:
    for name in report.removed:
        ok(f"removed deployment [bold]{name}[/]")
    for name, why in {**report.skipped, **report.failed}.items():
        console.print(f"[yellow]left[/] {name}: {why}")
