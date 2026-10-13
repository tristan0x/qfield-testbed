"""The bisect scenario: fixed components, the varying component with its range, and the test."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from qfield_testbed.errors import SpecError
from qfield_testbed.spec import (
    COMPONENT_NAMES,
    NAME_PATTERN,
    DeploymentSpec,
    QFieldAndroidSpec,
    QFieldCloudSpec,
    QFieldDesktopSpec,
    QFieldIosSpec,
    QFieldSyncSpec,
    QgisSpec,
)

_TAGS_RULE = re.compile(r"^tags:(.+)$")


class FixedComponents(BaseModel):
    """The deployment part of a scenario: the same keys as a spec, without `name`.

    The varying component may appear too (to carry options such as `repo` or `remember_me`); its `version`
    is replaced by each candidate.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    qfieldcloud: QFieldCloudSpec | dict[str, Any] | None = None
    qgis: QgisSpec | None = None
    qfieldsync: QFieldSyncSpec | dict[str, Any] | None = None
    qfield_android: QFieldAndroidSpec | dict[str, Any] | None = Field(default=None, alias="qfield-android")
    qfield_ios: QFieldIosSpec | None = Field(default=None, alias="qfield-ios")
    qfield_desktop: QFieldDesktopSpec | None = Field(default=None, alias="qfield-desktop")

    def as_mapping(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        return {key: value for key, value in data.items() if key in COMPONENT_NAMES}


class BisectRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Literal["qfieldcloud", "qfieldsync", "qfield-android"]
    good: str = Field(description="A ref known good (tag, branch, sha)")
    bad: str = Field(description="A ref known bad")
    candidates: str | list[str] = Field(
        default="first-parent",
        description="`first-parent` (default), `tags:<glob>`, or an explicit list of refs oldest first",
    )
    source: Literal["git", "release", "artifact"] = Field(
        default="git",
        description="How candidates are materialized: built from git, official releases (tag candidates only), "
        "or CI artifacts (qfield-android)",
    )
    reset: Literal["volumes", "none"] = Field(
        default="volumes", description="Wipe the component's persistent data between candidates"
    )

    @field_validator("candidates")
    @classmethod
    def _rule(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list):
            if not value:
                raise ValueError("an explicit candidate list cannot be empty")
            return value
        if value == "first-parent" or _TAGS_RULE.match(value):
            return value
        raise ValueError("candidates must be `first-parent`, `tags:<glob>` or a list of refs")

    @property
    def tags_glob(self) -> str | None:
        if isinstance(self.candidates, str) and (match := _TAGS_RULE.match(self.candidates)):
            return match.group(1).strip("\"'")
        return None

    @model_validator(mode="after")
    def _coherent(self) -> BisectRange:
        if self.source == "release" and self.tags_glob is None and not isinstance(self.candidates, list):
            raise ValueError('source `release` needs tag candidates: `candidates: tags:"v26.*"` or a list of tags')
        if self.source == "artifact" and self.component != "qfield-android":
            raise ValueError("source `artifact` is only available for qfield-android")
        if self.source == "release" and self.component == "qfield-android":
            raise ValueError(
                "qfield-android candidates come from `artifact` (CI) sources; releases are too sparse to bisect"
            )
        return self


class TestSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script: Path | None = Field(default=None, description="Exit 0 good, 125 skip, other bad, >= 128 aborts")
    manual: bool = Field(default=False, description="Prompt good / bad / skip after each swap")
    timeout: int = Field(default=900, ge=1)
    args: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_of(self) -> TestSpec:
        if (self.script is None) == (not self.manual):
            raise ValueError("a test is either a `script` or `manual: true`")
        return self


class BisectScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    deployment: FixedComponents = FixedComponents()
    bisect: BisectRange
    test: TestSpec

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value) or len(value) > 16:  # noqa: PLR2004
            raise ValueError("name must match [a-z][a-z0-9-]{0,15} (it prefixes the deployment name)")
        return value

    @property
    def deployment_name(self) -> str:
        return f"bs-{self.name}"

    def deployment_spec(self, version: dict[str, Any]) -> DeploymentSpec:
        """The deployment spec for one candidate: fixed components plus the varying one at ``version``."""
        data = self.deployment.as_mapping()
        component = dict(data.get(self.bisect.component) or {})
        component["version"] = version
        data[self.bisect.component] = component
        data["name"] = self.deployment_name
        try:
            return DeploymentSpec.model_validate(data)
        except ValidationError as exc:
            raise SpecError(f"scenario '{self.name}' does not form a valid deployment: {exc}") from exc

    @classmethod
    def from_file(cls, path: Path) -> BisectScenario:
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise SpecError(f"cannot read scenario {path}: {exc}") from exc
        try:
            scenario = cls.model_validate(data)
        except ValidationError as exc:
            lines = [f"invalid scenario {path}:"]
            lines += [f"  - {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            raise SpecError("\n".join(lines)) from exc
        if scenario.test.script is not None and not scenario.test.script.is_absolute():
            scenario.test.script = (path.parent / scenario.test.script).resolve()
        # the fixed part must itself be a valid deployment once the varying component is added
        scenario.deployment_spec({"kind": "git", "ref": scenario.bisect.bad})
        return scenario

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=False)


EXAMPLE_SCENARIO = """\
name: qfs-crash-on-qfc
description: "QFieldSync (worktree) crashes when packaging against recent QFieldCloud"

deployment:                      # fixed components: a spec without `name`
  qgis: { version: host }
  qfieldsync: { version: worktree:~/src/github.com/opengisch/qfieldsync }

bisect:
  component: qfieldcloud         # the one that varies — exactly one per session
  good: v26.20                   # known good ref
  bad: master                    # known bad ref
  candidates: first-parent       # first-parent | tags:"v26.*" | list: [ref, ...]
  source: git                    # git (build from checkout) | release (tags only, official images)
  reset: volumes                 # wipe the component's data on every swap

test:
  script: ./repro.sh             # exit 0 = good, 125 = skip, anything else = bad
  timeout: 900
  # manual: true                 # or prompt good / bad / skip after each swap
"""
