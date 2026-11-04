"""Agent-in-the-loop evals: a fresh Claude Code session with only the qfield-testbed plugin, on a real checkout.

Each case (``evals/cases/*.yaml``) gives a prompt that never names qft, the checkout it runs in, and what must hold
afterwards: the skill was used, commands came in order, forbidden ones never ran, the final report says what it
should, the deployment had the right shape, and nothing was left behind.

The runs are **local and on the Claude subscription only**: the runner refuses to start while `ANTHROPIC_API_KEY`
is set (it would be billed) and authenticates the child sessions with a subscription token (`claude setup-token`).
Each child gets an empty Claude configuration directory — no other plugin, no user CLAUDE.md, no memory — so it sees
what a newcomer who just installed the plugin sees; and its own `QFT_HOME` and port range, so it never touches your
deployments. The shared cache is reused, so images are not pulled again.

    uv run python -m evals.runner [--kind behaviour|scenario|all] [--only NAME,…] [--keep] [--model M]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO = Path(__file__).resolve().parents[1]
CASES = REPO / "evals" / "cases"
RESULTS = REPO / "evals" / "results"
PLUGIN = REPO / "plugin"
FIXTURES = REPO / "tests" / "fixtures" / "repos"
TOKEN_FILE = Path(os.environ.get("QFT_EVAL_TOKEN_FILE", "~/.config/qfield-testbed/claude-token")).expanduser()
PORT_BASE = 40000  # far from the default 20000 of your own deployments
DEFAULT_TOOLS = [
    *("Skill", "Read", "Write", "Edit", "Glob", "Grep"),
    *("Bash(qft *)", "Bash(git *)", "Bash(ls *)", "Bash(chmod +x *)"),
]
# what the child must not inherit: a paid key, this session's identity, and the agent session of whoever runs us
STRIPPED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "QFT_AGENT_SESSION",
)


class EvalSetupError(Exception):
    """The evals cannot run as asked: the message says why and what to do."""


# --- cases -----------------------------------------------------------------------------------------------------


class Checkout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture: str | None = None  # a folder of tests/fixtures/repos, made into a git repository
    remote: str | None = None  # its `origin`
    repo: str | None = None  # or a repository to clone
    ref: str | None = None


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: bool | None = None  # the qfield-testbed skill was (or was not) loaded
    commands_in_order: list[str] = Field(default_factory=list)  # regexes, matched in order over the Bash commands
    never: list[str] = Field(default_factory=list)  # regexes no Bash command may match
    final_contains: list[str] = Field(default_factory=list)  # regexes the final message must match (ignoring case)
    final_not_contains: list[str] = Field(default_factory=list)
    deployment: dict[str, str] | None = None  # component → regex on its version in the last successful ensure
    files: list[str] = Field(default_factory=list)  # globs that must exist in the checkout afterwards
    scenario: str | None = None  # glob of a bisect scenario the agent wrote, which must be valid
    keep: bool = False  # the deployment may stay (otherwise anything left is a leak)


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["behaviour", "scenario"]
    description: str = ""
    checkout: Checkout = Checkout()
    setup: list[str] = Field(default_factory=list)  # shell commands, in the checkout, before the agent runs
    prompt: str
    requires: list[str] = Field(default_factory=list)  # `qft doctor` capabilities; skipped when one is missing
    env: dict[str, str] = Field(default_factory=dict)  # extra environment of the agent and of `setup`; ${VAR} expands
    copies: int = Field(default=1, ge=1, le=4)  # agents run at once on the same host, each in its own checkout
    allowed_tools: list[str] = Field(default_factory=lambda: list(DEFAULT_TOOLS))
    max_turns: int = 40
    max_minutes: float = 20
    expect: Expect = Expect()

    @classmethod
    def load(cls, path: Path) -> Case:
        return cls.model_validate({"name": path.stem, **yaml.safe_load(path.read_text())})


def load_cases(kind: str = "all", only: list[str] | None = None, directory: Path = CASES) -> list[Case]:
    cases = [Case.load(path) for path in sorted(directory.glob("*.yaml"))]
    return [c for c in cases if kind in {"all", c.kind} and (not only or c.name in only)]


# --- the run ---------------------------------------------------------------------------------------------------


@dataclass
class Trace:
    """What the stream of `claude -p --output-format stream-json` says happened."""

    tool_uses: list[dict[str, Any]] = field(default_factory=list)  # {id, name, input}
    tool_results: dict[str, str] = field(default_factory=dict)  # tool_use id → its text
    final: str = ""
    cost_usd: float | None = None
    turns: int | None = None
    is_error: bool = False
    permission_denials: list[Any] = field(default_factory=list)

    @property
    def commands(self) -> list[str]:
        return [str(use["input"].get("command", "")) for use in self.tool_uses if use["name"] == "Bash"]

    @property
    def qft_commands(self) -> list[str]:
        return [command for command in self.commands if re.search(r"(^|[\s;&|(])qft\s", command)]

    @property
    def skill_used(self) -> bool:
        return any(
            use["name"] == "Skill" and "qfield-testbed" in json.dumps(use["input"]) for use in self.tool_uses
        ) or any("skills/qfield-testbed" in json.dumps(use["input"]) for use in self.tool_uses if use["name"] == "Read")

    def envelopes(self) -> list[dict[str, Any]]:
        """The JSON envelopes qft printed, in order."""
        found = []
        for use in self.tool_uses:
            text = self.tool_results.get(use["id"], "")
            start = text.find('{\n  "schema_version"')
            if use["name"] == "Bash" and start >= 0:
                try:
                    found.append(json.JSONDecoder().raw_decode(text[start:])[0])
                except ValueError:
                    continue
        return found


def parse_stream(lines: list[str]) -> Trace:
    trace = Trace()
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_use":
                trace.tool_uses.append(
                    {"id": block.get("id"), "name": block.get("name"), "input": block.get("input") or {}}
                )
            elif block.get("type") == "tool_result":
                value = block.get("content")
                if isinstance(value, list):
                    value = "\n".join(str(part.get("text", "")) for part in value if isinstance(part, dict))
                trace.tool_results[str(block.get("tool_use_id"))] = str(value)
        if event.get("type") == "result":
            trace.final = str(event.get("result") or "")
            trace.cost_usd = event.get("total_cost_usd")
            trace.turns = event.get("num_turns")
            trace.is_error = bool(event.get("is_error"))
            trace.permission_denials = list(event.get("permission_denials") or [])
    return trace


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def check(case: Case, trace: Trace, checkout: Path) -> list[Check]:
    expect = case.expect
    checks: list[Check] = []
    if expect.skill is not None:
        checks.append(Check("skill", trace.skill_used == expect.skill, f"skill used: {trace.skill_used}"))
    position = 0
    commands = trace.commands
    for pattern in expect.commands_in_order:
        index = next((i for i in range(position, len(commands)) if re.search(pattern, commands[i])), None)
        checks.append(Check(f"command /{pattern}/", index is not None, "" if index is not None else "not run in order"))
        position = index + 1 if index is not None else position
    for pattern in expect.never:
        hits = [command for command in commands if re.search(pattern, command)]
        checks.append(Check(f"never /{pattern}/", not hits, "; ".join(hits)[:300]))
    checks.extend(
        Check(f"says /{pattern}/", bool(re.search(pattern, trace.final, re.IGNORECASE)))
        for pattern in expect.final_contains
    )
    checks.extend(
        Check(f"does not say /{pattern}/", not re.search(pattern, trace.final, re.IGNORECASE))
        for pattern in expect.final_not_contains
    )
    if expect.deployment is not None:
        checks.append(_deployment_shape(expect.deployment, trace))
    checks.extend(Check(f"file {pattern}", bool(_matches(checkout, pattern))) for pattern in expect.files)
    if expect.scenario is not None:
        checks.append(_scenario_valid(checkout, expect.scenario))
    checks.append(Check("no denied tool", not trace.permission_denials, json.dumps(trace.permission_denials)[:300]))
    checks.append(Check("finished", not trace.is_error, trace.final[:200] if trace.is_error else ""))
    return checks


def _matches(checkout: Path, pattern: str) -> list[Path]:
    """The files matching ``pattern`` anywhere in the checkout, outside `.git`."""
    return sorted(path for path in checkout.rglob(pattern) if ".git" not in path.relative_to(checkout).parts)


def _scenario_valid(checkout: Path, pattern: str) -> Check:
    from qfield_testbed.bisect.scenario import BisectScenario  # noqa: PLC0415 - only for this check
    from qfield_testbed.errors import TestbedError  # noqa: PLC0415

    problems = []
    for path in _matches(checkout, pattern):
        name = str(path.relative_to(checkout))
        try:
            scenario = BisectScenario.from_file(path)
        except TestbedError as exc:
            problems.append(f"{name}: {exc.message[:200]}")
            continue
        script = scenario.test.script
        if script is not None and not script.is_file():
            problems.append(f"{name}: test.script {script} does not exist")
            continue
        return Check("valid scenario", ok=True, detail=name)
    return Check("valid scenario", ok=False, detail="; ".join(problems) or f"no file matches {pattern}")


def _deployment_shape(expected: dict[str, str], trace: Trace) -> Check:
    ready = [
        envelope["data"]
        for envelope in trace.envelopes()
        if envelope.get("ok") and isinstance(envelope.get("data"), dict) and "components" in envelope["data"]
    ]
    if not ready:
        return Check("deployment", ok=False, detail="no successful deployment in the trace")
    components = {name: str(info.get("version", "")) for name, info in ready[-1]["components"].items()}
    wrong = {
        name: components.get(name)
        for name, pattern in expected.items()
        if not re.search(pattern, components.get(name) or "")
    }
    return Check("deployment", not wrong, f"got {components}" if wrong else json.dumps(components))


# --- environment -----------------------------------------------------------------------------------------------


def subscription_token() -> str:
    """The subscription token of the child sessions; never an API key."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        raise EvalSetupError(
            "ANTHROPIC_API_KEY is set: the evals would be billed to it. Unset it; they run on the subscription only."
        )
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or (
        TOKEN_FILE.read_text().strip() if TOKEN_FILE.is_file() else ""
    )
    if not token:
        raise EvalSetupError(
            f"no subscription token: run `claude setup-token`, then store it in {TOKEN_FILE} (mode 600) "
            "or export CLAUDE_CODE_OAUTH_TOKEN"
        )
    return token


