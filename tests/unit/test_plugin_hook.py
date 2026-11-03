"""The plugin's SessionStart hook: silent outside the QField stack, one line of context and the agent session inside."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from qfield_testbed.runtimes.shell import SubprocessRunner
from qfield_testbed.suggest.detect import detect
from tests.unit.test_suggest import _checkout

HOOK = Path(__file__).parents[2] / "plugin" / "scripts" / "session_context.py"
HOOKS_JSON = HOOK.parents[1] / "hooks" / "hooks.json"


def _run(cwd: Path, *, session_id: str = "0123ABCD-ffff", env_file: Path | None = None, stdin: str | None = None):
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_ENV_FILE"}
    if env_file is not None:
        env["CLAUDE_ENV_FILE"] = str(env_file)
    payload = stdin if stdin is not None else json.dumps({"cwd": str(cwd), "session_id": session_id})
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(HOOK)], input=payload, capture_output=True, text=True, check=False, env=env
    )
    assert time.monotonic() - started < 1
    assert result.returncode == 0
    return result.stdout


def _context(stdout: str) -> str:
    output = json.loads(stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    return output["additionalContext"]


PHRASES = {"qfieldsync": "QFieldSync", "qfieldcloud": "QFieldCloud", "qfield": "QField", "qgis-plugin": "QGIS plugin"}

CASES = [
    ("qfieldsync", None, "qfieldsync"),
    ("qfieldsync", "git@github.com:me/QFieldSync.git", "qfieldsync"),
    ("qfieldcloud", "https://github.com/opengisch/QFieldCloud", "qfieldcloud"),
    ("qfield", None, "qfield"),
    ("qgis-plugin", None, "qgis-plugin"),
    ("qgis-plugin", "https://github.com/opengisch/qfieldsync.git", "qfieldsync"),
]


@pytest.mark.parametrize(("fixture", "remote", "project"), CASES)
def test_hook_names_the_project_and_agrees_with_detect(tmp_path, fixture, remote, project):
    root = _checkout(tmp_path, fixture, remote)
    context = _context(_run(root))
    assert context.startswith(f"This is a {PHRASES[project]} checkout")
    assert "qfield-testbed skill" in context
    assert "--agent-session claude-0123abcd" in context
    # same rules as `qft spec suggest`
    assert detect(SubprocessRunner(), root).project == project


def test_hook_from_a_subdirectory_of_a_fork_without_matching_layout(tmp_path):
    root = _checkout(tmp_path, "qfieldcloud", "git@github.com:me/QFieldCloud.git")
    (root / "docker-app" / "qfieldcloud" / "core").mkdir(parents=True)
    assert "QFieldCloud checkout" in _context(_run(root / "docker-app" / "qfieldcloud" / "core"))


def test_hook_is_silent_outside_the_qfield_stack(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _run(plain) == ""
    assert _run(plain, stdin="not json") == ""
    assert _run(plain, stdin="[]") == ""
    assert _run(tmp_path / "missing") == ""


def test_hook_exports_the_agent_session_when_claude_offers_an_env_file(tmp_path):
    root = _checkout(tmp_path, "qfieldsync")
    env_file = tmp_path / "env.sh"
    context = _context(_run(root, env_file=env_file))
    assert env_file.read_text() == "export QFT_AGENT_SESSION=claude-0123abcd\n"
    assert "QFT_AGENT_SESSION=claude-0123abcd is exported" in context


def test_hook_without_a_session_id(tmp_path):
    root = _checkout(tmp_path, "qfieldsync")
    env_file = tmp_path / "env.sh"
    assert "Pick one --agent-session" in _context(_run(root, session_id="", env_file=env_file))
    assert not env_file.exists()


def test_hooks_json_points_at_the_script():
    config = json.loads(HOOKS_JSON.read_text())
    (entry,) = config["hooks"]["SessionStart"]
    (hook,) = entry["hooks"]
    assert hook["command"] == '"${CLAUDE_PLUGIN_ROOT}/scripts/session_context.py"'
    assert HOOK.stat().st_mode & 0o111
