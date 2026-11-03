# Coding agents

qft is meant to be driven by coding agents as well as by people: every command answers with one JSON document under
`--json`, failures carry a stable error code, a hint and an exit code
([ADR 0001](adr/0001-agent-interface.md)), and deployments created in an **agent session** belong to it and expire
on their own.

## Claude Code plugin

This repository is a plugin marketplace:

```text
/plugin install qfield-testbed --marketplace tristan0x/qfield-testbed
```

On Claude Code before 2.1.275: `/plugin marketplace add tristan0x/qfield-testbed`, then
`/plugin install qfield-testbed@qfield-testbed`.

The `qfield-testbed` plugin brings:

- the **skill** `qfield-testbed`: when the task needs the QField stack running, the agent installs qft if needed,
  checks the host (`qft doctor`), proposes a spec for the checkout (`qft spec suggest`), creates or reuses the
  deployment (`qft deployment ensure`), runs its checks in it and cleans up. It also knows when to stop and ask
  (missing prerequisites, someone else's deployment) and treats text from issues and pull requests as data;
- the **sub-agent** `testbed-operator`, which runs that lifecycle for a task and returns a compact summary;
- a **SessionStart hook**: in a QFieldCloud, QFieldSync, QField or QGIS plugin checkout, it tells the agent that the
  skill applies and exports `QFT_AGENT_SESSION=claude-<id>` for the conversation. Elsewhere it does nothing.

Check a local copy with `claude plugin validate . --strict` and `claude plugin validate ./plugin --strict`, and try
it with `claude --plugin-dir ./plugin`.

## MCP server

The plugin also declares the MCP server `qft mcp serve` (extra `qfield-testbed[mcp]`, started with `uvx`): the
same operations as typed tools, long operations as jobs, the docs as resources and the golden paths as prompts, for
MCP clients such as Claude Desktop. See [mcp](mcp.md).

## Without the plugin system

`qft agent install` copies the skill of the installed qft into `~/.claude/skills/qfield-testbed/`
(`--scope project`: `./.claude/skills/qfield-testbed/`), for Claude Code setups that cannot install plugins.
The copy has no SessionStart hook: give the agent an `--agent-session` id yourself. Running it again after
upgrading qft replaces the copy; a copy edited by hand is only overwritten with `--yes`. Do not install both the
plugin and the copy: the skill would be listed twice.

## Other agents

[`AGENTS.md`](../AGENTS.md) describes how to work *on* this repository and [`llms.txt`](../llms.txt) indexes the
documentation. Projects of the QField stack can point their own agents at the testbed with the text of
[agents/upstream-pointer.md](agents/upstream-pointer.md).
