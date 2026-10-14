"""Shared cache of materializations, with an index recording who uses what so `cache prune` can decide.

Entries are keyed ``<component>/<kind>/<revision key>``; the index stores the path, size hint, timestamps and
the deployments (or bisect sessions) that referenced the entry. Entries referenced by an existing deployment
are never pruned.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from qfield_testbed.paths import Paths


def _now() -> datetime:
    return datetime.now(tz=UTC)


class CacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Path
    created_at: datetime = Field(default_factory=_now)
    last_used: datetime = Field(default_factory=_now)
    users: set[str] = Field(default_factory=set, description="Deployment names that referenced this entry")
    images: list[str] = Field(default_factory=list, description="Docker image tags belonging to this entry")


class CacheIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: dict[str, CacheEntry] = Field(default_factory=dict)


class Cache:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths

    @contextmanager
    def locked(self) -> Iterator[CacheIndex]:
        self.paths.cache.mkdir(parents=True, exist_ok=True)
        with FileLock(self.paths.cache / "index.lock", timeout=30):
            index = self.load()
            yield index
            self.paths.cache_index.write_text(index.model_dump_json(indent=2) + "\n")

    def load(self) -> CacheIndex:
        if not self.paths.cache_index.is_file():
            return CacheIndex()
        return CacheIndex.model_validate_json(self.paths.cache_index.read_text())

    def touch(self, key: str, path: Path, *, user: str | None = None, images: list[str] | None = None) -> None:
        """Record that ``key`` lives at ``path`` and that ``user`` (a deployment) depends on it."""
        with self.locked() as index:
            entry = index.entries.get(key) or CacheEntry(path=path)
            entry.path = path
            entry.last_used = _now()
            if user:
                entry.users.add(user)
            if images:
                entry.images = sorted(set(entry.images) | set(images))
            index.entries[key] = entry

    def release(self, user: str) -> None:
        """Forget a deployment: its entries stay cached but become prunable."""
        with self.locked() as index:
            for entry in index.entries.values():
                entry.users.discard(user)

    def prunable(self, *, older_than: timedelta, live_users: set[str]) -> dict[str, CacheEntry]:
        cutoff = _now() - older_than
        index = self.load()
        return {
            key: entry
            for key, entry in index.entries.items()
            if not (entry.users & live_users) and entry.last_used < cutoff
        }

    def trim(self, prefix: str, *, keep: int, protect: set[str]) -> dict[str, CacheEntry]:
        """Forget the least recently used entries under ``prefix`` holding built (``qft-``) images, beyond ``keep``.

        ``protect`` keys always stay (and count towards ``keep``). Only index entries go: their files are shared
        (an image set points at its QFieldCloud checkout), so the caller removes the images it no longer needs.
        """
        with self.locked() as index:
            built = [
                key
                for key, entry in index.entries.items()
                if key.startswith(prefix) and any(image.startswith("qft-") for image in entry.images)
            ]
            newest_first = sorted(built, key=lambda key: index.entries[key].last_used, reverse=True)
            room = max(keep - sum(1 for key in newest_first if key in protect), 0)
            candidates = [key for key in newest_first if key not in protect]
            return {key: index.entries.pop(key) for key in candidates[room:]}

    def drop(self, key: str) -> CacheEntry | None:
        with self.locked() as index:
            entry = index.entries.pop(key, None)
        if entry and entry.path.exists():
            if entry.path.is_dir():
                shutil.rmtree(entry.path)
            else:
                entry.path.unlink()
        return entry


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:  # noqa: PLR2004
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"
