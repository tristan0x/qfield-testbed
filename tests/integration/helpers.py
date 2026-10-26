"""Driving the real `qft` and looking at what it left in Docker and in QFieldCloud."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

QFIELDCLOUD_RELEASE = "v26.28"
"""The release every scenario deploys, so one image set serves them all (the bisect adds the one before it)."""


@dataclass
class Qft:
    """The `qft` command line, in a subprocess, bound to a testbed home, cache and port range of its own."""

    root: Path

    @property
    def env(self) -> dict[str, str]:
        return {
            **os.environ,
            "QFT_CONFIG": str(self.root / "config.yaml"),
            "QFT_HOME": str(self.root / "home"),
            "QFT_CACHE_DIR": str(self.root / "cache"),
        }

    def __call__(self, *args: str, check: bool = True, timeout: float = 1800) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "qfield_testbed", *args],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            pytest.fail(f"`qft {' '.join(args)}` exited with {result.returncode}\n{result.stdout}\n{result.stderr}")
        return result

    def add(self, spec: str, *args: str) -> None:
        self("deployment", "add", self.write(spec), "--start", *args)

    def show(self, name: str) -> dict[str, Any]:
        return dict(json.loads(self("deployment", "show", name, "--json").stdout))

    def names(self) -> list[str]:
        registry = self.root / "home" / "registry.json"
        return sorted(json.loads(registry.read_text())["deployments"]) if registry.is_file() else []

    def write(self, text: str, name: str = "spec.yaml") -> str:
        path = self.root / name
        path.write_text(text)
        return str(path)


def qfieldcloud_spec(name: str, *, demo_project: bool = True) -> str:
    return (
        f"name: {name}\n"
        "qfieldcloud:\n"
        f"  version: release:{QFIELDCLOUD_RELEASE}\n"
        "  test_account:\n"
        f"    demo_project: {str(demo_project).lower()}\n"
    )


def api(info: dict[str, Any]) -> str:
    return str(info["components"]["qfieldcloud"]["url"]) + "api/v1/"


def token_for(info: dict[str, Any]) -> str:
    account = info["test_account"]
    response = requests.post(
        api(info) + "auth/token/", json={"username": account["username"], "password": account["password"]}, timeout=30
    )
    response.raise_for_status()
    return str(response.json()["token"])


def projects(info: dict[str, Any]) -> list[dict[str, Any]]:
    response = requests.get(api(info) + "projects/", headers={"Authorization": f"Token {token_for(info)}"}, timeout=30)
    response.raise_for_status()
    return list(response.json())


def docker_lines(*args: str) -> list[str]:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=True)
    return result.stdout.split()


def compose_containers(project: str) -> list[str]:
    return docker_lines("ps", "-aq", "--filter", f"label=com.docker.compose.project={project}")


def compose_volumes(project: str) -> list[str]:
    return docker_lines("volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}")


def network_exists(name: str) -> bool:
    return subprocess.run(["docker", "network", "inspect", name], capture_output=True, check=False).returncode == 0
