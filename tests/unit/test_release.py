"""The version is the same everywhere, and every install reference points to its tag."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

REPO = Path(__file__).parents[2]
_REFERENCE = re.compile(r"git\+https://github\.com/tristan0x/qfield-testbed(@v[\w.]+)?")


def _version() -> str:
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert isinstance(version, str)
    return version


def test_the_plugin_has_the_package_version() -> None:
    plugin = json.loads((REPO / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == _version()


def test_install_references_point_to_the_release_tag() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    found: list[str] = []
    wrong: list[str] = []
    for name in tracked:
        path = REPO / name
        if name == "CHANGELOG.md" or not path.is_file():  # the changelog quotes the past
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for match in _REFERENCE.finditer(text):
            found.append(name)
            if match.group(1) != f"@v{_version()}":
                wrong.append(f"{name}: {match.group(0)}")
    assert len(found) >= 7
    assert not wrong, "\n".join(wrong)