@dataclass
class Sandbox:
    """Where one case runs: its checkout, its qft home, an empty Claude configuration."""

    root: Path
    token: str
    qft: str

    @property
    def checkout(self) -> Path:
        return self.root / "work"

    def env(self, extra: dict[str, str] | None = None, copy: int = 0) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in STRIPPED_ENV}
        bin_dir = self.root / "bin"
        env.update(
            {
                "CLAUDE_CONFIG_DIR": str(self.root / f"claude-{copy}"),
                "CLAUDE_CODE_OAUTH_TOKEN": self.token,
                "QFT_HOME": str(self.root / "qft-home"),
                "QFT_CONFIG": str(self.root / "qft-config.yaml"),
                "QFT_EVAL_ROOT": str(self.root),
                "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            }
        )
        for key, value in (extra or {}).items():
            env[key] = re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), ""), value)
        return env

    def checkout_of(self, copy: int) -> Path:
        return self.checkout if copy == 0 else self.root / f"work-{copy}"

    def prepare(self, case: Case) -> None:
        for copy in range(case.copies):
            (self.root / f"claude-{copy}").mkdir(parents=True)
        (self.root / "qft-config.yaml").write_text(f"port_base: {PORT_BASE}\nmax_slots: 10\n")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        qft = bin_dir / "qft"  # the qft of this checkout, not a release: the evals test the working tree
        qft.write_text(f'#!/bin/sh\nexec {self.qft} "$@"\n')
        qft.chmod(0o755)
        _make_checkout(case.checkout, self.checkout)
        for copy in range(1, case.copies):
            shutil.copytree(self.checkout, self.checkout_of(copy), symlinks=True)
        for command in case.setup:
            subprocess.run(command, shell=True, cwd=self.checkout, env=self.env(case.env), check=True, timeout=600)  # noqa: S602 - commands of our own case files

    def qft_json(self, *args: str) -> dict[str, Any]:
        result = subprocess.run(
            [self.qft, "--json", *args], capture_output=True, text=True, env=self.env(), timeout=600, check=False
        )
        try:
            envelope: dict[str, Any] = json.loads(result.stdout)
        except ValueError:
            return {"ok": False, "error": {"code": "ERROR", "message": result.stderr[-500:]}}
        return envelope


