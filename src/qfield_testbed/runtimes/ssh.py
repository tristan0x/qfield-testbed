"""SSH to a remote host (the macOS machine running the iOS Simulator): commands, file copies, reverse tunnels."""

from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass

from qfield_testbed.runtimes.shell import Completed, Runner

log = logging.getLogger(__name__)

_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]


@dataclass(frozen=True)
class Ssh:
    runner: Runner
    destination: str

    def run(self, *cmd: str, check: bool = True, timeout: float = 300) -> Completed:
        return self.runner.run(
            ["ssh", *_OPTIONS, self.destination, "--", shlex.join(cmd)], check=check, timeout=timeout
        )

    def reachable(self) -> bool:
        return self.run("true", check=False, timeout=20).returncode == 0

    def copy_to(self, local: str, remote: str) -> None:
        self.runner.run(
            ["rsync", "-a", "-e", f"ssh {shlex.join(_OPTIONS)}", local, f"{self.destination}:{remote}"], timeout=1800
        )

    def reverse_tunnel(self, remote_port: int, local_port: int) -> int:
        """Start `ssh -N -R` in the background so the remote's ``localhost:<remote_port>`` reaches our ``local_port``."""
        cmd = ["ssh", *_OPTIONS, "-N", "-R", f"{remote_port}:127.0.0.1:{local_port}", self.destination]
        log.debug("$ %s &", " ".join(cmd))
        proc = subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL)
        return proc.pid


def stop_process(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        log.warning("cannot stop process %d", pid)


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
