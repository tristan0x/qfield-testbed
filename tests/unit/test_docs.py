"""`qft docs`: the bundled documentation, and the topics error messages point to."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import qfield_testbed
from qfield_testbed.docs import read, topics
from qfield_testbed.errors import NotFoundError
from tests.unit.test_cli_deployment import invoke

SRC = Path(qfield_testbed.__file__).parent


def test_topics_cover_the_docs_folder() -> None:
    names = {t.topic for t in topics()}
    assert {"spec", "glossary", "environment", "json-output", "adr/0001-agent-interface", "components/qgis"} <= names
    assert next(t for t in topics() if t.topic == "glossary").title == "Glossary"


def test_unknown_topic_suggests_close_ones() -> None:
    with pytest.raises(NotFoundError) as raised:
        read("specc")
    assert raised.value.hint is not None
    assert "spec" in raised.value.hint


def test_topics_cannot_escape_the_docs_folder() -> None:
    with pytest.raises(NotFoundError):
        read("../README")


def _docs_topics_in_source() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.keyword) and node.arg == "docs" and isinstance(node.value, ast.Constant):
                found.add(str(node.value.value))
    return found


def test_every_docs_topic_named_by_an_error_exists() -> None:
    named = _docs_topics_in_source()
    assert named, "the scan should find the docs= of the errors"
    assert named <= {t.topic for t in topics()}


def test_docs_command() -> None:
    listed = invoke(["docs"])
    assert "adr/0001-agent-interface" in listed.output
    page = invoke(["docs", "spec"])
    assert page.exit_code == 0
    assert page.output.startswith("# ")  # raw Markdown when stdout is not a terminal
    as_json = json.loads(invoke(["--json", "docs", "--list"]).stdout)
    assert any(t["topic"] == "spec" for t in as_json["data"]["topics"])
