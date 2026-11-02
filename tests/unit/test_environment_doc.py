"""docs/environment.md lists every `QFT_*` variable the components export: it is the contract of test scripts."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import qfield_testbed

COMPONENTS = Path(qfield_testbed.__file__).parent / "components"
DOC = Path(__file__).parents[2] / "docs" / "environment.md"
DROPPED = {"QFT_QFIELDCLOUD_IMAGES"}  # qfieldcloud.env() removes it: too long, internal


def _exported(path: Path) -> set[str]:
    """`QFT_<COMPONENT>_<KEY>` for every literal key of the dict each `describe()` returns."""
    variables: set[str] = set()
    for cls in (node for node in ast.parse(path.read_text()).body if isinstance(node, ast.ClassDef)):
        names = [
            node.value.value
            for node in cls.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "name"
            and isinstance(node.value, ast.Constant)
        ]
        for function in (node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "describe"):
            for ret in (node for node in ast.walk(function) if isinstance(node, ast.Return)):
                if names and isinstance(ret.value, ast.Dict):
                    prefix = "QFT_" + str(names[0]).upper().replace("-", "_")
                    variables |= {
                        f"{prefix}_{key.value.upper()}"
                        for key in ret.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
    return variables - DROPPED


@pytest.mark.parametrize("module", sorted(p.name for p in COMPONENTS.glob("*.py") if p.name != "__init__.py"))
def test_every_component_variable_is_documented(module: str) -> None:
    text = DOC.read_text()
    missing = sorted(v for v in _exported(COMPONENTS / module) if f"`{v}`" not in text)
    assert not missing, f"document in docs/environment.md: {missing}"


def test_the_scan_finds_the_components() -> None:
    assert "QFT_QGIS_LAUNCHER" in _exported(COMPONENTS / "qgis.py")
    assert "QFT_QFIELD_ANDROID_ADB" in _exported(COMPONENTS / "qfield_android.py")
