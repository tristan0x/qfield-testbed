from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
import yaml

from qfield_testbed.bisect.session import BisectSession, SessionStatus
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import TestbedError
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401

CANDIDATES = ["c1", "c2", "c3"]


def _scenario(name: str, test: str, candidates: list[str] = CANDIDATES) -> str:
    return (
        f"name: {name}\ndeployment:\n  qgis: {{version: host}}\nbisect:\n  component: qfieldcloud\n"
        f"  good: v1\n  bad: v2\n  candidates: [{', '.join(candidates)}]\ntest:\n{test}"
    )


MANUAL = "  manual: true\n"
SCRIPT = "  script: ./repro.sh\n"


@pytest.fixture
def testbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> Testbed:
    testbed = isolate(tmp_path, monkeypatch, runner)
    # every candidate ref resolves to a sha derived from its name, with a subject and a date
    for i, ref in enumerate([*CANDIDATES, "broken"]):
        sha = "dead" * 10 if ref == "broken" else f"{i + 1:040x}"
        runner.reply_when(lambda args, ref=ref: f"{ref}^{{commit}}" in args, stdout=sha + "\n")
        runner.reply_when(lambda args, sha=sha: "--format=%s" in args and sha in args, stdout=f"subject {ref}\n")
    runner.reply_when(lambda args: "--format=%cs" in args, stdout="2026-01-01\n")
    return testbed


def _write(tmp_path: Path, text: str, name: str = "s.yaml") -> Path:
    file = tmp_path / name
    file.write_text(text)
    return file


def _script(tmp_path: Path, codes: dict[int, int]) -> None:
    cases = " ".join(f"{index}) exit {code};;" for index, code in codes.items())
    script = tmp_path / "repro.sh"
    script.write_text(f'#!/bin/sh\necho "testing $QFT_BISECT_CANDIDATE"\ncase "$QFT_BISECT_INDEX" in {cases} esac\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def manual(testbed: Testbed, tmp_path: Path) -> BisectSession:
    result = invoke(["bisect", "start", str(_write(tmp_path, _scenario("m", MANUAL)))])
    assert result.exit_code == 0, result.output
    return BisectSession(testbed, "m")


def test_example_is_a_valid_scenario() -> None:
    result = invoke(["bisect", "example"])
    assert result.exit_code == 0
    assert "bisect" in yaml.safe_load(result.output)


@pytest.mark.usefixtures("fake_components", "testbed")
def test_no_session_yet() -> None:
    listed = invoke(["bisect", "list"])
    assert listed.exit_code == 0
    assert "No bisect session yet" in listed.output
    status = invoke(["bisect", "status"])
    assert status.exit_code == 1
    assert "no bisect session yet" in str(status.exception)


@pytest.mark.usefixtures("fake_components", "testbed")
def test_start_announces_candidates_and_next_step(tmp_path: Path) -> None:
    result = invoke(["bisect", "start", str(_write(tmp_path, _scenario("m", MANUAL))), "--session", "hunt"])
    assert result.exit_code == 0, result.output
    assert "session hunt: 3 candidates between v1 and v2" in result.output
    assert "Next: qft bisect run hunt" in result.output
    listed = invoke(["bisect", "list"])
    row = next(line for line in listed.output.splitlines() if line.startswith("hunt"))
    assert row.split() == ["hunt", "created", "qfieldcloud", "v1..v2", "0/3", "bs-m"]


@pytest.mark.usefixtures("fake_components")
def test_manual_next_and_verdicts(manual: BisectSession, testbed: Testbed) -> None:
    prepared = invoke(["bisect", "next"])
    assert prepared.exit_code == 0, prepared.output
    assert "candidate 1 ready: c2 — subject c2" in prepared.output
    assert "deployment: bs-m" in prepared.output
    assert testbed.deployment("bs-m").summary().session == "m"

    again = invoke(["bisect", "next", "m"])
    assert "candidate 1 (c2) is already prepared; give a verdict first" in again.output

    status = invoke(["bisect", "status"])
    assert "prepared" in next(line for line in status.output.splitlines() if " c2 " in line)

    bad = invoke(["bisect", "bad"])
    assert bad.exit_code == 0
    assert "candidate 1 (c2) → bad" in bad.output
    assert "steps left — qft bisect next" in bad.output

    status = invoke(["bisect", "status"])
    assert "next" in next(line for line in status.output.splitlines() if " c1 " in line)

    assert invoke(["bisect", "next"]).exit_code == 0
    good = invoke(["bisect", "good"])
    assert "candidate 0 (c1) → good" in good.output
    assert "First bad qfieldcloud revision: c2 — subject c2 (2026-01-01)" in good.output
    assert f"report: {manual.directory / 'report.md'}" in good.output
    assert BisectSession(testbed, "m").state.status is SessionStatus.DONE

    finished = invoke(["bisect", "next"])
    assert "First bad qfieldcloud revision: c2" in finished.output

    status = invoke(["bisect", "status", "m"])
    assert "m  done  qfieldcloud v1..v2  deployment bs-m  2 tested / 0 skipped / 3 candidates" in status.output
    assert "First bad" in status.output
    info = json.loads(invoke(["bisect", "status", "--json"]).output)
    assert info["first_bad"]["label"] == "c2"
    assert [row["verdict"] for row in info["verdicts"]] == ["good", "bad", None]


@pytest.mark.usefixtures("fake_components")
def test_verdict_needs_a_prepared_candidate(manual: BisectSession) -> None:
    result = invoke(["bisect", "skip", manual.name])
    assert result.exit_code == 1
    assert "no candidate is prepared" in str(result.exception)


@pytest.mark.usefixtures("fake_components", "manual")
def test_skip_and_logs() -> None:
    invoke(["bisect", "next"])
    skipped = invoke(["bisect", "skip"])
    assert "candidate 1 (c2) → skip" in skipped.output

    summary = invoke(["bisect", "log"])
    assert summary.exit_code == 0
    assert "1 c2 → skip (0.0s)" in summary.output
    one = invoke(["bisect", "log", "--index", "1"])
    assert "manual verdict: skip" in one.output
    missing = invoke(["bisect", "log", "-i", "2"])
    assert missing.exit_code == 1
    assert "no log for candidate 2" in str(missing.exception)


@pytest.mark.usefixtures("fake_components", "testbed")
def test_several_sessions_need_a_name(tmp_path: Path) -> None:
    invoke(["bisect", "start", str(_write(tmp_path, _scenario("a", MANUAL), "a.yaml"))])
    invoke(["bisect", "start", str(_write(tmp_path, _scenario("b", MANUAL), "b.yaml"))])
    result = invoke(["bisect", "status"])
    assert result.exit_code == 1
    assert isinstance(result.exception, TestbedError)
    assert "several sessions exist; name one: a, b" in str(result.exception)
    assert invoke(["bisect", "status", "b"]).exit_code == 0


@pytest.mark.usefixtures("fake_components", "manual")
def test_run_prompts_for_manual_verdicts() -> None:
    result = invoke(["bisect", "run"], stdin="what\nbad\ngood\n")
    assert result.exit_code == 0, result.output
    assert "step 1 · candidate 1 · c2" in result.output
    assert "Verdict for this candidate [good/bad/skip/quit]" in result.output
    assert "→ bad" in result.output
    assert "First bad qfieldcloud revision: c2" in result.output


@pytest.mark.usefixtures("fake_components")
def test_run_can_be_paused(manual: BisectSession, testbed: Testbed) -> None:
    result = invoke(["bisect", "run"], stdin="q\n")
    assert result.exit_code == 130
    assert "session paused by the user" in result.stderr
    assert BisectSession(testbed, manual.name).state.outcomes == {}


@pytest.mark.usefixtures("fake_components", "testbed")
@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ({0: 0, 1: 1, 2: 1}, ("First bad qfieldcloud revision: c2", "→ bad (exit 1)", "2 tested, 0 skipped")),
        ({0: 125, 1: 1}, ("First bad qfieldcloud revision: c2", "may also be the culprit: c1", "1 tested, 1 skipped")),
        ({0: 0, 1: 0, 2: 0}, ("No bad candidate isolated: every tested candidate was good.", "→ good (exit 0)", "")),
        ({0: 0, 1: 0, 2: 125}, ("No bad candidate isolated", "Skipped: c3.", "→ skip (exit 125)")),
    ],
    ids=["first-bad", "ambiguous", "all-good", "all-good-with-skip"],
)
def test_start_and_run_a_script(tmp_path: Path, codes: dict[int, int], expected: tuple[str, str, str]) -> None:
    _script(tmp_path, codes)
    result = invoke(["bisect", "start", str(_write(tmp_path, _scenario("s", SCRIPT))), "--run"])
    assert result.exit_code == 0, result.output
    for text in expected:
        assert text in result.output


