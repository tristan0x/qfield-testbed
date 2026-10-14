from __future__ import annotations

import stat
from collections.abc import Set as AbstractSet
from pathlib import Path

import pytest

from qfield_testbed.bisect.algorithm import Search, Verdict
from qfield_testbed.bisect.scenario import BisectScenario
from qfield_testbed.bisect.session import BisectSession, SessionStatus, resolve_candidates
from qfield_testbed.components import REGISTRY, register
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import SpecError, TestbedError
from qfield_testbed.revisions.git import Mirror
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_deployment import FakeQgis

# --- algorithm -------------------------------------------------------------------------------------------


def oracle_search(size: int, first_bad: int, skips: AbstractSet[int] = frozenset()) -> Search:
    """Drive a Search with a perfect oracle; returns the finished search."""
    search = Search(size=size)
    steps = 0
    while (index := search.next_index()) is not None:
        steps += 1
        assert steps <= size + 1, "the search must terminate"
        if index in skips:
            search.record(index, Verdict.SKIP)
        else:
            search.record(index, Verdict.BAD if index >= first_bad else Verdict.GOOD)
    return search


@pytest.mark.parametrize("size", [1, 2, 3, 7, 8, 100])
@pytest.mark.parametrize("first_bad_ratio", [0.0, 0.3, 0.5, 0.99])
def test_finds_first_bad(size: int, first_bad_ratio: float) -> None:
    first_bad = min(size - 1, int(size * first_bad_ratio))
    search = oracle_search(size, first_bad)
    result = search.result()
    assert result.first_bad == first_bad
    assert result.ambiguous == []
    assert result.tested <= size.bit_length() + 1  # logarithmic


def test_all_good_means_bad_ref_itself() -> None:
    search = oracle_search(5, first_bad=5)
    assert search.result().first_bad is None
    assert search.result().ambiguous == []


def test_skips_make_the_answer_ambiguous() -> None:
    search = oracle_search(10, first_bad=6, skips={5, 4})
    result = search.result()
    assert result.first_bad == 6
    assert result.ambiguous == [4, 5]
    assert result.skipped == 2


def test_skipped_candidate_is_not_asked_again() -> None:
    search = Search(size=3)
    search.record(1, Verdict.SKIP)
    assert search.next_index() in (0, 2)
    search.record(0, Verdict.SKIP)
    search.record(2, Verdict.SKIP)
    assert search.done
    assert search.result().first_bad is None
    assert search.result().ambiguous == [0, 1, 2]


def test_next_is_deterministic_and_middle() -> None:
    search = Search(size=8)
    assert search.next_index() == 3  # middle of -1..8 is 3.5 → earlier of the two
    search.record(3, Verdict.GOOD)
    assert search.next_index() == 5  # middle of 3..8
    assert search.remaining_steps() >= 1
    with pytest.raises(IndexError):
        search.record(8, Verdict.BAD)
    with pytest.raises(ValueError, match="at least one"):
        Search(size=0)


def test_verdict_from_exit_code() -> None:
    assert Verdict.from_exit_code(0) is Verdict.GOOD
    assert Verdict.from_exit_code(125) is Verdict.SKIP
    assert Verdict.from_exit_code(1) is Verdict.BAD
    assert Verdict.from_exit_code(124) is Verdict.BAD


# --- scenario --------------------------------------------------------------------------------------------

SCENARIO = """\
name: demo
deployment:
  qgis: {version: host}
  qfieldsync: {version: release:v4.23.1, remember_me: false}
bisect:
  component: qfieldcloud
  good: v26.20
  bad: master
test:
  script: ./repro.sh
"""


def test_scenario_parses_and_builds_deployment_specs(tmp_path: Path) -> None:
    file = tmp_path / "scenario.yaml"
    file.write_text(SCENARIO)
    scenario = BisectScenario.from_file(file)
    assert scenario.test.script == (tmp_path / "repro.sh").resolve()
    assert scenario.deployment_name == "bs-demo"
    spec = scenario.deployment_spec({"kind": "git", "ref": "abc"})
    assert spec.name == "bs-demo"
    assert str(spec.qfieldcloud.version) == "git:abc"  # type: ignore[union-attr]
    assert spec.qfieldsync.remember_me is False  # type: ignore[union-attr]


def test_scenario_keeps_varying_component_options(tmp_path: Path) -> None:
    text = SCENARIO.replace(
        "  qgis: {version: host}\n", "  qgis: {version: host}\n  qfieldcloud: {tls: true, worker_replicas: 2}\n"
    )
    file = tmp_path / "s.yaml"
    file.write_text(text)
    spec = BisectScenario.from_file(file).deployment_spec({"kind": "release", "tag": "v26.21"})
    assert spec.qfieldcloud.tls is True  # type: ignore[union-attr]
    assert spec.qfieldcloud.worker_replicas == 2  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("line", "replacement", "message"),
    [
        ("  script: ./repro.sh\n", "  script: ./repro.sh\n  manual: true\n", "either a `script` or `manual: true`"),
        ("  bad: master\n", "  bad: master\n  source: release\n", "release` needs tag candidates"),
        ("  bad: master\n", "  bad: master\n  source: artifact\n", "only available for qfield-android"),
    ],
)
def test_scenario_validation_errors(tmp_path: Path, line: str, replacement: str, message: str) -> None:
    text = SCENARIO.replace(line, replacement)
    file = tmp_path / "s.yaml"
    file.write_text(text)
    with pytest.raises(SpecError, match=message):
        BisectScenario.from_file(file)


