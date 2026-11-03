"""Copy the qfield-testbed skill into a Claude Code skills directory, for clients without the plugin system.

The wheel carries `plugin/skills/qfield-testbed` as `qfield_testbed/_skill`; an editable install or a checkout reads
the repository's folder instead. Next to the copy, `.qft-install.json` records the version and the hash of each file,
so that a later install replaces an untouched copy silently and refuses to overwrite one edited by hand.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Literal

from qfield_testbed import __version__
from qfield_testbed.errors import NotFoundError

Scope = Literal["user", "project"]
SKILL_NAME = "qfield-testbed"
MANIFEST = ".qft-install.json"
_REPOSITORY_SKILL = Path(__file__).resolve().parents[2] / "plugin" / "skills" / SKILL_NAME


def skill_root() -> Path:
    packaged = Path(str(resources.files("qfield_testbed") / "_skill"))
    root = packaged if packaged.is_dir() else _REPOSITORY_SKILL
    if not (root / "SKILL.md").is_file():
        raise NotFoundError(f"the qfield-testbed skill is missing from this installation ({root})")
    return root


def target_dir(scope: Scope, *, home: Path, cwd: Path) -> Path:
    base = home if scope == "user" else cwd
    return base / ".claude" / "skills" / SKILL_NAME


@dataclass
class InstallPlan:
    scope: Scope
    target: Path
    files: list[str]  # paths relative to the skill, as installed
    replaced: bool  # a previous copy is there
    modified: list[str] = field(default_factory=list)  # files of the previous copy changed by hand
    foreign: bool = False  # the target exists but qft did not install it

    @property
    def needs_confirmation(self) -> bool:
        return self.foreign or bool(self.modified)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skill_files(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != MANIFEST)


def _manifest(target: Path) -> dict[str, str] | None:
    try:
        data = json.loads((target / MANIFEST).read_text())
    except (OSError, ValueError):
        return None
    files = data.get("files") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in files.items()} if isinstance(files, dict) else None


def plan_install(scope: Scope, *, home: Path, cwd: Path) -> InstallPlan:
    target = target_dir(scope, home=home, cwd=cwd)
    files = _skill_files(skill_root())
    if not target.exists():
        return InstallPlan(scope, target, files, replaced=False)
    manifest = _manifest(target)
    if manifest is None:
        return InstallPlan(scope, target, files, replaced=True, foreign=True)
    modified = sorted(
        rel for rel in _skill_files(target) if rel not in manifest or _sha256(target / rel) != manifest[rel]
    )
    return InstallPlan(scope, target, files, replaced=True, modified=modified)


def install(plan: InstallPlan) -> None:
    """Replace ``plan.target`` with the skill of this version (the caller has dealt with confirmations)."""
    source = skill_root()
    if plan.target.exists():
        shutil.rmtree(plan.target)
    plan.target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, plan.target)
    manifest = {"version": __version__, "files": {rel: _sha256(plan.target / rel) for rel in plan.files}}
    (plan.target / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
