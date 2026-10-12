from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from qfield_testbed.components.qfieldcloud import (
    QFieldCloud,
    compose_services,
    env_value,
    parse_env_blocks,
    patch_env,
    release_tuple,
    storages_with_endpoint,
)
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import TestbedError, UnsupportedComponentError
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec

FIXTURES = Path(__file__).parent.parent / "fixtures" / "qfieldcloud"
SHA = "0123456789abcdef0123456789abcdef01234567"


def fake_checkout(tmp_path: Path, version: str) -> Path:
    """A directory shaped like a QFieldCloud checkout of the given tag."""
    checkout = tmp_path / "checkouts" / version
    checkout.mkdir(parents=True)
    shutil.copy(FIXTURES / f"env.example.{version}", checkout / ".env.example")
    shutil.copy(FIXTURES / f"docker-compose.yml.{version}", checkout / "docker-compose.yml")
    shutil.copy(
        FIXTURES / f"docker-compose.override.standalone.yml.{version}",
        checkout / "docker-compose.override.standalone.yml",
    )
    return checkout


# --- pure helpers ----------------------------------------------------------------------------------------


def test_parse_env_blocks_keeps_multiline_storages() -> None:
    blocks = parse_env_blocks((FIXTURES / "env.example.v26.28").read_text())
    keys = [key for key, _ in blocks if key]
    assert "STORAGES" in keys
    storages = env_value(blocks, "STORAGES")
    assert storages is not None
    assert json.loads(storages)["default"]["OPTIONS"]["endpoint_url"] == "http://172.17.0.1:8009"
    assert env_value(blocks, "DJANGO_DEV_PORT") == "8011"
    assert env_value(blocks, "NOPE") is None


def test_patch_env_replaces_appends_and_drops() -> None:
    text = "A=1\n# comment\nB='x\ny'\nCOMPOSE_FILE=a:b\n"
    out = patch_env(text, {"A": "2", "NEW": "3"})
    assert "A=2\n" in out
    assert "B='x\ny'" in out
    assert "# COMPOSE_FILE is set by qfield-testbed" in out
    assert out.rstrip().endswith("NEW=3")


def test_storages_with_endpoint() -> None:
    raw = '{"default": {"BACKEND": "x", "OPTIONS": {"endpoint_url": "http://172.17.0.1:8009", "bucket_name": "b"}}, "webdav": {"OPTIONS": {}}}'
    out = storages_with_endpoint(raw, "http://172.17.0.1:20104")
    assert out.startswith("'")
    assert out.endswith("'")
    data = json.loads(out[1:-1])
    assert data["default"]["OPTIONS"]["endpoint_url"] == "http://172.17.0.1:20104"
    assert data["webdav"]["OPTIONS"] == {}


def test_release_tuple() -> None:
    assert release_tuple("v26.28") == (26, 28)
    with pytest.raises(TestbedError):
        release_tuple("26.28")


def test_compose_services_across_versions(tmp_path: Path) -> None:
    new = compose_services(fake_checkout(tmp_path, "v26.28"))
    old = compose_services(fake_checkout(tmp_path, "v26.1"))
    assert {"app", "worker_wrapper", "qgis3", "qgis4", "createbuckets", "rustfs", "nginx"} <= new
    assert {"app", "worker_wrapper", "qgis", "createbuckets", "minio"} <= old
    assert "qgis3" not in old


# --- component -------------------------------------------------------------------------------------------


@pytest.fixture
def deployment(testbed: Testbed, runner: FakeRunner, tmp_path: Path):
    runner.reply(["docker", "info"])
    runner.reply(["docker", "compose", "version"], stdout="5.6.0\n")
    runner.reply(
        ["git", "-C", str(testbed.paths.git_mirrors / "opengisch-QFieldCloud.git"), "rev-parse"], stdout=SHA + "\n"
    )
    runner.reply(
        ["git", "-C", str(testbed.paths.git_mirrors / "opengisch-QFieldCloud.git"), "tag"], stdout="v26.27\nv26.28\n"
    )
    runner.reply(["docker", "image", "inspect"], returncode=1)  # nothing cached locally
    spec = DeploymentSpec.from_yaml("name: demo\nqfieldcloud: {version: release:v26.28}\n")
    return testbed.add(spec)


