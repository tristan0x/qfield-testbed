from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from qfield_testbed.components import REGISTRY, register
from qfield_testbed.components.qfield_android import QFieldAndroid
from qfield_testbed.deployment import Deployment, Testbed
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError, UnsupportedComponentError
from qfield_testbed.provision.android_settings import merge_qfieldcloud_section
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import ArtifactSource, BuildSource, DeploymentSpec, GitSource, ReleaseSource
from qfield_testbed.state import Credentials
from tests.unit.test_deployment import FakeQgis

SHA = "9b93f81fc6b13d44b6430afcca8bed0a4d007090"


def test_merge_qfieldcloud_section_keeps_other_keys() -> None:
    existing = "[General]\nfoo=bar\n\n[QFieldCloud]\nurl=https://app.qfield.cloud\nprovider=x\n"
    out = merge_qfieldcloud_section(existing, "http://10.0.2.2:8000/", "testbed")
    assert "foo=bar" in out
    assert "provider=x" in out
    assert "url=http://10.0.2.2:8000/" in out
    assert "username=testbed" in out
    assert "urls=http://10.0.2.2:8000/" in out
    fresh = merge_qfieldcloud_section("", "http://10.0.2.2:8000/", "u")
    assert fresh.startswith("[QFieldCloud]")


@pytest.fixture
def cloud_stub():
    class FakeCloud(FakeQgis):
        name = "qfieldcloud"

        def describe(self) -> dict[str, object]:
            return {"url": "http://127.0.0.1:30000/", "url_internal": "http://app:8000/"}

    saved = REGISTRY.get("qfieldcloud")
    register(FakeCloud)
    yield
    if saved is not None:
        REGISTRY["qfieldcloud"] = saved


@pytest.fixture
def android_runner(runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    kvm = tmp_path / "kvm"
    kvm.write_text("")
    monkeypatch.setattr("qfield_testbed.components.qfield_android.KVM_DEVICE", kvm)
    runner.reply(["docker", "info"])
    runner.reply(["docker", "network", "inspect"], returncode=1)  # network not created yet
    runner.reply(["gh", "api", "repos/opengisch/QField/commits/v4.3.5"], stdout=SHA + "\n")
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "wait-for-device", "shell", "getprop"], stdout="1\n")
    runner.reply(
        ["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "dumpsys window | grep -i mCurrentFocus"],
        stdout="mCurrentFocus=Window{Launcher}\n",
    )
    runner.reply(
        [
            "docker",
            "exec",
            "qft-a-tb-android-1",
            "adb",
            "shell",
            "dumpsys package ch.opengis.qfield | grep versionName",
        ],
        stdout="    versionName=4.3.5\n",
    )
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "id"], stdout="uid=0(root)\n")
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "su root sh -c 'find"], stdout="")
    runner.reply(["docker", "exec", "qft-a-tb-android-1", "adb", "shell", "su root sh -c 'stat"], stdout="u0_a150\n")
    return runner


