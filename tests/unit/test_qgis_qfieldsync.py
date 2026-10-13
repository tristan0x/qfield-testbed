from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from qfield_testbed.backends.qgis_host import QgisHostBackend
from qfield_testbed.components.qfieldsync import (
    QFieldSync,
    compatibility_warnings,
    libqfieldsync_pin,
    plugin_metadata,
)
from qfield_testbed.components.qgis import Qgis
from qfield_testbed.deployment import Testbed
from qfield_testbed.errors import TestbedError
from qfield_testbed.provision.qgis_profile import AuthConfig, ProfileLayout, parse_seed_report, render_seed_script
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec
from qfield_testbed.state import Credentials

SHA = "abcdef0123456789abcdef0123456789abcdef01"
LIB_SHA = "fbec56f451e347d136cbb6ac4b45d5b0b144f758"


def fake_qfieldsync_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "qfieldsync-wt"
    plugin = checkout / "qfieldsync"
    plugin.mkdir(parents=True)
    (plugin / "metadata.txt").write_text(
        "[general]\nname=QFieldSync\nversion=dev\nqgisMinimumVersion=3.40\nsupportsQt6=True\n"
    )
    (plugin / "__init__.py").write_text("")
    (checkout / ".gitmodules").write_text(
        '[submodule "qfieldsync/setting_manager"]\n\tpath = qfieldsync/setting_manager\n'
    )
    (checkout / "requirements.txt").write_text(
        f"# pin\nlibqfieldsync @ https://github.com/opengisch/libqfieldsync/archive/{LIB_SHA}.tar.gz\n"
    )
    return checkout


@pytest.fixture
def runner_with_qgis(runner: FakeRunner) -> FakeRunner:
    runner.available.add("uv")
    runner.reply(["/usr/bin/qgis", "--version"], stdout="QGIS 4.2.3-Belém do Pará 'Belém do Pará' (b041a18904a)\n")
    runner.reply(
        ["/usr/bin/python3"],
        stdout='Warning: something\n{"settings_dir": "/x/profiles/default/", "authcfg": "abc1234"}\n',
    )
    return runner


# --- pure helpers ----------------------------------------------------------------------------------------


def test_libqfieldsync_pin() -> None:
    url, sha = libqfieldsync_pin(
        f"libqfieldsync @ https://github.com/opengisch/libqfieldsync/archive/{LIB_SHA}.tar.gz\n"
    )
    assert sha == LIB_SHA
    assert url.endswith(f"{LIB_SHA}.tar.gz")
    with pytest.raises(TestbedError):
        libqfieldsync_pin("libqfieldsync @ git+https://github.com/opengisch/libqfieldsync\n")


def test_compatibility_warnings() -> None:
    assert compatibility_warnings({"qgisminimumversion": "3.40", "supportsqt6": "True"}, "4.2.3") == []
    assert compatibility_warnings({"qgisminimumversion": "3.40"}, "4.2.3") == [
        "plugin revision does not declare supportsQt6 but QGIS 4.2.3 is Qt 6 — expect import errors"
    ]
    assert (
        "needs QGIS >= 4.4" in compatibility_warnings({"qgisminimumversion": "4.4", "supportsqt6": "True"}, "4.2.3")[0]
    )
    assert compatibility_warnings({}, None) == []


