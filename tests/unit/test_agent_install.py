"""`qft agent install`: the skill copied for Claude Code, idempotent, never over a copy edited by hand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qfield_testbed import agent_install
from qfield_testbed.agent_install import MANIFEST, plan_install, skill_root
from qfield_testbed.errors import NotFoundError, RefusedError
from tests.unit.test_cli_deployment import invoke

SKILL = Path(__file__).parents[2] / "plugin" / "skills" / "qfield-testbed"


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / ".claude" / "skills" / "qfield-testbed"


def test_installs_a_copy_identical_to_the_plugin_skill(project: Path) -> None:
    result = invoke(["--json", "agent", "install", "--scope", "project"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["replaced"] is False
    assert "SKILL.md" in data["files"]
    installed = _tree(project)
    manifest = json.loads(installed.pop(MANIFEST))
    assert installed == _tree(SKILL)
    assert set(manifest["files"]) == set(installed)


def test_user_scope_goes_to_the_home(project: Path, tmp_path: Path) -> None:
    assert invoke(["agent", "install"]).exit_code == 0
    assert (tmp_path / "home" / ".claude" / "skills" / "qfield-testbed" / "SKILL.md").is_file()
    assert not project.exists()


def test_reinstalling_replaces_an_untouched_copy(project: Path) -> None:
    assert invoke(["agent", "install", "--scope", "project"]).exit_code == 0
    (project / "references" / "obsolete.md").write_text("gone in this version")
    manifest = json.loads((project / MANIFEST).read_text())
    manifest["files"]["references/obsolete.md"] = agent_install._sha256(project / "references" / "obsolete.md")
    (project / MANIFEST).write_text(json.dumps(manifest))
    result = invoke(["--json", "agent", "install", "--scope", "project"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["replaced"] is True
    assert not (project / "references" / "obsolete.md").exists()


def test_a_copy_changed_by_hand_is_refused_unless_yes(project: Path) -> None:
    assert invoke(["agent", "install", "--scope", "project"]).exit_code == 0
    with (project / "SKILL.md").open("a") as handle:
        handle.write("my notes\n")
    assert isinstance(invoke(["--json", "agent", "install", "--scope", "project"]).exception, RefusedError)
    assert "my notes" in (project / "SKILL.md").read_text()
    assert invoke(["agent", "install", "--scope", "project", "--yes"]).exit_code == 0
    assert (project / "SKILL.md").read_bytes() == (SKILL / "SKILL.md").read_bytes()


def test_a_directory_qft_did_not_make_is_refused(project: Path) -> None:
    project.mkdir(parents=True)
    (project / "SKILL.md").write_text("someone else's skill")
    assert plan_install("project", home=Path.home(), cwd=Path.cwd()).foreign
    assert isinstance(invoke(["agent", "install", "--scope", "project"]).exception, RefusedError)
    assert (project / "SKILL.md").read_text() == "someone else's skill"


def test_dry_run_writes_nothing(project: Path) -> None:
    result = invoke(["agent", "install", "--scope", "project", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would install" in result.output
    assert not project.exists()


def test_a_missing_skill_is_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_install, "_REPOSITORY_SKILL", tmp_path / "nowhere")
    with pytest.raises(NotFoundError):
        skill_root()


def test_declining_the_prompt_leaves_the_copy(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from qfield_testbed.cli import agent  # noqa: PLC0415

    assert invoke(["agent", "install", "--scope", "project"]).exit_code == 0
    (project / "SKILL.md").write_text("mine")
    monkeypatch.setattr(agent, "confirm", lambda *_, **__: False)
    assert isinstance(invoke(["agent", "install", "--scope", "project"]).exception, RefusedError)
    monkeypatch.setattr(agent, "confirm", lambda *_, **__: True)
    assert invoke(["agent", "install", "--scope", "project"]).exit_code == 0
