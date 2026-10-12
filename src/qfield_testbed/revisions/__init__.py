"""Resolving version sources into revisions (git refs, GitHub releases and CI artifacts)."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from qfield_testbed.spec import VersionSource

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class Revision(BaseModel):
    """The resolved, immutable identity of a version source.

    ``sha`` is the commit when known (git-backed sources); binary-only sources keep ``sha=None`` and are
    identified by their ``label``. ``key`` is a filesystem/Docker-tag safe form used to address the cache.
    """

    model_config = ConfigDict(frozen=True)

    source: VersionSource
    label: str
    sha: str | None = None
    dirty: bool = False

    @property
    def short_sha(self) -> str | None:
        return self.sha[:12] if self.sha else None

    @property
    def key(self) -> str:
        """Filesystem- and Docker-tag-safe form of the label (``v26.28``, ``master-1a2b3c4d5e6f``, ``worktree-…-dirty``)."""
        key = _UNSAFE.sub("-", self.label).strip("-")
        return f"{key}-dirty" if self.dirty else key

    def __str__(self) -> str:
        short = self.short_sha
        suffix = f" ({short})" if short and short not in self.label else ""
        return f"{self.label}{suffix}{'+dirty' if self.dirty else ''}"
