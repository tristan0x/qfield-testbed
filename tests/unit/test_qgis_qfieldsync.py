from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
import yaml

from qfield_testbed.backends.qgis_docker import QgisDockerBackend
from qfield_testbed.backends.qgis_host import QgisHostBackend
from qfield_testbed.components.base import Component
from qfield_testbed.components.qfieldsync import (
    QFieldSync,
    compatibility_warnings,
    libqfieldsync_pin,
    plugin_metadata,
)
from qfield_testbed.components.qgis import Qgis, _replace_with_symlink, qt_platform
from qfield_testbed.deployment import Deployment, Testbed
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError
from qfield_testbed.provision.qgis_profile import AuthConfig, ProfileLayout, parse_seed_report, render_seed_script
from qfield_testbed.revisions import Revision
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.spec import DeploymentSpec, GitSource, PluginSpec, ReleaseSource, WorktreeSource
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


def _run_host_launcher(tmp_path: Path, env: dict[str, str], spec_env: dict[str, str]) -> dict[str, str]:
    """Run the host launcher with a fake `qgis` that prints the Python-related variables it receives."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    qgis = _executable(
        tmp_path / "qgis",
        '#!/bin/sh\necho "VIRTUAL_ENV=${VIRTUAL_ENV-unset}"\necho "PYTHONHOME=${PYTHONHOME-unset}"\necho "PATH=$PATH"\n',
    )
    backend = QgisHostBackend(runner=FakeRunner(), binary=qgis)
    launcher = _executable(tmp_path / "launcher", "\n".join(backend.launcher_lines(tmp_path / "p", spec_env)) + "\n")
    bash = shutil.which("bash")
    assert bash
    out = subprocess.run(
        [bash, str(launcher)], env={"DISPLAY": ":0", **env}, capture_output=True, text=True, check=True
    )
    return dict(line.split("=", 1) for line in out.stdout.splitlines())


def test_qgis_host_launcher_drops_the_callers_virtualenv(tmp_path: Path) -> None:
    venv_bin = f"{tmp_path}/.venv/bin"
    seen = _run_host_launcher(
        tmp_path,
        {"VIRTUAL_ENV": f"{tmp_path}/.venv", "PYTHONHOME": "/x", "PATH": f"{venv_bin}:/usr/bin:{venv_bin}:/bin"},
        {},
    )
    assert seen == {"VIRTUAL_ENV": "unset", "PYTHONHOME": "unset", "PATH": "/usr/bin:/bin"}


def test_qgis_host_launcher_keeps_path_without_virtualenv(tmp_path: Path) -> None:
    seen = _run_host_launcher(tmp_path, {"PATH": "/usr/bin:/bin"}, {"PYTHONHOME": "/from/spec"})
    assert seen == {"VIRTUAL_ENV": "unset", "PYTHONHOME": "/from/spec", "PATH": "/usr/bin:/bin"}


def _run_docker_launcher(tmp_path: Path, args: list[str], env: dict[str, str], platform: str = "session") -> list[str]:
    """Run the generated Docker launcher with a fake `docker` that prints the arguments it receives."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    _executable(bin_dir / "docker", '#!/bin/sh\nprintf "%s\\n" "$@"\n')
    grep = shutil.which("grep")
    assert grep
    (bin_dir / "grep").symlink_to(grep)
    backend = QgisDockerBackend(runner=FakeRunner(), tag="ltr")
    launcher = _executable(
        tmp_path / "launcher", "\n".join(backend.launcher_lines(tmp_path / "profiles", {}, platform)) + "\n"
    )
    bash = shutil.which("bash")
    assert bash
    result = subprocess.run(
        [bash, str(launcher), *args],
        env={"PATH": str(bin_dir), "HOME": str(tmp_path), **env},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def _env_flags(argv: list[str]) -> set[str]:
    return {argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-e"}


def test_qgis_docker_launcher_forwards_qft_variables(tmp_path: Path) -> None:
    argv = _run_docker_launcher(
        tmp_path,
        ["--nologo"],
        {"QFT_DEPLOYMENT": "demo", "QFT_FORWARD_ENV": "FOO,BAR", "FOO": "1", "OTHER": "2"},
    )
    flags = _env_flags(argv)
    assert {"QFT_DEPLOYMENT", "QFT_FORWARD_ENV", "FOO", "BAR"} <= flags  # names only: docker copies the values
    assert "OTHER" not in flags
    assert argv.index("-e") < argv.index("qft-qgis:ltr") < argv.index("--nologo")
    assert argv.index("--init") < argv.index("qft-qgis:ltr")  # an init process passes signals on to QGIS


def test_qgis_docker_launcher_without_variables_to_forward(tmp_path: Path) -> None:
    argv = _run_docker_launcher(tmp_path, [], {})
    assert not any(flag.startswith("QFT_") for flag in _env_flags(argv))
    assert argv[-2:] == ["--profile", "default"]


def _volumes(argv: list[str]) -> set[str]:
    return {argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-v"}


@pytest.mark.parametrize("relative", [False, True], ids=["absolute", "relative"])
def test_qgis_docker_launcher_mounts_the_code_script_directory(tmp_path: Path, relative: bool) -> None:
    scripts = tmp_path / "elsewhere" / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "check.py"
    script.write_text("")
    without = _volumes(_run_docker_launcher(tmp_path / "a", ["--nologo"], {}))
    path = os.path.relpath(script) if relative else str(script)
    with_code = _volumes(_run_docker_launcher(tmp_path / "b", ["--nologo", "--code", path], {}))
    resolved = scripts.resolve()
    assert with_code - without == {f"{resolved}:{resolved}"}


def test_qgis_docker_launcher_leaves_a_missing_script_to_qgis(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing" / "x.py")
    argv = _run_docker_launcher(tmp_path, ["--code", missing], {})
    assert argv[-2:] == ["--code", missing]
    assert not any("missing" in volume for volume in _volumes(argv))


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
    assert seed[0].startswith("docker run --rm --network host --init")
    assert "--label qft.deployment=q --label qft.component=qgis" in seed[0]
    assert "--label qft.deployment=q --label qft.component=qgis" in launcher
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


@pytest.mark.usefixtures("cloud_stub")
def test_qfieldsync_worktree_materialize_and_wire(
    testbed: Testbed, runner_with_qgis: FakeRunner, tmp_path: Path
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


@pytest.mark.usefixtures("cloud_stub")
def test_qfieldsync_release_uses_cached_extracted_zip(testbed: Testbed, runner_with_qgis: FakeRunner) -> None:
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


# --- component steps, one at a time ----------------------------------------------------------------------


def _probe(testbed: Testbed, text: str, name: str) -> Component:
    """A component instance outside any deployment lifecycle (no host checks)."""
    testbed.paths.ensure()
    spec = DeploymentSpec.from_yaml(text)
    return Deployment.probe(testbed, spec, name, spec.components()[name])


def _qgis(testbed: Testbed, text: str = "name: q\nqgis: {version: host}\n") -> Qgis:
    component = _probe(testbed, text, "qgis")
    assert isinstance(component, Qgis)
    return component


def _sync(testbed: Testbed, version: str) -> QFieldSync:
    text = f"name: s\nqfieldcloud: {{version: release:v26.28}}\nqgis: {{version: host}}\nqfieldsync: {{version: '{version}'}}\n"
    component = _probe(testbed, text, "qfieldsync")
    assert isinstance(component, QFieldSync)
    return component


def _plugin_zip(path: Path, *members: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for member in members:
            zf.writestr(member, "[general]\nname=Zipped\n")
    return path


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        """Always fine."""


def test_qgis_check_host_problems(
    testbed: Testbed, runner_with_qgis: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _qgis(testbed, f"name: q\nqgis:\n  version: host\n  plugins: [{{path: {bare}}}]\n").check_host() == [
        f"plugin path {bare} has no metadata.txt"
    ]
    monkeypatch.setattr(QgisHostBackend, "python", property(lambda _: tmp_path / "no-python"))
    assert _qgis(testbed).check_host() == [f"{tmp_path / 'no-python'} missing (needed to seed the profile)"]
    runner_with_qgis.reply(["docker", "info"], returncode=1)
    assert _qgis(testbed, "name: q\nqgis: {version: docker:ltr}\n").check_host() == ["docker daemon not reachable"]


def test_qgis_check_host_without_usable_qgis(testbed: Testbed, runner: FakeRunner) -> None:
    runner.reply(["/usr/bin/qgis", "--version"], stdout="not qgis\n")
    assert _qgis(testbed).check_host() == ["cannot read the QGIS version from `/usr/bin/qgis --version`: 'not qgis'"]
    runner.available.discard("qgis")
    (problem,) = _qgis(testbed).check_host()
    assert problem.startswith("no `qgis` executable on PATH")


def test_qgis_docker_mounts_include_worktree_and_plugins(testbed: Testbed, tmp_path: Path) -> None:
    checkout = fake_qfieldsync_checkout(tmp_path)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    text = (
        "name: q\nqfieldcloud: {version: release:v26.28}\n"
        f"qgis:\n  version: docker:ltr\n  plugins: [{{path: {plugin}}}, {{zip: /x/p.zip}}]\n"
        f"qfieldsync: {{version: worktree:{checkout}}}\n"
    )
    qgis = _qgis(testbed, text)
    assert qgis.docker_mounts() == [testbed.paths.home, testbed.paths.cache, checkout, plugin]


def test_qgis_install_plugin_from_zip_replaces_previous_copy(testbed: Testbed, tmp_path: Path) -> None:
    archive = _plugin_zip(tmp_path / "zipped.zip", "zipped/metadata.txt", "zipped/__init__.py")
    qgis = _qgis(testbed)
    layout = qgis.layout
    layout.ensure()
    stale = layout.plugins / "zipped"
    stale.mkdir()
    (stale / "old.py").write_text("")
    assert qgis.install_plugin(PluginSpec(zip=str(archive)), layout) == "zipped"
    assert (stale / "metadata.txt").is_file()
    assert not (stale / "old.py").exists()
    # a previous symlink (e.g. from a `path:` plugin of the same name) is replaced, not followed
    shutil.rmtree(stale)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    stale.symlink_to(elsewhere)
    qgis.install_plugin(PluginSpec(zip=str(archive)), layout)
    assert not stale.is_symlink()
    assert list(elsewhere.iterdir()) == []


def test_qgis_install_plugin_zip_errors(testbed: Testbed, tmp_path: Path) -> None:
    qgis = _qgis(testbed)
    layout = qgis.layout
    two_roots = _plugin_zip(tmp_path / "two.zip", "a/metadata.txt", "b/metadata.txt")
    with pytest.raises(TestbedError, match=r"exactly one top-level folder, found \['a', 'b'\]"):
        qgis.install_plugin(PluginSpec(zip=str(two_roots)), layout)
    with pytest.raises(TestbedError, match=r"plugin zip not found: /nope.zip"):
        qgis.install_plugin(PluginSpec(zip="/nope.zip"), layout)


def test_qgis_fetch_plugin_zip_downloads_once(
    testbed: Testbed, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _plugin_zip(tmp_path / "dl.zip", "remote/metadata.txt").read_bytes()
    urls: list[str] = []

    def get(url: str, timeout: float) -> _Response:
        assert timeout > 0
        urls.append(url)
        return _Response(payload)

    monkeypatch.setattr("qfield_testbed.components.qgis.requests.get", get)
    qgis = _qgis(testbed)
    url = "https://plugins.qgis.org/plugins/remote/version/1.2/download/"
    first = qgis.fetch_plugin_zip(url)
    assert first.parent == testbed.paths.artifacts / "qgis-plugins"
    assert first.read_bytes() == payload
    assert qgis.fetch_plugin_zip(url) == first
    assert urls == [url]
    assert [e.users for k, e in testbed.cache.load().entries.items() if k.startswith("qgis/plugin/")] == [{"q"}]
    assert qgis.install_plugin(PluginSpec(zip=url), qgis.layout) == "remote"


def test_replace_with_symlink_over_dir_and_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "plugins" / "p"
    target.mkdir(parents=True)
    (target / "f").write_text("")
    _replace_with_symlink(target, source)
    assert target.is_symlink()
    assert target.resolve() == source
    target.unlink()
    target.write_text("a file")
    _replace_with_symlink(target, source)
    assert target.resolve() == source


def test_qgis_wire_without_plugins_seeds_nothing_and_describe_defaults(
    testbed: Testbed, runner_with_qgis: FakeRunner
) -> None:
    qgis = _qgis(testbed)
    qgis.wire()
    qgis.start()
    qgis.stop()
    qgis.remove()
    assert runner_with_qgis.calls == []
    info = qgis.describe()
    assert info["version"] is None
    assert info["backend"] == "qgis-host"
    assert info["extra_plugins"] is None
    assert info["launcher"] == str(testbed.paths.deployment_dir("q") / "bin" / "qgis")


def test_qfieldsync_check_host(testbed: Testbed, runner: FakeRunner) -> None:
    runner.available.clear()
    assert _sync(testbed, "release:v4.23.1").check_host() == [
        "git missing",
        "gh missing (needed to download release zips)",
        "neither uv nor pip available to build the libqfieldsync wheel",
    ]
    runner.available.update({"git", "pip"})
    assert _sync(testbed, "git:main").check_host() == []


def test_qfieldsync_needs_qgis(testbed: Testbed) -> None:
    sync = _sync(testbed, "release:v4.23.1")
    sync.ctx.spec = sync.ctx.spec.model_copy(update={"qgis": None})
    with pytest.raises(TestbedError, match="qfieldsync requires the qgis component"):
        sync.qgis()


def test_qfieldsync_release_download_and_extract(testbed: Testbed, runner: FakeRunner) -> None:
    sync = _sync(testbed, "release:v4.23.1")
    revision = Revision(source=ReleaseSource(tag="v4.23.1"), label="v4.23.1", sha=SHA)
    target = testbed.paths.artifacts / "qfieldsync" / "v4.23.1"
    target.mkdir(parents=True)
    # what `gh release download` leaves behind
    _plugin_zip(target / "qfieldsync.v4.23.1.zip", "qfieldsync/metadata.txt", "qfieldsync/__init__.py")
    assert sync.materialize(revision) == target / "qfieldsync"
    assert (target / "qfieldsync" / "metadata.txt").is_file()
    download = next(c for c in runner.commands() if c.startswith("gh release download"))
    assert "v4.23.1 --repo opengisch/qfieldsync --pattern qfieldsync.*.zip" in download
    assert testbed.cache.load().entries["qfieldsync/release/v4.23.1"].users == {"s"}

    broken = Revision(source=ReleaseSource(tag="v0.1"), label="v0.1", sha=SHA)
    broken_dir = testbed.paths.artifacts / "qfieldsync" / "v0.1"
    broken_dir.mkdir(parents=True)
    _plugin_zip(broken_dir / "qfieldsync.v0.1.zip", "other/metadata.txt")
    with pytest.raises(MaterializationUnavailableError, match=r"release v0.1: zip has no qfieldsync/metadata.txt"):
        sync.materialize(broken)


def test_qfieldsync_git_ref_builds_wheel_and_drops_stale_ones(
    testbed: Testbed, runner_with_qgis: FakeRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync = _sync(testbed, "git:main")
    checkout = sync.mirror.checkouts / SHA
    shutil.copytree(fake_qfieldsync_checkout(tmp_path), checkout)
    (checkout / ".git").write_text("gitdir: fake\n")
    (checkout / "qfieldsync" / "libqfieldsync_000000000000.whl").write_bytes(b"stale")
    # the libqfieldsync tarball GitHub serves for the pinned sha
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        info = tarfile.TarInfo(f"libqfieldsync-{LIB_SHA}/pyproject.toml")
        info.size = 0
        tf.addfile(info, io.BytesIO(b""))
    requested: list[str] = []

    def get(url: str, timeout: float) -> _Response:
        assert timeout > 0
        requested.append(url)
        return _Response(buffer.getvalue())

    monkeypatch.setattr("qfield_testbed.components.qfieldsync.requests.get", get)
    dist = testbed.paths.artifacts / "libqfieldsync" / LIB_SHA[:12] / "dist"
    dist.mkdir(parents=True)
    (dist / "libqfieldsync-1.0-py3-none-any.whl").write_bytes(b"wheel")  # what `uv build` produces

    revision = Revision(source=GitSource(ref="main"), label=f"main@{SHA[:12]}", sha=SHA)
    plugin_dir = sync.materialize(revision)
    assert plugin_dir == checkout / "qfieldsync"
    assert [p.name for p in plugin_dir.glob("*.whl")] == [f"libqfieldsync_{LIB_SHA[:12]}.whl"]
    assert requested == [f"https://github.com/opengisch/libqfieldsync/archive/{LIB_SHA}.tar.gz"]
    build = next(c for c in runner_with_qgis.commands() if c.startswith("uv build --wheel"))
    assert build.endswith(f"libqfieldsync-{LIB_SHA}")
    entries = testbed.cache.load().entries
    assert entries[f"qfieldsync/checkout/{SHA[:12]}"].users == {"s"}
    assert f"libqfieldsync/wheel/{LIB_SHA[:12]}" in entries


def test_qfieldsync_wheel_with_pip_and_build_failure(testbed: Testbed, runner: FakeRunner, tmp_path: Path) -> None:
    sync = _sync(testbed, "git:main")
    checkout = fake_qfieldsync_checkout(tmp_path)
    project = testbed.paths.artifacts / "libqfieldsync" / LIB_SHA[:12] / "src" / "libqfieldsync"
    project.mkdir(parents=True)  # already downloaded and extracted
    with pytest.raises(TestbedError, match="no wheel produced in"):
        sync.libqfieldsync_wheel(checkout)
    assert runner.calls[-1].args[:5] == ("python3", "-m", "pip", "wheel", "--no-deps")


def test_qfieldsync_rejects_non_checkouts(testbed: Testbed, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    sync = _sync(testbed, f"worktree:{empty}")
    with pytest.raises(TestbedError, match="does not look like a QFieldSync checkout"):
        sync.materialize(Revision(source=WorktreeSource(path=empty), label="worktree@x", sha=SHA))


def test_qfieldsync_render_warns_about_incompatible_qgis(
    testbed: Testbed, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sync = _sync(testbed, "release:v1.0")
    plugin_dir = tmp_path / "qfieldsync"
    plugin_dir.mkdir()
    (plugin_dir / "metadata.txt").write_text("[general]\nversion=1.0\n")
    sync.ctx.state.component("qgis").facts["version"] = "4.2.3"
    sync.render(Revision(source=ReleaseSource(tag="v1.0"), label="v1.0"), plugin_dir)
    assert "[qfieldsync] plugin revision does not declare supportsQt6" in caplog.text
    assert sync.describe()["libqfieldsync"] is None
    assert sync.describe()["live_edits"] is False
    assert (sync.qgis().layout.plugins / "qfieldsync").resolve() == plugin_dir


def test_qfieldsync_wire_needs_a_provisioned_cloud(testbed: Testbed) -> None:
    sync = _sync(testbed, "release:v1.0")
    with pytest.raises(TestbedError, match="qfieldcloud component must be running and provisioned first"):
        sync.wire()
    sync.ctx.state.component("qfieldcloud").facts["url"] = "http://127.0.0.1:30000/"
    with pytest.raises(TestbedError, match="provisioned first"):
        sync.wire()  # still no credentials


@pytest.mark.parametrize("method", ["stop", "remove"])
def test_qgis_docker_removes_leftover_containers(testbed: Testbed, runner: FakeRunner, method: str) -> None:
    qgis = _qgis(testbed, "name: q\nqgis: {version: docker:ltr}\n")
    runner.reply(["docker", "ps", "-aq"], stdout="abc\ndef\n")
    getattr(qgis, method)()
    commands = runner.commands()
    assert "docker ps -aq --filter label=qft.deployment=q --filter label=qft.component=qgis" in commands
    assert "docker rm -f abc def" in commands


def test_qgis_docker_without_leftover_containers(testbed: Testbed, runner: FakeRunner) -> None:
    qgis = _qgis(testbed, "name: q\nqgis: {version: docker:ltr}\n")
    runner.reply(["docker", "ps", "-aq"], stdout="")
    qgis.stop()
    assert not any(cmd.startswith("docker rm") for cmd in runner.commands())


def test_qgis_host_has_no_container_to_remove(testbed: Testbed, runner: FakeRunner) -> None:
    _qgis(testbed).remove()
    assert not any(cmd.startswith("docker") for cmd in runner.commands())


@pytest.mark.parametrize(
    "case",
    [
        ("session", {}, "unset"),
        ("xcb", {}, "xcb"),
        ("offscreen", {}, "offscreen"),
        ("xcb", {"QT_QPA_PLATFORM": "wayland"}, "wayland"),
    ],
    ids=["session", "xcb", "offscreen", "caller-wins"],
)
def test_qgis_host_launcher_platform(tmp_path: Path, case: tuple[str, dict[str, str], str]) -> None:
    platform, caller_env, expected = case
    qgis = _executable(tmp_path / "qgis", '#!/bin/sh\necho "${QT_QPA_PLATFORM-unset}"\n')
    backend = QgisHostBackend(runner=FakeRunner(), binary=qgis)
    launcher = _executable(
        tmp_path / "launcher", "\n".join(backend.launcher_lines(tmp_path / "p", {}, platform)) + "\n"
    )
    bash = shutil.which("bash")
    assert bash
    out = subprocess.run(
        [bash, str(launcher)], env={"DISPLAY": ":0", **caller_env}, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == expected


@pytest.mark.parametrize(
    "case",
    [("session", {}, "xcb"), ("offscreen", {}, "offscreen"), ("xcb", {"QT_QPA_PLATFORM": "offscreen"}, "offscreen")],
    ids=["session-is-x11", "offscreen", "caller-wins"],
)
def test_qgis_docker_launcher_platform(tmp_path: Path, case: tuple[str, dict[str, str], str]) -> None:
    platform, caller_env, expected = case
    argv = _run_docker_launcher(tmp_path, [], caller_env, platform)
    assert f"QT_QPA_PLATFORM={expected}" in _env_flags(argv)


@pytest.mark.parametrize(
    "case",
    [
        ("{version: host}", {}, "session"),
        ("{version: host}", {"XDG_SESSION_TYPE": "wayland"}, "session (wayland)"),
        ("{version: host, platform: xcb}", {"XDG_SESSION_TYPE": "wayland"}, "xcb"),
        ("{version: host, env: {QT_QPA_PLATFORM: wayland}}", {}, "wayland"),
        ("{version: 'docker:ltr'}", {}, "xcb"),
        ("{version: host, platform: xcb}", {"QT_QPA_PLATFORM": "offscreen"}, "offscreen (from QT_QPA_PLATFORM)"),
    ],
    ids=["session", "session-type", "spec", "spec-env", "docker", "caller"],
)
def test_qt_platform_reported(case: tuple[str, dict[str, str], str]) -> None:
    qgis_yaml, environ, expected = case
    spec = DeploymentSpec.from_yaml(f"name: p\nqgis: {qgis_yaml}\n").qgis
    assert spec is not None
    assert qt_platform(spec, environ) == expected


def test_qgis_describe_reports_the_platform(testbed: Testbed) -> None:
    assert _qgis(testbed, "name: q\nqgis: {version: host, platform: xcb}\n").describe()["platform"] == "xcb"
