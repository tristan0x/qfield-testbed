from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from qfield_testbed import service
from qfield_testbed.bisect.scenario import BisectScenario, scenario_json_schema
from qfield_testbed.cli._console import console, emit, ok, warn
from qfield_testbed.errors import UsageError
from qfield_testbed.spec import EXAMPLE_FULL, EXAMPLE_MINIMAL, DeploymentSpec, spec_json_schema
from qfield_testbed.suggest.propose import NEEDS, Proposal

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
def suggest(
    path: Annotated[Path, typer.Option("--path", "-p", help="The checkout to put live")] = Path(),
    need: Annotated[list[str], typer.Option("--need", help=f"Also wanted, repeatable: {', '.join(NEEDS)}")] = [],  # noqa: B006
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Write the proposed spec there")] = None,
) -> None:
    """Propose a spec for a QFieldCloud, QFieldSync, QField or QGIS plugin checkout (applied by `ensure`)."""
    proposal = service.spec_suggest(path, need)
    if output is not None:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(proposal.spec_yaml)
        except OSError as exc:
            raise UsageError(
                f"cannot write the spec to {output}: {exc.strerror}", hint="pass `--output` a writable path"
            ) from exc
    emit(proposal, human=lambda: _print_proposal(proposal))


def _print_proposal(proposal: Proposal) -> None:
    comments = [f"# {line}" for line in proposal.rationale] + [f"# note: {line}" for line in proposal.notes]
    sys.stdout.write("\n".join([*comments, ""]) + proposal.spec_yaml)


@app.command()
def validate(
    spec_file: Annotated[Path, typer.Argument(help="Spec (YAML); `-` reads stdin", show_default=False)],
    scenario: Annotated[
        bool, typer.Option("--scenario", help="A bisect scenario instead, checked as `bisect start` would")
    ] = False,
) -> None:
    """Validate a spec without registering a deployment; errors give the path and line of each problem."""
    from_stdin = str(spec_file) == "-"
    if scenario:
        if from_stdin:
            raise UsageError(
                "a scenario is read from its file", hint="its relative `test.script` is resolved from there"
            )
        checked = service.scenario_validate(BisectScenario.from_file(spec_file))
        for problem in checked.problems:
            warn(problem)
        emit(checked, human=lambda: ok(f"valid scenario '{checked.name}' (deployment {checked.deployment})"))
        return
    spec = DeploymentSpec.from_yaml(sys.stdin.read()) if from_stdin else DeploymentSpec.from_file(spec_file)
    result = service.spec_validate(spec)
    emit(result, human=lambda: ok(f"valid spec '{result.name}' with components: {', '.join(result.components)}"))
