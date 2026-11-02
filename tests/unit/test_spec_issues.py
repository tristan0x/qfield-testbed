"""Spec and scenario errors are located: a dotted path and the YAML line, as data in the error details."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qfield_testbed.bisect.scenario import BisectScenario
from qfield_testbed.errors import SpecError
from qfield_testbed.spec import DeploymentSpec
from tests.unit.test_cli_deployment import invoke

BAD = "name: demo\nqgis:\n  version: host\nqfieldsync:\n  version: nope:1\n  colour: red\n"


def test_validation_errors_carry_path_and_line() -> None:
    with pytest.raises(SpecError) as raised:
        DeploymentSpec.from_yaml(BAD)
    issues = raised.value.details["issues"]
    assert [(i["path"], i["line"], i["column"]) for i in issues] == [
        ("qfieldsync.version", 5, 3),
        ("qfieldsync.colour", 6, 3),
    ]
    assert "unknown version source 'nope:1'" in issues[0]["message"]
    assert "qfieldsync.version: Value error, unknown version source" in str(raised.value)
    assert "(line 5)" in str(raised.value)
    assert raised.value.docs == "spec"


def test_errors_in_lists_point_at_the_item() -> None:
    text = "name: demo\nqgis:\n  version: host\n  plugins:\n    - zip: a.zip\n    - colour: red\n"
    with pytest.raises(SpecError) as raised:
        DeploymentSpec.from_yaml(text)
    lines = {issue["line"] for issue in raised.value.details["issues"]}
    assert lines == {6}


def test_overrides_have_no_line() -> None:
    with pytest.raises(SpecError) as raised:
        DeploymentSpec.from_yaml("name: demo\n", overrides={"qgis.version": "nope:1"})
    [issue] = raised.value.details["issues"]
    assert (issue["path"], issue["line"]) == ("qgis.version", None)


@pytest.mark.parametrize(
    ("text", "line"),
    [("name: demo\nqgis: [unclosed\n", 3), ("- just\n- a list\n", 1)],
    ids=["syntax", "not-a-mapping"],
)
def test_yaml_problems_are_located(text: str, line: int) -> None:
    with pytest.raises(SpecError) as raised:
        DeploymentSpec.from_yaml(text)
    [issue] = raised.value.details["issues"]
    assert issue["line"] == line


def test_scenario_errors_are_located(tmp_path: Path) -> None:
    file = tmp_path / "s.yaml"
    file.write_text("name: s\nbisect:\n  component: qfieldcloud\n  good: v1\n  bad: v2\ntest:\n  manual: maybe\n")
    with pytest.raises(SpecError) as raised:
        BisectScenario.from_file(file)
    [issue] = raised.value.details["issues"]
    assert (issue["path"], issue["line"]) == ("test.manual", 7)


def test_validate_reads_stdin_and_answers_with_issues() -> None:
    ok = invoke(["--json", "spec", "validate", "-"], stdin="name: demo\nqgis: {version: host}\n")
    assert json.loads(ok.stdout)["data"] == {"name": "demo", "components": ["qgis"]}
    bad = invoke(["spec", "validate", "-"], stdin=BAD)
    assert isinstance(bad.exception, SpecError)
    assert bad.exception.details["issues"][0]["line"] == 5
