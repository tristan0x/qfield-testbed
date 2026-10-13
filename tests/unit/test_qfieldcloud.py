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
from qfield_testbed.deployment import Deployment, Testbed
from qfield_testbed.errors import TestbedError, UnsupportedComponentError
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import Completed, FakeRunner
from qfield_testbed.spec import DeploymentSpec, GitSource, WorktreeSource

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


def test_parse_env_blocks_unterminated_quote_runs_to_the_end() -> None:
    blocks = parse_env_blocks("A=1\nB='open\nstill open\n")
    assert blocks == [("A", "A=1"), ("B", "B='open\nstill open")]
    assert patch_env("A=1\n", {"A": "2"}) == "A=2\n"  # nothing to append: no trailer


def test_compose_services_tolerates_missing_files(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text("services:\n  app: {}\n")
    assert compose_services(tmp_path) == {"app"}
    (tmp_path / "docker-compose.override.standalone.yml").write_text("")
    assert compose_services(tmp_path) == {"app"}


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ((1, 0, True), ["docker daemon not reachable"]),
        ((0, 1, True), ["docker compose plugin missing"]),
        ((0, 0, False), ["git missing"]),
        ((0, 0, True), []),
    ],
    ids=["no-daemon", "no-compose", "no-git", "fine"],
)
def test_check_host(testbed: Testbed, runner: FakeRunner, host: tuple[int, int, bool], expected: list[str]) -> None:
    info, compose, git = host
    runner.reply(["docker", "info"], returncode=info)
    runner.reply(["docker", "compose", "version"], returncode=compose, stdout="5.6.0\n")
    if not git:
        runner.available.discard("git")
    spec = DeploymentSpec.from_yaml("name: demo\nqfieldcloud: {version: git:master}\n")
    component = Deployment.probe(testbed, spec, "qfieldcloud", spec.qfieldcloud)
    assert component.check_host() == expected


def test_compose_before_render_is_refused(deployment) -> None:
    component = _component(deployment)
    with pytest.raises(TestbedError, match="not rendered yet"):
        component.compose()
    # tearing down a never-rendered component is a no-op
    component.stop()
    component.remove()
    component.state.initialized = True
    component.state.facts["demo_project_id"] = "p"
    component.reset()
    assert not component.state.initialized
    assert "demo_project_id" not in component.state.facts


def test_render_refuses_a_non_checkout(deployment, tmp_path: Path) -> None:
    component = _component(deployment)
    with pytest.raises(TestbedError, match=r"has no .env.example"):
        component.render(component.resolve(), tmp_path)


def test_materialize_git_ref_pulls_nothing_and_cached_release_images_are_kept(
    deployment, runner: FakeRunner, tmp_path: Path
) -> None:
    component = _component(deployment)
    target = component.mirror.checkouts / SHA
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fake_checkout(tmp_path, "v26.28"), target)
    (target / ".git").write_text("gitdir: fake\n")
    git = Revision(source=GitSource(ref="master"), label=f"master@{SHA[:12]}", sha=SHA)
    assert component.materialize(git) == target
    assert not any(cmd.startswith(("docker pull", "docker image inspect")) for cmd in runner.commands())
    runner.responses.insert(0, (("docker", "image", "inspect"), Completed((), 0)))  # every image already local
    component.materialize(component.resolve())
    assert not any(cmd.startswith("docker pull") for cmd in runner.commands())
    assert "qfieldcloud/images/v26.28" in deployment.testbed.cache.load().entries


def test_render_clean_worktree_and_env_without_storages(testbed: Testbed, runner: FakeRunner, tmp_path: Path) -> None:
    checkout = fake_checkout(tmp_path, "v26.28")
    (checkout / ".env.example").write_text("DJANGO_DEV_PORT=8011\n")
    runner.reply(["git", "-C", str(checkout), "rev-parse", "HEAD:docker-app"], stdout="a" * 40 + "\n")
    # docker-qgis and docker-createbuckets: no tree sha (e.g. untracked) → per-deployment tag
    spec = DeploymentSpec.from_yaml(f"name: wt\nqfieldcloud: {{version: worktree:{checkout}}}\n")
    component = Deployment.probe(testbed, spec, "qfieldcloud", spec.qfieldcloud)
    assert isinstance(component, QFieldCloud)
    revision = Revision(source=WorktreeSource(path=checkout), label="worktree@x", sha=SHA)
    component.render(revision, checkout)
    images = component.state.facts["images"]
    assert images["app"] == "qft-qfieldcloud-app:aaaaaaaaaaaa"
    assert images["qgis3"] == "qft-qfieldcloud-qgis3:wt-wt"
    env = (component.directory / ".env").read_text()
    assert "STORAGES" not in env
    assert "DJANGO_DEV_PORT=0\n" in env


