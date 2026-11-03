"""`qft mcp serve`: the tools, their annotations and schemas, errors as the CLI's error object, the agent session."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from qfield_testbed import caller
from qfield_testbed.cli._console import OUTPUT
from qfield_testbed.mcp import server as mcp_server
from qfield_testbed.mcp.server import build_server, prepare_process
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401 - fixtures

pytestmark = pytest.mark.anyio

SPEC = "name: demo\nqgis: {version: host}\n"

TOOLS = {
    "testbed_doctor": "read-only",
    "spec_suggest": "read-only",
    "spec_validate": "read-only",
    "deployment_ensure": "changes",
    "deployment_list": "read-only",
    "deployment_show": "read-only",
    "deployment_start": "changes",
    "deployment_stop": "changes",
    "deployment_restart": "changes",
    "deployment_keep": "changes",
    "deployment_remove": "destructive",
    "deployment_logs": "read-only",
    "bisect_start": "changes",
    "bisect_step": "destructive",
    "bisect_run": "destructive",
    "job_status": "read-only",
    "job_logs": "read-only",
    "job_list": "read-only",
    "bisect_status": "read-only",
    "cache_list": "read-only",
    "cache_prune": "destructive",
    "gc": "destructive",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: FakeRunner, fake_components: None) -> Iterator[Path]:  # noqa: F811
    del fake_components  # only its setup matters
    isolate(tmp_path, monkeypatch, runner)
    yield tmp_path
    caller.set_agent_session(None)


@pytest.fixture
async def client(home: Path) -> AsyncIterator[Client]:
    async with Client(build_server()) as connected:
        yield connected


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None, result
    if result.is_error:
        assert json.loads(result.content[0].text) == result.structured_content  # type: ignore[union-attr]
    else:
        assert not result.is_error
    return result.structured_content


async def done(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    """A long tool that finishes within the call: its result, unwrapped from `{job, result}`."""
    answer = await call(client, tool, **arguments)
    if "error" in answer:
        return answer
    assert answer["job"] is None, answer
    return answer["result"]


async def test_every_tool_is_listed_with_its_annotations_and_schemas(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == set(TOOLS)
    for name, kind in TOOLS.items():
        tool = tools[name]
        assert tool.description, name
        assert tool.input_schema["type"] == "object", name
        assert tool.output_schema is not None, name
        assert tool.output_schema["type"] == "object", name
        hints = tool.annotations
        assert hints is not None, name
        assert hints.read_only_hint is (kind == "read-only"), name
        if kind != "read-only":
            assert hints.destructive_hint is (kind == "destructive"), name


async def test_deployment_run_only_with_allow_run(home: Path) -> None:
    async with Client(build_server(allow_run=True)) as connected:
        tools = {tool.name: tool for tool in (await connected.list_tools()).tools}
        run = tools["deployment_run"]
        assert run.annotations is not None
        assert run.annotations.destructive_hint
        assert run.annotations.open_world_hint
        spec = home / "demo.yaml"
        spec.write_text(SPEC)
        await done(connected, "deployment_ensure", spec_path=str(spec))
        result = await call(connected, "deployment_run", name="demo", command=[sys.executable, "-c", "print('hi')"])
        assert (result["exit_code"], result["stdout_tail"]) == (0, "hi\n")


async def test_a_deployment_lifecycle(client: Client, home: Path) -> None:
    ensured = await done(client, "deployment_ensure", spec_yaml=SPEC, timeout="1m")
    assert (ensured["name"], ensured["created"], ensured["status"], ensured["warnings"]) == (
        "demo",
        True,
        "running",
        [],
    )
    assert (await done(client, "deployment_ensure", spec_yaml=SPEC))["created"] is False
    assert (await call(client, "deployment_show", name="demo"))["components"]["qgis"]["profile"] == "testbed"
    assert [d["name"] for d in (await call(client, "deployment_list"))["deployments"]] == ["demo"]
    assert (await call(client, "deployment_logs", name="demo"))["components"] == {"qgis": "qgis says hi\n"}
    assert (await call(client, "deployment_stop", name="demo"))["status"] == "stopped"
    assert (await done(client, "deployment_start", name="demo"))["status"] == "running"
    assert (await done(client, "deployment_restart", name="demo"))["status"] == "running"
    assert (await call(client, "deployment_keep", name="demo", ttl="3d"))["ttl_s"] == 3 * 86400
    assert (await call(client, "gc"))["dry_run"] is True
    assert (await call(client, "cache_prune"))["dry_run"] is True
    assert set(await call(client, "cache_list")) == {"cache_dir", "entries", "total_bytes"}
    assert await call(client, "deployment_remove", name="demo") == {"name": "demo", "removed": True}
    applied = await call(client, "gc", dry_run=False)
    assert applied["dry_run"] is False


async def test_specs_from_a_file_or_text(client: Client, home: Path) -> None:
    spec = home / "demo.yaml"
    spec.write_text(SPEC)
    assert await call(client, "spec_validate", spec_path=str(spec)) == {"name": "demo", "components": ["qgis"]}
    both = await call(client, "spec_validate", spec_path=str(spec), spec_yaml=SPEC)
    assert both["error"]["code"] == "USAGE"
    invalid = await call(client, "spec_validate", spec_yaml="name: demo\nqgis: {version: nope:1}\n")
    assert invalid["error"]["code"] == "INVALID_SPEC"
    assert invalid["error"]["details"]["issues"]
    renamed = await done(client, "deployment_ensure", spec_yaml=SPEC, name="other")
    assert renamed["name"] == "other"


async def test_errors_carry_the_cli_error_object(client: Client) -> None:
    missing = await call(client, "deployment_show", name="nope")
    assert missing["error"] == {
        "code": "NOT_FOUND",
        "message": "No deployment named 'nope'.",
        "hint": "run `qft deployment list`",
        "docs": None,
        "details": {},
    }
    assert (await call(client, "bisect_status"))["error"]["code"] == "NOT_FOUND"
    assert (await call(client, "testbed_doctor", require=["warp-drive"]))["error"]["code"] == "USAGE"


async def test_unexpected_failures_are_errors_too(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(mcp_server.service, "cache_list", boom)
    failed = await call(client, "cache_list")
    assert failed["error"]["code"] == "ERROR"
    assert "disk on fire" in failed["error"]["message"]


async def test_the_server_is_one_agent_session(home: Path) -> None:
    session = prepare_process(None)
    assert session.startswith("mcp-")
    assert OUTPUT.non_interactive
    async with Client(build_server()) as connected:
        ensured = await done(connected, "deployment_ensure", spec_yaml=SPEC)
        assert ensured["status"] == "running"
        listed = (await call(connected, "deployment_list", agent_session=session))["deployments"]
        assert [(d["owner"], d["agent_session"]) for d in listed] == [("agent", session)]
    assert prepare_process("claude-1234") == "claude-1234"
    caller.set_agent_session(None)
    OUTPUT.non_interactive = False


def test_serve_needs_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "qfield_testbed.mcp.server", None)
    result = invoke(["--json", "mcp", "serve"])
    assert type(result.exception).__name__ == "PrerequisiteMissingError"
    assert "qfield-testbed[mcp]" in (result.exception.hint or "")  # type: ignore[union-attr]


def test_serve_over_stdio(tmp_path: Path) -> None:
    """The real process: stdout carries nothing but JSON-RPC, a tool answers."""
    env = {
        **os.environ,
        "QFT_HOME": str(tmp_path / "home"),
        "QFT_CACHE_DIR": str(tmp_path / "cache"),
        "QFT_CONFIG": str(tmp_path / "config.yaml"),
    }
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "deployment_list", "arguments": {}}},
    ]
    process = subprocess.Popen(
        [sys.executable, "-c", "from qfield_testbed.cli import main; main()", "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        process.stdin.write("".join(json.dumps(r) + "\n" for r in requests))
        process.stdin.flush()
        messages: dict[Any, dict[str, Any]] = {}
        while 3 not in messages:  # every line is one JSON-RPC message, or the test fails here
            message = json.loads(process.stdout.readline())
            assert message["jsonrpc"] == "2.0"
            messages[message.get("id")] = message
    finally:
        process.stdin.close()
        process.wait(timeout=30)
        assert process.stdout.read() == ""
        process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    assert len(messages[2]["result"]["tools"]) == len(TOOLS)
    assert messages[3]["result"]["structuredContent"] == {"deployments": []}


# every operation of `service`, and the tool that offers it — or why none does
OFFERED_BY = {
    "doctor": "testbed_doctor",
    "check_capabilities": "testbed_doctor",
    "require": "testbed_doctor",
    "spec_suggest": "spec_suggest",
    "spec_validate": "spec_validate",
    "deployment_ensure": "deployment_ensure",
    "deployment_list": "deployment_list",
    "deployment_show": "deployment_show",
    "deployment_start": "deployment_start",
    "deployment_stop": "deployment_stop",
    "deployment_restart": "deployment_restart",
    "deployment_keep": "deployment_keep",
    "removal_check": "deployment_remove",
    "deployment_remove": "deployment_remove",
    "deployment_logs": "deployment_logs",
    "deployment_run": "deployment_run",  # with --allow-run
    "deployment_env": None,  # shell variables, for `eval`: an MCP client reads deployment_show instead
    "gc_plan": "gc",
    "gc_apply": "gc",
    "cache_list": "cache_list",
    "cache_prune": "cache_prune",
    "bisect_session": "bisect_status",
    "bisect_summary": "bisect_status",
    "bisect_start": "bisect_start",
    "bisect_next": "bisect_step",
    "bisect_verdict": "bisect_step",
    "bisect_run": "bisect_run",
    "seconds": None,  # a helper of the others
}


async def test_no_operation_is_left_without_a_tool(home: Path) -> None:
    import inspect  # noqa: PLC0415

    from qfield_testbed import service  # noqa: PLC0415

    operations = {
        name
        for name, member in inspect.getmembers(service, inspect.isfunction)
        if member.__module__ == service.__name__ and not name.startswith("_")
    }
    assert operations == set(OFFERED_BY), "a new operation of service.py needs a tool here, or a reason not to"
    async with Client(build_server(allow_run=True)) as connected:
        tools = {tool.name for tool in (await connected.list_tools()).tools}
    assert {tool for tool in OFFERED_BY.values() if tool} <= tools
