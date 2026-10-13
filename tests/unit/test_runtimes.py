from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import requests

from qfield_testbed.errors import CommandError, HealthTimeoutError
from qfield_testbed.runtimes import adb as adb_module
from qfield_testbed.runtimes import http, ssh
from qfield_testbed.runtimes.adb import Adb
from qfield_testbed.runtimes.docker import (
    Compose,
    compose_version,
    daemon_available,
    ensure_network,
    image_exists,
    list_images,
    published_host_ports,
    remove_network,
)
from qfield_testbed.runtimes.shell import Completed, FakeRunner, SubprocessRunner
from qfield_testbed.runtimes.ssh import Ssh, process_alive, stop_process


class FakeClock:
    """Stands in for the ``time`` module: ``sleep`` advances ``monotonic`` instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# --- shell ------------------------------------------------------------------------------------------------


def test_completed_check_passes_on_success() -> None:
    done = Completed(("true",), 0)
    assert done.check() is done


def test_completed_check_raises_with_stderr_tail() -> None:
    stderr = "\n".join(f"line {i}" for i in range(20))
    with pytest.raises(CommandError, match=r"`git fetch` exited with 128\nline 5\n") as excinfo:
        Completed(("git", "fetch"), 128, stderr=stderr).check()
    assert "line 4" not in str(excinfo.value)
    assert str(excinfo.value).endswith("line 19")


def test_completed_check_without_stderr_has_no_trailing_newline() -> None:
    with pytest.raises(CommandError) as excinfo:
        Completed(("false",), 1).check()
    assert str(excinfo.value) == "`false` exited with 1"


def test_subprocess_runner_captures_output_and_merges_env(tmp_path: Path) -> None:
    result = SubprocessRunner().run(
        [sys.executable, "-c", "import os, sys; print(os.environ['QFT_X'], os.getcwd()); print(sys.stdin.read())"],
        cwd=tmp_path,
        env={"QFT_X": "hello"},
        stdin="piped",
    )
    assert result.returncode == 0
    assert result.stdout.splitlines() == [f"hello {tmp_path}", "piped"]
    assert result.args[0] == sys.executable


def test_subprocess_runner_check_raises_on_failure() -> None:
    cmd = [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"]
    with pytest.raises(CommandError, match="exited with 3\nboom"):
        SubprocessRunner().run(cmd)
    unchecked = SubprocessRunner().run(cmd, check=False)
    assert (unchecked.returncode, unchecked.stderr) == (3, "boom")


def test_subprocess_runner_without_capture_returns_empty_strings() -> None:
    result = SubprocessRunner().run([sys.executable, "-c", "pass"], capture=False)
    assert (result.stdout, result.stderr) == ("", "")


def test_subprocess_runner_missing_program() -> None:
    with pytest.raises(CommandError, match="program not found: qft-no-such-program"):
        SubprocessRunner().run(["qft-no-such-program"])


def test_subprocess_runner_timeout() -> None:
    with pytest.raises(CommandError, match=r"timed out after 0.2s"):
        SubprocessRunner().run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.2)


def test_subprocess_runner_which() -> None:
    runner = SubprocessRunner()
    assert runner.which("qft-no-such-program") is None
    assert runner.which(Path(sys.executable).name) is not None


def test_fake_runner_matching_order_and_defaults(runner: FakeRunner) -> None:
    runner.reply(["git", "rev-parse"], stdout="abc\n")
    runner.reply_when(lambda args: "--version" in args, stdout="1.0")
    runner.reply(["git"], returncode=1, stderr="nope")
    assert runner.run(["git", "rev-parse", "HEAD"]).stdout == "abc\n"
    assert runner.run(["docker", "--version"]).stdout == "1.0"
    assert runner.run(["git", "status"], check=False).returncode == 1
    with pytest.raises(CommandError, match="nope"):
        runner.run(["git", "log"])
    assert runner.run(["ls"]) == Completed(("ls",), 0)
    assert runner.commands() == ["git rev-parse HEAD", "docker --version", "git status", "git log", "ls"]
    assert runner.which("git") == "/usr/bin/git"
    runner.available.discard("git")
    assert runner.which("git") is None


# --- http -------------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> Any:
        return self.payload


def test_wait_for_http_returns_once_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    answers: list[FakeResponse | Exception] = [requests.ConnectionError(), FakeResponse(502), FakeResponse(200)]

    def fake_get(url: str, timeout: float) -> FakeResponse:
        assert (url, timeout) == ("http://x/health", 5)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(http, "time", clock)
    monkeypatch.setattr(http.requests, "get", fake_get)
    http.wait_for_http("http://x/health", timeout=60, interval=3)
    assert answers == []
    assert clock.sleeps == [3, 3]


@pytest.mark.parametrize(
    ("answer", "last"),
    [(FakeResponse(503), "HTTP 503"), (requests.Timeout(), "Timeout")],
)
def test_wait_for_http_times_out_with_last_reason(
    monkeypatch: pytest.MonkeyPatch, answer: FakeResponse | Exception, last: str
) -> None:
    def fake_get(*_: object, **__: object) -> FakeResponse:
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(http, "time", FakeClock())
    monkeypatch.setattr(http.requests, "get", fake_get)
    with pytest.raises(HealthTimeoutError, match=rf"http://x/ not healthy after 10s \({last}\)"):
        http.wait_for_http("http://x/", timeout=10)


def test_wait_for_http_zero_timeout_never_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "time", FakeClock())
    monkeypatch.setattr(http.requests, "get", lambda *_, **__: pytest.fail("polled"))
    with pytest.raises(HealthTimeoutError, match=r"\(no response yet\)"):
        http.wait_for_http("http://x/", timeout=0)


def test_get_json_passes_headers_and_decodes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_get(url: str, headers: dict[str, str] | None, timeout: float) -> FakeResponse:
        seen.update(url=url, headers=headers, timeout=timeout)
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(http.requests, "get", fake_get)
    assert http.get_json("http://x/api", headers={"A": "b"}) == {"ok": True}
    assert seen == {"url": "http://x/api", "headers": {"A": "b"}, "timeout": 10}


def test_get_json_raises_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http.requests, "get", lambda *_, **__: FakeResponse(404))
    with pytest.raises(requests.HTTPError, match="404"):
        http.get_json("http://x/api")


def test_post_json(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], headers: dict[str, str] | None, timeout: float) -> FakeResponse:
        seen.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResponse(201, [1, 2])

    monkeypatch.setattr(http.requests, "post", fake_post)
    assert http.post_json("http://x/api", {"k": "v"}) == [1, 2]
    assert seen == {"url": "http://x/api", "json": {"k": "v"}, "headers": None, "timeout": 30}
    monkeypatch.setattr(http.requests, "post", lambda *_, **__: FakeResponse(500))
    with pytest.raises(requests.HTTPError, match="500"):
        http.post_json("http://x/api", {})


# --- ssh --------------------------------------------------------------------------------------------------

SSH_PREFIX = "ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new me@mac"


def test_ssh_run_quotes_the_remote_command(runner: FakeRunner) -> None:
    runner.reply(["ssh"], stdout="out")
    assert Ssh(runner, "me@mac").run("echo", "a b", "$HOME").stdout == "out"
    assert runner.calls[0].args[-2:] == ("--", "echo 'a b' '$HOME'")
    assert runner.commands() == [f"{SSH_PREFIX} -- echo 'a b' '$HOME'"]


@pytest.mark.parametrize(("returncode", "reachable"), [(0, True), (255, False)])
def test_ssh_reachable(runner: FakeRunner, returncode: int, reachable: bool) -> None:
    runner.reply(["ssh"], returncode=returncode)
    assert Ssh(runner, "me@mac").reachable() is reachable
    assert runner.commands() == [f"{SSH_PREFIX} -- true"]


def test_ssh_reverse_tunnel_starts_a_detached_process(runner: FakeRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[list[str], dict[str, Any]]] = []

    class FakePopen:
        pid = 4242

        def __init__(self, cmd: list[str], **kwargs: Any) -> None:
            started.append((cmd, kwargs))

    monkeypatch.setattr(ssh.subprocess, "Popen", FakePopen)
    assert Ssh(runner, "me@mac").reverse_tunnel(8011, 30001) == 4242
    cmd, kwargs = started[0]
    assert " ".join(cmd) == f"{SSH_PREFIX.removesuffix(' me@mac')} -N -R 8011:127.0.0.1:30001 me@mac"
    assert kwargs == {"start_new_session": True, "stdin": subprocess.DEVNULL}
    assert runner.calls == []


def test_stop_process_signals_the_group(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(ssh.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    stop_process(None)
    stop_process(0)
    stop_process(123)
    assert killed == [(123, signal.SIGTERM)]


@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_stop_process_tolerates_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, error: type[OSError]
) -> None:
    def killpg(*_: int) -> None:
        raise error

    monkeypatch.setattr(ssh.os, "killpg", killpg)
    stop_process(77)
    assert ("cannot stop process 77" in caplog.text) is (error is PermissionError)


@pytest.mark.parametrize(
    ("error", "alive"),
    [(None, True), (ProcessLookupError, False), (PermissionError, True)],
)
def test_process_alive(monkeypatch: pytest.MonkeyPatch, error: type[OSError] | None, alive: bool) -> None:
    probed: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        probed.append((pid, sig))
        if error:
            raise error

    monkeypatch.setattr(ssh.os, "kill", kill)
    assert process_alive(None) is False
    assert process_alive(55) is alive
    assert probed == [(55, 0)]


# --- adb --------------------------------------------------------------------------------------------------

ADB = "docker exec emu adb"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(adb_module, "time", fake)
    return fake


def test_adb_command_and_shell(runner: FakeRunner) -> None:
    adb = Adb(runner, "emu")
    runner.reply([*adb.prefix(), "shell", "echo"], stdout="  hi \n")
    assert adb.command() == ADB
    assert adb.shell("echo") == "hi"
    assert runner.commands() == [f"{ADB} shell echo"]


class ScriptedRunner(FakeRunner):
    """A FakeRunner replying from per-command scripts: the first key found in the command line pops its next reply (default: success)."""

    def __init__(self, scripts: dict[str, list[tuple[int, str]]]) -> None:
        super().__init__()
        self.scripts = scripts

    def run(self, cmd: Any, **kwargs: Any) -> Completed:
        line = " ".join(cmd)
        key = next((key for key in self.scripts if key in line), None)
        returncode, stdout = self.scripts[key].pop(0) if key else (0, "")
        self.responses = [(tuple(cmd), Completed((), returncode, stdout))]
        return super().run(cmd, check=kwargs.get("check", True))


@pytest.mark.usefixtures("clock")
def test_adb_wait_for_boot_waits_for_launcher() -> None:
    runner = ScriptedRunner(
        {
            "getprop": [(1, ""), (0, "1\n"), (0, "1\n")],
            "mCurrentFocus": [(0, "mCurrentFocus=null"), (0, "mCurrentFocus=Window{launcher}")],
        }
    )
    Adb(runner, "emu").wait_for_boot(timeout=60)
    assert runner.commands() == [
        f"{ADB} wait-for-device shell getprop sys.boot_completed",
        f"{ADB} wait-for-device shell getprop sys.boot_completed",
        f"{ADB} shell dumpsys window | grep -i mCurrentFocus",
        f"{ADB} wait-for-device shell getprop sys.boot_completed",
        f"{ADB} shell dumpsys window | grep -i mCurrentFocus",
    ]
    assert runner.scripts == {"getprop": [], "mCurrentFocus": []}


def test_adb_wait_for_boot_times_out_with_last_state(runner: FakeRunner, clock: FakeClock) -> None:
    runner.reply_when(lambda args: "getprop" in args, returncode=1, stderr="error: no devices/emulators found")
    with pytest.raises(HealthTimeoutError, match=r"in emu not ready after 12s \(error: no devices/emulators found\)"):
        Adb(runner, "emu").wait_for_boot(timeout=12)
    assert clock.sleeps == [5, 5, 5]


@pytest.mark.usefixtures("clock")
def test_adb_wait_for_boot_reports_launcher_wait(runner: FakeRunner) -> None:
    runner.reply_when(lambda args: "getprop" in args, stdout="1\n")
    runner.reply_when(lambda args: "dumpsys window | grep -i mCurrentFocus" in args, stdout="mCurrentFocus=null")
    with pytest.raises(HealthTimeoutError, match=r"booted, waiting for the launcher \('mCurrentFocus=null'\)"):
        Adb(runner, "emu").wait_for_boot(timeout=5)


@pytest.mark.usefixtures("clock")
def test_adb_wait_for_boot_without_output(runner: FakeRunner) -> None:
    runner.reply_when(lambda args: "getprop" in args, stdout="0")
    with pytest.raises(HealthTimeoutError, match=r"\(0\)"):
        Adb(runner, "emu").wait_for_boot(timeout=1)
    runner.responses.clear()
    runner.reply_when(lambda args: "getprop" in args, returncode=1)
    with pytest.raises(HealthTimeoutError, match=r"\(not booted yet\)"):
        Adb(runner, "emu").wait_for_boot(timeout=1)


def test_adb_copy_install_launch_stop(runner: FakeRunner, tmp_path: Path) -> None:
    adb = Adb(runner, "emu")
    adb.copy_in(tmp_path / "qfield.apk", "/tmp/qfield.apk")
    adb.install("/tmp/qfield.apk")
    adb.launch("ch.opengis.qfield")
    adb.force_stop("ch.opengis.qfield")
    assert runner.commands() == [
        f"docker cp {tmp_path / 'qfield.apk'} emu:/tmp/qfield.apk",
        f"{ADB} install -r -g /tmp/qfield.apk",
        f"{ADB} shell monkey -p ch.opengis.qfield -c android.intent.category.LAUNCHER 1",
        f"{ADB} shell am force-stop ch.opengis.qfield",
    ]


@pytest.mark.parametrize(("stdout", "version"), [("    versionName=3.4.5\n", "3.4.5"), ("", None)])
def test_adb_installed_version(runner: FakeRunner, stdout: str, version: str | None) -> None:
    runner.reply_when(lambda args: "versionName" in args[-1], stdout=stdout)
    assert Adb(runner, "emu").installed_version("ch.opengis.qfield") == version
    assert runner.commands() == [f"{ADB} shell dumpsys package ch.opengis.qfield | grep versionName"]


@pytest.mark.usefixtures("clock")
@pytest.mark.parametrize(
    ("root_code", "id_out", "expected"),
    [(1, "", False), (0, "uid=0(root) gid=0(root)", True), (0, "uid=2000(shell)", False)],
)
def test_adb_root(runner: FakeRunner, root_code: int, id_out: str, expected: bool) -> None:
    runner.reply([*Adb(runner, "emu").prefix(), "root"], returncode=root_code)
    runner.reply([*Adb(runner, "emu").prefix(), "shell", "id"], stdout=id_out)
    assert Adb(runner, "emu").root() is expected
    expected_cmds = [f"{ADB} root"] + ([f"{ADB} wait-for-device", f"{ADB} shell id"] if root_code == 0 else [])
    assert runner.commands() == expected_cmds


def test_adb_su_files(runner: FakeRunner) -> None:
    adb = Adb(runner, "emu")
    runner.reply_when(lambda args: "find" in args[-1], stdout="/data/a/QField.conf\n\n /data/b/QField.conf \n")
    runner.reply_when(lambda args: "cat" in args[-1], stdout="[General]\n")
    runner.reply_when(lambda args: "stat -c" in args[-1], stdout="u0_a123\n")
    assert adb.find_files("/data/data/p", "*.conf") == ["/data/a/QField.conf", "/data/b/QField.conf"]
    assert adb.read_file("/data/x y.conf") == "[General]\n"
    assert adb.package_user("p") == "u0_a123"
    assert runner.commands() == [
        f"{ADB} shell su root sh -c 'find /data/data/p -name '\"'\"'*.conf'\"'\"' 2>/dev/null'",
        f"{ADB} shell su root sh -c 'cat '\"'\"'/data/x y.conf'\"'\"''",
        f"{ADB} shell su root sh -c 'stat -c %U /data/data/p'",
    ]


def test_adb_package_user_missing(runner: FakeRunner) -> None:
    assert Adb(runner, "emu").package_user("p") is None


def test_adb_su_check_raises(runner: FakeRunner) -> None:
    runner.reply_when(lambda args: "su root" in args[-1], returncode=1, stderr="denied")
    assert Adb(runner, "emu").su("id").returncode == 1
    with pytest.raises(CommandError, match="denied"):
        Adb(runner, "emu").su("id", check=True)


@pytest.mark.parametrize("owner", ["u0_a5", None])
def test_adb_write_file(runner: FakeRunner, owner: str | None) -> None:
    Adb(runner, "emu").write_file("/data/d/f.conf", "a='b'\n", owner=owner)
    scripts = [call.args[-1] for call in runner.calls]
    assert scripts[0].startswith("su root sh -c ")
    assert "mkdir -p /data/d && printf %s" in scripts[0]
    assert "> /data/d/f.conf" in scripts[0]
    if owner:
        assert scripts[1] == "su root sh -c 'chown -R u0_a5:u0_a5 /data/d'"
    assert len(scripts) == (2 if owner else 1)


def test_adb_write_file_fails_loudly(runner: FakeRunner) -> None:
    runner.reply_when(lambda args: "mkdir" in args[-1], returncode=1, stderr="read-only")
    with pytest.raises(CommandError, match="read-only"):
        Adb(runner, "emu").write_file("/d/f", "x", owner="u")
    assert len(runner.calls) == 1


# --- docker -----------------------------------------------------------------------------------------------


def test_published_host_ports(runner: FakeRunner) -> None:
    runner.reply(
        ["docker", "ps"],
        stdout="0.0.0.0:30000->80/tcp, :::30000->80/tcp\n\n127.0.0.1:30001->5432/tcp\n5432/tcp\n",
    )
    assert published_host_ports(runner) == {30000, 30001}
    assert runner.commands() == ["docker ps -a --format {{.Ports}}"]


def test_published_host_ports_without_daemon(runner: FakeRunner) -> None:
    runner.reply(["docker", "ps"], returncode=1, stdout="0.0.0.0:1->2")
    assert published_host_ports(runner) == set()


def test_daemon_available(runner: FakeRunner) -> None:
    assert daemon_available(runner) is True
    runner.reply(["docker", "info"], returncode=1)
    assert daemon_available(runner) is False
    runner.available.discard("docker")
    calls = len(runner.calls)
    assert daemon_available(runner) is False
    assert len(runner.calls) == calls


def test_compose_version_and_image_exists(runner: FakeRunner) -> None:
    runner.reply(["docker", "compose", "version"], stdout="2.29.1\n")
    runner.reply(["docker", "image", "inspect", "missing"], returncode=1)
    assert compose_version(runner) == "2.29.1"
    assert image_exists(runner, "present") is True
    assert image_exists(runner, "missing") is False
    runner.responses.insert(0, (("docker", "compose"), Completed((), 1)))
    assert compose_version(runner) is None


def test_ensure_and_remove_network(runner: FakeRunner) -> None:
    runner.reply(["docker", "network", "inspect", "absent"], returncode=1)
    ensure_network(runner, "absent")
    ensure_network(runner, "present")
    remove_network(runner, "absent")
    remove_network(runner, "present")
    assert runner.commands() == [
        "docker network inspect absent",
        "docker network create absent",
        "docker network inspect present",
        "docker network inspect absent",
        "docker network inspect present",
        "docker network rm present",
    ]


def test_list_images_skips_dangling(runner: FakeRunner) -> None:
    runner.reply(["docker", "image", "ls"], stdout="qft/app:v1\nqft/app:<none>\n\nqft/app:v2\n")
    assert list_images(runner, "qft/*") == ["qft/app:v1", "qft/app:v2"]
    assert runner.commands() == [
        "docker image ls --format {{.Repository}}:{{.Tag}} --filter reference=qft/*",
    ]


class RecordingRunner(FakeRunner):
    """A FakeRunner that also keeps the keyword arguments of each call."""

    def __init__(self) -> None:
        super().__init__()
        self.kwargs: list[dict[str, Any]] = []

    def run(self, cmd: Any, **kwargs: Any) -> Completed:
        self.kwargs.append(kwargs)
        return super().run(cmd, check=kwargs.get("check", True))


def test_compose_base_with_everything(tmp_path: Path) -> None:
    runner = RecordingRunner()
    compose = Compose(
        runner,
        "qft-a",
        [tmp_path / "a.yml", tmp_path / "b.yml"],
        env_file=tmp_path / ".env",
        project_directory=tmp_path,
        profiles=("worker",),
        env={"X": "1"},
    )
    assert compose.base() == [
        *["docker", "compose", "-p", "qft-a", "--project-directory", str(tmp_path)],
        *["--env-file", str(tmp_path / ".env"), "-f", str(tmp_path / "a.yml"), "-f", str(tmp_path / "b.yml")],
        *["--profile", "worker"],
    ]
    compose.up("app", build=True, pull=True)
    assert runner.calls[0].args[-5:] == ("--remove-orphans", "--build", "--pull", "always", "app")
    assert runner.kwargs[0] == {
        "cwd": tmp_path,
        "env": {"PWD": str(tmp_path), "X": "1"},
        "check": True,
        "capture": False,
        "timeout": 1800,
    }


def test_compose_commands_minimal() -> None:
    runner = RecordingRunner()
    compose = Compose(runner, "p", [Path("c.yml")])
    compose.up()
    compose.stop()
    compose.down(volumes=False)
    compose.down(volumes=True)
    compose.exec_run("app", "manage.py", "migrate")
    compose.logs("app", follow=True, tail="10")
    compose.logs()
    base = "docker compose -p p -f c.yml"
    assert runner.commands() == [
        f"{base} up -d --remove-orphans --no-build",
        f"{base} stop",
        f"{base} down --remove-orphans",
        f"{base} down --remove-orphans -v",
        f"{base} run --rm --no-deps app manage.py migrate",
        f"{base} logs --tail 10 -f app",
        f"{base} logs --tail 200",
    ]
    assert all(kwargs["env"] is None and kwargs["cwd"] is None for kwargs in runner.kwargs)
    assert [kwargs["check"] for kwargs in runner.kwargs] == [True, False, False, False, True, False, False]