def _component(deployment) -> QFieldCloud:
    component = deployment.components("qfieldcloud")[0]
    assert isinstance(component, QFieldCloud)
    return component


def test_resolve_release(deployment, runner: FakeRunner) -> None:
    revision = _component(deployment).resolve()
    assert revision.label == "v26.28"
    assert revision.sha == SHA
    assert revision.key == "v26.28"
    assert any("git clone --mirror" in cmd for cmd in runner.commands())


def test_release_images_and_pull(deployment, runner: FakeRunner, tmp_path: Path) -> None:
    component = _component(deployment)
    revision = component.resolve()
    checkout = fake_checkout(tmp_path, "v26.28")
    # the mirror's `worktree add` is faked: pretend the checkout already exists at the cache path
    target = component.mirror.checkouts / SHA
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(checkout, target)
    (target / ".git").write_text("gitdir: fake\n")

    materialized = component.materialize(revision)
    assert materialized == target
    images = component.images(revision, materialized)
    assert images["app"] == "ghcr.io/opengisch/qfieldcloud-app:26.28"
    assert images["worker_wrapper"] == "ghcr.io/opengisch/qfieldcloud-worker-wrapper:26.28"
    assert set(images) == {"app", "worker_wrapper", "createbuckets", "qgis3", "qgis4"}
    pulled = [cmd for cmd in runner.commands() if cmd.startswith("docker pull")]
    assert len(pulled) == 5
    assert deployment.testbed.cache.load().entries["qfieldcloud/images/v26.28"].users == {"demo"}


def test_render_env_and_override(deployment, runner: FakeRunner, tmp_path: Path) -> None:
    component = _component(deployment)
    revision = component.resolve()
    checkout = fake_checkout(tmp_path, "v26.28")
    component.render(revision, checkout)

    env = (component.directory / ".env").read_text()
    blocks = parse_env_blocks(env)
    base = deployment.ports.base
    assert env_value(blocks, "COMPOSE_PROJECT_NAME") == "qft-demo-qfc"
    assert env_value(blocks, "QFIELDCLOUD_DEFAULT_NETWORK") == "qft-demo"
    assert env_value(blocks, "DJANGO_DEV_PORT") == str(base)
    assert env_value(blocks, "OBJECT_STORAGE_API_PORT") == str(base + 4)
    assert env_value(blocks, "SMTP4DEV_SMTP_PORT") == str(base + 7)
    storages = env_value(blocks, "STORAGES")
    assert storages
    assert json.loads(storages)["default"]["OPTIONS"]["endpoint_url"] == f"http://172.17.0.1:{base + 4}"
    assert env_value(blocks, "QFIELDCLOUD_QGIS3_IMAGE_NAME") == "ghcr.io/opengisch/qfieldcloud-qgis3:26.28"
    assert env_value(blocks, "SENTRY_RELEASE") == "v26.28"
    assert env_value(blocks, "ENVIRONMENT") == "qft-demo"  # isolates the worker's container label
    assert env_value(blocks, "QFIELDCLOUD_WORKER_DEQUEUE_INTERVAL_S") == "1"
    assert env_value(blocks, "COMPOSE_FILE") is None
    assert env_value(blocks, "SECRET_KEY") != "change_me"
    assert not env_value(blocks, "DEBUG_QGIS_WORKER_HOST_PATH")  # only set for worktree sources

    override = yaml.safe_load((component.directory / "compose.override.yml").read_text())
    assert override["services"]["app"]["ports"] == [f"127.0.0.1:{base}:8000"]
    assert override["services"]["app"]["image"] == "ghcr.io/opengisch/qfieldcloud-app:26.28"
    assert "command" not in override["services"]["app"]
    assert override["services"]["nginx"]["profiles"] == ["tls"]
    assert override["services"]["mirror_transformation_grids"]["profiles"] == ["grids"]
    assert override["networks"]["default"]["name"] == "qft-demo"
    assert component.state.facts["checkout"] == str(checkout)


