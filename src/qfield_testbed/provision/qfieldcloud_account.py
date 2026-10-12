"""Talking to a deployment's QFieldCloud as the test account: token, demo project upload."""

from __future__ import annotations

import logging
import time
from importlib import resources
from pathlib import Path
from typing import Any

import requests

from qfield_testbed.errors import TestbedError
from qfield_testbed.runtimes.http import get_json, post_json

log = logging.getLogger(__name__)

DEMO_PROJECT_NAME = "testbed-demo"


def api(url: str) -> str:
    return url.rstrip("/") + "/api/v1/"


def login(url: str, username: str, password: str) -> str:
    """``POST /api/v1/auth/token/`` → the token key."""
    try:
        data = post_json(api(url) + "auth/token/", {"username": username, "password": password})
    except requests.RequestException as exc:
        raise TestbedError(f"login of '{username}' failed on {url}: {exc}") from exc
    token = data.get("token")
    if not token:
        raise TestbedError(f"login of '{username}' on {url} returned no token: {data}")
    return str(token)


def server_info(url: str) -> dict[str, Any]:
    try:
        return dict(get_json(api(url) + "server/info/"))
    except requests.RequestException as exc:
        raise TestbedError(f"cannot read server info from {url}: {exc}") from exc


def sample_project_dir() -> Path:
    return Path(str(resources.files("qfield_testbed").joinpath("assets", "sample_project")))


def ensure_demo_project(
    url: str, token: str, owner: str, source: Path | None = None, *, job_timeout: float = 180
) -> tuple[str, str]:
    """Create ``testbed-demo`` for the test account (if missing), upload the sample project, process it.

    Returns ``(project id, project status)``. Uses ``qfieldcloud-sdk`` so the upload path is the one
    QFieldSync's own tooling relies on; the ``process_projectfile`` job is forced and awaited so the project
    is ready for clients (and so a broken worker shows up at `start`, not later in QField).
    """
    from qfieldcloud_sdk import sdk  # noqa: PLC0415 - optional dependency, imported lazily

    client = sdk.Client(url=api(url), token=token)
    projects = client.list_projects()
    existing = next((p for p in projects if p["name"] == DEMO_PROJECT_NAME and p["owner"] == owner), None)
    if existing:
        project_id = str(existing["id"])
    else:
        created = client.create_project(
            name=DEMO_PROJECT_NAME, owner=owner, description="qfield-testbed sample project"
        )
        project_id = str(created["id"])
        log.info("created demo project %s (%s)", DEMO_PROJECT_NAME, project_id)
    client.upload_files(
        project_id,
        upload_type=sdk.FileTransferType.PROJECT,
        project_path=str(source or sample_project_dir()),
        filter_glob="*",
        throw_on_error=True,
    )
    job = client.job_trigger(project_id, sdk.JobTypes.PROCESS_PROJECTFILE, force=True)
    deadline = time.monotonic() + job_timeout
    status = str(job.get("status", "pending"))
    while status not in ("finished", "failed", "stopped") and time.monotonic() < deadline:
        time.sleep(2)
        status = str(client.job_status(job["id"]).get("status"))
    if status != "finished":
        feedback = client.job_status(job["id"]).get("feedback") or {}
        log.warning("demo project processing ended with status '%s': %s", status, feedback.get("error", ""))
    return project_id, str(client.get_project(project_id).get("status"))