def test_candidate_rules(runner: FakeRunner, testbed: Testbed, tmp_path: Path) -> None:
    testbed.paths.ensure()
    mirror = Mirror(runner, testbed.paths, "https://github.com/opengisch/QFieldCloud")
    git = ["git", "-C", str(mirror.path)]
    runner.reply([*git, "tag"], stdout="v26.19\nv26.20\nv26.21\nv26.22\nmaster\n")
    runner.reply([*git, "rev-list"], stdout="a" * 40 + "\n" + "b" * 40 + "\n")
    runner.reply([*git, "rev-parse"], stdout="c" * 40 + "\n")
    runner.reply([*git, "log"], stdout="subject\n")
    file = tmp_path / "s.yaml"

    file.write_text(SCENARIO)
    first_parent = resolve_candidates(mirror, BisectScenario.from_file(file))
    assert [c.ref for c in first_parent] == ["a" * 40, "b" * 40]
    assert first_parent[0].label == "c" * 12  # sha labels are shortened (rev-parse is faked)

    file.write_text(
        SCENARIO.replace("  bad: master\n", '  bad: v26.22\n  candidates: tags:"v26.*"\n  source: release\n')
    )
    tags = resolve_candidates(mirror, BisectScenario.from_file(file))
    assert [c.ref for c in tags] == ["v26.21", "v26.22"]
    assert tags[0].label == "v26.21"

    file.write_text(SCENARIO.replace("  bad: master\n", "  bad: master\n  candidates: [x1, x2, x3]\n"))
    explicit = resolve_candidates(mirror, BisectScenario.from_file(file))
    assert [c.ref for c in explicit] == ["x1", "x2", "x3"]


# --- session (fake components, real orchestration) --------------------------------------------------------


class FakeVaryingCloud(FakeQgis):
    """A `qfieldcloud` stand-in whose describe() exposes the revision it runs, like SENTRY_RELEASE does."""

    name = "qfieldcloud"

    def resolve(self):
        from qfield_testbed.revisions import Revision  # noqa: PLC0415

        ref = self.spec.version.ref  # type: ignore[union-attr]
        return Revision(source=self.spec.version, label=ref[:12], sha=ref)

    def describe(self) -> dict[str, object]:
        return {"url": "http://127.0.0.1:1/", "version": self.state.revision.label if self.state.revision else None}


@pytest.fixture
def fake_components():
    saved = dict(REGISTRY)
    REGISTRY.clear()
    register(FakeQgis)
    register(FakeVaryingCloud)
    FakeQgis.calls = []
    yield
    REGISTRY.clear()
    REGISTRY.update(saved)


