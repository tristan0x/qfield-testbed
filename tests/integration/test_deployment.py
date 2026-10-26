"""A QFieldCloud release deployed for real: lifecycle, side-by-side isolation, use from scripts.

The tests share one deployment (`it-alpha`) to start QFieldCloud once, so they run in file order: the last ones
stop and remove it.
"""

from __future__ import annotations

import shlex
import sys
from typing import Any

import pytest
import requests

from tests.integration.helpers import (
    Qft,
    api,
    compose_containers,
    compose_volumes,
    network_exists,
    projects,
    qfieldcloud_spec,
    token_for,
)

pytestmark = pytest.mark.docker

ALPHA = "it-alpha"
BETA = "it-beta"
DEMO_PROJECT = "testbed-demo"


@pytest.fixture(scope="module")
def alpha(qft: Qft) -> dict[str, Any]:
    qft.add(qfieldcloud_spec(ALPHA))
    return qft.show(ALPHA)


def test_start_provisions_the_test_account_and_the_demo_project(alpha: dict[str, Any]) -> None:
    qfieldcloud = alpha["components"]["qfieldcloud"]
    assert alpha["status"] == "running"
    assert qfieldcloud["status"] == "running"
    assert qfieldcloud["version"]
    demo = [project for project in projects(alpha) if project["name"] == DEMO_PROJECT]
    assert [project["id"] for project in demo] == [qfieldcloud["demo_project_id"]]
    assert qfieldcloud["demo_project_status"] == "ok"


def test_env_exports_the_deployment(qft: Qft, alpha: dict[str, Any]) -> None:
    exported = dict(
        shlex.split(line.removeprefix("export "))[0].split("=", 1)
        for line in qft("deployment", "env", ALPHA).stdout.splitlines()
    )
    assert exported["QFT_DEPLOYMENT"] == ALPHA
    assert exported["QFT_NETWORK"] == alpha["network"]
    assert exported["QFT_QFIELDCLOUD_URL"] == alpha["components"]["qfieldcloud"]["url"]
    assert exported["QFT_QFIELDCLOUD_USERNAME"] == alpha["test_account"]["username"]
    assert exported["QFT_QFIELDCLOUD_PASSWORD"] == alpha["test_account"]["password"]
    assert exported["QFT_QFIELDCLOUD_TOKEN"]


def test_run_gives_a_command_the_deployment(qft: Qft, alpha: dict[str, Any]) -> None:
    # another client logging in (a script with `requests`, curl…) must not expire the exported token
    token_for(alpha)
    # the command reaches the server with nothing but the QFT_* variables
    script = (
        "import os, requests\n"
        "url = os.environ['QFT_QFIELDCLOUD_URL'] + 'api/v1/projects/'\n"
        "headers = {'Authorization': 'Token ' + os.environ['QFT_QFIELDCLOUD_TOKEN']}\n"
        "print(*[p['name'] for p in requests.get(url, headers=headers, timeout=30).json()])\n"
    )
    result = qft("deployment", "run", ALPHA, "--", sys.executable, "-c", script)
    assert DEMO_PROJECT in result.stdout.split()

    failing = qft("deployment", "run", ALPHA, "--", sys.executable, "-c", "raise SystemExit(3)", check=False)
    assert failing.returncode == 3


def test_a_second_deployment_runs_beside_the_first_one(qft: Qft, alpha: dict[str, Any]) -> None:
    qft.add(qfieldcloud_spec(BETA, demo_project=False))
    beta = qft.show(BETA)
    beta_project = beta["components"]["qfieldcloud"]["compose_project"]

    assert beta["slot"] != alpha["slot"]
    assert not set(beta["ports"].values()) & set(alpha["ports"].values())
    assert beta["network"] != alpha["network"]
    assert beta_project != alpha["components"]["qfieldcloud"]["compose_project"]
    # one database each: the same username with the other deployment's password is refused
    assert projects(beta) == []
    assert DEMO_PROJECT in [project["name"] for project in projects(alpha)]
    stolen = {"username": alpha["test_account"]["username"], "password": alpha["test_account"]["password"]}
    assert requests.post(api(beta) + "auth/token/", json=stolen, timeout=30).status_code == 401

    qft("deployment", "remove", BETA, "--force")
    assert compose_containers(beta_project) == []
    assert compose_volumes(beta_project) == []
    assert not network_exists(beta["network"])
    assert DEMO_PROJECT in [project["name"] for project in projects(alpha)]


def test_stop_then_start_keeps_the_data(qft: Qft, alpha: dict[str, Any]) -> None:
    demo_project_id = alpha["components"]["qfieldcloud"]["demo_project_id"]

    qft("deployment", "stop", ALPHA)
    assert qft.show(ALPHA)["status"] == "stopped"
    with pytest.raises(requests.ConnectionError):
        token_for(alpha)

    qft("deployment", "start", ALPHA)
    assert qft.show(ALPHA)["status"] == "running"
    assert demo_project_id in [project["id"] for project in projects(alpha)]


def test_remove_leaves_nothing_behind(qft: Qft, alpha: dict[str, Any]) -> None:
    compose_project = alpha["components"]["qfieldcloud"]["compose_project"]

    qft("deployment", "remove", ALPHA, "--force")

    assert ALPHA not in qft.names()
    assert not (qft.root / "home" / "deployments" / ALPHA).exists()
    assert compose_containers(compose_project) == []
    assert compose_volumes(compose_project) == []
    assert not network_exists(alpha["network"])
