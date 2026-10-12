"""Global configuration: where the testbed lives, port allocation, remote iOS hosts.

Loaded from ``~/.config/qfield-testbed/config.yaml`` (``$QFT_CONFIG`` overrides the path). Every key has a
default so the file is optional. ``$QFT_HOME`` and ``$QFT_CACHE_DIR`` override the two directories.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_FILE = Path("~/.config/qfield-testbed/config.yaml")
DEFAULT_HOME = Path("~/.local/share/qfield-testbed")
DEFAULT_CACHE_DIR = Path("~/.cache/qfield-testbed")


class IosHost(BaseModel):
    """A macOS machine reachable over SSH that runs the iOS Simulator."""

    model_config = ConfigDict(extra="forbid")

    ssh: str = Field(description="SSH destination, e.g. `user@mac-mini.local`")
    cache_dir: str = Field(default="~/.cache/qfield-testbed", description="Where QField builds are copied on the Mac")


class CacheConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_image_sets: int = Field(default=4, ge=1, description="Built QFieldCloud image sets kept during a bisect")
    prune_after_days: int = Field(default=30, ge=0)


class Repositories(BaseModel):
    """Upstream repositories; override to point at forks."""

    model_config = ConfigDict(extra="forbid")

    qfieldcloud: str = "https://github.com/opengisch/QFieldCloud"
    qfieldsync: str = "https://github.com/opengisch/qfieldsync"
    qfield: str = "https://github.com/opengisch/QField"


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    home: Path = DEFAULT_HOME
    cache_dir: Path = DEFAULT_CACHE_DIR
    port_base: int = Field(default=20000, ge=1024, le=64000)
    max_slots: int = Field(default=50, ge=1, le=400)
    repositories: Repositories = Repositories()
    ios_hosts: dict[str, IosHost] = Field(default_factory=dict)
    cache: CacheConfig = CacheConfig()

    @classmethod
    def load(cls, path: Path | None = None) -> GlobalConfig:
        """Read the config file (if any) and apply environment overrides."""
        file = (path or Path(os.environ.get("QFT_CONFIG", DEFAULT_CONFIG_FILE))).expanduser()
        data: dict[str, object] = {}
        if file.is_file():
            data = yaml.safe_load(file.read_text()) or {}
        if home := os.environ.get("QFT_HOME"):
            data["home"] = home
        if cache_dir := os.environ.get("QFT_CACHE_DIR"):
            data["cache_dir"] = cache_dir
        config = cls.model_validate(data)
        config.home = config.home.expanduser()
        config.cache_dir = config.cache_dir.expanduser()
        return config

    @staticmethod
    def file_path() -> Path:
        return Path(os.environ.get("QFT_CONFIG", DEFAULT_CONFIG_FILE)).expanduser()
