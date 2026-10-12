from __future__ import annotations

from pathlib import Path

import pytest

from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import Testbed
from qfield_testbed.runtimes.shell import FakeRunner


@pytest.fixture
def config(tmp_path: Path) -> GlobalConfig:
    return GlobalConfig(home=tmp_path / "home", cache_dir=tmp_path / "cache", port_base=30000, max_slots=5)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def testbed(config: GlobalConfig, runner: FakeRunner) -> Testbed:
    return Testbed(config, runner)
