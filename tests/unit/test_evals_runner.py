"""The eval runner (evals/runner.py): parsing a session's stream, the checks, the leak check and the paid-key guard.

No real Claude session runs here: fake `claude` and `qft` executables stand in for them.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from evals import runner
from evals.runner import Case, Check, EvalSetupError, Expect, Trace, check, load_cases, parse_stream, summary


def _use(use_id: str, name: str, **tool_input: Any) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": use_id, "name": name, "input": tool_input}]},
        }
    )


def _result_of(use_id: str, text: str) -> str:
    return json.dumps(
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}}
    )


ENSURED = json.dumps(
    {
        "schema_version": 1,
        "ok": True,
        "data": {
            "name": "qfieldsync",
            "slot": 0,
            "components": {
                "qfieldsync": {"version": "worktree:/w", "status": "running"},
                "qfieldcloud": {"version": "v26.29", "source": "release:v26.29", "status": "running"},
            },
        },
        "warnings": [],
    },
    indent=2,
)

STREAM = [
    json.dumps({"type": "system", "subtype": "init"}),
    _use("1", "Skill", skill="qfield-testbed:qfield-testbed"),
    _use("2", "Bash", command="qft --json doctor"),
    _result_of("2", '{\n  "schema_version": 1, "ok": true, "data": {}}'),
    _use("3", "Bash", command="qft --json spec suggest --path . --output spec.yaml"),
    _use("4", "Bash", command="qft --json deployment ensure spec.yaml --timeout 20m"),
    _result_of("4", "starting…\n" + ENSURED),
    # `deployment logs` also answers `components`, which must not count as the deployment
    _use("4b", "Bash", command="qft --json deployment logs qfieldsync --tail 10"),
    _result_of("4b", '{"schema_version": 1, "ok": true, "data": {"components": {}}, "warnings": []}'),
    _use("5", "Bash", command="git status"),
    "not json",
    json.dumps(
        {
            "type": "result",
            "result": "QFieldSync worktree against QFieldCloud v26.29: packaging works.",
            "total_cost_usd": 0.42,
            "num_turns": 7,
            "is_error": False,
            "permission_denials": [],
        }
    ),
]


def test_parse_stream() -> None:
    trace = parse_stream(STREAM)
    assert trace.skill_used
    assert trace.qft_commands == [
        "qft --json doctor",
        "qft --json spec suggest --path . --output spec.yaml",
        "qft --json deployment ensure spec.yaml --timeout 20m",
        "qft --json deployment logs qfieldsync --tail 10",
    ]
    assert len(trace.tool_uses) == 6
    assert (trace.cost_usd, trace.turns, trace.is_error) == (0.42, 7, False)
    assert [envelope["data"].get("name") for envelope in trace.envelopes()] == [None, "qfieldsync"]


def _case(**expect: Any) -> Case:
    return Case(name="c", kind="behaviour", prompt="p", expect=Expect(**expect))


def _failed(checks: list[Check]) -> list[str]:
    return [c.name for c in checks if not c.ok]


def test_checks_pass(tmp_path: Path) -> None:
    (tmp_path / "spec.yaml").write_text("name: x\n")
    case = _case(
        skill=True,
        commands_in_order=["doctor", "spec suggest", "deployment ensure"],
        never=["--force"],
        final_contains=["packaging works"],
        final_not_contains=["error"],
        deployment={"qfieldsync": "^worktree:", "qfieldcloud": "^(release|git):"},
        files=["spec.yaml"],
    )
    assert _failed(check(case, parse_stream(STREAM), tmp_path)) == []


def test_checks_fail(tmp_path: Path) -> None:
    case = _case(
        skill=False,
        commands_in_order=["deployment ensure", "doctor"],  # wrong order
        never=["git status"],
        final_contains=["leak"],
        deployment={"qfieldcloud": "^git:"},
        files=["spec.yaml"],
        scenario="*.yaml",
    )
    failed = _failed(check(case, parse_stream(STREAM), tmp_path))
    assert failed == [
        "skill",
        "command /doctor/",
        "never /git status/",
        "says /leak/",
        "deployment",
        "file spec.yaml",
        "valid scenario",
    ]
    denied = Trace(permission_denials=[{"tool_name": "Bash"}], is_error=True)
    assert _failed(check(_case(), denied, tmp_path)) == ["no denied tool", "finished"]
    assert _failed(check(_case(deployment={"qgis": "."}), Trace(), tmp_path)) == ["deployment"]


def test_scenario_check(tmp_path: Path) -> None:
    from tests.unit.test_cli_bisect import SCRIPT, _scenario  # noqa: PLC0415

    (tmp_path / "bad.yaml").write_text("name: x\n")
    assert _failed(check(_case(scenario="*.yaml"), Trace(), tmp_path)) == ["valid scenario"]
    # in a subdirectory, with a script path that does not resolve from there
    (tmp_path / "bisect").mkdir()
    (tmp_path / "bisect" / "s.yaml").write_text(_scenario("s", "  script: ./bisect/repro.sh\n"))
    (tmp_path / "bisect" / "repro.sh").write_text("#!/bin/sh\nexit 0\n")
    failed = check(_case(scenario="*.yaml", files=["*.sh"]), Trace(), tmp_path)
    assert _failed(failed) == ["valid scenario"]
    assert "bisect/bisect/repro.sh does not exist" in next(c.detail for c in failed if not c.ok)

    (tmp_path / "bisect" / "s.yaml").write_text(_scenario("s", SCRIPT))
    assert _failed(check(_case(scenario="*.yaml", files=["*.sh"]), Trace(), tmp_path)) == []


def test_the_cases_load() -> None:
    cases = load_cases()
    assert {"triggers-in-qfieldsync", "uc8-host-cannot", "untrusted-issue-text"} <= {c.name for c in cases}
    assert all("qft" not in c.prompt for c in cases)  # the prompts never name the tool
    assert {c.kind for c in load_cases("behaviour")} == {"behaviour"}
    assert [c.name for c in load_cases(only=["uc8-host-cannot"])] == ["uc8-host-cannot"]


def test_no_paid_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "TOKEN_FILE", tmp_path / "token")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-paid")
    with pytest.raises(EvalSetupError, match="billed"):
        runner.subscription_token()
    assert runner.main(["--only", "uc8-host-cannot"]) == 2
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(EvalSetupError, match="setup-token"):
        runner.subscription_token()
    (tmp_path / "token").write_text("sk-ant-oat-x\n")
    assert runner.subscription_token() == "sk-ant-oat-x"


def _executable(path: Path, body: str) -> str:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


FAKE_QFT = """\\
import json, os, sys
from pathlib import Path
home = Path(os.environ["QFT_HOME"]); home.mkdir(parents=True, exist_ok=True)
names = sorted(p.stem for p in home.glob("*.deployment"))
args = [a for a in sys.argv[1:] if a != "--json"]
if args[:2] == ["deployment", "list"]:
    data = {"deployments": [{"name": n} for n in names]}
