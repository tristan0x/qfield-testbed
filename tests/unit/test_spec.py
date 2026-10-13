from __future__ import annotations

from pathlib import Path

import pytest

from qfield_testbed.errors import SpecError
from qfield_testbed.spec import (
    EXAMPLE_FULL,
    EXAMPLE_MINIMAL,
    DeploymentSpec,
    DockerSource,
    GitSource,
    HostSource,
    ReleaseSource,
    WorktreeSource,
    parse_version_source,
)


def test_minimal_example_is_valid() -> None:
    spec = DeploymentSpec.from_yaml(EXAMPLE_MINIMAL)
    assert spec.name == "smoke"
    assert list(spec.components()) == ["qfieldcloud", "qgis", "qfieldsync"]
    assert spec.qfieldcloud is not None
    assert spec.qfieldcloud.version == ReleaseSource(tag="v26.28")
    assert spec.qgis is not None
    assert spec.qgis.version == HostSource()


def test_full_example_is_valid_and_ordered() -> None:
    spec = DeploymentSpec.from_yaml(EXAMPLE_FULL)
    assert list(spec.components()) == [
        "qfieldcloud",
        "qgis",
        "qfieldsync",
        "qfield-android",
        "qfield-ios",
        "qfield-desktop",
    ]
    assert isinstance(spec.qfieldcloud.version, WorktreeSource)  # type: ignore[union-attr]
    assert spec.qfieldcloud.version.path.is_absolute()  # type: ignore[union-attr]
    assert spec.qfield_android.device == "Samsung Galaxy S10"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("shorthand", "expected"),
    [
        ("release:v26.28", {"kind": "release", "tag": "v26.28"}),
        ("git:master", {"kind": "git", "ref": "master"}),
        ("git:feature/x:y", {"kind": "git", "ref": "feature/x:y"}),
        ("worktree:/tmp/x", {"kind": "worktree", "path": "/tmp/x"}),
        ("artifact:abc123", {"kind": "artifact", "ref": "abc123"}),
        ("build:/src/QField", {"kind": "build", "path": "/src/QField"}),
        ("host", {"kind": "host"}),
        ("docker:3.44.15", {"kind": "docker", "tag": "3.44.15"}),
    ],
)
def test_shorthand(shorthand: str, expected: dict[str, str]) -> None:
    assert parse_version_source(shorthand) == expected


@pytest.mark.parametrize("bad", ["nope:v1", "release", "release:", "host:x"])
def test_shorthand_errors(bad: str) -> None:
    with pytest.raises(ValueError, match="version source"):
        parse_version_source(bad)


def test_mapping_form_accepted() -> None:
    spec = DeploymentSpec.from_yaml("name: a\nqfieldcloud:\n  version: {kind: git, ref: master}\n")
    assert spec.qfieldcloud.version == GitSource(ref="master")  # type: ignore[union-attr]


def test_qgis_only_accepts_host_or_docker() -> None:
    with pytest.raises(SpecError, match=r"qgis\.version"):
        DeploymentSpec.from_yaml("name: a\nqgis:\n  version: release:v1\n")
    spec = DeploymentSpec.from_yaml("name: a\nqgis:\n  version: docker:ltr\n")
    assert spec.qgis.version == DockerSource(tag="ltr")  # type: ignore[union-attr]


def test_qfieldsync_requires_qgis() -> None:
    with pytest.raises(SpecError, match="requires `qgis`"):
        DeploymentSpec.from_yaml(
            "name: a\nqfieldcloud: {version: release:v26.28}\nqfieldsync: {version: release:v4.23.1}\n"
        )


def test_clients_require_qfieldcloud() -> None:
    with pytest.raises(SpecError, match="require"):
        DeploymentSpec.from_yaml("name: a\nqgis: {version: host}\nqfieldsync: {version: release:v4.23.1}\n")


def test_empty_spec_rejected() -> None:
    with pytest.raises(SpecError, match="at least one component"):
        DeploymentSpec.from_yaml("name: a\n")


@pytest.mark.parametrize("name", ["Bad", "1abc", "a" * 21, "with_underscore", ""])
def test_invalid_names(name: str) -> None:
    with pytest.raises(SpecError, match="name"):
        DeploymentSpec.from_yaml(f"name: '{name}'\nqgis: {{version: host}}\n")


def test_unknown_keys_rejected() -> None:
    with pytest.raises(SpecError, match="Extra"):
        DeploymentSpec.from_yaml("name: a\nqgis: {version: host, colour: blue}\n")


def test_overrides_apply_before_validation() -> None:
    spec = DeploymentSpec.from_yaml(
        EXAMPLE_MINIMAL,
        overrides={"name": "other", "qfieldsync.version": "release:v4.22.3", "qfieldsync.remember_me": "false"},
    )
    assert spec.name == "other"
    assert spec.qfieldsync.version == ReleaseSource(tag="v4.22.3")  # type: ignore[union-attr]
    assert spec.qfieldsync.remember_me is False  # type: ignore[union-attr]


def test_roundtrip_yaml(tmp_path: Path) -> None:
    spec = DeploymentSpec.from_yaml(EXAMPLE_FULL)
    file = tmp_path / "spec.yaml"
    file.write_text(spec.to_yaml())
    again = DeploymentSpec.from_file(file)
    assert again == spec
    assert "qfield-android" in file.read_text()  # aliases survive the dump


def test_invalid_yaml_reports_spec_error(tmp_path: Path) -> None:
    file = tmp_path / "spec.yaml"
    file.write_text("name: [\n")
    with pytest.raises(SpecError, match="not valid YAML"):
        DeploymentSpec.from_file(file)
    with pytest.raises(SpecError, match="cannot read"):
        DeploymentSpec.from_file(tmp_path / "missing.yaml")


@pytest.mark.parametrize("platform", ["session", "xcb", "wayland", "offscreen"])
def test_qgis_platform_values(platform: str) -> None:
    spec = DeploymentSpec.from_yaml(f"name: p\nqgis: {{version: host, platform: {platform}}}\n")
    assert spec.qgis is not None
    assert spec.qgis.platform == platform


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("qgis: {version: 'docker:4.2.3', platform: wayland}", "needs `version: host`"),
        ("qgis: {version: host, platform: xcb, env: {QT_QPA_PLATFORM: wayland}}", "not both"),
        ("qgis: {version: host, platform: x11}", "platform"),
    ],
)
def test_qgis_platform_refusals(yaml_text: str, message: str) -> None:
    with pytest.raises(SpecError, match=message):
        DeploymentSpec.from_yaml(f"name: p\n{yaml_text}\n")


def test_qgis_platform_defaults_to_session_and_env_alone_is_fine() -> None:
    spec = DeploymentSpec.from_yaml("name: p\nqgis: {version: host, env: {QT_QPA_PLATFORM: xcb}}\n")
    assert spec.qgis is not None
    assert spec.qgis.platform == "session"
