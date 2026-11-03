"""`qft agent` — set up coding agents to drive qft."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from qfield_testbed.agent_install import InstallPlan, Scope, install, plan_install
from qfield_testbed.cli._console import confirm, console, emit, ok
from qfield_testbed.errors import RefusedError

app = typer.Typer(no_args_is_help=True)

PLUGIN_ROUTE = "/plugin install qfield-testbed --marketplace tristan0x/qfield-testbed"


@app.command("install")
def install_(
    scope: Annotated[Scope, typer.Option("--scope", help="user: ~/.claude/skills, project: ./.claude/skills")] = "user",
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Show what would be written")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Overwrite a copy changed by hand")] = False,
) -> None:
    """Copy the qfield-testbed skill for Claude Code (when the plugin cannot be installed)."""
    plan = plan_install(scope, home=Path.home(), cwd=Path.cwd())
    if plan.needs_confirmation and not dry_run and not yes:
        what = "not installed by qft" if plan.foreign else f"changed by hand ({', '.join(plan.modified)})"
        if not confirm(f"{plan.target} is {what}. Overwrite it?", action=f"overwrite {plan.target}"):
            raise RefusedError(f"left {plan.target} as it is", hint="pass --yes to overwrite it")
    if not dry_run:
        install(plan)
    data = {
        "scope": plan.scope,
        "target": plan.target,
        "files": plan.files,
        "replaced": plan.replaced,
        "modified": plan.modified,
        "dry_run": dry_run,
    }
    emit(data, human=lambda: _print(plan, dry_run=dry_run))


def _print(plan: InstallPlan, *, dry_run: bool) -> None:
    verb = "Would install" if dry_run else ("Updated" if plan.replaced else "Installed")
    ok(f"{verb} the qfield-testbed skill in {plan.target} ({len(plan.files)} files)")
    console.print(
        f"With the Claude Code plugin system, prefer the plugin, which also brings the session hook: {PLUGIN_ROUTE}"
        " (do not use both: the skill would be listed twice).",
        markup=False,
        highlight=False,
    )
