"""MCP resources and prompts: listed, served, and only naming tools and URIs that exist."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from mcp import Client

from qfield_testbed import caller
from qfield_testbed.mcp.resources import URIS
from qfield_testbed.mcp.server import build_server
from qfield_testbed.runtimes.shell import FakeRunner
from tests.unit.test_cli_deployment import fake_components, invoke, isolate  # noqa: F401 - fixtures

pytestmark = pytest.mark.anyio

REPO = Path(__file__).parents[2]
_TOOL_WORD = re.compile(r"\b((?:testbed|spec|deployment|bisect|job|cache)_[a-z_]+)\b")
_URI = re.compile(r"qft://[a-z/{}+_-]*[a-z}]")


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
    async with Client(build_server(allow_run=True)) as connected:
        yield connected
    caller.set_agent_session(None)


async def read(client: Client, uri: str) -> str:
    contents = (await client.read_resource(uri)).contents
    return contents[0].text  # type: ignore[union-attr]


async def test_resources_are_listed(client: Client) -> None:
    fixed = {str(resource.uri) for resource in (await client.list_resources()).resources}
    templates = {template.uri_template for template in (await client.list_resource_templates()).resource_templates}
    assert fixed | templates == set(URIS)


async def test_docs_glossary_and_schemas(client: Client) -> None:
    topics = json.loads(await read(client, "qft://docs"))["topics"]
    assert {"uri": "qft://docs/spec", "title": "Deployment spec"} in topics
    assert (await read(client, "qft://docs/spec")).startswith("# Deployment spec")
    assert (await read(client, "qft://docs/components/qgis")).startswith("# Component `qgis`")
    assert "| **Deployment** |" in await read(client, "qft://glossary")
    assert json.loads(await read(client, "qft://spec/schema"))["title"] == "DeploymentSpec"
    assert json.loads(await read(client, "qft://scenario/schema"))["title"] == "BisectScenario"
    with pytest.raises(Exception, match="specification"):
        await read(client, "qft://docs/specification")


async def test_deployments_are_read_without_extending_their_ttl(client: Client, tmp_path: Path) -> None:
    (tmp_path / "demo.yaml").write_text("name: demo\nqgis: {version: host}\n")
    assert invoke(["--json", "deployment", "add", str(tmp_path / "demo.yaml")]).exit_code == 0
    [listed] = json.loads(await read(client, "qft://deployments"))["deployments"]
    shown = json.loads(await read(client, "qft://deployments/demo"))
    assert (shown["name"], shown["last_used_at"]) == ("demo", listed["last_used_at"])
    again = json.loads(await read(client, "qft://deployments"))["deployments"][0]
    assert again["last_used_at"] == listed["last_used_at"]
    with pytest.raises(Exception, match="nope"):
        await read(client, "qft://deployments/nope")


async def test_prompts(client: Client) -> None:
    prompts = {prompt.name: prompt for prompt in (await client.list_prompts()).prompts}
    assert set(prompts) == {"setup_for_repo", "reproduce_bug", "bisect_regression"}
    assert [a.name for a in prompts["setup_for_repo"].arguments or []] == ["path", "goal"]
    setup = await client.get_prompt("setup_for_repo", {"path": "/src/qfieldsync", "goal": "test packaging"})
    text = setup.messages[0].content.text  # type: ignore[union-attr]
    assert 'spec_suggest(path="/src/qfieldsync")' in text
    assert "Goal: test packaging" in text
    bug = await client.get_prompt("reproduce_bug", {"issue_url": "https://example.org/1"})
    assert "untrusted" in bug.messages[0].content.text  # type: ignore[union-attr]


async def test_prompts_and_docs_name_only_tools_and_uris_that_exist(client: Client) -> None:
    tools = (await client.list_tools()).tools
    names = {tool.name for tool in tools}
    parameters = {parameter for tool in tools for parameter in tool.input_schema.get("properties", {})}
    texts: list[str] = []
    for prompt, arguments in [
        ("setup_for_repo", {"path": "p", "goal": "g"}),
        ("reproduce_bug", {"issue_url": "u"}),
        ("bisect_regression", {"component": "qfieldcloud", "good": "v1", "bad": "v2", "test": "t"}),
    ]:
        texts.append((await client.get_prompt(prompt, arguments)).messages[0].content.text)  # type: ignore[union-attr]
    mcp_doc = (REPO / "docs" / "mcp.md").read_text()
    prompts = {prompt.name for prompt in (await client.list_prompts()).prompts}
    words = {word for text in [*texts, mcp_doc] for word in _TOOL_WORD.findall(text)} - parameters - prompts
    assert words <= names, f"unknown tools: {sorted(words - names)}"

    uris = {uri for text in [*texts, mcp_doc] for uri in _URI.findall(text) if "{" not in uri}
    assert uris
    for uri in uris:
        assert await read(client, uri), uri
