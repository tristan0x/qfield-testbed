"""A bisect over two QFieldCloud releases, with a test script that knows the answer.

The script declares the newer release bad, so the session must swap the deployment from one release to the
other and name it. On the way the script checks that the server really runs the candidate under test and that
the test account was provisioned again after the reset of the volumes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.integration.helpers import QFIELDCLOUD_RELEASE, Qft, compose_containers

pytestmark = pytest.mark.docker

SESSION = "it-releases"
GOOD = "v26.26"
CANDIDATE_BEFORE = "v26.27"

SCENARIO = f"""\
name: {SESSION}
deployment:
  qfieldcloud:
    test_account: {{ demo_project: false }}
bisect:
  component: qfieldcloud
  good: {GOOD}
  bad: {QFIELDCLOUD_RELEASE}
  candidates: tags:"v26.2*"
  source: release
test:
  script: ./verdict.py
  timeout: 300
"""

# any exit code from 128 aborts the session: a deployment that does not run the candidate is not a verdict
VERDICT = f"""\
#!{sys.executable}
import os, sys, requests

candidate = os.environ["QFT_BISECT_CANDIDATE"]
api = os.environ["QFT_QFIELDCLOUD_URL"] + "api/v1/"
info = requests.get(api + "server/info/", timeout=30).json()
print("candidate", candidate, "server", info)
if info.get("version") and info["version"].removeprefix("v") != candidate.removeprefix("v"):
    sys.exit(129)
account = {{"username": os.environ["QFT_QFIELDCLOUD_USERNAME"], "password": os.environ["QFT_QFIELDCLOUD_PASSWORD"]}}
if requests.post(api + "auth/token/", json=account, timeout=30).status_code != 200:
    sys.exit(130)
sys.exit(1 if candidate == "{QFIELDCLOUD_RELEASE}" else 0)
"""


def test_bisect_names_the_first_bad_release(qft: Qft) -> None:
    scenario = Path(qft.write(SCENARIO, "scenario.yaml"))
    verdict = scenario.parent / "verdict.py"
    verdict.write_text(VERDICT)
    verdict.chmod(0o755)

    qft("bisect", "start", str(scenario), "--run", timeout=3600)

    info = json.loads(qft("bisect", "status", SESSION, "--json").stdout)
    assert info["status"] == "done"
    assert [(row["label"], row["verdict"]) for row in info["verdicts"]] == [
        (CANDIDATE_BEFORE, "good"),
        (QFIELDCLOUD_RELEASE, "bad"),
    ]
    assert info["first_bad"]["label"] == QFIELDCLOUD_RELEASE
    report = qft.root / "home" / "bisect" / SESSION / "report.md"
    assert QFIELDCLOUD_RELEASE in report.read_text()

    deployment = qft.show(info["deployment"])
    compose_project = deployment["components"]["qfieldcloud"]["compose_project"]
    assert deployment["components"]["qfieldcloud"]["revision"].startswith(QFIELDCLOUD_RELEASE)

    qft("bisect", "remove", SESSION, "--force")
    assert info["deployment"] not in qft.names()
    assert compose_containers(compose_project) == []
