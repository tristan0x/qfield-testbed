"""Shared console helpers: one rich console, coloured status words, errors on stderr."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console

from qfield_testbed.state import Status

console = Console()
stderr = Console(stderr=True)

_STATUS_STYLE = {
    Status.CREATED: "dim",
    Status.STOPPED: "yellow",
    Status.STARTING: "cyan",
    Status.RUNNING: "green",
    Status.FAILED: "red",
}


def styled_status(status: Status) -> str:
    return f"[{_STATUS_STYLE[status]}]{status.value}[/]"


def error(message: str) -> None:
    stderr.print(f"[bold red]error:[/] {message}")


def warn(message: str) -> None:
    stderr.print(f"[bold yellow]warning:[/] {message}")


def ok(message: str) -> None:
    console.print(f"[green]✓[/] {message}")


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))
