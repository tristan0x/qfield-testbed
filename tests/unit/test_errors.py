"""The error taxonomy is the machine contract of `qft` (docs/adr/0001-agent-interface.md): codes never drift."""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

import qfield_testbed
from qfield_testbed.errors import TestbedError, UnknownDeploymentError

# every error class of the package, with the (code, exit code) agents rely on
EXPECTED = {
    "TestbedError": ("ERROR", 1),
    "UsageError": ("USAGE", 2),
    "SpecError": ("INVALID_SPEC", 2),
    "AlreadyExistsError": ("ALREADY_EXISTS", 2),
    "DeploymentExistsError": ("ALREADY_EXISTS", 2),
    "PrerequisiteMissingError": ("PREREQ_MISSING", 3),
    "NeedsHumanError": ("NEEDS_HUMAN", 3),
    "UnsupportedComponentError": ("PREREQ_MISSING", 3),
    "ResourceExhaustedError": ("RESOURCE_EXHAUSTED", 4),
    "NoFreeSlotError": ("RESOURCE_EXHAUSTED", 4),
    "NotFoundError": ("NOT_FOUND", 5),
    "UnknownDeploymentError": ("NOT_FOUND", 5),
    "UnknownSessionError": ("NOT_FOUND", 5),
    "TimeoutExceededError": ("TIMEOUT", 6),
    "HealthTimeoutError": ("TIMEOUT", 6),
    "ComponentFailureError": ("COMPONENT_FAILED", 7),
    "MaterializationUnavailableError": ("COMPONENT_FAILED", 7),
    "WiringError": ("COMPONENT_FAILED", 7),
    "CommandError": ("COMPONENT_FAILED", 7),
    "RefusedError": ("REFUSED", 8),
    "BisectAbortError": ("ABORTED", 1),
}

SRC = Path(qfield_testbed.__file__).parent


def _all_error_classes() -> dict[str, type[TestbedError]]:
    for module in pkgutil.walk_packages(qfield_testbed.__path__, prefix="qfield_testbed."):
        importlib.import_module(module.name)
    found: dict[str, type[TestbedError]] = {}
    pending: list[type[TestbedError]] = [TestbedError]
    while pending:
        cls = pending.pop()
        found[cls.__name__] = cls
        pending.extend(cls.__subclasses__())
    return found


def test_every_error_class_has_its_documented_code_and_exit_code() -> None:
    classes = _all_error_classes()
    assert sorted(classes) == sorted(EXPECTED), "add new error classes to EXPECTED and to the ADR"
    for name, cls in classes.items():
        assert (cls.code, cls.exit_code) == EXPECTED[name], name


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(SRC)))
def test_no_untyped_raise(path: Path) -> None:
    """`raise TestbedError(...)` would exit 1 with code ERROR: pick the subclass that says what went wrong."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            func = node.exc.func
            assert not (isinstance(func, ast.Name) and func.id == "TestbedError"), f"{path.name}:{node.lineno}"


def test_hint_docs_and_details_are_carried() -> None:
    exc = UnknownDeploymentError("demo")
    assert str(exc) == exc.message == "No deployment named 'demo'."
    assert exc.hint == "run `qft deployment list`"
    assert exc.docs is None
    assert exc.details == {}
    detailed = TestbedError("boom", hint="h", docs="spec", details={"k": 1})
    assert (detailed.hint, detailed.docs, detailed.details) == ("h", "spec", {"k": 1})
