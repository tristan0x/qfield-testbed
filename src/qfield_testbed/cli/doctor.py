"""`qft doctor` — is this host able to run the components?"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from qfield_testbed import service
from qfield_testbed.cli._console import console, emit
from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import Testbed
from qfield_testbed.doctor import Capability, Check, HostFacts


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
    service.check_capabilities(require)
    report = service.doctor(Testbed(GlobalConfig.load()))
    emit(report, human=lambda: _print_report(report.checks, report.capabilities, report.host))
    service.require(report, require)


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
