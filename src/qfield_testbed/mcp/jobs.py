"""Long operations of the MCP server as jobs: run in a thread, followed by polling or progress notifications.

A tool that may take minutes (a first `deployment_ensure` builds images) starts a job and waits for it at most the
call ceiling (`mcp_call_ceiling`, `QFT_MCP_CALL_CEILING`). Finished by then, the call answers with the result as
usual; otherwise it answers with the job, which `job_status` and `job_logs` follow. Jobs live in the server process:
they end with it, while the deployment keeps the state it had reached (`deployment_show`).
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from qfield_testbed.deployment import STEP_SINK
from qfield_testbed.errors import NotFoundError, TestbedError, error_object

log = logging.getLogger(__name__)

LOG_LINES = 500
_JSONABLE = TypeAdapter(Any)


class Progress(BaseModel):
    steps_done: int = 0
    step: str | None = None  # e.g. "qfieldcloud: health"


class JobRef(BaseModel):
    """A job still running when the call answered: follow it with `job_status`."""

    job_id: str
    tool: str
    status: Literal["running", "succeeded", "failed"]
    progress: Progress


class Job(JobRef):
    deployment: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    result: Any = None  # the tool's result once it succeeded
    error: dict[str, Any] | None = None  # the CLI's error object once it failed

    def ref(self) -> JobRef:
        return JobRef(job_id=self.job_id, tool=self.tool, status=self.status, progress=self.progress)


class JobList(BaseModel):
    jobs: list[Job]


class JobLogs(BaseModel):
    job_id: str
    lines: list[str] = Field(description="The job's log lines, oldest first")


class _ThreadLogs(logging.Handler):
    """The log records emitted by one thread."""

    def __init__(self, thread_id: int, lines: deque[str]) -> None:
        super().__init__(logging.DEBUG)
        self.thread_id = thread_id
        self.lines = lines
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread == self.thread_id:
            self.lines.append(self.format(record))


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._logs: dict[str, deque[str]] = {}
        self._done: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def start(self, tool: str, function: Callable[[], Any], *, deployment: str | None = None) -> Job:
        """Run ``function`` as a job; while a job runs on ``deployment``, return that one instead of starting another."""
        with self._lock:
            if deployment is not None and (running := self.running_for(deployment)) is not None:
                return running
            job = Job(
                job_id=f"job-{secrets.token_hex(4)}",
                tool=tool,
                status="running",
                progress=Progress(),
                deployment=deployment,
                started_at=datetime.now(tz=UTC),
            )
            self._jobs[job.job_id] = job
            self._logs[job.job_id] = deque(maxlen=LOG_LINES)
            self._done[job.job_id] = threading.Event()
        threading.Thread(target=self._run, args=(job, function), name=job.job_id, daemon=True).start()
        return job

    def _run(self, job: Job, function: Callable[[], Any]) -> None:
        handler = _ThreadLogs(threading.get_ident(), self._logs[job.job_id])
        root = logging.getLogger()
        root.addHandler(handler)

        def on_step(component: str, step: str) -> None:
            job.progress = Progress(steps_done=job.progress.steps_done + 1, step=f"{component}: {step}")

        token = STEP_SINK.set(on_step)
        try:
            job.result = _JSONABLE.dump_python(function(), mode="json", fallback=str)
            job.status = "succeeded"
        except TestbedError as exc:
            job.error = error_object(
                exc.code, exc.message or str(exc), hint=exc.hint, docs=exc.docs, details=exc.details
            )
            job.status = "failed"
        except Exception as exc:
            log.exception("job %s failed", job.job_id)
            job.error = error_object("ERROR", f"{type(exc).__name__}: {exc}")
            job.status = "failed"
        finally:
            STEP_SINK.reset(token)
            root.removeHandler(handler)
            job.finished_at = datetime.now(tz=UTC)
            self._done[job.job_id].set()

    def running_for(self, deployment: str) -> Job | None:
        return next(
            (job for job in self._jobs.values() if job.deployment == deployment and job.status == "running"), None
        )

    def get(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise NotFoundError(
                f"no job '{job_id}' in this server",
                hint="jobs end with the server process: `deployment_show` tells what state the deployment reached",
                docs="mcp",
            ) from None

    def logs(self, job_id: str, tail: int = 100) -> JobLogs:
        self.get(job_id)
        lines = list(self._logs[job_id])
        return JobLogs(job_id=job_id, lines=lines[-tail:] if tail > 0 else lines)

    def wait(self, job_id: str, timeout: float) -> bool:
        """Whether the job has ended within ``timeout`` seconds."""
        return self._done[job_id].wait(timeout)

    def list(self) -> JobList:
        return JobList(jobs=list(self._jobs.values()))
