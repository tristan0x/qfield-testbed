"""The `ios-simulator` backend: Xcode's Simulator on a remote macOS host, driven with `xcrun simctl` over SSH."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from qfield_testbed.errors import TestbedError
from qfield_testbed.runtimes.ssh import Ssh

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimDevice:
    udid: str
    name: str
    runtime: str
    state: str


@dataclass(frozen=True)
class IosSimulatorBackend:
    ssh: Ssh
    device_name: str
    runtime: str | None = None

    def simctl(self, *args: str, check: bool = True, timeout: float = 300) -> str:
        return self.ssh.run("xcrun", "simctl", *args, check=check, timeout=timeout).stdout

    def available(self) -> bool:
        return (
            self.ssh.run("xcrun", "simctl", "list", "-j", "devices", "available", check=False, timeout=60).returncode
            == 0
        )

    def devices(self) -> list[SimDevice]:
        data: dict[str, Any] = json.loads(self.simctl("list", "-j", "devices", "available") or "{}")
        found: list[SimDevice] = []
        for runtime, devices in (data.get("devices") or {}).items():
            found.extend(SimDevice(d["udid"], d["name"], runtime, d.get("state", "")) for d in devices)
        return found

    def runtimes(self) -> list[str]:
        data = json.loads(self.simctl("list", "-j", "runtimes", "available") or "{}")
        return [r["identifier"] for r in data.get("runtimes", []) if r.get("isAvailable", True)]

    def device_types(self) -> dict[str, str]:
        data = json.loads(self.simctl("list", "-j", "devicetypes") or "{}")
        return {d["name"]: d["identifier"] for d in data.get("devicetypes", [])}

    def ensure_device(self, name: str) -> SimDevice:
        """Find the testbed's simulator (named after the deployment) or create it from `device_name`/`runtime`."""
        for device in self.devices():
            if device.name == name and (self.runtime is None or device.runtime.endswith(self.runtime)):
                return device
        types = self.device_types()
        if self.device_name not in types:
            raise TestbedError(
                f"no simulator device type named '{self.device_name}' on the Mac; one of: {', '.join(sorted(types))}"
            )
        runtimes = self.runtimes()
        if not runtimes:
            raise TestbedError("no iOS runtime available on the Mac; install one in Xcode")
        runtime = (
            next((r for r in runtimes if self.runtime and r.endswith(self.runtime)), None)
            if self.runtime
            else runtimes[-1]
        )
        if runtime is None:
            raise TestbedError(f"runtime '{self.runtime}' not available on the Mac; one of: {', '.join(runtimes)}")
        udid = self.simctl("create", name, types[self.device_name], runtime).strip()
        log.info("created simulator %s (%s, %s)", name, self.device_name, runtime)
        return SimDevice(udid, name, runtime, "Shutdown")

    def boot(self, udid: str) -> None:
        self.simctl("bootstatus", udid, "-b", timeout=600)

    def shutdown(self, udid: str) -> None:
        self.simctl("shutdown", udid, check=False)

    def delete(self, udid: str) -> None:
        self.simctl("delete", udid, check=False)

    def install(self, udid: str, app_path: str) -> None:
        self.simctl("install", udid, app_path, timeout=600)

    def launch(self, udid: str, bundle: str) -> None:
        self.simctl("launch", udid, bundle, check=False)

    def terminate(self, udid: str, bundle: str) -> None:
        self.simctl("terminate", udid, bundle, check=False)

    def app_container(self, udid: str, bundle: str) -> str | None:
        result = self.ssh.run("xcrun", "simctl", "get_app_container", udid, bundle, "data", check=False)
        return result.stdout.strip() or None if result.returncode == 0 else None

    def open_simulator_app(self) -> None:
        self.ssh.run("open", "-a", "Simulator", check=False)
