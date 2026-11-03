"""The CLI (`qft --json`) and the MCP server answer the same: same data, same error codes and details.

Each scenario runs once through each adapter against the same state; only fields that change with every call
(timestamps of use) are left out of the comparison.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from qfield_testbed import caller
from qfield_testbed.mcp.server import build_server
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401 - fixtures

pytestmark = pytest.mark.anyio

SESSION = "claude-contract"
SPEC = "name: demo\nqgis: {version: host}\n"
VOLATILE = {"last_used_at", "expires_at", "warnings"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner: FakeRunner,
    fake_components: None,  # noqa: F811
) -> AsyncIterator[Client]:
    del fake_components  # only its setup matters
    isolate(tmp_path, monkeypatch, runner)
    (tmp_path / "demo.yaml").write_text(SPEC)
    async with Client(build_server()) as connected:
        yield connected
    caller.set_agent_session(None)


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [_stable(item) for item in value]
    return value


def cli(*args: str) -> dict[str, Any]:
    """The envelope of `qft --json --agent-session SESSION ARGS`: `data`, or `error`."""
    result = invoke(["--json", "--agent-session", SESSION, *args])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        # outside `main()` the CLI raises; build the envelope `main()` would print from the typed error
        from qfield_testbed.errors import TestbedError, error_object  # noqa: PLC0415

        exc = result.exception
        assert isinstance(exc, TestbedError), result.output
        return {"error": error_object(exc.code, exc.message, hint=exc.hint, docs=exc.docs, details=exc.details)}
    return {"data": json.loads(result.stdout)["data"]}


async def mcp(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    caller.set_agent_session(SESSION)  # what `qft mcp serve --agent-session SESSION` does once
    result = await client.call_tool(tool, arguments)
    assert result.structured_content is not None
    content = dict(result.structured_content)
    if result.is_error:
        return content
    if set(content) == {"job", "result"}:  # a long tool that finished within the call
        assert content["job"] is None
        content = content["result"]
    return {"data": content}


async def same(client: Client, cli_args: list[str], tool: str, **arguments: Any) -> dict[str, Any]:
    by_cli = cli(*cli_args)
    by_mcp = await mcp(client, tool, **arguments)
    assert _stable(by_mcp) == _stable(by_cli)
    return by_cli


async def test_deployments(client: Client, tmp_path: Path) -> None:
    spec = str(tmp_path / "demo.yaml")
    created = cli("deployment", "ensure", spec)
    assert created["data"]["created"] is True
    reused = await same(client, ["deployment", "ensure", spec], "deployment_ensure", spec_path=spec)
    assert reused["data"]["created"] is False
    await same(client, ["deployment", "show", "demo"], "deployment_show", name="demo")
    await same(client, ["deployment", "list"], "deployment_list")
    await same(client, ["deployment", "list", "--owner", "agent"], "deployment_list", owner="agent")
    await same(client, ["deployment", "logs", "demo", "--tail", "5"], "deployment_logs", name="demo", tail=5)
    await same(client, ["deployment", "stop", "demo"], "deployment_stop", name="demo")
    await same(client, ["deployment", "keep", "demo", "--ttl", "3d"], "deployment_keep", name="demo", ttl="3d")
    await same(client, ["spec", "validate", spec], "spec_validate", spec_path=spec)
    await same(client, ["gc", "--dry-run"], "gc")
    await same(client, ["cache", "list"], "cache_list")
    await same(client, ["cache", "prune", "--dry-run"], "cache_prune")


async def test_errors(client: Client, tmp_path: Path) -> None:
    assert (await same(client, ["deployment", "show", "nope"], "deployment_show", name="nope"))["error"][
        "code"
    ] == "NOT_FOUND"
    bad_spec = tmp_path / "bad.yaml"
    bad_spec.write_text("name: demo\nqgis: {version: nope:1}\n")
    invalid = await same(client, ["spec", "validate", str(bad_spec)], "spec_validate", spec_path=str(bad_spec))
    assert invalid["error"]["code"] == "INVALID_SPEC"
    assert invalid["error"]["details"]["issues"]
    unknown_owner = await same(client, ["deployment", "list", "--owner", "x"], "deployment_list", owner="x")
    assert unknown_owner["error"]["code"] == "USAGE"
    missing = await same(client, ["doctor", "--require", "qfield-ios"], "testbed_doctor", require=["qfield-ios"])
    assert missing["error"]["code"] == "PREREQ_MISSING"
    assert (await same(client, ["bisect", "status"], "bisect_status"))["error"]["code"] == "NOT_FOUND"


async def test_another_sessions_deployment_is_refused(client: Client, tmp_path: Path) -> None:
    human = invoke(["--json", "deployment", "ensure", str(tmp_path / "demo.yaml")])
    assert human.exit_code == 0, human.output
    for cli_args, tool, arguments in [
        (["deployment", "stop", "demo"], "deployment_stop", {"name": "demo"}),
        (["deployment", "remove", "demo", "--yes"], "deployment_remove", {"name": "demo"}),
        (["deployment", "keep", "demo", "--ttl", "1d"], "deployment_keep", {"name": "demo", "ttl": "1d"}),
    ]:
        refused = await same(client, cli_args, tool, **arguments)
        assert refused["error"]["code"] == "REFUSED"
