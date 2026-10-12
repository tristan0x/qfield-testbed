"""On-disk layout of the testbed home and the shared cache (see docs/isolation.md)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qfield_testbed.config import GlobalConfig


@dataclass(frozen=True)
class Paths:
    home: Path
    cache: Path

    @classmethod
    def from_config(cls, config: GlobalConfig) -> Paths:
        return cls(home=config.home, cache=config.cache_dir)

    # --- testbed home -------------------------------------------------------------------------------------

    @property
    def registry_file(self) -> Path:
        return self.home / "registry.json"

    @property
    def registry_lock(self) -> Path:
        return self.home / "registry.lock"

    @property
    def deployments_dir(self) -> Path:
        return self.home / "deployments"

    def deployment_dir(self, name: str) -> Path:
        return self.deployments_dir / name

    @property
    def bisect_dir(self) -> Path:
        return self.home / "bisect"

    def session_dir(self, session: str) -> Path:
        return self.bisect_dir / session

    # --- shared cache -------------------------------------------------------------------------------------

    @property
    def cache_index(self) -> Path:
        return self.cache / "index.json"

    @property
    def git_mirrors(self) -> Path:
        return self.cache / "git"

    @property
    def checkouts(self) -> Path:
        return self.cache / "checkouts"

    @property
    def artifacts(self) -> Path:
        return self.cache / "artifacts"

    @property
    def wheels(self) -> Path:
        return self.cache / "wheels"

    def ensure(self) -> None:
        for directory in (
            self.deployments_dir,
            self.bisect_dir,
            self.git_mirrors,
            self.checkouts,
            self.artifacts,
            self.wheels,
        ):
            directory.mkdir(parents=True, exist_ok=True)
