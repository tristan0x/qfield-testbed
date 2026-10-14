"""A bisect session: a scenario, its ordered candidates, the verdicts so far, and the deployment it drives.

Every verdict is persisted before the next swap, so a session interrupted at any point resumes where it was.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from qfield_testbed.bisect.algorithm import Result, Search, Verdict
from qfield_testbed.bisect.runner import ManualTest, Outcome, ScriptTest
from qfield_testbed.bisect.scenario import BisectScenario
from qfield_testbed.deployment import Deployment, Testbed
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError
from qfield_testbed.revisions.git import Mirror
from qfield_testbed.spec import VersionSource

log = logging.getLogger(__name__)

IMAGE_SETS = "qfieldcloud/images/"
"""Cache key prefix of QFieldCloud image sets (one per revision)."""

_VERSION = TypeAdapter(VersionSource)
_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _now() -> datetime:
    return datetime.now(tz=UTC)


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    sha: str | None = None
    label: str
    subject: str = ""
    date: str = ""


class OutcomeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    exit_code: int | None = None
    duration_s: float = 0.0
    log_file: str | None = None
    note: str = ""


class SessionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    DONE = "done"


class SessionState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    scenario: BisectScenario
    repo_url: str
    candidates: list[Candidate]
    outcomes: dict[int, OutcomeRecord] = Field(default_factory=dict)
    current: int | None = Field(default=None, description="Candidate prepared in the deployment, awaiting a verdict")
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def deployment_name(self) -> str:
        return self.scenario.deployment_name


class UnknownSessionError(TestbedError):
    def __init__(self, name: str) -> None:
        super().__init__(f"No bisect session named '{name}'. Run `qft bisect list`.")


# ---------------------------------------------------------------------------------------------------------
# Candidate resolution
# ---------------------------------------------------------------------------------------------------------


def repository_url(testbed: Testbed, scenario: BisectScenario) -> str:
    component = scenario.bisect.component
    fixed = scenario.deployment.as_mapping().get(component) or {}
    if component == "qfieldcloud":
        return str(fixed.get("repo") or testbed.config.repositories.qfieldcloud)
    if component == "qfieldsync":
        return str(fixed.get("repo") or testbed.config.repositories.qfieldsync)
    slug = str(fixed.get("repo") or "opengisch/QField")
    return slug if slug.startswith("http") else f"https://github.com/{slug}"


def resolve_candidates(mirror: Mirror, scenario: BisectScenario) -> list[Candidate]:
    """Ordered candidates (oldest first) strictly after `good`, up to and including `bad`."""
    rule = scenario.bisect
    if isinstance(rule.candidates, list):
        refs = rule.candidates
    elif rule.tags_glob is not None:
        tags = mirror.tags(rule.tags_glob)
        start = tags.index(rule.good) + 1 if rule.good in tags else 0
        end = tags.index(rule.bad) + 1 if rule.bad in tags else len(tags)
        refs = tags[start:end]
    else:
        refs = mirror.rev_list_first_parent(rule.good, rule.bad)
    if not refs:
        raise TestbedError(f"no candidates between {rule.good} and {rule.bad} with rule `{rule.candidates}`")
    candidates = []
    for ref in refs:
        sha = mirror.rev_parse(ref)
        label = sha[:12] if _SHA.match(ref) else ref
        candidates.append(Candidate(ref=ref, sha=sha, label=label, subject=mirror.subject(sha), date=mirror.date(sha)))
    return candidates


def version_for(scenario: BisectScenario, candidate: Candidate) -> dict[str, Any]:
    source = scenario.bisect.source
    if source == "release":
        return {"kind": "release", "tag": candidate.ref}
    if source == "artifact":
        return {"kind": "artifact", "ref": candidate.sha or candidate.ref}
    return {"kind": "git", "ref": candidate.sha or candidate.ref}


# ---------------------------------------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------------------------------------


class BisectSession:
    def __init__(self, testbed: Testbed, name: str) -> None:
        self.testbed = testbed
        self.name = name
        self.directory = testbed.paths.session_dir(name)
        if not self.state_file.is_file():
            raise UnknownSessionError(name)
        self.state = SessionState.model_validate_json(self.state_file.read_text())

    @property
    def state_file(self) -> Path:
        return self.directory / "state.json"

    @property
    def log_dir(self) -> Path:
        return self.directory / "log"

    @classmethod
    def create(cls, testbed: Testbed, scenario: BisectScenario, name: str | None = None) -> BisectSession:
        name = name or scenario.name
        directory = testbed.paths.session_dir(name)
        if (directory / "state.json").exists():
            raise TestbedError(f"bisect session '{name}' already exists; `qft bisect reset {name}` or pick --session")
        repo_url = repository_url(testbed, scenario)
        mirror = Mirror(testbed.runner, testbed.paths, repo_url)
        candidates = resolve_candidates(mirror, scenario)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "scenario.yaml").write_text(scenario.to_yaml())
        state = SessionState(name=name, scenario=scenario, repo_url=repo_url, candidates=candidates)
        (directory / "state.json").write_text(state.model_dump_json(indent=2) + "\n")
        log.info(
            "session '%s': %d candidates between %s and %s",
            name,
            len(candidates),
            scenario.bisect.good,
            scenario.bisect.bad,
        )
        return cls(testbed, name)

    @staticmethod
    def names(testbed: Testbed) -> list[str]:
        if not testbed.paths.bisect_dir.is_dir():
            return []
        return sorted(p.name for p in testbed.paths.bisect_dir.iterdir() if (p / "state.json").is_file())

    def save(self) -> None:
        self.state.updated_at = _now()
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(self.state.model_dump_json(indent=2) + "\n")
        tmp.replace(self.state_file)

    # --- search -----------------------------------------------------------------------------------------

    def search(self) -> Search:
        return Search(size=len(self.state.candidates), verdicts={i: o.verdict for i, o in self.state.outcomes.items()})

    def candidate(self, index: int) -> Candidate:
        return self.state.candidates[index]

    def deployment(self) -> Deployment | None:
        try:
            return self.testbed.deployment(self.state.deployment_name)
        except TestbedError:
            return None

    # --- steps ------------------------------------------------------------------------------------------

    def prepare(self, index: int) -> Deployment | None:
        """Bring the deployment to candidate ``index``; None (and a recorded skip) when it cannot be materialized."""
        scenario = self.state.scenario
        candidate = self.candidate(index)
        version = version_for(scenario, candidate)
        log.info("step %d/%d: %s — %s", index + 1, len(self.state.candidates), candidate.label, candidate.subject)
        self.state.status = SessionStatus.RUNNING
        try:
            deployment = self.deployment()
            if deployment is None:
                deployment = self.testbed.add(scenario.deployment_spec(version), session=self.name)
                deployment.start()
            else:
                deployment.swap(scenario.bisect.component, _VERSION.validate_python(version))
        except MaterializationUnavailableError as exc:
            log.warning("candidate %s cannot be materialized: %s → skip", candidate.label, exc)
            self.record(index, Outcome(Verdict.SKIP, None, 0.0, str(exc)), note="materialization unavailable")
            return None
        self.state.current = index
        self.save()
        if scenario.bisect.component == "qfieldcloud":
            self.trim_image_sets(deployment)
        return deployment

    def trim_image_sets(self, deployment: Deployment) -> None:
        """Keep at most ``cache.max_image_sets`` built QFieldCloud image sets: each revision builds its own."""
        revision = deployment.state.components["qfieldcloud"].revision
        protect = {f"{IMAGE_SETS}{revision.key}"} if revision else set()
        cache = self.testbed.cache
        dropped = cache.trim(IMAGE_SETS, keep=self.testbed.config.cache.max_image_sets, protect=protect)
        # sets share images when a build context did not change between revisions: keep what a kept set uses
        still_used = {image for entry in cache.load().entries.values() for image in entry.images}
        for image in sorted({image for entry in dropped.values() for image in entry.images} - still_used):
            log.info("removing image %s (beyond cache.max_image_sets)", image)
            self.testbed.runner.run(["docker", "image", "rm", image], check=False)

    def environment(self, index: int, deployment: Deployment) -> dict[str, str]:
        candidate = self.candidate(index)
        return {
            **deployment.env(),
            "QFT_SESSION": self.name,
            "QFT_SESSION_DIR": str(self.directory),
            "QFT_BISECT_COMPONENT": self.state.scenario.bisect.component,
            "QFT_BISECT_INDEX": str(index),
            "QFT_BISECT_CANDIDATE": candidate.label,
            "QFT_BISECT_SHA": candidate.sha or "",
        }

    def test(self, index: int, deployment: Deployment, prompt: Callable[[str], str] | None = None) -> Outcome:
        spec = self.state.scenario.test
        log_file = self.log_dir / f"{index:03d}-{self.candidate(index).label.replace('/', '_')}.txt"
        if spec.manual:
            if prompt is None:
                raise TestbedError("this scenario has a manual test; run it interactively with `qft bisect run`")
            return ManualTest(prompt).run(self.environment(index, deployment), log_file)
        assert spec.script is not None  # noqa: S101 - validated by TestSpec
        return ScriptTest(spec.script, spec.args, spec.timeout).run(self.environment(index, deployment), log_file)

    def record(self, index: int, outcome: Outcome, *, note: str = "") -> None:
        log_file = self.log_dir / f"{index:03d}-{self.candidate(index).label.replace('/', '_')}.txt"
        if not log_file.exists():
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(outcome.log)
        self.state.outcomes[index] = OutcomeRecord(
            verdict=outcome.verdict,
            exit_code=outcome.exit_code,
            duration_s=round(outcome.duration_s, 1),
            log_file=str(log_file),
            note=note,
        )
        self.state.current = None
        if self.search().done:
            self.state.status = SessionStatus.DONE
            self.write_report()
        self.save()
        log.info("candidate %s → %s", self.candidate(index).label, outcome.verdict)

    def step(self, prompt: Callable[[str], str] | None = None) -> bool:
        """Prepare, test and record the next candidate; returns False when the search is complete."""
        index = self.search().next_index()
        if index is None:
            self.state.status = SessionStatus.DONE
            self.save()
            return False
        deployment = self.prepare(index)
        if deployment is None:
            return True
        outcome = self.test(index, deployment, prompt)
        self.record(index, outcome)
        return True

    def run(self, prompt: Callable[[str], str] | None = None) -> Result:
        while self.step(prompt):
            pass
        return self.search().result()

    def reset(self, *, remove_deployment: bool = False) -> None:
        self.state.outcomes = {}
        self.state.current = None
        self.state.status = SessionStatus.CREATED
        (self.directory / "report.md").unlink(missing_ok=True)
        self.save()
        if remove_deployment and (deployment := self.deployment()) is not None:
            deployment.remove()

    def remove(self) -> None:
        if (deployment := self.deployment()) is not None:
            deployment.remove()
        for file in sorted(self.directory.rglob("*"), reverse=True):
            file.unlink() if file.is_file() else file.rmdir()
        self.directory.rmdir()

    # --- report -----------------------------------------------------------------------------------------

    def link(self, candidate: Candidate) -> str:
        base = self.state.repo_url.removesuffix(".git")
        if self.state.scenario.bisect.source == "release":
            return f"{base}/releases/tag/{candidate.ref}"
        return f"{base}/commit/{candidate.sha or candidate.ref}"

    def summary(self) -> dict[str, Any]:
        search = self.search()
        result = search.result()
        scenario = self.state.scenario
        first_bad = self.candidate(result.first_bad) if result.first_bad is not None else None
        return {
            "session": self.name,
            "status": self.state.status,
            "component": scenario.bisect.component,
            "good": scenario.bisect.good,
            "bad": scenario.bisect.bad,
            "deployment": self.state.deployment_name,
            "candidates": len(self.state.candidates),
            "tested": result.tested,
            "skipped": result.skipped,
            "remaining_steps": search.remaining_steps(),
            "current": self.state.current,
            "next": search.next_index(),
            "first_bad": (
                {
                    "index": result.first_bad,
                    "label": first_bad.label,
                    "sha": first_bad.sha,
                    "subject": first_bad.subject,
                    "date": first_bad.date,
                    "url": self.link(first_bad),
                }
                if first_bad
                else None
            ),
            "ambiguous": [self.candidate(i).label for i in result.ambiguous],
            "verdicts": [
                {
                    "index": i,
                    "label": c.label,
                    "subject": c.subject,
                    "verdict": self.state.outcomes[i].verdict if i in self.state.outcomes else None,
                    "exit_code": self.state.outcomes[i].exit_code if i in self.state.outcomes else None,
                }
                for i, c in enumerate(self.state.candidates)
            ],
        }

    def write_report(self) -> Path:
        info = self.summary()
        lines = [f"# Bisect `{self.name}`", ""]
        lines.append(
            f"Component **{info['component']}** between `{info['good']}` (good) and `{info['bad']}` (bad): "
            f"{info['candidates']} candidates, {info['tested']} tested, {info['skipped']} skipped."
        )
        lines.append("")
        if info["first_bad"]:
            fb = info["first_bad"]
            lines += [
                "## First bad revision",
                "",
                f"- **{fb['label']}** — {fb['subject']} ({fb['date']})",
                f"- {fb['url']}",
            ]
            if info["ambiguous"]:
                lines.append(f"- skipped right before it, could also be the culprit: {', '.join(info['ambiguous'])}")
        else:
            lines += [
                "## No bad candidate isolated",
                "",
                "Every tested candidate was good; the regression is in the `bad` ref itself"
                + (f" or in the skipped candidates {', '.join(info['ambiguous'])}." if info["ambiguous"] else "."),
            ]
        lines += ["", "## Steps", "", "| # | candidate | verdict | exit | log |", "|---|---|---|---|---|"]
        for index, record in sorted(self.state.outcomes.items()):
            candidate = self.candidate(index)
            lines.append(
                f"| {index} | `{candidate.label}` {candidate.subject[:60]} | {record.verdict} | {record.exit_code if record.exit_code is not None else ''} | {record.log_file or ''} |"
            )
        report = self.directory / "report.md"
        report.write_text("\n".join(lines) + "\n")
        return report
