"""The documentation shipped with the package: `qft docs` reads the pages matching the installed version.

The wheel carries `docs/` as `qfield_testbed/_docs`; an editable install or a checkout reads the repository's
`docs/` instead.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from qfield_testbed.errors import NotFoundError

_REPOSITORY_DOCS = Path(__file__).resolve().parents[2] / "docs"


@dataclass
class Topic:
    topic: str  # path under docs/ without `.md`, e.g. `components/qgis`
    title: str


def docs_root() -> Path:
    packaged = Path(str(resources.files("qfield_testbed") / "_docs"))
    return packaged if packaged.is_dir() else _REPOSITORY_DOCS


def topics() -> list[Topic]:
    root = docs_root()
    return [Topic(_name(root, page), _title(page)) for page in sorted(root.rglob("*.md"))]


def read(topic: str) -> tuple[Topic, str]:
    """The page of ``topic`` and its Markdown; an unknown topic suggests the closest ones."""
    root = docs_root()
    page = root / f"{topic.removesuffix('.md')}.md"
    if not page.is_file() or root not in page.resolve().parents:
        known = [t.topic for t in topics()]
        close = difflib.get_close_matches(topic, known, n=3, cutoff=0.4)
        hint = f"did you mean: {', '.join(close)}?" if close else "run `qft docs --list`"
        raise NotFoundError(f"no documentation topic '{topic}'", hint=hint)
    return Topic(_name(root, page), _title(page)), page.read_text()


def _name(root: Path, page: Path) -> str:
    return page.relative_to(root).with_suffix("").as_posix()


def _title(page: Path) -> str:
    for line in page.read_text().splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return page.stem
