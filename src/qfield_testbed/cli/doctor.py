"""`qft doctor` — is this host able to run the components?"""

from __future__ import annotations

from rich.table import Table

from qfield_testbed.cli._console import console, emit
from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import Testbed
from qfield_testbed.doctor import Check, run_checks


def doctor() -> None:
    """Check the host for Docker, KVM, gh, QGIS, display and configured iOS hosts."""
    testbed = Testbed(GlobalConfig.load())
    checks = run_checks(testbed)
    data = {
        "checks": checks,
        "home": testbed.paths.home,
        "cache": testbed.paths.cache,
        "config": GlobalConfig.file_path(),
    }
    emit(data, human=lambda: _print_checks(testbed, checks))


def _print_checks(testbed: Testbed, checks: list[Check]) -> None:
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("")
    table.add_column("CHECK")
    table.add_column("DETAIL")
    table.add_column("NEEDED BY", style="dim")
    for check in checks:
        table.add_row("[green]✓[/]" if check.ok else "[red]✗[/]", check.name, check.detail, ", ".join(check.needed_by))
    console.print(table)
    fixes = [check for check in checks if not check.ok and check.fix_hint]
    if fixes:
        console.print("\n[bold]To fix[/]")
        for check in fixes:
            console.print(f"  {check.name}: {check.fix_hint}")
    console.print(f"\nhome: {testbed.paths.home}\ncache: {testbed.paths.cache}\nconfig: {GlobalConfig.file_path()}")