elif args[:2] == ["deployment", "remove"]:
    (home / f"{args[2]}.deployment").unlink(); data = {}
elif args[:1] == ["gc"]:
    data = {"plan": {"orphaned_networks": []}}
elif args[:1] == ["doctor"]:
    data = {"capabilities": {"qfieldcloud": {"can_run": True}, "qfield-android": {"can_run": False}}}
elif args[:2] == ["deployment", "add"]:
    (home / f"left-{os.getpid()}.deployment").touch(); data = {}
print(json.dumps({"schema_version": 1, "ok": True, "data": data}))
"""

FAKE_CLAUDE = """\
import json, os, sys, subprocess
assert "ANTHROPIC_API_KEY" not in os.environ and "QFT_AGENT_SESSION" not in os.environ
assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-x"
assert "--plugin-dir" in sys.argv and sys.argv[sys.argv.index("--output-format") + 1] == "stream-json"
if "leave" in sys.argv[2]:
    subprocess.run(["qft", "deployment", "add", "x.yaml"], check=True, capture_output=True)
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "1", "name": "Bash", "input": {"command": "qft --json doctor"}}]}}))
print(json.dumps({"type": "result", "result": "done: " + os.getcwd(), "num_turns": 2, "total_cost_usd": 0.01}))
"""


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("QFT_AGENT_SESSION", "outer-session")
    return {
        "qft": _executable(tmp_path / "qft", FAKE_QFT),
        "claude": _executable(tmp_path / "claude", FAKE_CLAUDE),
    }


def test_run_case_and_the_leak_check(fakes: dict[str, str], tmp_path: Path) -> None:
    clean = Case(
        name="clean", kind="behaviour", prompt="go", checkout=runner.Checkout(fixture="qfieldsync", remote="x")
    )
    result = runner.run_case(clean, token="sk-ant-oat-x", qft=fakes["qft"], claude=fakes["claude"], out=tmp_path)
    assert result.status == "passed", result.checks
    assert (result.tool_calls, result.qft_calls, result.turns, result.leaked) == (1, 1, 2, [])
    assert (tmp_path / "clean.stream.jsonl").is_file()

    leaky = Case(name="leaky", kind="behaviour", prompt="leave something", copies=2)
    result = runner.run_case(leaky, token="sk-ant-oat-x", qft=fakes["qft"], claude=fakes["claude"])
    assert result.status == "failed"
    assert len(result.leaked) == 2
    assert all(name.startswith("left-") for name in result.leaked)
    assert [c.name for c in result.checks if not c.ok] == ["no leak"]
    assert "work-1" in result.final  # each copy in its own checkout

    kept = Case(name="kept", kind="behaviour", prompt="leave something", expect=Expect(keep=True))
    assert runner.run_case(kept, token="sk-ant-oat-x", qft=fakes["qft"], claude=fakes["claude"]).status == "passed"


def test_skipped_when_the_host_cannot(fakes: dict[str, str]) -> None:
    case = Case(name="android", kind="scenario", prompt="p", requires=["qfield-android"])
    result = runner.run_case(case, token="t", qft=fakes["qft"], claude=fakes["claude"])
    assert (result.status, result.reason) == ("skipped", "host cannot run: qfield-android")


def test_setup_and_env_expansion(fakes: dict[str, str]) -> None:
    case = Case(
        name="env",
        kind="behaviour",
        prompt="p",
        setup=['echo "$MARK" > "$QFT_EVAL_ROOT/mark"'],
        env={"MARK": "root=${QFT_EVAL_ROOT}"},
    )
    result = runner.run_case(case, token="sk-ant-oat-x", qft=fakes["qft"], claude=fakes["claude"], keep=True)
    assert result.status == "passed"
    root = Path(result.final.removeprefix("done: ")).parent
    assert (root / "mark").read_text() == f"root={root}\n"


def test_main_writes_a_summary(fakes: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-x")
    monkeypatch.setattr(runner, "RESULTS", tmp_path / "results")
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases" / "one.yaml").write_text("kind: behaviour\nprompt: go\n")
    monkeypatch.setattr(runner, "CASES", tmp_path / "cases")
    monkeypatch.setattr(runner, "load_cases", lambda kind, only: load_cases(kind, only, tmp_path / "cases"))
    assert runner.main(["--claude", fakes["claude"], "--qft", fakes["qft"]]) == 0
    [out] = (tmp_path / "results").iterdir()
    assert "| one | behaviour | passed |" in (out / "summary.md").read_text()
    assert json.loads((out / "one.json").read_text())["status"] == "passed"
    assert runner.main(["--only", "nope"]) == 2
    assert os.environ["QFT_AGENT_SESSION"] == "outer-session"


def test_summary() -> None:
    results = [
        runner.Result("a", "behaviour", "passed", cost_equivalent_usd=0.5),
        runner.Result("b", "scenario", "failed", checks=[Check("no leak", ok=False, detail="x")]),
        runner.Result("c", "scenario", "skipped", reason="host cannot run: qfield-android"),
    ]
    text = summary(results)
    assert "| b | scenario | failed | no leak: x |" in text
    assert "1/2 passed, 1 skipped." in text
