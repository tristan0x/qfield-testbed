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
    orphaned: Annotated[
        bool, typer.Option("--orphaned", help="qft-* containers, networks, volumes and files no deployment owns")
    ] = False,
    agent_session: Annotated[
        str | None, typer.Option("--agent-session", help="Every deployment of this agent session, expired or not")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Show what would be removed")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation")] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="From an agent session: clean another session's deployments")
    ] = False,
) -> None:
    """Remove expired deployments and leftovers no deployment owns (both without --expired / --orphaned)."""
    if agent_session and CALLER.agent_session not in {None, agent_session} and not force:
        raise RefusedError(
            f"agent session '{CALLER.agent_session}' may not clean up agent session '{agent_session}'",
            hint="pass --force",
            docs="isolation",
        )
    if not (expired or orphaned or agent_session):
        expired = orphaned = True
    testbed = Testbed()
    plan = plan_gc(testbed, expired=expired, orphaned=orphaned, agent_session=agent_session)
    if dry_run or plan.empty():
        emit({"dry_run": dry_run, "plan": plan}, human=lambda: _print_plan(plan, dry_run=dry_run))
        return
    _print_plan(plan, dry_run=True)
    if not yes and not confirm("Remove all of the above?", action="garbage-collect"):
        raise typer.Abort
    report = apply_gc(testbed, plan)
    emit({"dry_run": False, "plan": plan, "report": report}, human=lambda: _print_report(report))


def _print_plan(plan: GcPlan, *, dry_run: bool) -> None:
    if plan.empty():
        console.print("Nothing to clean up.")
        return
    verb = "would remove" if dry_run else "to remove"
    lines = [
        *(f"deployment {name} (expired)" for name in plan.expired),
        *(f"compose project {project} (orphaned)" for project in plan.orphaned_projects),
        *(f"containers of {name} (orphaned)" for name in plan.orphaned_containers),
        *(f"network {network} (orphaned)" for network in plan.orphaned_networks),
        *(f"directory of {name} (no registry entry)" for name in plan.orphaned_directories),
        *(f"registry entry {name} (its directory is gone)" for name in plan.stale_entries),
    ]
    for line in lines:
        console.print(f"{verb} {line}", markup=False)


def _print_report(report: GcReport) -> None:
    for name in report.removed:
        ok(f"removed deployment [bold]{name}[/]")
    for name, why in {**report.skipped, **report.failed}.items():
        console.print(f"[yellow]left[/] {name}: {why}")
