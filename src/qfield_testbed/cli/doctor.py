"""`qft doctor` — is this host able to run the components?"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from rich.table import Table

from qfield_testbed.backends.qgis_host import NO_DISPLAY_HINT
from qfield_testbed.cli._console import console
from qfield_testbed.config import GlobalConfig
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
    needed_by: str = ""


def run_checks(testbed: Testbed) -> list[Check]:
    runner = testbed.runner
    checks: list[Check] = []

    docker_ok = docker.daemon_available(runner)
    checks.append(
        Check("docker", docker_ok, "daemon reachable" if docker_ok else "not installed or not running", "all")
    )
    compose = docker.compose_version(runner) if docker_ok else None
    checks.append(Check("docker compose", compose is not None, compose or "missing", "qfieldcloud, qfield-android"))

    kvm = Path("/dev/kvm").exists() and os.access("/dev/kvm", os.R_OK | os.W_OK)
    checks.append(Check("/dev/kvm", kvm, "usable" if kvm else "missing or no permission", "qfield-android"))

    gh = runner.which("gh")
    gh_auth = bool(gh) and runner.run(["gh", "auth", "status"], check=False).returncode == 0
    checks.append(
        Check(
            "gh",
            gh_auth,
            "authenticated" if gh_auth else ("not logged in" if gh else "missing"),
            "release and artifact sources",
        )
    )

    git = runner.which("git")
    checks.append(Check("git", git is not None, git or "missing", "git and worktree sources"))

    qgis = runner.which("qgis")
    version = runner.run(["qgis", "--version"], check=False).stdout.strip().splitlines() if qgis else []
    checks.append(Check("qgis (host)", qgis is not None, version[0] if version else "missing", "qgis: host"))

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
            "qfieldsync (profile seeding)",
        )
    )

    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    checks.append(Check("display", bool(display), display or "no DISPLAY", "qgis, qfield-desktop"))

    # QGIS refuses to start without a display, even with --code: the host launcher falls back to xvfb-run
    xvfb = runner.which("xvfb-run")
    if xvfb:
        xvfb_detail = xvfb
    elif display:
        xvfb_detail = "missing: needed only without a display"
    else:
        xvfb_detail = f"missing: {NO_DISPLAY_HINT.removeprefix('qft: ')}"
    checks.append(Check("xvfb-run", xvfb is not None, xvfb_detail, "qgis: host without a display (--code)"))

    for name, host in testbed.config.ios_hosts.items():
        reachable = (
            runner.run(
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=5",
                    host.ssh,
                    "xcrun",
                    "simctl",
                    "list",
                    "-j",
                    "devices",
                ],
                check=False,
            ).returncode
            == 0
        )
        checks.append(
            Check(
                f"ios host '{name}'",
                reachable,
                host.ssh if reachable else f"{host.ssh}: unreachable or no Xcode",
                "qfield-ios",
            )
        )
    if not testbed.config.ios_hosts:
        checks.append(Check("ios hosts", False, "none configured (ios_hosts in config)", "qfield-ios"))

    free_gib = shutil.disk_usage(testbed.paths.cache if testbed.paths.cache.exists() else Path.home()).free // 2**30
    checks.append(
        Check(
            "free disk",
            free_gib >= LOW_DISK_GIB,
            f"{free_gib} GiB"
            + ("" if free_gib >= BUILD_DISK_GIB else f" (< {BUILD_DISK_GIB} GiB: build sources refused)"),
            "cache, builds",
        )
    )
    return checks


def doctor() -> None:
    """Check the host for Docker, KVM, gh, QGIS, display and configured iOS hosts."""
    testbed = Testbed(GlobalConfig.load())
    table = Table(box=None, header_style="bold", pad_edge=False)
    table.add_column("")
    table.add_column("CHECK")
    table.add_column("DETAIL")
    table.add_column("NEEDED BY", style="dim")
    for check in run_checks(testbed):
        table.add_row("[green]✓[/]" if check.ok else "[red]✗[/]", check.name, check.detail, check.needed_by)
    console.print(table)
    console.print(f"\nhome: {testbed.paths.home}\ncache: {testbed.paths.cache}\nconfig: {GlobalConfig.file_path()}")
