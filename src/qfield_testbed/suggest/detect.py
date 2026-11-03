"""Which project a checkout is: QFieldCloud, QFieldSync, QField, a QGIS plugin — forks and worktrees included.

The git remote names the repository; the files of the checkout confirm it, and win when the two disagree (a fork
renamed, a monorepo). Both are kept as evidence for the rationale of the proposal.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from qfield_testbed.runtimes.shell import Runner

Project = Literal["qfieldcloud", "qfieldsync", "qfield", "qgis-plugin", "unknown"]

REPOSITORY_NAMES: dict[str, Project] = {"qfieldcloud": "qfieldcloud", "qfieldsync": "qfieldsync", "qfield": "qfield"}


@dataclass
class Detection:
    project: Project
    root: Path
    remote: str | None = None
    plugin_dir: Path | None = None  # the folder holding metadata.txt, for a QGIS plugin
    evidence: list[str] = field(default_factory=list)


def detect(runner: Runner, path: Path) -> Detection:
    path = path.expanduser().resolve()
    top = runner.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], check=False)
    root = Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else path
    remote = _remote(runner, root)
    evidence: list[str] = []
    by_remote: Project | None = None
    if remote:
        by_remote = REPOSITORY_NAMES.get(repository_name(remote).lower())
        evidence.append(f"remote {remote}" + (f" is a {by_remote} repository" if by_remote else ""))
    by_layout, plugin_dir, why = _layout(root)
    if why:
        evidence.append(why)
    # a specific layout wins over the remote; "some QGIS plugin" is too generic to overrule it
    if by_layout == "qgis-plugin" and by_remote:
        by_layout = None
    elif by_remote and by_layout and by_remote != by_layout:
        evidence.append(f"the files say {by_layout}, the remote {by_remote}: going by the files")
    project: Project = by_layout or by_remote or "unknown"
    return Detection(project, root, remote, plugin_dir if project == "qgis-plugin" else None, evidence)


def repository_name(url: str) -> str:
    """``git@github.com:me/QFieldSync.git`` or ``https://github.com/me/QFieldSync`` → ``QFieldSync``."""
    return re.split(r"[/:]", url.rstrip("/").removesuffix(".git"))[-1]


def _remote(runner: Runner, root: Path) -> str | None:
    for name in ("origin", "upstream"):
        result = runner.run(["git", "-C", str(root), "remote", "get-url", name], check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _layout(root: Path) -> tuple[Project | None, Path | None, str | None]:
    """The project the files of ``root`` look like, the plugin folder for a QGIS plugin, and why."""
    if _squashed(_plugin_name(root / "qfieldsync" / "metadata.txt")) == "qfieldsync":
        return "qfieldsync", None, "qfieldsync/metadata.txt is the QFieldSync plugin"
    if (root / "docker-compose.yml").is_file() and (root / "docker-app" / "qfieldcloud").is_dir():
        return "qfieldcloud", None, "docker-compose.yml and docker-app/qfieldcloud/ are QFieldCloud's"
    if (root / "CMakeLists.txt").is_file() and (root / "src" / "qml").is_dir():
        return "qfield", None, "CMakeLists.txt and src/qml/ are QField's"
    for candidate in (root, *sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))):
        name = _plugin_name(candidate / "metadata.txt")
        if name:
            return "qgis-plugin", candidate, f"{(candidate / 'metadata.txt').relative_to(root)} is QGIS plugin '{name}'"
    return None, None, None


def _squashed(name: str | None) -> str:
    """`QField Sync` and `QFieldSync` alike."""
    return re.sub(r"[\s_-]+", "", name or "").lower()


def _plugin_name(metadata: Path) -> str | None:
    if not metadata.is_file():
        return None
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(metadata.read_text(errors="replace"))
    except configparser.Error:
        return None
    return parser.get("general", "name", fallback=None) if parser.has_section("general") else None
