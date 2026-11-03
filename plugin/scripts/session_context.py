#!/usr/bin/env python3
"""SessionStart hook: in a QField-stack checkout, tell the agent that the qfield-testbed skill applies.

Reads the hook payload (``cwd``, ``session_id``) on stdin. Outside QFieldCloud, QFieldSync, QField and QGIS plugin
checkouts it prints nothing. Inside, it prints one line of context and, when Claude Code offers ``CLAUDE_ENV_FILE``,
exports ``QFT_AGENT_SESSION`` there for the shell commands of the session.

Standard library only, no network, no qft: it runs before qft may be installed. The detection rules are those of
``qfield_testbed.suggest.detect`` (a parity test keeps them aligned); any failure stays silent.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_NAMES = {"qfieldcloud": "qfieldcloud", "qfieldsync": "qfieldsync", "qfield": "qfield"}

WHAT_RUNS = {
    "qfieldsync": "a QFieldSync checkout: the qfield-testbed skill can run it live in QGIS against a QFieldCloud",
    "qfieldcloud": "a QFieldCloud checkout: the qfield-testbed skill can run it live with QGIS and QFieldSync",
    "qfield": "a QField checkout: the qfield-testbed skill can run QField on an emulator against a QFieldCloud",
    "qgis-plugin": "a QGIS plugin checkout: the qfield-testbed skill can run it in an isolated QGIS profile",
}


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _squashed(name: str | None) -> str:
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


def _layout(root: Path) -> str | None:
    if _squashed(_plugin_name(root / "qfieldsync" / "metadata.txt")) == "qfieldsync":
        return "qfieldsync"
    if (root / "docker-compose.yml").is_file() and (root / "docker-app" / "qfieldcloud").is_dir():
        return "qfieldcloud"
    if (root / "CMakeLists.txt").is_file() and (root / "src" / "qml").is_dir():
        return "qfield"
    candidates = (root, *sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")))
    if any(_plugin_name(candidate / "metadata.txt") for candidate in candidates):
        return "qgis-plugin"
    return None


def detect(path: Path) -> str | None:
    """The project of the checkout at ``path``, or None outside the QField stack."""
    root = Path(_git(path, "rev-parse", "--show-toplevel") or path)
    remote = _git(root, "remote", "get-url", "origin") or _git(root, "remote", "get-url", "upstream")
    by_remote = None
    if remote:
        by_remote = REPOSITORY_NAMES.get(re.split(r"[/:]", remote.rstrip("/").removesuffix(".git"))[-1].lower())
    by_layout = _layout(root)
    if by_layout == "qgis-plugin" and by_remote:  # "some QGIS plugin" is too generic to overrule the remote
        by_layout = None
    return by_layout or by_remote


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        cwd = Path(payload.get("cwd") or Path.cwd())
        project = detect(cwd)
    except (OSError, ValueError, AttributeError):
        return 0
    if project is None:
        return 0
    session = "claude-" + re.sub(r"[^a-z0-9]", "", str(payload.get("session_id", "")).lower())[:8]
    exported = False
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file and session != "claude-":
        try:
            with Path(env_file).open("a") as handle:
                handle.write(f"export QFT_AGENT_SESSION={session}\n")
            exported = True
        except OSError:
            pass
    if exported:
        agent = f"QFT_AGENT_SESSION={session} is exported for the shell commands of this conversation."
    elif session != "claude-":
        agent = f"Use --agent-session {session} on every qft call of this conversation."
    else:
        agent = "Pick one --agent-session id for every qft call of this conversation."
    context = f"This is {WHAT_RUNS[project]} (a sandboxed deployment, via the qft CLI). {agent}"
    output = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
    sys.stdout.write(json.dumps(output) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