def test_seed_script_renders_valid_python(tmp_path: Path) -> None:
    layout = ProfileLayout(profiles_path=tmp_path / "qgis")
    auth = AuthConfig(
        name="qfieldcloud",
        method="Basic",
        uri="http://x/",
        config={"username": "u", "password": "p'q\"r"},
        id_settings=["QFieldSync/qfieldCloudAuthcfg"],
    )
    settings = {"QFieldSync/qfieldCloudServerUrl": "http://x/", "PythonPlugins/qfieldsync": True}
    script = render_seed_script("demo", layout, settings, auth, qgis_version="4.2.3")
    compile(script, "seed_profile.py", "exec")  # syntax check
    assert 'setApplicationName("QGIS4")' in script
    assert 'setApplicationName("QGIS3")' in render_seed_script("demo", layout, settings, None, qgis_version="3.44.15")
    assert str(layout.profiles_path) in script
    assert "QGIS_CUSTOM_CONFIG_PATH" in script
    # the constants survive the JSON-in-Python quoting: evaluate only the header of the generated script
    header = script.split("os.environ.setdefault")[0]
    namespace: dict[str, object] = {}
    exec(header, namespace)  # noqa: S102 - our own generated code, in the test process
    assert namespace["SETTINGS"] == settings
    assert namespace["AUTH"]["config"]["password"] == "p'q\"r"  # type: ignore[index]


def test_parse_seed_report() -> None:
    assert parse_seed_report('QStandardPaths: warning\n{"settings_dir": "/p/", "authcfg": "x"}\n') == {
        "settings_dir": "/p/",
        "authcfg": "x",
    }
    with pytest.raises(TestbedError):
        parse_seed_report("nothing here\n")


# --- qgis component --------------------------------------------------------------------------------------


def test_qgis_host_render_and_seed(testbed: Testbed, runner_with_qgis: FakeRunner, tmp_path: Path) -> None:
    plugin_src = tmp_path / "myplugin"
    plugin_src.mkdir()
    (plugin_src / "metadata.txt").write_text("[general]\nname=My\n")
    spec = DeploymentSpec.from_yaml(
        f"name: q\nqgis:\n  version: host\n  plugins: [{{path: {plugin_src}}}]\n  env: {{QGIS_DEBUG: '1'}}\n"
    )
    deployment = testbed.add(spec)
    deployment.start()
    qgis = deployment.components("qgis")[0]
    assert isinstance(qgis, Qgis)
    info = deployment.describe()["components"]["qgis"]
    assert info["version"] == "4.2.3"
    launcher = Path(info["launcher"])
    assert launcher.is_file()
    assert os.access(launcher, os.X_OK)
    text = launcher.read_text()
    assert "--profiles-path" in text
    assert "--profile default" in text
    assert "QGIS_AUTH_PASSWORD_FILE" in text
    assert "QGIS_DEBUG='1'" in text
    layout = qgis.layout
    assert layout.master_password_file.is_file()
    assert (layout.plugins / "myplugin").is_symlink()
    assert (layout.plugins / "myplugin").resolve() == plugin_src
    # wiring enabled the extra plugin through the seed script run with the host interpreter
    seed_calls = [c for c in runner_with_qgis.calls if c.args[0] == "/usr/bin/python3"]
    assert len(seed_calls) == 1
    assert json.loads(
        layout.seed_script.read_text().split("SETTINGS = json.loads(")[1].split(")\n")[0]
    )  # valid JSON literal
    assert "PythonPlugins/myplugin" in layout.seed_script.read_text()
    assert deployment.env()["QFT_QGIS_LAUNCHER"] == str(launcher)