@pytest.mark.usefixtures("cloud_stub")
def test_release_apk_is_cached_installed_and_seeded(
    testbed: Testbed, android_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    import qfield_testbed.provision.android_settings as mod  # noqa: PLC0415

    monkeypatch.setattr(
        mod, "seed_qfield_settings", lambda adb, url, username: mod.DEFAULT_CONF if adb.root() else None
    )
    testbed.paths.ensure()
    # the APK is already in the cache: no gh download
    apk_dir = testbed.paths.artifacts / "qfield" / "v4.3.5"
    apk_dir.mkdir(parents=True)
    (apk_dir / "qfield-android-x64.apk").write_bytes(b"PK\x03\x04fake")
    spec = DeploymentSpec.from_yaml(
        'name: a\nqfieldcloud: {version: release:v26.28}\nqfield-android:\n  version: release:v4.3.5\n  device: "Nexus 5"\n'
    )
    deployment = testbed.add(spec)
    deployment.state.credentials = Credentials(username="testbed", password="pw")
    deployment.save()
    deployment.start()

    component = deployment.components("qfield-android")[0]
    assert isinstance(component, QFieldAndroid)
    compose = yaml.safe_load((component.directory / "compose.yml").read_text())
    android = compose["services"]["android"]
    assert android["image"] == "budtmo/docker-android:emulator_14.0"
    assert android["environment"]["EMULATOR_DEVICE"] == "Nexus 5"
    assert android["devices"] == ["/dev/kvm"]
    assert f"127.0.0.1:{deployment.ports.port('novnc')}:6080" in android["ports"]
    assert compose["services"]["qfc-proxy"]["network_mode"] == "service:android"
    assert "TCP:app:8000" in compose["services"]["qfc-proxy"]["command"]
    assert compose["networks"]["default"] == {"name": "qft-a", "external": True}

    commands = android_runner.commands()
    assert any(cmd.startswith("docker network create qft-a") for cmd in commands)
    assert any("compose -p qft-a-tb" in cmd and " up -d" in cmd for cmd in commands)
    assert any(cmd.startswith("docker cp ") and cmd.endswith("qft-a-tb-android-1:/tmp/qfield.apk") for cmd in commands)
    assert any("adb install -r -g /tmp/qfield.apk" in cmd for cmd in commands)
    assert not any(cmd.startswith("gh release download") for cmd in commands)

    info = deployment.describe()["components"]["qfield-android"]
    assert info["qfieldcloud_url"] == "http://10.0.2.2:8000/"
    assert info["novnc_url"].startswith(f"http://127.0.0.1:{deployment.ports.port('novnc')}/")
    assert info["adb"] == "docker exec qft-a-tb-android-1 adb"
    assert info["installed_version"] == "4.3.5"
    assert info["settings_file"].endswith("OPENGIS.ch/QField.conf")
    env = deployment.env()
    assert env["QFT_QFIELD_ANDROID_ADB"] == "docker exec qft-a-tb-android-1 adb"
    assert env["QFT_QFIELD_ANDROID_QFIELDCLOUD_URL"] == "http://10.0.2.2:8000/"
    assert (deployment.directory / "bin" / "android").is_file()

    # a second start does not reinstall the same APK
    before = len([c for c in android_runner.commands() if "adb install" in c])
    deployment.start()
    assert len([c for c in android_runner.commands() if "adb install" in c]) == before


@pytest.mark.usefixtures("cloud_stub")
def test_android_requires_kvm(testbed: Testbed, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    runner.reply(["docker", "info"])
    monkeypatch.setattr("qfield_testbed.components.qfield_android.KVM_DEVICE", Path("/nonexistent/kvm"))
    with pytest.raises(UnsupportedComponentError, match="kvm"):
        testbed.add(
            DeploymentSpec.from_yaml(
                "name: a\nqfieldcloud: {version: release:v26.28}\nqfield-android: {version: release:v4.3.5}\n"
            )
        )


# --- component steps, one at a time ----------------------------------------------------------------------

BASE_SPEC = "name: a\nqfieldcloud: {version: release:v26.28}\n"
CONTAINER = "qft-a-tb-android-1"


def _android(testbed: Testbed, version: str, extra: str = "") -> QFieldAndroid:
    """A component instance outside any deployment lifecycle (no host checks)."""
    testbed.paths.ensure()
    spec = DeploymentSpec.from_yaml(f"{BASE_SPEC}qfield-android:\n  version: {version}\n{extra}")
    component = Deployment.probe(testbed, spec, "qfield-android", spec.components()["qfield-android"])
    assert isinstance(component, QFieldAndroid)
    return component


def _fake_qfield_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "QField"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts" / "build.sh").write_text("#!/bin/sh\n")
    return checkout


def test_check_host_reports_every_problem(
    testbed: Testbed, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.reply(["docker", "info"], returncode=1)
    runner.available.discard("gh")
    monkeypatch.setattr("qfield_testbed.components.qfield_android.KVM_DEVICE", Path("/nonexistent/kvm"))
    assert _android(testbed, "release:v4.3.5").check_host() == [
        "docker daemon not reachable",
        "/nonexistent/kvm missing: the emulator needs hardware virtualization",
        "gh missing (needed to download releases and CI artifacts)",
    ]
    assert (
        "gh missing (needed to download releases and CI artifacts)" in _android(testbed, "artifact:main").check_host()
    )


def test_check_host_build_source(
    testbed: Testbed, android_runner: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    android_runner.available.discard("gh")  # not needed for a local build
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(
        "qfield_testbed.components.qfield_android.shutil.disk_usage", lambda _: usage._replace(free=3 * 2**30)
    )
    not_a_checkout = tmp_path / "empty"
    not_a_checkout.mkdir()
    assert _android(testbed, f"build:{not_a_checkout}").check_host() == [
        f"{not_a_checkout} is not a QField checkout (no scripts/build.sh)",
        "building QField needs at least 15 GiB free, 3 available",
    ]
    monkeypatch.setattr(
        "qfield_testbed.components.qfield_android.shutil.disk_usage", lambda _: usage._replace(free=100 * 2**30)
    )
    assert _android(testbed, f"build:{_fake_qfield_checkout(tmp_path)}").check_host() == []


def test_commit_sha_rejects_failures_and_garbage(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["gh", "api", "repos/opengisch/QField/commits/missing"], returncode=1, stdout=SHA)
    runner.reply(["gh", "api", "repos/opengisch/QField/commits/short"], stdout="abc\n")
    runner.reply(["gh", "api", "repos/opengisch/QField/commits/main"], stdout=SHA + "\n")
    component = _android(testbed, "release:v4.3.5")
    assert component.commit_sha("missing") is None
    assert component.commit_sha("short") is None
    assert component.commit_sha("main") == SHA


def test_resolve_release_artifact_and_build(testbed: Testbed, android_runner: FakeRunner, tmp_path: Path) -> None:
    android_runner.reply(["gh", "api", "repos/opengisch/QField/commits/main"], stdout=SHA + "\n")
    android_runner.reply(["gh", "api", f"repos/opengisch/QField/commits/{SHA[:8]}"], stdout=SHA + "\n")
    release = _android(testbed, "release:v4.3.5").resolve()
    assert (release.label, release.sha) == ("v4.3.5", SHA)
    branch = _android(testbed, "artifact:main").resolve()
    assert (branch.label, branch.sha) == (f"main@{SHA[:12]}", SHA)
    assert _android(testbed, f"artifact:{SHA[:8]}").resolve().label == SHA[:8]
    checkout = _fake_qfield_checkout(tmp_path)
    android_runner.reply(["git", "-C", str(checkout), "rev-parse", "HEAD"], stdout=SHA + "\n")
    android_runner.reply(["git", "-C", str(checkout), "status"], stdout=" M src/core/foo.cpp\n")
    build = _android(testbed, f"build:{checkout}").resolve()
    assert (build.label, build.sha, build.dirty) == (f"build@{SHA[:12]}", SHA, True)
    assert build.key == f"build-{SHA[:12]}-dirty"


def test_resolve_unknown_artifact_ref(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["gh", "api", "repos/opengisch/QField/commits/nope"], returncode=1)
    with pytest.raises(TestbedError, match="unknown ref 'nope' in opengisch/QField"):
        _android(testbed, "artifact:nope", "  repo: https://github.com/opengisch/QField.git\n").resolve()


def test_unsupported_version_source(testbed: Testbed) -> None:
    component = _android(testbed, "release:v4.3.5")
    git = GitSource(ref="master")
    component.spec = component.spec.model_copy(update={"version": git})
    with pytest.raises(TestbedError, match="unsupported version source for qfield-android: git:master"):
        component.resolve()
    with pytest.raises(TestbedError, match="unsupported version source"):
        component.materialize(Revision(source=git, label="master"))


def test_materialize_release_downloads_and_caches(testbed: Testbed, runner: FakeRunner) -> None:
    component = _android(testbed, "release:v4.3.5")
    target = testbed.paths.artifacts / "qfield" / "v4.3.5"
    target.mkdir(parents=True)
    (target / "qfield-v4.3.5-android-x64.apk").write_bytes(b"PK apk")  # what `gh release download` leaves
    apk = component.materialize(Revision(source=ReleaseSource(tag="v4.3.5"), label="v4.3.5"))
    assert apk == target / "qfield-android-x64.apk"
    assert apk.read_bytes() == b"PK apk"
    assert not (target / "qfield-v4.3.5-android-x64.apk").exists()
    download = next(cmd for cmd in runner.commands() if cmd.startswith("gh release download"))
    assert "v4.3.5 --repo opengisch/QField --pattern qfield-*-android-x64.apk" in download
    assert testbed.cache.load().entries["qfield/apk/v4.3.5"].users == {"a"}


def test_materialize_artifact(testbed: Testbed, runner: FakeRunner) -> None:
    component = _android(testbed, "artifact:main")
    revision = Revision(source=ArtifactSource(ref="main"), label=f"main@{SHA[:12]}", sha=SHA)
    runs = ["gh", "api", "repos/opengisch/QField/actions/workflows/android.yml/runs"]

    runner.reply(runs, returncode=1)
    with pytest.raises(MaterializationUnavailableError, match=r"no successful android.yml run for main@"):
        component.materialize(revision)

    runner.responses.clear()
    runner.reply(runs, stdout=json.dumps({"workflow_runs": [{"id": 77}]}))
    with pytest.raises(MaterializationUnavailableError, match="artifact of run 77 contains no APK"):
        component.materialize(revision)
    assert any(cmd.startswith("gh run download 77 --repo opengisch/QField") for cmd in runner.commands())

    target = testbed.paths.artifacts / "qfield" / revision.key
    built = target / "artifact" / "qfield-dev-android-x64" / "qfield-dev.apk"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"PK from CI")
    apk = component.materialize(revision)
    assert apk.read_bytes() == b"PK from CI"
    assert f"qfield/apk/{revision.key}" in testbed.cache.load().entries


def test_materialize_build_copies_newest_apk(testbed: Testbed, runner: FakeRunner, tmp_path: Path) -> None:
    checkout = _fake_qfield_checkout(tmp_path)
    component = _android(testbed, f"build:{checkout}")
    revision = Revision(source=BuildSource(path=checkout), label=f"build@{SHA[:12]}", sha=SHA, dirty=True)
    with pytest.raises(MaterializationUnavailableError, match=r"scripts/build.sh produced no APK"):
        component.materialize(revision)
    assert runner.calls[-1].args == ("./scripts/build.sh",)
    assert (testbed.paths.cache / "qfield-build").is_dir()

    out = checkout / "build-docker" / "out"
    out.mkdir(parents=True)
    old, new = out / "old.apk", out / "sub" / "new.apk"
    new.parent.mkdir()
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    os.utime(old, (1, 1))
    apk = component.materialize(revision)
    assert apk.read_bytes() == b"new"
    # a dirty revision is rebuilt even when the APK is already there
    new.write_bytes(b"newer")
    assert component.materialize(revision).read_bytes() == b"newer"
    assert len([c for c in runner.commands() if c == "./scripts/build.sh"]) == 3


def test_render_writes_compose_launcher_and_facts(testbed: Testbed, tmp_path: Path) -> None:
    component = _android(testbed, "release:v4.3.5")
    apk = tmp_path / "q.apk"
    apk.write_bytes(b"abc")
    component.render(Revision(source=ReleaseSource(tag="v4.3.5"), label="v4.3.5"), apk)
    facts = component.state.facts
    assert facts["apk_sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert facts["container"] == CONTAINER
    assert facts["device"] == "Samsung Galaxy S10"
    launcher = Path(facts["launcher"])
    assert os.access(launcher, os.X_OK)
    assert "?autoconnect=true" in launcher.read_text()
    assert (component.directory / "compose.yml").is_file()


def test_start_reinstalls_when_the_apk_changed(testbed: Testbed, android_runner: FakeRunner, tmp_path: Path) -> None:
    component = _android(testbed, "release:v4.3.5")
    apk = tmp_path / "q.apk"
    apk.write_bytes(b"new build")
    component.state.facts.update({"apk": str(apk), "apk_sha256": "new", "installed_sha256": "old"})
    component.start()
    assert any("adb install -r -g /tmp/qfield.apk" in cmd for cmd in android_runner.commands())
    assert component.state.facts["installed_sha256"] == "new"
    assert component.state.facts["installed_version"] == "4.3.5"


def test_wire_variants(testbed: Testbed, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import qfield_testbed.provision.android_settings as mod  # noqa: PLC0415

    component = _android(testbed, "release:v4.3.5", "  seed_settings: false\n")
    component.wire()  # no credentials yet: nothing to point at
    assert "qfieldcloud_url" not in component.state.facts
    component.ctx.state.credentials = Credentials(username="testbed", password="pw")
    component.wire()
    assert component.state.facts == {"qfieldcloud_url": "http://10.0.2.2:8000/"}

    def refuse(*_: object) -> str:
        raise TestbedError("no su")

    monkeypatch.setattr(mod, "seed_qfield_settings", refuse)
    seeding = _android(testbed, "release:v4.3.5")
    seeding.ctx.state.credentials = Credentials(username="testbed", password="pw")
    seeding.wire()
    assert seeding.state.facts["settings_file"] is None
    assert "could not seed QField settings: no su" in caplog.text
    assert "http://10.0.2.2:8000/" in caplog.text  # the manual steps are logged instead


def test_stop_reset_remove_logs_health(testbed: Testbed, android_runner: FakeRunner) -> None:
    component = _android(testbed, "release:v4.3.5")
    component.state.facts["installed_sha256"] = "x"
    component.health(timeout=5)
    component.stop()
    component.reset()
    assert "installed_sha256" not in component.state.facts
    component.remove()
    component.logs(follow=True)
    commands = [c for c in android_runner.commands() if c.startswith("docker compose -p qft-a-tb")]
    assert [c.split(" -f ")[-1].split(" ", 1)[1] for c in commands] == [
        "stop",
        "down --remove-orphans -v",
        "down --remove-orphans -v",
        "logs --tail 200 -f",
    ]
    assert any("getprop sys.boot_completed" in c for c in android_runner.commands())


def test_describe_defaults_before_start(testbed: Testbed) -> None:
    info = _android(testbed, "release:v4.3.5").describe()
    assert info["qfieldcloud_url"] == "http://10.0.2.2:8000/"
    assert info["adb"] == f"docker exec {CONTAINER} adb"
    assert info["apk"] is None
    assert info["image"] == "budtmo/docker-android:emulator_14.0"
