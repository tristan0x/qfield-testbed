"""The console script as users run it, in a subprocess: in-process tests cannot see what `main()` exits with."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


def _qft(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    config = tmp_path / "config.yaml"
    config.write_text("port_base: 30000\nmax_slots: 5\n")
    env = {
        **os.environ,
        "QFT_CONFIG": str(config),
        "QFT_HOME": str(tmp_path / "home"),
        "QFT_CACHE_DIR": str(tmp_path / "cache"),
    }
    return subprocess.run(
        [sys.executable, "-m", "qfield_testbed", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (["deployment", "show"], 2, "Missing argument 'name'"),
        (["spec", "validate", "missing.yaml"], 2, "cannot read spec"),
        (["deployment", "show", "nothing"], 1, "No deployment named 'nothing'"),
        (["--version"], 0, "qfield-testbed"),
    ],
    ids=["usage-error", "spec-error", "testbed-error", "version"],
)
def test_qft_exit_status(tmp_path: Path, args: list[str], code: int, message: str) -> None:
    result = _qft(tmp_path, *args)
    assert result.returncode == code
    assert message in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
