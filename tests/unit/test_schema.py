"""`qft spec schema`: what an agent or an editor validates a spec against must accept what qft accepts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from qfield_testbed.bisect.scenario import EXAMPLE_SCENARIO, scenario_json_schema
from qfield_testbed.spec import EXAMPLE_FULL, EXAMPLE_MINIMAL, spec_json_schema
from tests.unit.test_cli_deployment import invoke

EXAMPLES = Path(__file__).parents[2] / "examples"


def _errors(schema: dict[str, Any], document: Any) -> list[str]:
    return [error.message for error in Draft202012Validator(schema).iter_errors(document)]


@pytest.mark.parametrize("text", [EXAMPLE_FULL, EXAMPLE_MINIMAL, (EXAMPLES / "full.yaml").read_text()])
def test_example_specs_follow_the_schema(text: str) -> None:
    assert _errors(spec_json_schema(), yaml.safe_load(text)) == []


@pytest.mark.parametrize("file", sorted(EXAMPLES.glob("bisect-*.yaml")), ids=lambda p: p.name)
def test_example_scenarios_follow_the_scenario_schema(file: Path) -> None:
    assert _errors(scenario_json_schema(), yaml.safe_load(file.read_text())) == []


def test_the_built_in_scenario_example_follows_it() -> None:
    assert _errors(scenario_json_schema(), yaml.safe_load(EXAMPLE_SCENARIO)) == []


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("release:v26.28", True),
        ("git:master", True),
        ({"kind": "git", "ref": "master"}, True),
        ("nope:1", False),
        ("release:", False),
        ("host", False),  # host is a QGIS source, not a QFieldCloud one
    ],
)
def test_version_shorthands_match_what_qft_accepts(version: Any, accepted: bool) -> None:
    spec = {"name": "demo", "qfieldcloud": {"version": version}}
    assert (_errors(spec_json_schema(), spec) == []) is accepted


def test_qgis_takes_host_or_docker() -> None:
    schema = spec_json_schema()
    assert _errors(schema, {"name": "q", "qgis": {"version": "host"}}) == []
    assert _errors(schema, {"name": "q", "qgis": {"version": "docker:3.44"}}) == []
    assert _errors(schema, {"name": "q", "qgis": {"version": "release:3.44"}}) != []


def test_spec_schema_command() -> None:
    raw = invoke(["spec", "schema"])
    assert json.loads(raw.stdout)["title"] == "DeploymentSpec"
    wrapped = json.loads(invoke(["--json", "spec", "schema", "--scenario"]).stdout)
    assert wrapped["data"]["schema"]["title"] == "BisectScenario"
