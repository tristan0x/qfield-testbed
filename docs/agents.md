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

## Evaluating the skill

`evals/` holds agent-in-the-loop evals. Each case starts a fresh Claude Code session with only this plugin:
- an empty Claude configuration (no other plugin, no user `CLAUDE.md`, no memory);
- its own `QFT_HOME` and ports;
- the `qft` of the working tree.

The session gets a prompt that never names qft, and the runner then checks what happened:
- the skill was used;
- commands came in order, and forbidden ones never ran;
- the final report says what it should;
- the deployment had the right shape;
- **nothing was left behind**.

Two kinds of cases:
- **behaviour** cases decide without starting anything heavy: a spec for a checkout, a host that cannot run the
  stack, a person's deployment in the way, instructions planted in an issue, a bisect set up;
- **scenario** cases (UC1–UC8) run real deployments and need Docker; UC3 also needs KVM. A case whose
  requirements the host lacks is skipped.

```sh
claude setup-token                        # once: a token of your Claude subscription
install -m 600 /dev/null ~/.config/qfield-testbed/claude-token && $EDITOR ~/.config/qfield-testbed/claude-token
scripts/evals.sh behaviour                # minutes
scripts/evals.sh scenario --only uc1-qfieldsync,uc8-no-docker
```

The evals run **locally, on the subscription**: `scripts/evals.sh` and the runner refuse to start while
`ANTHROPIC_API_KEY` is set, since it would be billed. The cost they print is an equivalent, for comparison
only. Results go to `evals/results/<timestamp>/`: a `summary.md`, one JSON file per case, and each session's
stream.

`claude plugin eval` is not used: its Bash sandbox cannot start where unprivileged user namespaces are
restricted (Ubuntu's AppArmor default), and it cannot check the state of the machine after a run.

## Other agents

[`AGENTS.md`](../AGENTS.md) describes how to work *on* this repository and [`llms.txt`](../llms.txt) indexes the
documentation. Projects of the QField stack can point their own agents at the testbed with the text of
[agents/upstream-pointer.md](agents/upstream-pointer.md).
