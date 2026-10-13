from __future__ import annotations

import pytest

from qfield_testbed.cli.doctor import Check, run_checks
from qfield_testbed.deployment import Testbed
from qfield_testbed.runtimes.shell import FakeRunner


def _check(testbed: Testbed, name: str) -> Check:
    return next(c for c in run_checks(testbed) if c.name == name)


@pytest.mark.parametrize(
    "case",
    [
        ("", True, True, "/usr/bin/xvfb-run"),
        (":0", False, False, "needed only without a display"),
        ("", False, False, "even with --code; install xvfb"),
    ],
    ids=["installed", "display", "neither"],
)
def test_doctor_reports_xvfb_run(
    testbed: Testbed, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, case: tuple[str, bool, bool, str]
) -> None:
    display, with_xvfb, ok, detail = case
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", display)
    if with_xvfb:
        runner.available.add("xvfb-run")
    check = _check(testbed, "xvfb-run")
    assert check.ok is ok
    assert detail in check.detail
