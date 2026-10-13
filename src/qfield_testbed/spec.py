"""Deployment spec: which components, from which version source, with which options.

A spec is a YAML document validated into :class:`DeploymentSpec`. Version sources accept a shorthand string
(``release:v26.28``, ``git:master``, ``worktree:/path``, ``artifact:<ref>``, ``build:/path``, ``host``,
``docker:3.44``) or the equivalent mapping (``{kind: release, tag: v26.28}``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from qfield_testbed.errors import SpecError

# ---------------------------------------------------------------------------------------------------------
# Version sources
# ---------------------------------------------------------------------------------------------------------


class ReleaseSource(BaseModel):
    """An official release identified by its tag; binaries come from GitHub releases or ghcr.io images."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["release"] = "release"
    tag: str

    def __str__(self) -> str:
        return f"release:{self.tag}"


class GitSource(BaseModel):
    """Any git ref (tag, branch, sha) built from a checkout of the component repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["git"] = "git"
    ref: str

    def __str__(self) -> str:
        return f"git:{self.ref}"


class WorktreeSource(BaseModel):
    """A local checkout (typically a git worktree) used as-is; edits are picked up live when possible."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["worktree"] = "worktree"
    path: Path

    @field_validator("path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    def __str__(self) -> str:
        return f"worktree:{self.path}"


class ArtifactSource(BaseModel):
    """A binary produced by the component's own CI for a git ref (GitHub Actions artifact)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["artifact"] = "artifact"
    ref: str

    def __str__(self) -> str:
        return f"artifact:{self.ref}"


class BuildSource(BaseModel):
    """A binary built locally from a checkout with the component's own build scripts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["build"] = "build"
    path: Path

    @field_validator("path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    def __str__(self) -> str:
        return f"build:{self.path}"


class HostSource(BaseModel):
    """The version installed on the host (QGIS only)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["host"] = "host"

    def __str__(self) -> str:
        return "host"


class DockerSource(BaseModel):
    """A published Docker image tag (QGIS only: ``qgis/qgis:<tag>``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["docker"] = "docker"
    tag: str

    def __str__(self) -> str:
        return f"docker:{self.tag}"


VersionSource = ReleaseSource | GitSource | WorktreeSource | ArtifactSource | BuildSource | HostSource | DockerSource

_SHORTHAND_FIELDS: dict[str, str | None] = {
    "release": "tag",
    "git": "ref",
    "worktree": "path",
    "artifact": "ref",
    "build": "path",
    "host": None,
    "docker": "tag",
}


def parse_version_source(value: Any) -> Any:
    """Expand the ``kind:value`` shorthand into the mapping pydantic expects."""
    if not isinstance(value, str):
        return value
    kind, sep, rest = value.partition(":")
    if kind not in _SHORTHAND_FIELDS:
        msg = f"unknown version source '{value}'; expected one of {', '.join(_SHORTHAND_FIELDS)}"
        raise ValueError(msg)
    field = _SHORTHAND_FIELDS[kind]
    if field is None:
        if sep:
            raise ValueError(f"version source '{kind}' takes no value")
        return {"kind": kind}
    if not rest:
        raise ValueError(f"version source '{kind}' needs a value, e.g. '{kind}:<{field}>'")
    return {"kind": kind, field: rest}


_Shorthand = BeforeValidator(parse_version_source)

CodeSource = Annotated[Annotated[ReleaseSource | GitSource | WorktreeSource, Field(discriminator="kind")], _Shorthand]
"""Sources for components built from a repository (QFieldCloud, QFieldSync)."""

QgisSource = Annotated[Annotated[HostSource | DockerSource, Field(discriminator="kind")], _Shorthand]

AppSource = Annotated[Annotated[ReleaseSource | ArtifactSource | BuildSource, Field(discriminator="kind")], _Shorthand]
"""Sources for QField binaries on Android."""

SimulatorAppSource = Annotated[BuildSource, _Shorthand]
"""iOS: only a simulator build made on the Mac; CI artifacts are device `.ipa` files."""

AppImageSource = Annotated[ReleaseSource, _Shorthand]


# ---------------------------------------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------------------------------------

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,19}$")

COMPONENT_NAMES: tuple[str, ...] = (
    "qfieldcloud",
    "qgis",
    "qfieldsync",
    "qfield-android",
    "qfield-ios",
    "qfield-desktop",
)
"""Component names, in start order: servers first, then the desktop, then the mobile clients."""


class TestAccountSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = "testbed"
    email: str = "testbed@example.com"
    password: str = Field(default="auto", description="`auto` generates a random password at add time")
    superuser: bool = True
    demo_project: bool = Field(default=True, description="Upload the bundled sample project after provisioning")


class QFieldCloudSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: CodeSource
    repo: str | None = Field(default=None, description="Git URL; defaults to the configured upstream")
    tls: bool = Field(default=False, description="Also start nginx with mkcert certificates")
    worker_replicas: int = Field(default=1, ge=0, le=8)
    test_account: TestAccountSpec = TestAccountSpec()


class PluginSpec(BaseModel):
    """An extra QGIS plugin: a zip URL/path, or a local plugin folder."""

    model_config = ConfigDict(extra="forbid")

    zip: str | None = None
    path: Path | None = None

    @model_validator(mode="after")
    def _one_of(self) -> PluginSpec:
        if (self.zip is None) == (self.path is None):
            raise ValueError("a plugin needs exactly one of `zip` or `path`")
        if self.path is not None:
            self.path = self.path.expanduser().resolve()
        return self


QtPlatform = Literal["session", "xcb", "wayland", "offscreen"]
"""Qt platform plugin of QGIS: `session` follows the desktop session (host) or uses X11 (Docker)."""


class QgisSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: QgisSource = HostSource()
    plugins: list[PluginSpec] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment for the QGIS process")
    platform: QtPlatform = Field(
        default="session", description="Qt platform: window-manager-dependent bugs differ between X11 and Wayland"
    )

    @model_validator(mode="after")
    def _platform(self) -> QgisSpec:
        if self.platform == "wayland" and isinstance(self.version, DockerSource):
            raise ValueError("`platform: wayland` needs `version: host`; the Docker QGIS only reaches the X11 socket")
        if self.platform != "session" and "QT_QPA_PLATFORM" in self.env:
            raise ValueError("set the Qt platform with `platform` or with `env.QT_QPA_PLATFORM`, not both")
        return self


class QFieldSyncSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: CodeSource
    repo: str | None = None
    remember_me: bool = Field(default=True, description="Log in to QFieldCloud automatically when QGIS starts")


class QFieldAndroidSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: AppSource
    repo: str = Field(default="opengisch/QField", description="GitHub `owner/name` used for releases and artifacts")
    image: str = "budtmo/docker-android:emulator_14.0"
    device: str = Field(default="Samsung Galaxy S10", description="Emulator device profile (EMULATOR_DEVICE)")
    seed_settings: bool = Field(default=True, description="Pre-fill the QFieldCloud URL in the app settings")


class QFieldIosSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: SimulatorAppSource
    host: str = Field(description="Key of an entry in `ios_hosts` of the global config")
    device: str = "iPhone 16"
    runtime: str | None = Field(default=None, description="simctl runtime id; newest available when unset")


class QFieldDesktopSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: AppImageSource
    repo: str = "opengisch/QField"


ComponentSpec = QFieldCloudSpec | QgisSpec | QFieldSyncSpec | QFieldAndroidSpec | QFieldIosSpec | QFieldDesktopSpec


class DeploymentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    description: str = ""
    qfieldcloud: QFieldCloudSpec | None = None
    qgis: QgisSpec | None = None
    qfieldsync: QFieldSyncSpec | None = None
    qfield_android: QFieldAndroidSpec | None = Field(default=None, alias="qfield-android")
    qfield_ios: QFieldIosSpec | None = Field(default=None, alias="qfield-ios")
    qfield_desktop: QFieldDesktopSpec | None = Field(default=None, alias="qfield-desktop")

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError("name must match [a-z][a-z0-9-]{0,19} (used in container and network names)")
        return value

    @model_validator(mode="after")
    def _dependencies(self) -> DeploymentSpec:
        if not self.components():
            raise ValueError("a deployment needs at least one component")
        if self.qfieldsync and not self.qgis:
            raise ValueError("`qfieldsync` requires `qgis`")
        clients = [n for n in ("qfieldsync", "qfield-android", "qfield-ios", "qfield-desktop") if self.component(n)]
        if clients and not self.qfieldcloud:
            raise ValueError(f"{', '.join(clients)} require(s) `qfieldcloud`")
        return self

    def component(self, name: str) -> ComponentSpec | None:
        return getattr(self, name.replace("-", "_"))  # type: ignore[no-any-return]

    def components(self) -> dict[str, ComponentSpec]:
        """Enabled components keyed by name, in start order."""
        return {name: spec for name in COMPONENT_NAMES if (spec := self.component(name)) is not None}

    # --- I/O --------------------------------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, text: str, *, overrides: dict[str, str] | None = None) -> DeploymentSpec:
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise SpecError(f"spec is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise SpecError("spec must be a YAML mapping")
        for dotted, value in (overrides or {}).items():
            _set_dotted(data, dotted, value)
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise SpecError(_format_validation_error(exc)) from exc

    @classmethod
    def from_file(cls, path: Path, *, overrides: dict[str, str] | None = None) -> DeploymentSpec:
        try:
            text = path.read_text()
        except OSError as exc:
            raise SpecError(f"cannot read spec {path}: {exc}") from exc
        return cls.from_yaml(text, overrides=overrides)

    def to_yaml(self) -> str:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        return yaml.safe_dump(data, sort_keys=False)


def _set_dotted(data: dict[str, Any], dotted: str, value: str) -> None:
    """Apply a `--set a.b=c` override; plain scalars are parsed as YAML so `true`/`3` keep their type."""
    *parents, leaf = dotted.split(".")
    node = data
    for key in parents:
        node = node.setdefault(key, {})
        if not isinstance(node, dict):
            raise SpecError(f"cannot set '{dotted}': '{key}' is not a mapping")
    node[leaf] = yaml.safe_load(value)


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["invalid spec:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


EXAMPLE_MINIMAL = """\
name: smoke
qfieldcloud:
  version: release:v26.28
qgis:
  version: host
qfieldsync:
  version: release:v4.23.1
"""

EXAMPLE_FULL = """\
name: demo
description: "QFieldSync fix #806 against QFieldCloud master"

qfieldcloud:
  version: worktree:~/src/github.com/opengisch/QFieldCloud   # or release:v26.28 | git:master | git:<sha>
  tls: false                 # plain-HTTP Django port is the client endpoint; true also starts nginx + mkcert
  worker_replicas: 1
  test_account:
    username: testbed
    password: auto           # or a literal password
    superuser: true
    demo_project: true       # upload the bundled sample project

qgis:
  version: host              # or docker:4.2.3 | docker:3.44.15 | docker:ltr
  plugins:
    - zip: https://plugins.qgis.org/plugins/qgis_swiss_locator/version/4.0.3/download/
    - path: ~/src/github.com/opengisch/qgis-swiss-locator/swiss_locator

qfieldsync:
  version: worktree:~/src/github.com/opengisch/qfieldsync-fix-cloud-rm-806   # or release:v4.23.1 | git:v4.22.3
  remember_me: true

qfield-android:
  version: release:v4.3.5    # or artifact:<ref> (CI artifact on `repo`) | build:/path/to/QField
  repo: opengisch/QField
  image: budtmo/docker-android:emulator_14.0
  device: "Samsung Galaxy S10"
  seed_settings: true

qfield-ios:
  version: build:/Users/me/QField/build-ios-simulator   # simulator .app built on the Mac
  host: mac-mini             # key in `ios_hosts` of ~/.config/qfield-testbed/config.yaml
  device: "iPhone 16"

qfield-desktop:
  version: release:v4.3.5    # Linux AppImage
"""
