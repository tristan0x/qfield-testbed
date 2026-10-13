"""Shared console helpers: one rich console, coloured status words, errors on stderr."""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console

from qfield_testbed.errors import TestbedError
from qfield_testbed.state import Status

# soft_wrap: never fold long URLs/paths; piped output (eval "$(qft deployment env …)") must stay one item per line
console = Console(soft_wrap=True)
stderr = Console(stderr=True, soft_wrap=True)

_STATUS_STYLE = {
    Status.CREATED: "dim",
    Status.STOPPED: "yellow",
    Status.STARTING: "cyan",
    Status.RUNNING: "green",
    Status.FAILED: "red",
    Status.WIRING_FAILED: "red",
}


def styled_status(status: Status) -> str:
    return f"[{_STATUS_STYLE[status]}]{status.value}[/]"


def stdin_is_terminal() -> bool:
    return sys.stdin.isatty()


def confirm(question: str, *, action: str) -> bool:
    """Ask ``question``; without a terminal to answer it, fail at once and name the way out instead."""
    if not stdin_is_terminal():
        raise TestbedError(f"refusing to {action} without confirmation: stdin is not a terminal; pass --force")
    return typer.confirm(question)


def error(message: str) -> None:
    stderr.print(f"[bold red]error:[/] {message}")


def warn(message: str) -> None:
    stderr.print(f"[bold yellow]warning:[/] {message}")


def ok(message: str) -> None:
    console.print(f"[green]✓[/] {message}")


def print_json(data: Any) -> None:
    console.print_json(json.dumps(data, default=str))