def _write_script(path: Path, first_bad: int) -> None:
    path.write_text(
        f'#!/bin/sh\necho "testing $QFT_BISECT_CANDIDATE on $QFT_QFIELDCLOUD_VERSION"\n[ "$QFT_BISECT_INDEX" -lt {first_bad} ]\n'
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.mark.usefixtures("fake_components")
def test_session_runs_to_the_first_bad_candidate(testbed: Testbed, runner: FakeRunner, tmp_path: Path) -> None:
    testbed.paths.ensure()
    shas = [f"{i:040x}" for i in range(1, 11)]
    mirror = Mirror(runner, testbed.paths, "https://github.com/opengisch/QFieldCloud")
    git = ["git", "-C", str(mirror.path)]
    runner.reply([*git, "rev-list"], stdout="\n".join(shas) + "\n")
    for sha in shas:
        runner.reply([*git, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], stdout=sha + "\n")
        runner.reply([*git, "log", "-1", "--format=%s", sha], stdout=f"commit {sha[-1]}\n")
    runner.reply([*git, "log"], stdout="2026-01-01\n")
    script = tmp_path / "repro.sh"
    _write_script(script, first_bad=7)
    scenario_file = tmp_path / "s.yaml"
    scenario_file.write_text(
        "name: s\ndeployment:\n  qgis: {version: host}\nbisect:\n  component: qfieldcloud\n  good: v1\n  bad: v2\ntest:\n  script: ./repro.sh\n"
    )

    session = BisectSession.create(testbed, BisectScenario.from_file(scenario_file))
    assert len(session.state.candidates) == 10
    assert session.search().remaining_steps() <= 4

    result = session.run()
    assert result.first_bad == 7
    assert session.state.status is SessionStatus.DONE
    assert session.candidate(7).sha == shas[7]
    assert result.tested <= 5
    # the deployment was created once then swapped for every other candidate
    deployment = testbed.deployment("bs-s")
    assert deployment.summary().session == "s"
    assert str(deployment.spec.qfieldcloud.version).startswith("git:")  # type: ignore[union-attr]
    logs = sorted(session.log_dir.glob("*.txt"))
    assert len(logs) == result.tested
    assert "testing" in logs[0].read_text()
    report = (session.directory / "report.md").read_text()
    assert shas[7][:12] in report
    assert "First bad revision" in report

    # resumable: a fresh object sees the same state and has nothing left to do
    again = BisectSession(testbed, "s")
    assert again.search().done
    assert again.summary()["first_bad"]["index"] == 7

    again.reset(remove_deployment=True)
    assert again.state.outcomes == {}
    with pytest.raises(TestbedError):
        testbed.deployment("bs-s")


class FakeBuildingCloud(FakeVaryingCloud):
    """Records one built image set per revision, like `QFieldCloud.start()`; the worker image never changes."""

    def resolve(self):
        from qfield_testbed.revisions import Revision  # noqa: PLC0415

        ref = self.spec.version.ref  # type: ignore[union-attr]
        return Revision(source=self.spec.version, label=ref[-12:], sha=ref)  # the test shas differ at the end

    def start(self) -> None:
        revision = self.state.revision
        assert revision is not None
        self.ctx.cache.touch(
            f"qfieldcloud/images/{revision.key}",
            self.directory,
            user=self.ctx.name,
            images=[f"qft-qfieldcloud-app:{revision.key}", "qft-qfieldcloud-worker:shared"],
        )


def test_session_keeps_at_most_max_image_sets(testbed: Testbed, runner: FakeRunner, tmp_path: Path) -> None:
    saved = dict(REGISTRY)
    REGISTRY.clear()
    register(FakeQgis)
    register(FakeBuildingCloud)
    try:
        testbed.config.cache.max_image_sets = 2
        testbed.paths.ensure()
        shas = [f"{i:040x}" for i in range(1, 11)]
        mirror = Mirror(runner, testbed.paths, "https://github.com/opengisch/QFieldCloud")
        git = ["git", "-C", str(mirror.path)]
        runner.reply([*git, "rev-list"], stdout="\n".join(shas) + "\n")
        for sha in shas:
            runner.reply([*git, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], stdout=sha + "\n")
            runner.reply([*git, "log", "-1", "--format=%s", sha], stdout=f"commit {sha[-1]}\n")
        runner.reply([*git, "log"], stdout="2026-01-01\n")
        _write_script(tmp_path / "repro.sh", first_bad=7)
        scenario_file = tmp_path / "s.yaml"
        scenario_file.write_text(
            "name: s\ndeployment:\n  qgis: {version: host}\nbisect:\n  component: qfieldcloud\n  good: v1\n  bad: v2\n"
            "test:\n  script: ./repro.sh\n"
        )
        session = BisectSession.create(testbed, BisectScenario.from_file(scenario_file))
        result = session.run()
        assert result.tested >= 3  # enough swaps to go past the limit
        sets = [key for key in testbed.cache.load().entries if key.startswith("qfieldcloud/images/")]
        assert len(sets) == 2
        current = testbed.deployment("bs-s").state.components["qfieldcloud"].revision
        assert current is not None
        assert f"qfieldcloud/images/{current.key}" in sets  # the running revision is never trimmed
        removed = [
            cmd.removeprefix("docker image rm ") for cmd in runner.commands() if cmd.startswith("docker image rm")
        ]
        assert len(removed) == result.tested - 2
        assert all(image.startswith("qft-qfieldcloud-app:") for image in removed)  # the shared image stays
    finally:
        REGISTRY.clear()
        REGISTRY.update(saved)


@pytest.mark.usefixtures("fake_components")
def test_manual_session_prompts_and_pauses(testbed: Testbed, runner: FakeRunner, tmp_path: Path) -> None:
    testbed.paths.ensure()
    mirror = Mirror(runner, testbed.paths, "https://github.com/opengisch/QFieldCloud")
    git = ["git", "-C", str(mirror.path)]
    runner.reply([*git, "rev-parse"], stdout="d" * 40 + "\n")
    runner.reply([*git, "log"], stdout="x\n")
    scenario_file = tmp_path / "s.yaml"
    scenario_file.write_text(
        "name: m\nbisect:\n  component: qfieldcloud\n  good: v1\n  bad: v2\n  candidates: [c1, c2, c3]\ntest:\n  manual: true\n"
    )
    session = BisectSession.create(testbed, BisectScenario.from_file(scenario_file))
    answers = iter(["nonsense", "bad", "good"])
    result = session.run(prompt=lambda _msg: next(answers))
    assert result.first_bad == 1
    assert [o.verdict for _, o in sorted(session.state.outcomes.items())] == [Verdict.GOOD, Verdict.BAD]
    session.reset()
    with pytest.raises(TestbedError, match="interactively"):
        session.step(prompt=None)