def test_render_old_release_uses_minio_keys(deployment, tmp_path: Path) -> None:
    component = _component(deployment)
    revision = component.resolve()
    checkout = fake_checkout(tmp_path, "v26.1")
    component.render(revision, checkout)
    blocks = parse_env_blocks((component.directory / ".env").read_text())
    assert env_value(blocks, "MINIO_API_PORT") == str(deployment.ports.base + 4)
    assert env_value(blocks, "OBJECT_STORAGE_API_PORT") is None
    assert env_value(blocks, "QFIELDCLOUD_QGIS_IMAGE_NAME") == "ghcr.io/opengisch/qfieldcloud-qgis:26.28"
    override = yaml.safe_load((component.directory / "compose.override.yml").read_text())
    assert "qgis" in override["services"]
    assert "qgis3" not in override["services"]


def test_worktree_render_enables_live_reload(testbed: Testbed, runner: FakeRunner, tmp_path: Path) -> None:
    runner.reply(["docker", "info"])
    runner.reply(["docker", "compose", "version"], stdout="5.6.0\n")
    checkout = fake_checkout(tmp_path, "v26.28")
    runner.reply(["git", "-C", str(checkout), "rev-parse", "HEAD"], stdout=SHA + "\n")
    runner.reply(
        ["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=no"], stdout=" M docker-app/x.py\n"
    )
    runner.reply(
        ["git", "-C", str(checkout), "status", "--porcelain", "--", "docker-app"], stdout=" M docker-app/x.py\n"
    )
    runner.reply(["git", "-C", str(checkout), "status", "--porcelain", "--", "docker-qgis"], stdout="")
    runner.reply(
        ["git", "-C", str(checkout), "rev-parse", "HEAD:docker-qgis"],
        stdout="feedfacefeedfacefeedfacefeedfacefeedface\n",
    )
    runner.reply(
        ["git", "-C", str(checkout), "rev-parse", "HEAD:docker-createbuckets"],
        stdout="cafebabecafebabecafebabecafebabecafebabe\n",
    )
    spec = DeploymentSpec.from_yaml(f"name: wt\nqfieldcloud: {{version: worktree:{checkout}}}\n")
    deployment = testbed.add(spec)
    component = _component(deployment)
    revision = component.resolve()
    assert revision.dirty
    assert revision.label == f"worktree@{SHA[:12]}"
    assert component.materialize(revision) == checkout
    component.render(revision, checkout)
    images = component.state.facts["images"]
    assert images["app"] == "qft-qfieldcloud-app:wt-wt"  # dirty context → per-deployment tag
    assert images["qgis3"] == "qft-qfieldcloud-qgis3:feedfacefeed"  # clean context → shared tree-sha tag
    override = yaml.safe_load((component.directory / "compose.override.yml").read_text())
    assert override["services"]["app"]["command"].startswith("python3 manage.py runserver")
    assert any(v.startswith(f"{checkout}/docker-app/qfieldcloud:") for v in override["services"]["app"]["volumes"])
    env = (component.directory / ".env").read_text()
    assert f"DEBUG_QGIS_WORKER_HOST_PATH={checkout}/docker-qgis" in env


def test_start_builds_missing_images_then_initializes(deployment, runner: FakeRunner, tmp_path: Path) -> None:
    component = _component(deployment)
    revision = component.resolve()
    component.render(revision, fake_checkout(tmp_path, "v26.28"))
    component.start()
    commands = runner.commands()
    assert not any(" build " in cmd for cmd in commands)  # release: images are pulled, never built
    up = next(cmd for cmd in commands if " up -d " in cmd)
    assert "-p qft-demo-qfc" in up
    assert "--env-file" in up
    assert "compose.override.yml" in up
    assert any("manage.py migrate" in cmd for cmd in commands)
    createuser = next(cmd for cmd in commands if "createuser" in cmd)
    assert "--username=testbed" in createuser
    assert "--superuser" in createuser
    assert component.state.initialized


def test_old_release_refused(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["docker", "info"])
    runner.reply(["docker", "compose", "version"], stdout="5.6.0\n")
    with pytest.raises(UnsupportedComponentError, match="oldest supported"):
        testbed.add(DeploymentSpec.from_yaml("name: old\nqfieldcloud: {version: release:v25.9}\n"))


def test_describe_and_env(deployment) -> None:
    component = _component(deployment)
    info = component.describe()
    assert info["url"] == f"http://127.0.0.1:{deployment.ports.base}/"
    assert info["url_internal"] == "http://app:8000/"
    env = component.env()
    assert env["QFT_QFIELDCLOUD_URL"] == info["url"]
    assert "QFT_QFIELDCLOUD_IMAGES" not in env
