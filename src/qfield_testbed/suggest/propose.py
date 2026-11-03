"""Propose a deployment spec for a detected checkout: the checkout live, the rest released, fit to this host.

The proposal is validated as a spec but never applied: `qft deployment ensure` does that, once an agent or a
person agrees with it. Every choice is explained in ``rationale``; what had to be left out, in ``notes``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from qfield_testbed.deployment import Testbed
from qfield_testbed.doctor import Capability, capabilities, run_checks
from qfield_testbed.errors import PrerequisiteMissingError, SpecError, UsageError
from qfield_testbed.spec import NAME_PATTERN, DeploymentSpec
from qfield_testbed.suggest.detect import Detection

NEEDS = ("android", "sync", "worker", "tls", "desktop", "master")
QFIELDCLOUD_TAG = r"v\d+\.\d+"
SEMVER_TAG = r"v\d+\.\d+\.\d+"


@dataclass
class Proposal:
    project: str
    path: Path
    spec: dict[str, Any]
    spec_yaml: str
    rationale: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def latest_release(testbed: Testbed, url: str, pattern: str) -> str | None:
    """Newest stable tag of ``url`` matching ``pattern``, read with `git ls-remote` (no clone, no `gh`)."""
    result = testbed.runner.run(["git", "ls-remote", "--tags", "--refs", url], check=False, timeout=60)
    tags = [line.split("refs/tags/", 1)[1] for line in result.stdout.splitlines() if "refs/tags/" in line]
    stable = [tag for tag in tags if re.fullmatch(pattern, tag)]
    return max(stable, key=lambda tag: tuple(int(n) for n in re.findall(r"\d+", tag))) if stable else None


class _Builder:
    def __init__(self, testbed: Testbed, detection: Detection, needs: set[str], matrix: dict[str, Capability]) -> None:
        self.testbed, self.detection, self.needs, self.matrix = testbed, detection, needs, matrix
        self.spec: dict[str, Any] = {"name": deployment_name(detection.root)}
        self.rationale = list(detection.evidence)
        self.notes: list[str] = []

    def can(self, key: str) -> bool:
        return self.matrix[key].can_run

    def missing(self, what: str, *keys: str) -> PrerequisiteMissingError:
        hints = [hint for key in keys for hint in self.matrix[key].fix_hints]
        checks = sorted({check for key in keys for check in self.matrix[key].missing})
        return PrerequisiteMissingError(
            f"this host cannot run {what} (missing: {', '.join(checks)})",
            hint="; ".join(dict.fromkeys(hints)) or "see `qft doctor`",
            docs="getting-started",
            details={"missing": {key: self.matrix[key].missing for key in keys}},
        )

    def release(self, component: str, url: str, pattern: str) -> str:
        if "master" in self.needs:
            self.rationale.append(f"{component} from git:master (--need master)")
            return "git:master"
        tag = latest_release(self.testbed, url, pattern)
        if tag is None:
            self.notes.append(f"no release of {url} reachable: {component} from git:master")
            return "git:master"
        self.rationale.append(f"{component} from its latest release, {tag}")
        return f"release:{tag}"

    def qgis(self) -> dict[str, Any]:
        if self.can("qgis:host"):
            return {"version": "host"}
        if self.can("qgis:docker"):
            self.notes.append("no QGIS on this host: QGIS from the docker:ltr image")
            return {"version": "docker:ltr"}
        raise self.missing("QGIS", "qgis:host", "qgis:docker")

    def server(self) -> None:
        if "qfieldcloud" not in self.spec:
            repos = self.testbed.config.repositories
            self.spec["qfieldcloud"] = {"version": self.release("qfieldcloud", repos.qfieldcloud, QFIELDCLOUD_TAG)}

    def client(self) -> None:
        self.spec.setdefault("qgis", self.qgis())
        if "qfieldsync" not in self.spec:
            repos = self.testbed.config.repositories
            self.spec["qfieldsync"] = {"version": self.release("qfieldsync", repos.qfieldsync, SEMVER_TAG)}

    def app(self, component: str, key: str) -> None:
        """QField on Android or desktop from its latest release, when the host can run it."""
        if not (self.can(key) and self.can("source:release")):
            raise self.missing(component, key, "source:release")
        url = f"https://github.com/{self.testbed.config.repositories.qfield.removeprefix('https://github.com/')}"
        tag = latest_release(self.testbed, url, SEMVER_TAG)
        if tag is None:
            raise PrerequisiteMissingError(f"no QField release reachable for {component}", hint="check the network")
        self.spec[component] = {"version": f"release:{tag}"}
        self.rationale.append(f"{component} from QField's latest release, {tag} (--need)")


def propose(
    testbed: Testbed, detection: Detection, needs: set[str], matrix: dict[str, Capability] | None = None
) -> Proposal:
    unknown = needs - set(NEEDS)
    if unknown:
        raise UsageError(f"unknown need: {', '.join(sorted(unknown))}", hint="one of: " + ", ".join(NEEDS))
    build = _Builder(testbed, detection, needs, matrix or capabilities(run_checks(testbed)))
    root = str(detection.root)
    match detection.project:
        case "qfieldsync":
            build.spec["qfieldsync"] = {"version": f"worktree:{root}"}
            build.rationale.append("this checkout as qfieldsync, live (worktree:)")
            build.client()
            build.server()
        case "qfieldcloud":
            build.spec["qfieldcloud"] = {"version": f"worktree:{root}"}
            build.rationale.append("this checkout as qfieldcloud (worktree:)")
            build.client()
        case "qfield":
            _qfield_app(build, root)
            build.server()
        case "qgis-plugin":
            build.spec["qgis"] = {**build.qgis(), "plugins": [{"path": str(detection.plugin_dir or detection.root)}]}
            build.rationale.append("this plugin installed from its folder into the isolated QGIS profile")
        case _:
            raise UsageError(
                f"{detection.root} is not a checkout qft recognizes",
                hint="looked for a QFieldCloud, QFieldSync, QField or QGIS plugin remote or layout; "
                "write the spec by hand (`qft spec example`)",
                docs="getting-started",
            )
    _apply_needs(build)
    return _finish(build, detection)


def _qfield_app(build: _Builder, root: str) -> None:
    if not build.can("qfield-android"):
        raise build.missing("QField on Android", "qfield-android")
    if build.can("source:build"):
        build.spec["qfield-android"] = {"version": f"build:{root}"}
        build.rationale.append("this checkout built for the Android emulator (build:)")
        return
    if not build.can("source:artifact"):
        raise build.missing("a QField build of this checkout", "source:build", "source:artifact")
    branch = build.testbed.runner.run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"], check=False)
    ref = branch.stdout.strip() or "master"
    build.spec["qfield-android"] = {"version": f"artifact:{ref}"}
    build.notes.append(f"not enough disk to build here: the CI artifact of branch {ref} instead (push it first)")


def _apply_needs(build: _Builder) -> None:
    if build.needs & {"sync", "worker", "tls"} or "qfieldsync" in build.spec:
        build.server()
    if "sync" in build.needs:
        build.client()
    if "worker" in build.needs:
        build.spec["qfieldcloud"]["worker_replicas"] = max(1, build.spec["qfieldcloud"].get("worker_replicas", 1))
    if "tls" in build.needs:
        build.spec["qfieldcloud"]["tls"] = True
    if "android" in build.needs and "qfield-android" not in build.spec:
        build.app("qfield-android", "qfield-android")
    if "desktop" in build.needs:
        build.app("qfield-desktop", "qfield-desktop")
    if "qfieldcloud" in build.spec and not build.can("qfieldcloud"):
        raise build.missing("qfieldcloud", "qfieldcloud")
    # seeding the QFieldSync profile needs the host's PyQGIS only with the host's QGIS
    host_qgis = build.spec.get("qgis", {}).get("version") == "host"
    if "qfieldsync" in build.spec and host_qgis and not build.can("qfieldsync"):
        raise build.missing("qfieldsync", "qfieldsync")


def _finish(build: _Builder, detection: Detection) -> Proposal:
    try:
        DeploymentSpec.model_validate(build.spec)
    except ValidationError as exc:  # a bug of the builder, not of the user: say so plainly
        raise SpecError(f"the proposed spec is invalid: {exc}") from exc
    spec_yaml = yaml.safe_dump(build.spec, sort_keys=False)
    return Proposal(detection.project, detection.root, build.spec, spec_yaml, build.rationale, build.notes)


def deployment_name(root: Path) -> str:
    """A valid deployment name from the checkout's folder: `qfieldsync-fix-cloud-rm-806` → `qfieldsync-fix-cloud`."""
    slug = re.sub(r"[^a-z0-9-]+", "-", root.name.lower()).strip("-") or "suggested"
    if not slug[0].isalpha():
        slug = f"d-{slug}"
    name = slug[:20].rstrip("-")
    assert NAME_PATTERN.match(name), name  # noqa: S101 - holds by construction
    return name
