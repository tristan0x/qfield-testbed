from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests
from qfieldcloud_sdk import sdk

from qfield_testbed.backends.ios_simulator import IosSimulatorBackend
from qfield_testbed.errors import TestbedError
from qfield_testbed.provision import android_settings, ios_settings, qfieldcloud_account
from qfield_testbed.runtimes import adb as adb_module
from qfield_testbed.runtimes import http as http_module
from qfield_testbed.runtimes.adb import Adb
from qfield_testbed.runtimes.shell import FakeRunner
from qfield_testbed.runtimes.ssh import Ssh
from tests.unit.test_runtimes import FakeClock, ScriptedRunner

URL = "http://127.0.0.1:30000/"

# --- qfieldcloud_account ----------------------------------------------------------------------------------


def test_api_normalizes_trailing_slash() -> None:
    assert qfieldcloud_account.api("http://h:1") == "http://h:1/api/v1/"
    assert qfieldcloud_account.api("http://h:1///") == "http://h:1/api/v1/"


def test_login_returns_token(monkeypatch: pytest.MonkeyPatch) -> None:
    posted: list[tuple[str, dict[str, Any]]] = []

    def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
        posted.append((url, payload))
        return {"token": 1234}

    monkeypatch.setattr(qfieldcloud_account, "post_json", post_json)
    assert qfieldcloud_account.login(URL, "testbed", "pw") == "1234"
    assert posted == [(f"{URL}api/v1/auth/token/", {"username": "testbed", "password": "pw"})]


def test_login_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qfieldcloud_account, "post_json", lambda *_: {"detail": "nope"})
    with pytest.raises(TestbedError, match=r"login of 'testbed' on .* returned no token: \{'detail': 'nope'\}"):
        qfieldcloud_account.login(URL, "testbed", "pw")


def test_login_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def post_json(*_: object) -> None:
        raise requests.HTTPError("400 Bad Request")

    monkeypatch.setattr(qfieldcloud_account, "post_json", post_json)
    with pytest.raises(TestbedError, match=r"login of 'testbed' failed on http://127.0.0.1:30000/: 400 Bad Request"):
        qfieldcloud_account.login(URL, "testbed", "pw")


def test_server_info(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def get_json(url: str) -> dict[str, Any]:
        fetched.append(url)
        return {"version": "26.28"}

    monkeypatch.setattr(qfieldcloud_account, "get_json", get_json)
    assert qfieldcloud_account.server_info(URL) == {"version": "26.28"}
    assert fetched == [f"{URL}api/v1/server/info/"]


def test_server_info_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = FakeClock()
    monkeypatch.setattr(http_module, "time", clock)

    def get_json(*_: object) -> None:
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(qfieldcloud_account, "get_json", get_json)
    with pytest.raises(TestbedError, match=r"cannot read server info from http://127\.0\.0\.1:30000/: refused"):
        qfieldcloud_account.server_info(URL)
    assert clock.now >= 60  # retried for the whole window before giving up


def test_login_survives_a_dropped_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_module, "time", FakeClock())
    answers: list[Exception | dict[str, Any]] = [requests.ConnectionError("reset"), {"token": "t"}]

    def post_json(*_: object) -> dict[str, Any]:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(qfieldcloud_account, "post_json", post_json)
    assert qfieldcloud_account.login(URL, "testbed", "pw") == "t"
    assert answers == []


def test_sample_project_dir_ships_the_project() -> None:
    assert (qfieldcloud_account.sample_project_dir() / "survey.qgs").is_file()


class FakeClient:
    """Stands in for ``qfieldcloud_sdk.sdk.Client``; ``statuses`` are the successive job statuses."""

    projects: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    instances: list[FakeClient] = []

    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self.log: list[tuple[Any, ...]] = []
        FakeClient.instances.append(self)

    def list_projects(self) -> list[dict[str, Any]]:
        return self.projects

    def create_project(self, name: str, owner: str, description: str) -> dict[str, Any]:
        self.log.append(("create", name, owner, description))
        return {"id": "new-id"}

    def upload_files(self, project_id: str, **kwargs: Any) -> None:
        self.log.append(("upload", project_id, kwargs))

    def job_trigger(self, project_id: str, job_type: Any, *, force: bool) -> dict[str, Any]:
        self.log.append(("trigger", project_id, job_type, force))
        return {"id": "job-1", "status": "pending"}

    def job_status(self, job_id: str) -> dict[str, Any]:
        self.log.append(("status", job_id))
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def get_project(self, project_id: str) -> dict[str, Any]:
        return {"id": project_id, "status": "ok"}


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(sdk, "Client", FakeClient)
    monkeypatch.setattr(qfieldcloud_account, "time", clock)
    monkeypatch.setattr(http_module, "time", clock)
    monkeypatch.setattr(FakeClient, "instances", [])
    return clock


