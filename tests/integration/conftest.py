"""Integration tests: the real `qft` against a Docker daemon, in a testbed home of their own.

Deselected by default (`-m 'not docker and not kvm'` in pyproject.toml) and skipped unless ``QFT_INTEGRATION=1``:
they pull several GB of images and start full QFieldCloud stacks. Every deployment they create is removed at
the end of the session, also when a test fails.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from tests.integration.helpers import Qft


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("QFT_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="integration test: set QFT_INTEGRATION=1")
    for item in items:
        if item.get_closest_marker("docker") or item.get_closest_marker("kvm"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def qft(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Qft]:
    root = tmp_path_factory.mktemp("qft")
    # away from the default 20000 so deployments of the person running the tests keep their slots
    (root / "config.yaml").write_text("port_base: 41000\nmax_slots: 10\n")
    tool = Qft(root)
    yield tool
    for name in tool.names():
        tool("deployment", "remove", name, "--force", check=False)