def _executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.mark.parametrize(
    ("display", "with_xvfb", "expected"),
    [
        (":0", True, (0, "qgis --profiles-path {p} --profile default --code x.py\n", "")),
        ("", True, (0, "xvfb-run -a\nqgis --profiles-path {p} --profile default --code x.py\n", "")),
        ("", False, (1, "", "qft: no display")),
    ],
    ids=["display", "xvfb-run", "neither"],
)
def test_qgis_host_launcher_without_display(
    tmp_path: Path, display: str, with_xvfb: bool, expected: tuple[int, str, str]
) -> None:
    returncode, stdout, stderr = expected
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    qgis = _executable(tmp_path / "qgis", '#!/bin/sh\necho qgis "$@"\n')
    if with_xvfb:
        _executable(bin_dir / "xvfb-run", '#!/bin/sh\necho xvfb-run "$1"\nshift\nexec "$@"\n')
    profiles = tmp_path / "profiles"
    backend = QgisHostBackend(runner=FakeRunner(), binary=qgis)
    launcher = _executable(tmp_path / "launcher", "\n".join(backend.launcher_lines(profiles, {})) + "\n")
    bash = shutil.which("bash")
    assert bash
    # PATH holds only the fake tools: a real xvfb-run on the machine must not leak into the "neither" case
    env = {"PATH": str(bin_dir), "DISPLAY": display}
    result = subprocess.run(
        [bash, str(launcher), "--code", "x.py"], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == returncode
    assert result.stdout == stdout.format(p=profiles)
    assert stderr in result.stderr


def test_qgis_docker_backend(testbed: Testbed, runner_with_qgis: FakeRunner, tmp_path: Path) -> None:
    runner_with_qgis.reply(["docker", "info"])
    runner_with_qgis.reply(["docker", "image", "inspect"], returncode=1)
    runner_with_qgis.reply_when(
        lambda args: args[0] == "docker" and args[-1] == "--version",
        stdout="QGIS 3.44.15-Solothurn 'Solothurn' (abc)\n",
    )
    runner_with_qgis.reply_when(
        lambda args: args[0] == "docker" and "python3" in args, stdout='{"settings_dir": "/x/", "authcfg": "d0cker"}\n'
    )
    plugin_src = tmp_path / "myplugin"
    plugin_src.mkdir()
    (plugin_src / "metadata.txt").write_text("[general]\nname=My\n")
    spec = DeploymentSpec.from_yaml(f"name: q\nqgis:\n  version: docker:ltr\n  plugins: [{{path: {plugin_src}}}]\n")
    deployment = testbed.add(spec)
    deployment.start()
    info = deployment.describe()["components"]["qgis"]
    assert info["backend"] == "qgis-docker"
    assert info["version"] == "3.44.15"
    commands = runner_with_qgis.commands()
    assert any(cmd.startswith("docker pull --quiet qgis/qgis:ltr") for cmd in commands)
    assert any(cmd.startswith("docker build --quiet -t qft-qgis:ltr") for cmd in commands)
    launcher = Path(info["launcher"]).read_text()
    assert "--network host" in launcher
    assert "qft-qgis:ltr" in launcher
    assert "QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-xcb}" in launcher
    assert "--profile default" in launcher
    assert f"-v {plugin_src}:{plugin_src}" in launcher  # the plugin folder is visible at the same path
    assert "/tmp/.X11-unix" in launcher
    # the seed script ran through the image, not the host interpreter
    seed = [c for c in commands if "python3" in c and "seed_profile.py" in c]
    assert seed
    assert seed[0].startswith("docker run --rm --network host")
    assert deployment.testbed.cache.load().entries["qgis/image/ltr"].images == ["qft-qgis:ltr"]


# --- qfieldsync component --------------------------------------------------------------------------------


@pytest.fixture
def cloud_stub():
    """Register a fake qfieldcloud so qfieldsync can be wired without Docker."""
    from qfield_testbed.components import REGISTRY, register  # noqa: PLC0415
    from tests.unit.test_deployment import FakeQgis  # noqa: PLC0415

    class FakeCloud(FakeQgis):
        name = "qfieldcloud"

        def describe(self) -> dict[str, object]:
            return {"url": "http://127.0.0.1:30000/", "url_internal": "http://app:8000/"}

    saved = REGISTRY.get("qfieldcloud")
    register(FakeCloud)
    yield FakeCloud
    if saved is not None:
        REGISTRY["qfieldcloud"] = saved


def test_qfieldsync_worktree_materialize_and_wire(
    testbed: Testbed, runner_with_qgis: FakeRunner, tmp_path: Path, cloud_stub
) -> None:
    checkout = fake_qfieldsync_checkout(tmp_path)
    runner_with_qgis.reply(["git", "-C", str(checkout), "rev-parse", "HEAD"], stdout=SHA + "\n")
    runner_with_qgis.reply(["git", "-C", str(checkout), "status"], stdout="")
    # a wheel already in the cache: no download/build needed
    testbed.paths.ensure()
    wheel = testbed.paths.wheels / f"libqfieldsync_{LIB_SHA[:12]}.whl"
    wheel.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    spec = DeploymentSpec.from_yaml(
        f"name: s\nqfieldcloud: {{version: release:v26.28}}\nqgis: {{version: host}}\nqfieldsync: {{version: worktree:{checkout}}}\n"
    )
    deployment = testbed.add(spec)
    deployment.state.credentials = Credentials(username="testbed", password="pw")
    deployment.save()
    deployment.start()

    sync = deployment.components("qfieldsync")[0]
    assert isinstance(sync, QFieldSync)
    assert (checkout / "qfieldsync" / wheel.name).is_file()  # wheel copied next to the plugin code
    assert any("submodule update --init" in cmd for cmd in runner_with_qgis.commands())
    layout = sync.qgis().layout
    link = layout.plugins / "qfieldsync"
    assert link.is_symlink()
    assert link.resolve() == checkout / "qfieldsync"
    facts = deployment.describe()["components"]["qfieldsync"]
    assert facts["plugin_version"] == "dev"
    assert facts["libqfieldsync"] == wheel.name
    assert facts["live_edits"] is True
    assert facts["authcfg"] == "abc1234"
    assert facts["qfieldcloud_url"] == "http://127.0.0.1:30000/"
    script = layout.seed_script.read_text()
    assert "QFieldSync/qfieldCloudServerUrl" in script
    assert "http://127.0.0.1:30000/" in script
    assert '\\"username\\": \\"testbed\\"' in script or '"username": "testbed"' in script.replace("\\", "")
    assert "QFieldSync/qfieldCloudAuthcfg" in script
    assert deployment.env()["QFT_QFIELDSYNC_QFIELDCLOUD_URL"] == "http://127.0.0.1:30000/"


def test_qfieldsync_release_uses_cached_extracted_zip(
    testbed: Testbed, runner_with_qgis: FakeRunner, cloud_stub
) -> None:
    testbed.paths.ensure()
    extracted = testbed.paths.artifacts / "qfieldsync" / "v4.23.1" / "qfieldsync"
    extracted.mkdir(parents=True)
    (extracted / "metadata.txt").write_text("[general]\nversion=4.23.1\nqgisMinimumVersion=3.40\nsupportsQt6=True\n")
    (extracted / "libqfieldsync_0f081eb6c7bd.whl").write_bytes(b"")
    mirror = testbed.paths.git_mirrors / "opengisch-qfieldsync.git"
    runner_with_qgis.reply(["git", "-C", str(mirror), "rev-parse"], stdout=SHA + "\n")
    spec = DeploymentSpec.from_yaml(
        "name: r\nqfieldcloud: {version: release:v26.28}\nqgis: {version: host}\nqfieldsync: {version: release:v4.23.1}\n"
    )
    deployment = testbed.add(spec)
    deployment.state.credentials = Credentials(username="testbed", password="pw")
    deployment.save()
    deployment.start()
    assert not any(cmd.startswith("gh release download") for cmd in runner_with_qgis.commands())
    facts = deployment.describe()["components"]["qfieldsync"]
    assert facts["plugin_version"] == "4.23.1"
    assert facts["libqfieldsync"] == "libqfieldsync_0f081eb6c7bd.whl"
    assert plugin_metadata(extracted)["supportsqt6"] == "True"
    spec_on_disk = yaml.safe_load((deployment.directory / "spec.yaml").read_text())
    assert spec_on_disk["qfieldsync"]["version"] == {"kind": "release", "tag": "v4.23.1"}
