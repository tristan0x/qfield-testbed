"""Running external commands through an injectable :class:`Runner`.

Components never call ``subprocess`` directly: they receive a runner, so unit tests can record the commands a
component would run (:class:`FakeRunner`) without Docker, git or adb on the machine.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from qfield_testbed.errors import CommandError

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


@dataclass
class FakeRunner:
    """Records commands; replies come from ``responses`` matched on a command prefix (first match wins)."""

    responses: list[tuple[tuple[str, ...], Completed]] = field(default_factory=list)
    calls: list[Completed] = field(default_factory=list)
    available: set[str] = field(default_factory=lambda: {"docker", "git", "gh", "adb", "ssh", "qgis", "python3"})

    def reply(self, prefix: Sequence[str], stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.responses.append((tuple(prefix), Completed(tuple(prefix), returncode, stdout, stderr)))

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
        for prefix, response in self.responses:
            if args[: len(prefix)] == prefix:
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