@pytest.mark.usefixtures("fake_components", "testbed")
def test_script_abort_stops_the_session(tmp_path: Path) -> None:
    _script(tmp_path, {1: 130})
    invoke(["bisect", "start", str(_write(tmp_path, _scenario("s", SCRIPT)))])
    result = invoke(["bisect", "run", "s"])
    assert result.exit_code == 130
    assert "test exited with 130; aborting the session" in result.stderr


@pytest.mark.usefixtures("fake_components", "testbed")
def test_next_skips_a_candidate_that_cannot_be_built(tmp_path: Path) -> None:
    started = invoke(["bisect", "start", str(_write(tmp_path, _scenario("x", MANUAL, ["c1", "broken", "c3"])))])
    assert started.exit_code == 0, started.output
    result = invoke(["bisect", "next"])
    assert result.exit_code == 0, result.output
    assert "candidate skipped (not materializable); run `qft bisect next` again" in result.output
    assert "1 broken → skip" in invoke(["bisect", "log"]).output


@pytest.mark.usefixtures("fake_components")
def test_reset_forgets_verdicts_and_deployment(manual: BisectSession, testbed: Testbed) -> None:
    invoke(["bisect", "next"])
    invoke(["bisect", "bad"])
    result = invoke(["bisect", "reset", "--remove-deployment"])
    assert result.exit_code == 0
    assert "session m reset" in result.output
    assert BisectSession(testbed, manual.name).state.outcomes == {}
    assert testbed.deployments() == []


@pytest.mark.usefixtures("terminal")
@pytest.mark.usefixtures("fake_components")
def test_remove_session(manual: BisectSession, testbed: Testbed) -> None:
    invoke(["bisect", "next"])
    declined = invoke(["bisect", "remove", "m"], stdin="n\n")
    assert declined.exit_code == 1
    assert "Remove session 'm' and deployment 'bs-m'?" in declined.output
    assert manual.directory.is_dir()

    removed = invoke(["bisect", "remove", "m", "-f"])
    assert removed.exit_code == 0
    assert "session m removed" in removed.output
    assert not manual.directory.exists()
    assert testbed.deployments() == []


@pytest.mark.usefixtures("fake_components")
def test_remove_session_without_a_terminal(manual: BisectSession) -> None:
    refused = invoke(["bisect", "remove", "m"])
    assert refused.exit_code == 1
    assert "refusing to remove session 'm' without confirmation" in str(refused.exception)
    assert manual.directory.is_dir()
