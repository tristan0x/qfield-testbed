"""Docker and Docker Compose helpers (all through the injected runner)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from qfield_testbed.runtimes.shell import Completed, Runner

_PUBLISHED = re.compile(r":(\d+)->")


def published_host_ports(runner: Runner) -> set[int]:
    """Host ports currently published by any container (running or not), so a port block can avoid them."""
    result = runner.run(["docker", "ps", "-a", "--format", "{{.Ports}}"], check=False)
    if result.returncode != 0:
        return set()
    return {int(port) for port in _PUBLISHED.findall(result.stdout)}


def daemon_available(runner: Runner) -> bool:
    return runner.which("docker") is not None and runner.run(["docker", "info"], check=False).returncode == 0


def compose_version(runner: Runner) -> str | None:
    result = runner.run(["docker", "compose", "version", "--short"], check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def image_exists(runner: Runner, image: str) -> bool:
    return runner.run(["docker", "image", "inspect", image], check=False).returncode == 0


def network_exists(runner: Runner, name: str) -> bool:
    return runner.run(["docker", "network", "inspect", name], check=False).returncode == 0


def ensure_network(runner: Runner, name: str) -> None:
    if not network_exists(runner, name):
        runner.run(["docker", "network", "create", name])


def remove_network(runner: Runner, name: str) -> None:
    if network_exists(runner, name):
        runner.run(["docker", "network", "rm", name], check=False)


def list_images(runner: Runner, reference: str) -> list[str]:
    result = runner.run(
        ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}", "--filter", f"reference={reference}"],
        check=False,
    )
    return [line for line in result.stdout.splitlines() if line and not line.endswith(":<none>")]


@dataclass
class Compose:
    """A `docker compose` invocation bound to a project name, a set of files and an env file."""

    runner: Runner
    project: str
    files: Sequence[Path]
    env_file: Path | None = None
    project_directory: Path | None = None
    profiles: Sequence[str] = field(default_factory=tuple)
    env: dict[str, str] = field(default_factory=dict)
    """Extra interpolation variables; QFieldCloud's compose files read ``${PWD}``, which subprocess does not set."""

    def base(self) -> list[str]:
        cmd = ["docker", "compose", "-p", self.project]
        if self.project_directory:
            cmd += ["--project-directory", str(self.project_directory)]
        if self.env_file:
            cmd += ["--env-file", str(self.env_file)]
        for file in self.files:
            cmd += ["-f", str(file)]
        for profile in self.profiles:
            cmd += ["--profile", profile]
        return cmd

    def run(self, *args: str, check: bool = True, capture: bool = True, timeout: float | None = None) -> Completed:
        env = {"PWD": str(self.project_directory), **self.env} if self.project_directory else self.env
        return self.runner.run(
            [*self.base(), *args],
            cwd=self.project_directory,
            env=env or None,
            check=check,
            capture=capture,
            timeout=timeout,
        )

    def up(self, *services: str, build: bool = False, pull: bool = False) -> None:
        args = ["up", "-d", "--remove-orphans"]
        args.append("--build" if build else "--no-build")
        if pull:
            args += ["--pull", "always"]
        self.run(*args, *services, capture=False, timeout=1800)

    def stop(self) -> None:
        self.run("stop", check=False, timeout=600)

    def down(self, *, volumes: bool) -> None:
        args = ["down", "--remove-orphans"]
        if volumes:
            args.append("-v")
        self.run(*args, check=False, timeout=600)

    def exec_run(self, service: str, *cmd: str, timeout: float | None = 900) -> Completed:
        """`compose run --rm <service> <cmd>` — for one-off management commands."""
        return self.run("run", "--rm", "--no-deps", service, *cmd, capture=False, timeout=timeout)

    def ps(self) -> list[dict[str, object]]:
        result = self.run("ps", "-a", "--format", "json", check=False)
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    def logs(self, *services: str, follow: bool = False, tail: str = "200") -> None:
        args = ["logs", "--tail", tail]
        if follow:
            args.append("-f")
        self.run(*args, *services, check=False, capture=False)
