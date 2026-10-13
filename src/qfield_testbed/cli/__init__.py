"""The ``qft`` command line (noun–verb: `qft deployment add`, `qft bisect run`, `qft cache prune`…)."""

from __future__ import annotations

import logging
import sys
from typing import Annotated

import typer
from rich.logging import RichHandler

from qfield_testbed import __version__
from qfield_testbed.cli import bisect, cache, config, deployment, doctor, spec
from qfield_testbed.cli._console import console, error
from qfield_testbed.errors import TestbedError

app = typer.Typer(
    name="qft",
    help="Sandboxed deployments of the QField stack (QGIS, QFieldSync, QFieldCloud, QField) and bisect sessions.",
    no_args_is_help=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(deployment.app, name="deployment", help="Add, start, stop, inspect and remove deployments.")
app.add_typer(bisect.app, name="bisect", help="Find the revision of a component that introduced a regression.")
app.add_typer(cache.app, name="cache", help="Inspect and prune the shared cache of materializations.")
app.add_typer(config.app, name="config", help="Show the global configuration.")
app.add_typer(spec.app, name="spec", help="Help writing deployment specs.")
app.command()(doctor.doctor)


def _version(value: bool) -> None:
    if value:
        console.print(f"qfield-testbed {__version__}")
        raise typer.Exit


@app.callback()
def root(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True, help="-v for info, -vv for commands")] = 0,
    _: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True, help="Show the version")] = False,
) -> None:
    level = logging.WARNING if verbose == 0 else logging.INFO if verbose == 1 else logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, show_path=False, show_time=verbose > 1, markup=False)],
    )


def main() -> None:
    try:
        # outside standalone mode click *returns* the code of a `typer.Exit` raised by a command
        code = app(standalone_mode=False)
    except TestbedError as exc:
        error(str(exc))
        sys.exit(exc.exit_code)
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
    except typer.Abort:
        error("aborted")
        sys.exit(130)
    except KeyboardInterrupt:
        error("interrupted")
        sys.exit(130)
    if isinstance(code, int) and code:
        sys.exit(code)
