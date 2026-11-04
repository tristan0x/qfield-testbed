"""`qft mcp serve` — the MCP server (optional extra `qfield-testbed[mcp]`)."""

from __future__ import annotations

from typing import Annotated

import typer

from qfield_testbed.caller import CALLER
from qfield_testbed.errors import PrerequisiteMissingError

app = typer.Typer(no_args_is_help=True)

INSTALL_HINT = "uv tool install 'qfield-testbed[mcp] @ git+https://github.com/tristan0x/qfield-testbed@v0.2.0'"


@app.command()
def serve(
    allow_run: Annotated[
        bool, typer.Option("--allow-run", help="Offer deployment_run, which runs commands on this host")
    ] = False,
) -> None:
    """Serve the qft tools over MCP (stdio), as one agent session (`--agent-session`, or a generated one)."""
    try:
        from qfield_testbed.mcp.server import serve as serve_  # noqa: PLC0415 - the extra is optional
    except ImportError as exc:
        raise PrerequisiteMissingError(
            f"the MCP server needs the `mcp` extra ({exc.name or exc} is missing)", hint=INSTALL_HINT, docs="mcp"
        ) from exc
    serve_(allow_run=allow_run, agent_session=CALLER.agent_session)
