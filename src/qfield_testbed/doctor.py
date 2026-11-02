"""Host checks behind `qft doctor`: which tools, devices and hosts are there, and what each one enables.

Each :class:`Check` names the *capability keys* it is needed by (a component such as ``qfield-android``, a
backend mode such as ``qgis:headless``, a version source such as ``source:build``), so that an agent can tell
what this host can run without parsing prose.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from qfield_testbed.backends.qgis_host import NO_DISPLAY_HINT
from qfield_testbed.deployment import Testbed
from qfield_testbed.runtimes import docker

LOW_DISK_GIB = 10
BUILD_DISK_GIB = 15
HOST_PYTHON = "/usr/bin/python3"
HOST_PYQGIS_PATH = "/usr/share/qgis/python"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    needed_by: tuple[str, ...] = field(default_factory=tuple)
    fix_hint: str | None = None


def run_checks(testbed: Testbed) -> list[Check]:
    runner = testbed.runner
    checks: list[Check] = []

    docker_ok = docker.daemon_available(runner)
    checks.append(
        Check(
            "docker",
            docker_ok,
            "daemon reachable" if docker_ok else "not installed or not running",
            ("qfieldcloud", "qfield-android", "qgis:docker"),
            "install Docker, start its daemon and add your user to the `docker` group",
        )
    )
    compose = docker.compose_version(runner) if docker_ok else None
    checks.append(
        Check(
            "docker compose",
            compose is not None,
            compose or "missing",
            ("qfieldcloud", "qfield-android"),
            "install the Docker Compose plugin (`docker compose version` must work)",
        )
    )

    kvm = Path("/dev/kvm").exists() and os.access("/dev/kvm", os.R_OK | os.W_OK)
    checks.append(
        Check(
            "/dev/kvm",
            kvm,
            "usable" if kvm else "missing or no permission",
            ("qfield-android",),
            "enable hardware virtualization and add your user to the `kvm` group (then log in again)",
        )
    )

    gh = runner.which("gh")
    gh_auth = bool(gh) and runner.run(["gh", "auth", "status"], check=False).returncode == 0
    checks.append(
        Check(
            "gh",
            gh_auth,
            "authenticated" if gh_auth else ("not logged in" if gh else "missing"),
            ("source:release", "source:artifact", "qfield-desktop"),
            "run `gh auth login`" if gh else "install the GitHub CLI (gh), then run `gh auth login`",
        )
    )

    git = runner.which("git")
    checks.append(
        Check(
            "git",
            git is not None,
            git or "missing",
            ("source:git", "source:worktree", "qfieldcloud"),
            "install git",
        )
    )

    qgis = runner.which("qgis")
    version = runner.run(["qgis", "--version"], check=False).stdout.strip().splitlines() if qgis else []
    checks.append(
        Check(
            "qgis (host)",
            qgis is not None,
            version[0] if version else "missing",
            ("qgis:host",),
            "install QGIS, or use `qgis: {version: docker:<tag>}`",
        )
    )

    # the system interpreter, not the venv's: QGIS ships its bindings in /usr/share/qgis/python
    pyqgis = (
        Path(HOST_PYTHON).exists()
        and runner.run(
            [HOST_PYTHON, "-c", "import qgis.core"], env={"PYTHONPATH": HOST_PYQGIS_PATH}, check=False
        ).returncode
        == 0
    )
    checks.append(
        Check(
            "pyqgis (host python)",
            pyqgis,
            f"{HOST_PYTHON} with PYTHONPATH={HOST_PYQGIS_PATH}" if pyqgis else "missing: install python3-qgis",
            ("qfieldsync",),
            "install python3-qgis (QFieldSync profile seeding with `qgis: host`)",
        )
    )

    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    checks.append(
        Check(
            "display",
            bool(display),
            display or "no DISPLAY",
            ("qgis:gui", "qfield-desktop"),
            "run from a desktop session (DISPLAY or WAYLAND_DISPLAY set)",
        )
    )

    # QGIS refuses to start without a display, even with --code: the host launcher falls back to xvfb-run
    xvfb = runner.which("xvfb-run")
    if xvfb:
        xvfb_detail = xvfb
    elif display:
        xvfb_detail = "missing: needed only without a display"
    else:
        xvfb_detail = f"missing: {NO_DISPLAY_HINT.removeprefix('qft: ')}"
    checks.append(
        Check("xvfb-run", xvfb is not None, xvfb_detail, ("qgis:headless",), "install xvfb (it provides xvfb-run)")
    )

    # X11 and Wayland reproduce different window-manager bugs; `qgis.platform: xcb` on Wayland needs XWayland
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if session == "wayland":
        xwayland = runner.which("Xwayland") is not None
        detail = "wayland, XWayland available" if xwayland else "wayland, no XWayland: `platform: xcb` cannot start"
        checks.append(Check("session", xwayland, detail, (), None if xwayland else "install XWayland"))
    else:
        checks.append(Check("session", bool(session), session or "unknown (XDG_SESSION_TYPE unset)"))

    checks.extend(_ios_checks(testbed))

    free_gib = shutil.disk_usage(testbed.paths.cache if testbed.paths.cache.exists() else Path.home()).free // 2**30
    checks.append(
        Check(
            "free disk",
            free_gib >= LOW_DISK_GIB,
            f"{free_gib} GiB",
            (),
            f"free at least {LOW_DISK_GIB} GiB (`qft cache prune`)",
        )
    )
    checks.append(
        Check(
            "free disk for builds",
            free_gib >= BUILD_DISK_GIB,
            f"{free_gib} GiB"
            + ("" if free_gib >= BUILD_DISK_GIB else f" (< {BUILD_DISK_GIB} GiB: build sources refused)"),
            ("source:build",),
            f"free at least {BUILD_DISK_GIB} GiB (`qft cache prune --docker-build-cache`)",
        )
    )
    return checks


def _ios_checks(testbed: Testbed) -> list[Check]:
    hint = "configure `ios_hosts` in the config, with SSH keys (BatchMode) and Xcode on the Mac"
    if not testbed.config.ios_hosts:
        return [Check("ios hosts", False, "none configured (ios_hosts in config)", ("qfield-ios",), hint)]
    checks = []
    for name, host in testbed.config.ios_hosts.items():
        command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host.ssh, "xcrun", "simctl", "list", "-j"]
        reachable = testbed.runner.run([*command, "devices"], check=False).returncode == 0
        checks.append(
            Check(
                f"ios host '{name}'",
                reachable,
                host.ssh if reachable else f"{host.ssh}: unreachable or no Xcode",
                ("qfield-ios",),
                hint,
            )
        )
    return checks
