"""`adb` against an emulator running inside a container, through `docker exec`."""

from __future__ import annotations

import logging
import shlex
import time
from dataclasses import dataclass
from pathlib import Path

from qfield_testbed.errors import HealthTimeoutError
from qfield_testbed.runtimes.shell import Completed, Runner

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Adb:
    runner: Runner
    container: str

    def prefix(self) -> list[str]:
        return ["docker", "exec", self.container, "adb"]

    def command(self) -> str:
        """What a script can use as `$QFT_QFIELD_ANDROID_ADB`."""
        return " ".join(self.prefix())

    def run(self, *args: str, check: bool = True, timeout: float = 120) -> Completed:
        return self.runner.run([*self.prefix(), *args], check=check, timeout=timeout)

    def shell(self, command: str, *, check: bool = True, timeout: float = 120) -> str:
        return self.run("shell", command, check=check, timeout=timeout).stdout.strip()

    def wait_for_boot(self, timeout: float = 600) -> None:
        """The emulator reports ``sys.boot_completed=1`` and has a focused window."""
        deadline = time.monotonic() + timeout
        last = "no device"
        while time.monotonic() < deadline:
            result = self.run("wait-for-device", "shell", "getprop", "sys.boot_completed", check=False, timeout=30)
            if result.returncode == 0 and result.stdout.strip() == "1":
                focus = self.shell("dumpsys window | grep -i mCurrentFocus", check=False, timeout=30)
                if focus and "null" not in focus:
                    return
                last = f"booted, waiting for the launcher ({focus[:60]!r})"
            else:
                last = (result.stdout + result.stderr).strip()[-80:] or "not booted yet"
            time.sleep(5)
        raise HealthTimeoutError(f"Android emulator in {self.container} not ready after {timeout:.0f}s ({last})")

    def copy_in(self, source: Path, dest: str) -> None:
        self.runner.run(["docker", "cp", str(source), f"{self.container}:{dest}"], timeout=600)

    def install(self, apk_in_container: str) -> None:
        self.run("install", "-r", "-g", apk_in_container, timeout=600)

    def installed_version(self, package: str) -> str | None:
        out = self.shell(f"dumpsys package {package} | grep versionName", check=False)
        return out.split("=", 1)[1].strip() if "=" in out else None

    def root(self) -> bool:
        result = self.run("root", check=False, timeout=60)
        if result.returncode != 0:
            return False
        time.sleep(2)
        self.run("wait-for-device", check=False, timeout=60)
        return "root" in self.shell("id", check=False)

    def su(self, command: str, *, check: bool = False) -> Completed:
        """Run a shell command as root on the device (works on Google-APIs images without `adb root`)."""
        return self.run("shell", f"su root sh -c {shlex.quote(command)}", check=check)

    def launch(self, package: str) -> None:
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", check=False, timeout=60)

    def force_stop(self, package: str) -> None:
        self.shell(f"am force-stop {package}", check=False)

    def find_files(self, root: str, pattern: str) -> list[str]:
        out = self.su(f"find {root} -name {shlex.quote(pattern)} 2>/dev/null").stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    def read_file(self, path: str) -> str:
        return self.su(f"cat {shlex.quote(path)}").stdout

    def write_file(self, path: str, content: str, *, owner: str | None = None) -> None:
        """Write a small text file as root, creating parent directories; chown to the app user when given."""
        directory = path.rsplit("/", 1)[0]
        quoted = shlex.quote(content)
        self.su(f"mkdir -p {shlex.quote(directory)} && printf %s {quoted} > {shlex.quote(path)}", check=True)
        if owner:
            self.su(f"chown -R {owner}:{owner} {shlex.quote(directory)}")

    def package_user(self, package: str) -> str | None:
        """The Linux user owning the app's data directory (e.g. ``u0_a123``)."""
        out = self.su(f"stat -c %U /data/data/{package}").stdout.strip()
        return out or None