def test_start_builds_local_images_and_skips_initialized(deployment, runner: FakeRunner, tmp_path: Path) -> None:
    component = _component(deployment)
    component.state.facts.update(
        {
            "checkout": str(tmp_path),
            "images": {
                "app": "qft-qfieldcloud-app:wt-demo",  # dirty worktree: always rebuilt
                "qgis3": "qft-qfieldcloud-qgis3:feedfacefeed",  # missing locally
                "createbuckets": "qft-qfieldcloud-createbuckets:cafebabecafe",  # already built
            },
        }
    )
    runner.responses.insert(
        0, (("docker", "image", "inspect", "qft-qfieldcloud-createbuckets:cafebabecafe"), Completed((), 0))
    )
    component.state.initialized = True
    component.start()
    commands = runner.commands()
    build = next(cmd for cmd in commands if " build " in cmd)
    assert build.endswith(" build app qgis3")
    assert any(" up -d " in cmd for cmd in commands)
    assert not any("manage.py" in cmd for cmd in commands)


def test_initialize_needs_credentials_and_honours_superuser(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["docker", "info"])
    runner.reply(["docker", "compose", "version"], stdout="5.6.0\n")
    spec = DeploymentSpec.from_yaml(
        "name: demo\nqfieldcloud:\n  version: git:master\n  test_account: {superuser: false, password: s3cret}\n"
    )
    component = _component(testbed.add(spec))
    component.state.facts["checkout"] = "/x"
    compose = component.compose()
    component.initialize(compose)
    createuser = next(cmd for cmd in runner.commands() if "createuser" in cmd)
    assert "--password=s3cret" in createuser
    assert "--superuser" not in createuser
    component.ctx.state.credentials = None
    with pytest.raises(TestbedError, match="no test account credentials"):
        component.initialize(compose)


def test_health_and_running_version(deployment, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    from qfield_testbed.provision import qfieldcloud_account  # noqa: PLC0415

    component = _component(deployment)
    waited: list[str] = []
    monkeypatch.setattr(
        "qfield_testbed.components.qfieldcloud.wait_for_http", lambda url, timeout: waited.append(f"{url} {timeout}")
    )
    monkeypatch.setattr(qfieldcloud_account, "server_info", lambda _: {"version": "26.28.0"})
    component.health(timeout=5)
    assert waited == [f"{component.url}api/v1/status/ 5"]
    assert component.state.facts["version"] == "26.28.0"

    # older servers do not report it: read SENTRY_RELEASE from the app container
    monkeypatch.setattr(qfieldcloud_account, "server_info", lambda _: {})
    runner.reply(["docker", "inspect", "qft-demo-qfc-app-1"], stdout="PATH=/usr/bin\nSENTRY_RELEASE=v26.28\n")
    assert component.running_version() == "v26.28"
    runner.responses.insert(0, (("docker", "inspect"), Completed((), 0, "SENTRY_RELEASE=\n")))
    assert component.running_version() is None
    runner.responses.insert(0, (("docker", "inspect"), Completed((), 1)))
    assert component.running_version() is None


def test_wire_logs_in_and_uploads_demo_project(deployment, monkeypatch: pytest.MonkeyPatch) -> None:
    from qfield_testbed.provision import qfieldcloud_account  # noqa: PLC0415

    component = _component(deployment)
    credentials = deployment.state.credentials
    assert credentials is not None
    logins: list[tuple[str, str, str]] = []

    def login(url: str, username: str, password: str) -> str:
        logins.append((url, username, password))
        return "tok"

    monkeypatch.setattr(qfieldcloud_account, "login", login)
    monkeypatch.setattr(qfieldcloud_account, "ensure_demo_project", lambda *args: ("pid", f"finished:{args[1:]}"))
    component.wire()
    assert logins == [(component.url, "testbed", credentials.password)]
    assert credentials.token == "tok"
    assert component.state.facts["demo_project_id"] == "pid"
    assert component.state.facts["demo_project_status"] == "finished:('tok', 'testbed')"
    deployment.state.credentials = None
    component.state.facts.clear()
    component.wire()  # nothing to log in with
    assert component.state.facts == {}


def test_stop_reset_remove_logs(deployment, runner: FakeRunner, tmp_path: Path) -> None:
    component = _component(deployment)
    component.state.facts.update(
        {
            "checkout": str(tmp_path),
            "images": {"app": "qft-qfieldcloud-app:wt-demo", "qgis3": "ghcr.io/opengisch/qfieldcloud-qgis3:26.28"},
        }
    )
    component.state.initialized = True
    component.stop()
    component.reset()
    component.remove()
    component.logs()
    compose = [cmd.split("compose.override.yml ", 1)[1] for cmd in runner.commands() if "compose -p" in cmd]
    assert compose == ["stop", "down --remove-orphans -v", "down --remove-orphans -v", "logs --tail 200"]
    assert "docker image rm qft-qfieldcloud-app:wt-demo" in runner.commands()
    assert not any("image rm ghcr.io" in cmd for cmd in runner.commands())
    assert not component.state.initialized
