"""Docker and Docker Compose helpers (all through the injected runner)."""

from __future__ import annotations

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


def remove_labelled(runner: Runner, labels: dict[str, str]) -> list[str]:
    """Force-remove every container, running or not, carrying all ``labels``; returns the removed ids."""
    filters = [arg for key, value in labels.items() for arg in ("--filter", f"label={key}={value}")]
    result = runner.run(["docker", "ps", "-aq", *filters], check=False)
    ids = result.stdout.split() if result.returncode == 0 else []
    if ids:
        runner.run(["docker", "rm", "-f", *ids], check=False)
    return ids


COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
DEPLOYMENT_LABEL = "qft.deployment"


def _labels(runner: Runner, kind: str, label: str) -> set[str]:
    result = runner.run(
        [
            "docker",
            kind,
            "ls",
            *(["-a"] if kind == "container" else []),
            "--filter",
            f"label={label}",
            "--format",
            f'{{{{.Label "{label}"}}}}',
        ],
        check=False,
    )
    return set(result.stdout.split()) if result.returncode == 0 else set()


def compose_projects(runner: Runner, prefix: str) -> set[str]:
    """Compose projects named ``prefix…`` that still have containers or volumes."""
    found = _labels(runner, "container", COMPOSE_PROJECT_LABEL) | _labels(runner, "volume", COMPOSE_PROJECT_LABEL)
    return {project for project in found if project.startswith(prefix)}


def labelled_deployments(runner: Runner) -> set[str]:
    """Deployment names carried by containers labelled ``qft.deployment`` (QGIS in Docker)."""
    return _labels(runner, "container", DEPLOYMENT_LABEL)


def networks(runner: Runner, prefix: str) -> set[str]:
    """Plain networks named ``prefix…``; compose project networks (``<project>_…``) are left to compose."""
    result = runner.run(["docker", "network", "ls", "--filter", f"name={prefix}", "--format", "{{.Name}}"], check=False)
    names = result.stdout.split() if result.returncode == 0 else []
    return {name for name in names if name.startswith(prefix) and "_" not in name}


def compose_down_project(runner: Runner, project: str) -> None:
    """Remove a compose project's containers, networks and volumes without its compose files."""
    runner.run(["docker", "compose", "-p", project, "down", "-v", "--remove-orphans"], check=False, timeout=600)


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

    def logs(
        self,
        *services: str,
        follow: bool = False,
        tail: int | None = 200,
        since: str | None = None,
        capture: bool = False,
    ) -> str | None:
        """Print the logs of ``services`` (all when none), or return them with ``capture``; ``tail=None`` is all."""
        args = ["logs", "--tail", "all" if tail is None else str(tail)]
        if since:
            args += ["--since", since]
        if follow:
            args.append("-f")
        completed = self.run(*args, *services, check=False, capture=capture)
        return completed.stdout if capture else None
