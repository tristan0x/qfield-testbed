from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from qfield_testbed.cli._console import console, ok
from qfield_testbed.spec import EXAMPLE_FULL, EXAMPLE_MINIMAL, DeploymentSpec

app = typer.Typer(no_args_is_help=True)


@app.command()
def example(minimal: Annotated[bool, typer.Option("--minimal", help="The smallest useful spec")] = False) -> None:
    """Print an example deployment spec to start from."""
    console.print(EXAMPLE_MINIMAL if minimal else EXAMPLE_FULL, markup=False, highlight=False, end="")


@app.command()
def validate(spec_file: Annotated[Path, typer.Argument(show_default=False)]) -> None:
    """Validate a spec without registering a deployment."""
    spec = DeploymentSpec.from_file(spec_file)
    ok(f"valid spec '{spec.name}' with components: {', '.join(spec.components())}")
