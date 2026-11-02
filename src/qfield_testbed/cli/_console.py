"""Shared console helpers: one rich console, coloured status words, errors on stderr, the JSON envelope.

In JSON mode (`qft --json …`, `QFT_OUTPUT=json`) stdout carries exactly one JSON document, the envelope of
docs/adr/0001-agent-interface.md; everything meant for humans (progress, ✓ lines, logs) goes to stderr.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import typer
from pydantic import TypeAdapter
from rich.console import Console

from qfield_testbed.errors import RefusedError
from qfield_testbed.state import Status

# soft_wrap: never fold long URLs/paths; piped output (eval "$(qft deployment env …)") must stay one item per line
console = Console(soft_wrap=True)
stderr = Console(stderr=True, soft_wrap=True)

SCHEMA_VERSION = 1


@dataclass
class OutputMode:
    json: bool = False
    requested: bool = False  # JSON asked for on the command line or in the environment, seen by `main()`
    warnings: list[str] = field(default_factory=list)
    emitted: bool = False


OUTPUT = OutputMode()


def json_requested(argv: list[str]) -> bool:
    """`--json` anywhere before a `--` separator, or ``QFT_OUTPUT=json``."""
    options = argv[: argv.index("--")] if "--" in argv else argv
    return "--json" in options or os.environ.get("QFT_OUTPUT", "").lower() == "json"


def set_json_output(enabled: bool) -> None:
    """Switch JSON mode on or off; in JSON mode the human console writes to stderr."""
    OUTPUT.json = enabled
    OUTPUT.warnings = []
    OUTPUT.emitted = False
    console.stderr = enabled


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
        raise RefusedError(f"refusing to {action} without confirmation: stdin is not a terminal", hint="pass --force")
    return typer.confirm(question)


def error(message: str) -> None:
    stderr.print(f"[bold red]error:[/] {message}")


def print_hint(message: str) -> None:
    stderr.print(f"[bold cyan]hint:[/] {message}")


def warn(message: str) -> None:
    if OUTPUT.json:
        OUTPUT.warnings.append(message)
    stderr.print(f"[bold yellow]warning:[/] {message}")


def ok(message: str) -> None:
    console.print(f"[green]✓[/] {message}")


def emit(data: Any, *, human: Callable[[], None]) -> None:
    """The result of a command: ``data`` in the JSON envelope, or what ``human`` prints."""
    if not OUTPUT.json:
        human()
        return
    _write_envelope({"ok": True, "data": data})


def emit_error(
    code: str,
    message: str,
    *,
    hint: str | None = None,
    docs: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    error_ = {"code": code, "message": message, "hint": hint, "docs": docs, "details": details or {}}
    _write_envelope({"ok": False, "error": error_})


def emit_default() -> None:
    """In JSON mode, a command that produced no data still answers with a success envelope."""
    if OUTPUT.json and not OUTPUT.emitted:
        _write_envelope({"ok": True, "data": None})


_JSONABLE = TypeAdapter(Any)


def _write_envelope(body: dict[str, Any]) -> None:
    OUTPUT.emitted = True
    document = {"schema_version": SCHEMA_VERSION, **body, "warnings": OUTPUT.warnings}
    # pydantic models, dataclasses, paths, dates and enums alike; anything else as its str()
    plain = _JSONABLE.dump_python(document, mode="json", fallback=str)
    # plain stdout, not rich: no markup, highlighting or wrapping may alter the document
    sys.stdout.write(json.dumps(plain, indent=2) + "\n")
    sys.stdout.flush()
