"""Who is calling qft: a person, or an agent session (`--agent-session`, `QFT_AGENT_SESSION`).

The CLI sets it once per invocation; the core reads it to record and check ownership (docs/isolation.md), so
any adapter (the CLI, the MCP server) gets the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Caller:
    agent_session: str | None = None


CALLER = Caller()


def set_agent_session(session: str | None) -> None:
    CALLER.agent_session = session or None
