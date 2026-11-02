"""Running external commands through an injectable :class:`Runner`.

Components never call ``subprocess`` directly: they receive a runner, so unit tests can record the commands a
component would run (:class:`FakeRunner`) without Docker, git or adb on the machine.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from qfield_testbed.errors import CommandError, NotFoundError

log = logging.getLogger(__name__)

Command = Sequence[str]


@dataclass(frozen=True)
class Completed:
    args: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    def check(self) -> Completed:
        if self.returncode != 0:
            tail = "\n".join(self.stderr.strip().splitlines()[-15:])
            raise CommandError(f"`{' '.join(self.args)}` exited with {self.returncode}\n{tail}".rstrip())
        return self


class Runner(Protocol):
    def run(
        self,
        cmd: Command,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> Completed: ...

    def which(self, program: str) -> str | None: ...


class SubprocessRunner:
    """The real thing. ``env`` is merged over the current environment."""

    def run(
        self,
        cmd: Command,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> Completed:
        log.debug("$ %s%s", " ".join(cmd), f"  (cwd={cwd})" if cwd else "")
        merged = {**os.environ, **env} if env else None
        try:
            proc = subprocess.run(  # noqa: PLW1510 - return code handled below
                list(cmd),
                cwd=cwd,
                env=merged,
                input=stdin,
                text=True,
                capture_output=capture,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise CommandError(f"program not found: {cmd[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandError(f"`{' '.join(cmd)}` timed out after {timeout}s") from exc
        completed = Completed(tuple(cmd), proc.returncode, proc.stdout or "", proc.stderr or "")
        return completed.check() if check else completed

    def which(self, program: str) -> str | None:
        return shutil.which(program)


Matcher = Callable[[tuple[str, ...]], bool]


@dataclass
class FakeRunner:
    """Records commands; replies come from ``responses`` matched on a command prefix or a predicate (first match wins)."""

    responses: list[tuple[tuple[str, ...] | Matcher, Completed]] = field(default_factory=list)
    calls: list[Completed] = field(default_factory=list)
    available: set[str] = field(default_factory=lambda: {"docker", "git", "gh", "adb", "ssh", "qgis", "python3"})

    def reply(self, prefix: Sequence[str], stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.responses.append((tuple(prefix), Completed(tuple(prefix), returncode, stdout, stderr)))

    def reply_when(self, matcher: Matcher, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        """Reply to any command the predicate accepts (e.g. ``lambda args: "--version" in args``)."""
        self.responses.append((matcher, Completed((), returncode, stdout, stderr)))

    def run(
        self,
        cmd: Command,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
        stdin: str | None = None,
        timeout: float | None = None,
    ) -> Completed:
        del cwd, env, capture, stdin, timeout
        args = tuple(cmd)
        for matcher, response in self.responses:
            matched = matcher(args) if callable(matcher) else args[: len(matcher)] == matcher
            if matched:
                completed = Completed(args, response.returncode, response.stdout, response.stderr)
                break
        else:
            completed = Completed(args, 0)
        self.calls.append(completed)
        return completed.check() if check else completed

    def which(self, program: str) -> str | None:
        return f"/usr/bin/{program}" if program in self.available else None

    def commands(self) -> list[str]:
        return [" ".join(call.args) for call in self.calls]


# --- user commands -----------------------------------------------------------------------------------------------

KILL_GRACE_S = 5


@dataclass
class ProcessResult:
    """How a user command ended; the tails are the last lines of its output when it was captured."""

    exit_code: int | None  # None when it was killed after a timeout
    duration_s: float
    timed_out: bool
    stdout_tail: str | None
    stderr_tail: str | None


def run_process(
    cmd: Command,
    *,
    env: Mapping[str, str],
    timeout: float | None = None,
    capture: bool = False,
    tail_lines: int = 200,
) -> ProcessResult:
    """Run a user's command (not a component's: no :class:`Runner`), bounded by ``timeout``.

    With a timeout or capture the command gets its own process group, killed as a whole on timeout — QGIS
    started by a script dies with it. Without either, it shares the terminal as an interactive command would.
    """
    isolated = timeout is not None or capture
    pipe = subprocess.PIPE if capture else None
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(cmd), env=dict(env), stdout=pipe, stderr=pipe, text=True, start_new_session=isolated
        )
    except FileNotFoundError as exc:
        raise NotFoundError(f"program not found: {cmd[0]}") from exc
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        stdout, stderr = proc.communicate()
    except KeyboardInterrupt:
        if isolated:
            _kill_group(proc)
        raise
    return ProcessResult(
        exit_code=None if timed_out else proc.returncode,
        duration_s=round(time.monotonic() - started, 3),
        timed_out=timed_out,
        stdout_tail=_tail(stdout, tail_lines) if capture else None,
        stderr_tail=_tail(stderr, tail_lines) if capture else None,
    )


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGTERM the command's process group, then SIGKILL whatever is left, children holding its pipes included."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=KILL_GRACE_S)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def _tail(text: str | None, lines: int) -> str:
    return "".join((text or "").splitlines(keepends=True)[-lines:]) if lines > 0 else ""