def _make_checkout(spec: Checkout, target: Path) -> None:
    if spec.repo:
        clone = ["git", "clone", "--quiet", "--depth", "50"]
        clone += ["--branch", spec.ref] if spec.ref else []
        subprocess.run([*clone, spec.repo, str(target)], check=True, timeout=600)
        return
    if spec.fixture:
        shutil.copytree(FIXTURES / spec.fixture, target)
    else:
        target.mkdir(parents=True)
    git = ["git", "-C", str(target)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    commit = [*git, "-c", "user.name=eval", "-c", "user.email=eval@example.org", "commit", "-q", "--allow-empty"]
    subprocess.run([*commit, "-m", "checkout"], check=True)
    if spec.remote:
        subprocess.run([*git, "remote", "add", "origin", spec.remote], check=True)


def leaks(sandbox: Sandbox, *, keep: bool) -> tuple[list[str], list[str]]:
    """What the run left behind, then cleaned: deployments (unless the case keeps them) and orphaned resources."""
    listed = sandbox.qft_json("deployment", "list").get("data") or {}
    left = [str(d["name"]) for d in listed.get("deployments", [])]
    orphans_plan = (sandbox.qft_json("gc", "--orphaned", "--dry-run").get("data") or {}).get("plan") or {}
    orphans = [str(item) for values in orphans_plan.values() if isinstance(values, list) for item in values]
    for name in left:
        sandbox.qft_json("deployment", "remove", name, "--yes")
    if orphans:
        sandbox.qft_json("gc", "--orphaned", "--yes")
    return ([] if keep else left), orphans


# --- one case --------------------------------------------------------------------------------------------------


@dataclass
class Result:
    case: str
    kind: str
    status: Literal["passed", "failed", "skipped", "error"]
    checks: list[Check] = field(default_factory=list)
    reason: str = ""
    tool_calls: int = 0
    qft_calls: int = 0
    turns: int | None = None
    minutes: float = 0.0
    cost_equivalent_usd: float | None = None
    interventions: int = 0
    leaked: list[str] = field(default_factory=list)
    qft_commands: list[str] = field(default_factory=list)
    final: str = ""


def missing_capabilities(sandbox: Sandbox, requires: list[str]) -> list[str]:
    if not requires:
        return []
    capabilities = (sandbox.qft_json("doctor").get("data") or {}).get("capabilities") or {}
    return [key for key in requires if not (capabilities.get(key) or {}).get("can_run")]


def _agent(
    case: Case, sandbox: Sandbox, copy: int, *, claude: str, model: str | None, out: Path | None
) -> tuple[Trace, bool]:
    """One headless Claude Code session on the case; its trace, and whether it was killed for taking too long."""
    command = [
        claude,
        "-p",
        case.prompt,
        "--plugin-dir",
        str(PLUGIN),
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(case.max_turns),
        "--strict-mcp-config",  # the skill and the CLI are under test here, not the MCP server
        "--no-session-persistence",
        "--allowedTools",
        *case.allowed_tools,
    ]
    command += ["--model", model] if model else []
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=sandbox.checkout_of(copy),
            env=sandbox.env(case.env, copy),
            capture_output=True,
            text=True,
            timeout=case.max_minutes * 60,
            check=False,
        )
        stdout = completed.stdout
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout or ""
    if out is not None:
        suffix = f"-{copy + 1}" if case.copies > 1 else ""
        (out / f"{case.name}{suffix}.stream.jsonl").write_text(stdout)
    return parse_stream(stdout.splitlines()), timed_out