def test_ensure_demo_project_creates_and_processes(fake_sdk: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeClient, "projects", [{"name": "testbed-demo", "owner": "someone-else", "id": "x"}])
    monkeypatch.setattr(FakeClient, "statuses", [{"status": "started"}, {"status": "finished"}])
    project_id, status = qfieldcloud_account.ensure_demo_project(URL, "tok", "testbed")
    assert (project_id, status) == ("new-id", "ok")
    client = FakeClient.instances[0]
    assert (client.url, client.token) == (f"{URL}api/v1/", "tok")
    assert client.log == [
        ("create", "testbed-demo", "testbed", "qfield-testbed sample project"),
        (
            "upload",
            "new-id",
            {
                "upload_type": sdk.FileTransferType.PROJECT,
                "project_path": str(qfieldcloud_account.sample_project_dir()),
                "filter_glob": "*",
                "throw_on_error": True,
            },
        ),
        ("trigger", "new-id", sdk.JobTypes.PROCESS_PROJECTFILE, True),
        ("status", "job-1"),
        ("status", "job-1"),
    ]
    assert fake_sdk.sleeps == [2, 2]


def test_ensure_demo_project_reuses_existing_and_reports_failure(
    fake_sdk: FakeClock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(FakeClient, "projects", [{"name": "testbed-demo", "owner": "testbed", "id": 42}])
    monkeypatch.setattr(FakeClient, "statuses", [{"status": "failed", "feedback": {"error": "bad qgs"}}])
    project_id, status = qfieldcloud_account.ensure_demo_project(URL, "tok", "testbed", tmp_path)
    assert (project_id, status) == ("42", "ok")
    log = FakeClient.instances[0].log
    assert [entry[0] for entry in log] == ["upload", "trigger", "status", "status"]
    assert log[0][2]["project_path"] == str(tmp_path)
    assert "ended with status 'failed': bad qgs" in caplog.text
    assert fake_sdk.sleeps == [2]


def test_ensure_demo_project_gives_up_after_timeout(fake_sdk: FakeClock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FakeClient, "projects", [])
    monkeypatch.setattr(FakeClient, "statuses", [{"status": "started"}])
    qfieldcloud_account.ensure_demo_project(URL, "tok", "testbed", job_timeout=5)
    assert fake_sdk.sleeps == [2, 2, 2]


class DroppingClient(FakeClient):
    """Creates the project, then loses the response once; uploads fail once with a 503."""

    created: list[str] = []
    upload_failures = 1

    def list_projects(self) -> list[dict[str, Any]]:
        return [{"name": "testbed-demo", "owner": "testbed", "id": i} for i in self.created]

    def create_project(self, name: str, owner: str, description: str) -> dict[str, Any]:
        super().create_project(name, owner, description)
        DroppingClient.created.append("made-once")
        raise requests.ConnectionError("Remote end closed connection without response")

    def upload_files(self, project_id: str, **kwargs: Any) -> None:
        if DroppingClient.upload_failures:
            DroppingClient.upload_failures -= 1
            response = requests.Response()
            response.status_code = 503
            raise requests.HTTPError("503", response=response)
        super().upload_files(project_id, **kwargs)


def test_ensure_demo_project_rides_out_dropped_connections(
    fake_sdk: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sdk, "Client", DroppingClient)
    monkeypatch.setattr(DroppingClient, "created", [])
    monkeypatch.setattr(FakeClient, "statuses", [{"status": "finished"}])
    project_id, status = qfieldcloud_account.ensure_demo_project(URL, "tok", "testbed")
    assert (project_id, status) == ("made-once", "ok")
    log = FakeClient.instances[0].log
    assert [entry[0] for entry in log].count("create") == 1  # the lost response did not create a second project
    assert [entry[0] for entry in log].count("upload") == 1
    assert len(fake_sdk.sleeps) >= 2


# --- android settings -------------------------------------------------------------------------------------

ADB = "docker exec emu adb"


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(android_settings, "time", FakeClock())
    monkeypatch.setattr(adb_module, "time", FakeClock())


@pytest.mark.usefixtures("no_sleep")
def test_android_seed_without_root(runner: FakeRunner, caplog: pytest.LogCaptureFixture) -> None:
    runner.reply_when(lambda args: args[-1] == "root", returncode=1)
    runner.reply_when(lambda args: args[-1] == "su root sh -c id", stdout="uid=2000(shell)")
    assert android_settings.seed_qfield_settings(Adb(runner, "emu"), URL, "testbed") is None
    assert runner.commands() == [f"{ADB} root", f"{ADB} shell su root sh -c id"]
    assert "cannot be pre-filled" in caplog.text


@pytest.mark.usefixtures("no_sleep")
def test_android_seed_merges_into_found_file(runner: FakeRunner) -> None:
    conf = "/data/data/ch.opengis.qfield/files/settings/OPENGIS.ch/QField.conf"
    runner.reply_when(lambda args: args[-1] == "root", returncode=1)
    runner.reply_when(lambda args: args[-1] == "su root sh -c id", stdout="uid=0(root)")
    runner.reply_when(lambda args: "find " in args[-1], stdout=conf + "\n")
    runner.reply_when(lambda args: "cat " in args[-1], stdout="[General]\nKeep=1\n")
    runner.reply_when(lambda args: "stat -c" in args[-1], stdout="u0_a77\n")
    assert android_settings.seed_qfield_settings(Adb(runner, "emu"), URL, "testbed") == conf
    commands = runner.commands()
    assert commands[2] == f"{ADB} shell monkey -p ch.opengis.qfield -c android.intent.category.LAUNCHER 1"
    assert commands[3] == f"{ADB} shell am force-stop ch.opengis.qfield"
    write = next(call.args[-1] for call in runner.calls if "printf" in call.args[-1])
    assert "Keep=1" in write
    assert f"url={URL}" in write
    assert "username=testbed" in write
    assert (
        commands[-1]
        == f"{ADB} shell su root sh -c 'chown -R u0_a77:u0_a77 /data/data/ch.opengis.qfield/files/settings/OPENGIS.ch'"
    )


@pytest.mark.usefixtures("no_sleep")
def test_android_seed_defaults_to_known_path(runner: FakeRunner) -> None:
    runner.reply_when(lambda args: args[-1] == "id", stdout="uid=0(root) gid=0(root)")
    assert android_settings.seed_qfield_settings(Adb(runner, "emu"), URL, "testbed") == android_settings.DEFAULT_CONF
    assert not any("cat " in command for command in runner.commands())
    write = next(call.args[-1] for call in runner.calls if "printf" in call.args[-1])
    assert write.startswith("su root sh -c 'mkdir -p /data/data/ch.opengis.qfield/files/settings/OPENGIS.ch")
    assert "[QFieldCloud]" in write
    # no package user: no chown
    assert "chown" not in runner.commands()[-1]


# --- iOS settings -----------------------------------------------------------------------------------------

UDID = "AAAA-BBBB"
CONTAINER = "/Users/me/Library/Developer/CoreSimulator/Devices/AAAA/data/Containers/Data/Application/XYZ"
PLIST = f"{CONTAINER}/Library/Preferences/ch.opengis.qfield.plist"


def seed_ios(runner: FakeRunner) -> str | None:
    ssh = Ssh(runner, "me@mac")
    return ios_settings.seed_qfield_settings(ssh, IosSimulatorBackend(ssh, "iPhone 16"), UDID, URL, "testbed")


def remote(runner: FakeRunner) -> list[str]:
    return [call.args[-1] for call in runner.calls]


def test_ios_seed_without_container(runner: FakeRunner, caplog: pytest.LogCaptureFixture) -> None:
    runner.reply_when(lambda args: "get_app_container" in args[-1], returncode=2)
    assert seed_ios(runner) is None
    assert remote(runner) == [
        f"xcrun simctl launch {UDID} ch.opengis.qfield",
        "sleep 5",
        f"xcrun simctl terminate {UDID} ch.opengis.qfield",
        f"xcrun simctl get_app_container {UDID} ch.opengis.qfield data",
    ]
    assert "no data container" in caplog.text


def test_ios_seed_replaces_keys(runner: FakeRunner) -> None:
    runner.reply_when(lambda args: "get_app_container" in args[-1], stdout=CONTAINER + "\n")
    assert seed_ios(runner) == PLIST
    assert remote(runner)[4:] == [
        f"plutil -replace QFieldCloud.url -string {URL} {PLIST}",
        f"plutil -replace QFieldCloud.username -string testbed {PLIST}",
        f"plutil -replace QFieldCloud.urls -string {URL} {PLIST}",
    ]


def test_ios_seed_creates_missing_plist() -> None:
    runner = ScriptedRunner(
        {
            "get_app_container": [(0, CONTAINER)],
            "plutil -replace QFieldCloud.url ": [(1, ""), (0, "")],
        }
    )
    assert seed_ios(runner) == PLIST
    assert remote(runner)[4:] == [
        f"plutil -replace QFieldCloud.url -string {URL} {PLIST}",
        f"plutil -create xml1 {PLIST}",
        f"plutil -replace QFieldCloud.url -string {URL} {PLIST}",
        f"plutil -replace QFieldCloud.username -string testbed {PLIST}",
        f"plutil -replace QFieldCloud.urls -string {URL} {PLIST}",
    ]


@pytest.mark.parametrize(
    ("create", "retry", "commands"),
    [((1, ""), None, 6), ((0, ""), (1, ""), 7)],
)
def test_ios_seed_gives_up(
    caplog: pytest.LogCaptureFixture,
    create: tuple[int, str],
    retry: tuple[int, str] | None,
    commands: int,
) -> None:
    runner = ScriptedRunner(
        {
            "get_app_container": [(0, CONTAINER)],
            "plutil -replace": [(1, ""), *([retry] if retry else [])],
            "plutil -create": [create],
        }
    )
    assert seed_ios(runner) is None
    assert len(runner.calls) == commands
    assert f"cannot write QFieldCloud.url in {PLIST}" in caplog.text
