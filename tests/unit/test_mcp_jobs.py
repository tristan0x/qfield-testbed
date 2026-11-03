"""Long MCP tools as jobs: answered within the call ceiling, or followed with job_status and progress."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client

from qfield_testbed import caller
from qfield_testbed.components import REGISTRY
from qfield_testbed.errors import HealthTimeoutError
from qfield_testbed.mcp.server import build_server
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_cli_bisect import MANUAL, SCRIPT, _scenario, _script
from tests.unit.test_cli_bisect import testbed as bisect_testbed  # noqa: F401 - fixture
from tests.unit.test_cli_deployment import CliQgis, fake_components, isolate  # noqa: F401 - fixtures

pytestmark = pytest.mark.anyio

SPEC = "name: demo\nqgis: {version: host}\n"


class SlowQgis(CliQgis):
    """A `qgis` whose start waits until the test lets it go, and can fail."""

    release = threading.Event()
    fail = False

    def start(self) -> None:
        logging.getLogger("tests.slow").warning("slow qgis starting")
        assert self.release.wait(10)
        if self.fail:
            raise HealthTimeoutError("qgis never became healthy")
        super().start()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def slow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, fake_components: None) -> Iterator[None]:  # noqa: F811
    del fake_components  # only its setup matters
    isolate(tmp_path, monkeypatch, runner)
    monkeypatch.setenv("QFT_MCP_CALL_CEILING", "0.3")
    REGISTRY["qgis"] = SlowQgis
    SlowQgis.release.clear()
    SlowQgis.fail = False
    yield
    SlowQgis.release.set()
    caller.set_agent_session(None)


@pytest.fixture
async def client(slow: None) -> AsyncIterator[Client]:
    del slow
    async with Client(build_server()) as connected:
        yield connected


async def structured(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None, result
    return result.structured_content


async def until_ended(client: Client, job_id: str) -> dict[str, Any]:
    for _ in range(100):
        status = await structured(client, "job_status", job_id=job_id)
        if status["status"] != "running":
            return status
        await anyio.sleep(0.05)
    raise AssertionError(f"{job_id} still running")


async def test_a_long_ensure_answers_with_a_job_to_follow(client: Client) -> None:
    started = time.monotonic()
    first = await structured(client, "deployment_ensure", spec_yaml=SPEC)
    assert time.monotonic() - started < 5
    assert first["result"] is None
    job = first["job"]
    assert (job["tool"], job["status"]) == ("deployment_ensure", "running")
    assert job["progress"]["step"] == "qgis: start"

    again = await structured(client, "deployment_ensure", spec_yaml=SPEC, wait=False)
    assert again["job"]["job_id"] == job["job_id"]  # one job per deployment at a time
    logging.getLogger("tests.slow").warning("not from the job")

    SlowQgis.release.set()
    ended = await until_ended(client, job["job_id"])
    assert ended["status"] == "succeeded"
    assert ended["error"] is None
    assert (ended["result"]["name"], ended["result"]["created"], ended["result"]["status"]) == ("demo", True, "running")
    assert ended["progress"]["steps_done"] >= 4

    lines = (await structured(client, "job_logs", job_id=job["job_id"]))["lines"]
    assert any("slow qgis starting" in line for line in lines)
    assert not any("not from the job" in line for line in lines)
    assert [listed["job_id"] for listed in (await structured(client, "job_list"))["jobs"]] == [job["job_id"]]

    reused = await structured(client, "deployment_ensure", spec_yaml=SPEC)
    assert (reused["job"], reused["result"]["created"]) == (None, False)


async def test_progress_notifications_while_waiting(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QFT_MCP_CALL_CEILING", "10s")
    steps: list[str | None] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        del progress, total
        steps.append(message)

    async def release_soon() -> None:
        await anyio.sleep(0.6)
        SlowQgis.release.set()

    result = None
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(release_soon)
        result = await client.call_tool("deployment_ensure", {"spec_yaml": SPEC}, progress_callback=on_progress)
    assert result is not None
    assert result.structured_content is not None
    assert result.structured_content["result"]["status"] == "running"
    assert "qgis: start" in steps


async def test_failures_within_the_call_and_in_a_job(client: Client) -> None:
    SlowQgis.fail = True
    pending = await structured(client, "deployment_ensure", spec_yaml=SPEC)
    SlowQgis.release.set()
    failed = await until_ended(client, pending["job"]["job_id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "TIMEOUT"
    assert (failed["error"]["details"]["component"], failed["error"]["details"]["step"]) == ("qgis", "start")

    stopped = await structured(client, "deployment_restart", name="demo")
    assert stopped["error"]["code"] == "TIMEOUT"  # released already: it fails within the call


async def test_wait_false_and_unknown_jobs(client: Client) -> None:
    pending = await structured(client, "deployment_ensure", spec_yaml=SPEC, wait=False)
    assert pending["job"]["status"] == "running"
    SlowQgis.release.set()
    assert (await until_ended(client, pending["job"]["job_id"]))["status"] == "succeeded"
    assert (await structured(client, "job_status", job_id="job-nope"))["error"]["code"] == "NOT_FOUND"
    assert (await structured(client, "job_logs", job_id="job-nope"))["error"]["code"] == "NOT_FOUND"


@pytest.mark.usefixtures("fake_components", "bisect_testbed")
async def test_bisect_run_as_a_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QFT_MCP_CALL_CEILING", "30s")
    _script(tmp_path, {0: 0, 1: 1, 2: 1})
    (tmp_path / "s.yaml").write_text(_scenario("s", SCRIPT))
    (tmp_path / "m.yaml").write_text(_scenario("m", MANUAL))
    async with Client(build_server()) as connected:
        assert (await structured(connected, "bisect_start", scenario_path=str(tmp_path / "s.yaml")))["session"] == "s"
        finished = await structured(connected, "bisect_run", session="s")
        assert finished["result"]["first_bad"]["label"] == "c2"
        await structured(connected, "bisect_start", scenario_path=str(tmp_path / "m.yaml"))
        manual = await structured(connected, "bisect_run", session="m")
        assert manual["error"]["code"] == "NEEDS_HUMAN"
        stepped = await structured(connected, "bisect_step", session="m")
        assert stepped["result"]["outcome"] == "prepared"
        recorded = await structured(connected, "bisect_step", session="m", verdict="bad")
        assert recorded["result"]["session"]["tested"] == 1
