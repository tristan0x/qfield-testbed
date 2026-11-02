from __future__ import annotations

import json
from pathlib import Path

import pytest

from qfield_testbed.config import IosHost
from qfield_testbed.deployment import Testbed
from qfield_testbed.doctor import CAPABILITIES, Capability, Check, capabilities, run_checks
from qfield_testbed.errors import PrerequisiteMissingError, UsageError
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_cli_deployment import invoke, isolate


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


@pytest.mark.parametrize(
    "case",
    [
        ("wayland", True, True, "XWayland available"),
        ("wayland", False, False, "no XWayland"),
        ("x11", False, True, "x11"),
        ("", False, False, "unknown"),
    ],
    ids=["wayland", "wayland-without-xwayland", "x11", "unknown"],
)
def test_doctor_reports_the_session(
    testbed: Testbed, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, case: tuple[str, bool, bool, str]
) -> None:
    session, with_xwayland, ok, detail = case
    monkeypatch.setenv("XDG_SESSION_TYPE", session)
    if with_xwayland:
        runner.available.add("Xwayland")
    check = _check(testbed, "session")
    assert check.ok is ok
    assert detail in check.detail


# --- capability matrix ---------------------------------------------------------------------------------------


def _matrix(*checks: Check) -> dict[str, Capability]:
    return capabilities(list(checks))


def test_a_capability_needs_every_check_that_names_it() -> None:
    kvm = Check("/dev/kvm", False, "missing", ("qfield-android",), "join the kvm group")
    docker_ = Check("docker", True, "ok", ("qfieldcloud", "qfield-android"))
    matrix = _matrix(kvm, docker_)
    assert matrix["qfieldcloud"] == Capability(True, [], [])
    assert matrix["qfield-android"] == Capability(False, ["/dev/kvm"], ["join the kvm group"])
    assert set(matrix) == set(CAPABILITIES)


def test_one_reachable_mac_is_enough_for_ios() -> None:
    down = Check("ios host 'a'", False, "unreachable", ("qfield-ios",), "fix ssh")
    up = Check("ios host 'b'", True, "ok", ("qfield-ios",), "fix ssh")
    assert _matrix(down, up)["qfield-ios"].can_run is True
    assert _matrix(down)["qfield-ios"] == Capability(False, ["ios host 'a'"], ["fix ssh"])


def test_host_matrix_without_display_xvfb_or_gh(
    testbed: Testbed, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    runner.available.add("gh")
    runner.reply(["gh", "auth", "status"], returncode=1)
    matrix = capabilities(run_checks(testbed))
    assert matrix["qgis:headless"].can_run is False
    assert matrix["qgis:headless"].fix_hints == ["install xvfb (it provides xvfb-run)"]
    assert matrix["source:release"].missing == ["gh"]
    assert matrix["source:release"].fix_hints == ["run `gh auth login`"]
    assert matrix["qfield-ios"].missing == ["ios hosts"]


def test_ios_hosts_are_checked_one_by_one(testbed: Testbed, runner: FakeRunner) -> None:
    testbed.config.ios_hosts = {"mac": IosHost(ssh="me@mac")}
    runner.reply_when(lambda args: "me@mac" in args, returncode=0)
    [ios] = [c for c in run_checks(testbed) if c.name.startswith("ios")]
    assert (ios.name, ios.ok, ios.needed_by) == ("ios host 'mac'", True, ("qfield-ios",))


# --- qft doctor ----------------------------------------------------------------------------------------------


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> FakeRunner:
    isolate(tmp_path, monkeypatch, runner)
    monkeypatch.setattr("qfield_testbed.doctor.KVM_DEVICE", tmp_path / "no-kvm")
    return runner


def test_require_a_missing_capability_exits_3(cli: FakeRunner) -> None:
    result = invoke(["doctor", "--require", "qfield-android", "--require", "qgis:docker"])
    assert isinstance(result.exception, PrerequisiteMissingError)
    assert "qfield-android" in result.exception.details["missing"]
    assert "/dev/kvm" in result.exception.details["missing"]["qfield-android"]
    assert result.exception.hint is not None


def test_require_an_available_capability_passes(cli: FakeRunner) -> None:
    cli.available.add("git")
    result = invoke(["--json", "doctor", "--require", "source:git"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["capabilities"]["source:git"] == {"can_run": True, "missing": [], "fix_hints": []}
    assert "Can run" not in result.stdout


def test_require_an_unknown_capability_is_a_usage_error(cli: FakeRunner) -> None:
    result = invoke(["doctor", "--require", "teleport"])
    assert isinstance(result.exception, UsageError)
    assert "qfield-android" in (result.exception.hint or "")


def test_doctor_text_lists_what_the_host_can_and_cannot_run(cli: FakeRunner) -> None:
    result = invoke(["doctor"])
    assert result.exit_code == 0
    assert "Can run:" in result.output
    assert "Cannot run:" in result.output
    assert "slots: 0/5 used" in result.output
