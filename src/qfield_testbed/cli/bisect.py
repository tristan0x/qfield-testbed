"""`qft bisect …` — scenario-driven search for the first bad revision of a component."""

from __future__ import annotations

import typer

from qfield_testbed.errors import TestbedError

app = typer.Typer(no_args_is_help=True)

_NOT_YET = "bisect sessions arrive in a later milestone (see docs/bisect.md for the planned workflow)"


@app.command()
def start() -> None:
    """Create a bisect session from a scenario and its dedicated deployment."""
    raise TestbedError(_NOT_YET)


@app.command()
def run() -> None:
    """Iterate candidates until the first bad revision is found."""
    raise TestbedError(_NOT_YET)


@app.command()
def status() -> None:
    """Show the candidates, verdicts and current step of a session."""
    raise TestbedError(_NOT_YET)