def run_case(
    case: Case,
    *,
    token: str,
    qft: str,
    claude: str = "claude",
    model: str | None = None,
    keep: bool = False,
    out: Path | None = None,
) -> Result:
    root = Path(tempfile.mkdtemp(prefix=f"qft-eval-{case.name}-"))
    sandbox = Sandbox(root, token, qft)
    try:
        sandbox.prepare(case)
        missing = missing_capabilities(sandbox, case.requires)
        if missing:
            return Result(case.name, case.kind, "skipped", reason=f"host cannot run: {', '.join(missing)}")
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=case.copies) as pool:
            runs = list(
                pool.map(
                    lambda copy: _agent(case, sandbox, copy, claude=claude, model=model, out=out), range(case.copies)
                )
            )
        minutes = (time.monotonic() - started) / 60
        checks: list[Check] = []
        for copy, (trace, timed_out) in enumerate(runs):
            prefix = f"[{copy + 1}] " if case.copies > 1 else ""
            checks += [Check(prefix + c.name, c.ok, c.detail) for c in check(case, trace, sandbox.checkout_of(copy))]
            if timed_out:
                checks.append(Check(f"{prefix}within time", ok=False, detail=f"killed after {case.max_minutes} min"))
        left, orphans = leaks(sandbox, keep=case.expect.keep)
        checks.append(Check("no leak", not (left or orphans), ", ".join(left + orphans)))
        traces = [trace for trace, _ in runs]
        return Result(
            case.name,
            case.kind,
            "passed" if all(c.ok for c in checks) else "failed",
            checks=checks,
            tool_calls=sum(len(trace.tool_uses) for trace in traces),
            qft_calls=sum(len(trace.qft_commands) for trace in traces),
            turns=sum(trace.turns or 0 for trace in traces) or None,
            minutes=round(minutes, 1),
            cost_equivalent_usd=sum(trace.cost_usd or 0 for trace in traces) or None,
            interventions=sum(len(trace.permission_denials) for trace in traces),
            leaked=left + orphans,
            qft_commands=[command for trace in traces for command in trace.qft_commands],
            final="\n\n---\n\n".join(trace.final for trace in traces),
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return Result(case.name, case.kind, "error", reason=f"{type(exc).__name__}: {exc}")
    finally:
        if not keep:
            shutil.rmtree(root, ignore_errors=True)


# --- report ----------------------------------------------------------------------------------------------------


def summary(results: list[Result]) -> str:
    lines = [
        "| Case | Kind | Status | Failed checks | Tool calls | qft calls | Turns | Minutes | Cost eq. (USD) | Denials | Leaks |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        failed = "; ".join(f"{c.name}: {c.detail}".strip(": ") for c in r.checks if not c.ok) or r.reason
        cost = f"{r.cost_equivalent_usd:.2f}" if r.cost_equivalent_usd is not None else ""
        lines.append(
            f"| {r.case} | {r.kind} | {r.status} | {failed.replace('|', '/')} | {r.tool_calls} | {r.qft_calls} | "
            f"{r.turns or ''} | {r.minutes} | {cost} | {r.interventions} | {len(r.leaked)} |"
        )
    passed = sum(r.status == "passed" for r in results)
    ran = sum(r.status in {"passed", "failed"} for r in results)
    lines += ["", f"{passed}/{ran} passed, {sum(r.status == 'skipped' for r in results)} skipped."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.runner", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--kind", choices=["behaviour", "scenario", "all"], default="all")
    parser.add_argument("--only", default="", help="comma-separated case names")
    parser.add_argument("--keep", action="store_true", help="keep each case's directory (for debugging)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--claude", default=os.environ.get("QFT_EVAL_CLAUDE", "claude"))
    parser.add_argument("--qft", default=os.environ.get("QFT_EVAL_QFT", str(REPO / ".venv" / "bin" / "qft")))
    args = parser.parse_args(argv)
    try:
        token = subscription_token()
    except EvalSetupError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    cases = load_cases(args.kind, [name for name in args.only.split(",") if name])
    if not cases:
        sys.stderr.write("error: no case matches\n")
        return 2
    out = RESULTS / datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    results: list[Result] = []
    for case in cases:
        sys.stderr.write(f"→ {case.name} ({case.kind})\n")
        result = run_case(
            case, token=token, qft=args.qft, claude=args.claude, model=args.model, keep=args.keep, out=out
        )
        sys.stderr.write(f"  {result.status} {result.reason}\n")
        results.append(result)
        (out / f"{case.name}.json").write_text(json.dumps(asdict(result), indent=2) + "\n")
    report = summary(results)
    (out / "summary.md").write_text(report)
    sys.stdout.write(report + f"\nResults: {out}\n")
    return 0 if all(r.status in {"passed", "skipped"} for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
