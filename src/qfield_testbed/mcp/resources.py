"""MCP resources: what a client can attach as context instead of calling tools — docs, schemas, live deployments."""

from __future__ import annotations

import json
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError
from pydantic import TypeAdapter

from qfield_testbed import docs, service
from qfield_testbed.bisect.scenario import scenario_json_schema
from qfield_testbed.errors import NotFoundError
from qfield_testbed.spec import spec_json_schema

_JSONABLE = TypeAdapter(Any)

# every resource and template, for the docs and the drift test
URIS = (
    "qft://docs",
    "qft://docs/{+topic}",
    "qft://glossary",
    "qft://spec/schema",
    "qft://scenario/schema",
    "qft://deployments",
    "qft://deployments/{name}",
)


def _json(data: Any) -> str:
    return json.dumps(_JSONABLE.dump_python(data, mode="json", fallback=str), indent=2)


def register(mcp: MCPServer) -> None:
    @mcp.resource("qft://docs", mime_type="application/json", title="Documentation topics")
    def docs_index() -> str:
        """The topics of the documentation of this qft version, to read as qft://docs/{topic}."""
        return _json({"topics": [{"uri": f"qft://docs/{t.topic}", "title": t.title} for t in docs.topics()]})

    @mcp.resource("qft://docs/{+topic}", mime_type="text/markdown", title="Documentation page")
    def docs_page(topic: str) -> str:
        """One page of the documentation, e.g. qft://docs/spec or qft://docs/components/qgis."""
        try:
            return docs.read(topic)[1]
        except NotFoundError as exc:
            raise ResourceNotFoundError(f"{exc.message} ({exc.hint})" if exc.hint else exc.message) from exc

    @mcp.resource("qft://glossary", mime_type="text/markdown", title="Glossary")
    def glossary() -> str:
        """The terms qft uses, each with its definition."""
        return docs.read("glossary")[1]

    @mcp.resource("qft://spec/schema", mime_type="application/json", title="Deployment spec schema")
    def spec_schema() -> str:
        """The JSON Schema of deployment specs (what deployment_ensure takes)."""
        return _json(spec_json_schema())

    @mcp.resource("qft://scenario/schema", mime_type="application/json", title="Bisect scenario schema")
    def scenario_schema() -> str:
        """The JSON Schema of bisect scenarios (what bisect_start takes)."""
        return _json(scenario_json_schema())

    @mcp.resource("qft://deployments", mime_type="application/json", title="Deployments")
    def deployments() -> str:
        """Every deployment on this host with status, owner and expiry (no credentials)."""
        return _json(service.deployment_list())

    @mcp.resource("qft://deployments/{name}", mime_type="application/json", title="Deployment")
    def deployment(name: str) -> str:
        """The live state of one deployment: URLs, ports, test account, revisions. Reading it does not extend its TTL."""
        try:
            return _json(service.deployment_show(name, touch=False))
        except NotFoundError as exc:
            raise ResourceNotFoundError(exc.message) from exc
