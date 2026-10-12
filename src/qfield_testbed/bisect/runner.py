"""Tests that produce verdicts: a script with `git bisect run` exit codes, or a prompt to the user."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from qfield_testbed.bisect.algorithm import ABORT_EXIT_CODE, Verdict
from qfield_testbed.errors import TestbedError


class BisectAbortError(TestbedError):
    """The test asked to stop the whole session (exit code >= 128)."""


@dataclass
class Outcome:
    verdict: Verdict
    exit_code: int | None
    duration_s: float
    log: str


class ScriptTest:
    def __init__(self, script: Path, args: list[str], timeout: int) -> None:
        self.script = script
        self.args = args
        self.timeout = timeout

    def run(self, env: Mapping[str, str], log_file: Path) -> Outcome:
        if not self.script.exists():
            raise TestbedError(f"test script not found: {self.script}")
        if not os.access(self.script, os.X_OK):
            raise TestbedError(f"test script is not executable: {self.script} (chmod +x)")
        started = time.monotonic()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("w") as log:
            log.write(f"$ {self.script} {' '.join(self.args)}\n".replace("  ", " "))
            log.flush()
            try:
                proc = subprocess.run(
                    [str(self.script), *self.args],
                    env={**os.environ, **env},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout,
                    check=False,
                )
                code = proc.returncode
            except subprocess.TimeoutExpired:
                log.write(f"\n[qft] timed out after {self.timeout}s → bad\n")
                code = 124
        duration = time.monotonic() - started
        if code >= ABORT_EXIT_CODE:
            raise BisectAbortError(f"test exited with {code}; aborting the session (see {log_file})")
        return Outcome(Verdict.from_exit_code(code), code, duration, log_file.read_text())


Prompt = Callable[[str], str]


class ManualTest:
    """Asks the user; ``prompt`` is injected so the CLI (or a test) decides how to ask."""

    def __init__(self, prompt: Prompt) -> None:
        self.prompt = prompt

    def run(self, env: Mapping[str, str], log_file: Path) -> Outcome:
        del env
        started = time.monotonic()
        while True:
            answer = self.prompt("Verdict for this candidate [good/bad/skip/quit]: ").strip().lower()
            if answer in ("good", "g"):
                verdict = Verdict.GOOD
            elif answer in ("bad", "b"):
                verdict = Verdict.BAD
            elif answer in ("skip", "s"):
                verdict = Verdict.SKIP
            elif answer in ("quit", "q"):
                raise BisectAbortError("session paused by the user; resume with `qft bisect run`")
            else:
                continue
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(f"manual verdict: {verdict}\n")
            return Outcome(verdict, None, time.monotonic() - started, log_file.read_text())
