"""`qft doctor` — is this host able to run the components?"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from qfield_testbed.cli._console import console, emit
from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import Testbed
from qfield_testbed.doctor import CAPABILITIES, Capability, Check, HostFacts, capabilities, host_facts, run_checks
from qfield_testbed.errors import PrerequisiteMissingError, UsageError


def doctor(
    require: Annotated[
        list[str],
        typer.Option(
            "--require",
            "-r",
            help="Exit 3 unless this capability is available (repeatable), e.g. qfield-android, source:build",
        ),
    ] = [],  # noqa: B006
) -> None:
    """Check the host for Docker, KVM, gh, QGIS, display and iOS hosts, and tell what it can run."""
    unknown = sorted(set(require) - set(CAPABILITIES))
    if unknown:
        raise UsageError(
            f"unknown capability: {', '.join(unknown)}",
            hint="one of: " + ", ".join(CAPABILITIES),
            docs="getting-started",
        )
    testbed = Testbed(GlobalConfig.load())
    checks = run_checks(testbed)
    matrix = capabilities(checks)
    host = host_facts(testbed)
    emit(
        {"checks": checks, "capabilities": matrix, "host": host},
        human=lambda: _print_report(checks, matrix, host),
    )
    missing = {key: matrix[key] for key in require if not matrix[key].can_run}
    if missing:
        hints = list(dict.fromkeys(hint for capability in missing.values() for hint in capability.fix_hints))
        raise PrerequisiteMissingError(
            f"this host cannot run: {', '.join(missing)}",
            hint="; ".join(hints) or None,
            details={"missing": {key: capability.missing for key, capability in missing.items()}},
        )


def _print_report(checks: list[Check], matrix: dict[str, Capability], host: HostFacts) -> None:
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("")
    table.add_column("CHECK")
    table.add_column("DETAIL")
    table.add_column("NEEDED BY", style="dim")
    for check in checks:
        table.add_row("[green]✓[/]" if check.ok else "[red]✗[/]", check.name, check.detail, ", ".join(check.needed_by))
    console.print(table)
    available = [key for key, capability in matrix.items() if capability.can_run]
    console.print(f"\n[bold]Can run:[/] {', '.join(available) or 'nothing'}")
    blocked = {key: capability for key, capability in matrix.items() if not capability.can_run}
    if blocked:
        console.print("[bold]Cannot run:[/]")
        for key, capability in blocked.items():
            fix = f" → {'; '.join(capability.fix_hints)}" if capability.fix_hints else ""
            console.print(f"  {key}: missing {', '.join(capability.missing)}{fix}")
    console.print(
        f"\nslots: {host.slots['used']}/{host.slots['max']} used, free disk: {host.free_disk_gib} GiB"
        f"\nhome: {host.home}\ncache: {host.cache}\nconfig: {host.config}"
    )
