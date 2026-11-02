from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from qfield_testbed.bisect.scenario import scenario_json_schema
from qfield_testbed.cli._console import console, emit, ok
from qfield_testbed.spec import EXAMPLE_FULL, EXAMPLE_MINIMAL, DeploymentSpec, spec_json_schema

app = typer.Typer(no_args_is_help=True)


@app.command()
def example(minimal: Annotated[bool, typer.Option("--minimal", help="The smallest useful spec")] = False) -> None:
    """Print an example deployment spec to start from."""
    text = EXAMPLE_MINIMAL if minimal else EXAMPLE_FULL
    emit({"yaml": text}, human=lambda: console.print(text, markup=False, highlight=False, end=""))


@app.command()
def schema(
    scenario: Annotated[bool, typer.Option("--scenario", help="The schema of bisect scenarios instead")] = False,
) -> None:
    """Print the JSON Schema of deployment specs (or of bisect scenarios), e.g. for editor completion."""
    document = scenario_json_schema() if scenario else spec_json_schema()
    emit({"schema": document}, human=lambda: sys.stdout.write(json.dumps(document, indent=2) + "\n"))


@app.command()
def validate(spec_file: Annotated[Path, typer.Argument(show_default=False)]) -> None:
    """Validate a spec without registering a deployment."""
    spec = DeploymentSpec.from_file(spec_file)
    components = list(spec.components())
    emit(
        {"name": spec.name, "components": components},
        human=lambda: ok(f"valid spec '{spec.name}' with components: {', '.join(components)}"),
    )
