from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import settings

from qfield_testbed.config import GlobalConfig
from qfield_testbed.deployment import Testbed
from qfield_testbed.runtimes.shell import FakeRunner

# CI explores more examples than a local run; select with HYPOTHESIS_PROFILE=ci
settings.register_profile("ci", max_examples=500, deadline=None, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def config(tmp_path: Path) -> GlobalConfig:
    return GlobalConfig(home=tmp_path / "home", cache_dir=tmp_path / "cache", port_base=30000, max_slots=5)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def testbed(config: GlobalConfig, runner: FakeRunner) -> Testbed:
    return Testbed(config, runner)
