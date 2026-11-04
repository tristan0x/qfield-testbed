# Coding agents

qft is meant to be driven by coding agents as well as by people: every command answers with one JSON document under
`--json`, failures carry a stable error code, a hint and an exit code
([ADR 0001](adr/0001-agent-interface.md)), and deployments created in an **agent session** belong to it and expire
on their own.

## Install

### The Claude Code plugin

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
  skill applies and exports `QFT_AGENT_SESSION=claude-<id>` for the conversation. Elsewhere it does nothing;
- the **MCP server** `qft mcp serve`, started with `uvx` (below).

Each version of the plugin installs and starts the qft of the same version.

### Without the plugin system

`qft agent install` copies the skill of the installed qft into `~/.claude/skills/qfield-testbed/`
(`--scope project`: `./.claude/skills/qfield-testbed/`), for Claude Code setups that cannot install plugins.
The copy has no SessionStart hook: give the agent an `--agent-session` id yourself. Running it again after
upgrading qft replaces the copy; a copy edited by hand is only overwritten with `--yes`. Do not install both the
plugin and the copy: the skill would be listed twice.

### The MCP server

`qft mcp serve` (extra `qfield-testbed[mcp]`) offers the same operations as typed tools, long operations as jobs,
the docs as resources and the golden paths as prompts, for MCP clients such as Claude Desktop. The plugin declares
it already; [mcp](mcp.md) shows how to add it to other clients.

### Checking the setup

1. `/plugin` lists `qfield-testbed` as enabled, and `/mcp` lists the `qfield-testbed` server.
2. Open Claude Code in a QFieldSync checkout and ask: *"Can this machine run QFieldCloud with my QFieldSync
   checkout live? Don't start anything."* The agent runs `qft --json doctor` and `qft --json spec suggest --path .`,
   and answers with what it would deploy.

To try a local copy of the plugin: `claude plugin validate ./plugin --strict`, then `claude --plugin-dir ./plugin`.

## What an agent can do

Each use case below is an eval of the skill (see [Evaluating the skill](#evaluating-the-skill)); its page in
[`examples/agents/`](../examples/agents/) gives the prompt, what the agent does and how to check it.

| | Use case | Example prompt |
|---|---|---|
| UC1 | [A QFieldSync change, checked end to end](../examples/agents/uc1-qfieldsync.md) | *"Check end to end that, with my code, QGIS can log in to a QFieldCloud running the latest release…"* |
| UC2 | [A QFieldCloud change, with a worker](../examples/agents/uc2-qfieldcloud.md) | *"Bring my code up with a worker, have it package a project, and tell me what the worker logged…"* |
| UC3 | [QField on the Android emulator](../examples/agents/uc3-qfield-android.md) | *"With the latest QField release on Android, check that the app starts in an emulator next to a QFieldCloud…"* |
| UC4 | [Any QGIS plugin, in a clean profile](../examples/agents/uc4-qgis-plugin.md) | *"Check that this QGIS plugin loads in a clean QGIS profile without errors…"* |
| UC5 | [Which release changed a behaviour](../examples/agents/uc5-bisect.md) | *"Find the first QFieldCloud release between v26.25 and v26.29 whose /api/v1/status/ answer differs…"* |
| UC6 | [A check in a throw-away environment](../examples/agents/uc6-pr-check.md) | *"Act as a CI check for this QFieldSync checkout… write the verdict as JSON…"* |
| UC7 | [Two agents on one host](../examples/agents/uc7-parallel.md) | *"Set up a QFieldCloud… then remove what you created — and only that."* |
| UC8 | [A host that cannot run the stack](../examples/agents/uc8-no-docker.md) | *"Check that this QFieldSync checkout can still package a project against QFieldCloud master."* |

## What it cannot do

- **Drive the QField UI**: the agent starts QField, seeds it and checks it with `adb`, but does not tap through the
  app. It tells you how to open the emulator (`qft deployment launch NAME qfield-android`) instead.
- **Run the stack on another machine**: deployments run on this host's Docker, and their URLs are on 127.0.0.1.
- **Reach the MCP server over a network**: `qft mcp serve` speaks stdio only, one process per client.
- **Work around a missing prerequisite**: on a host without Docker, or without `/dev/kvm` for the emulator,
  `qft doctor` says what is missing and the agent asks you rather than installing it.

## Safety model

- **Ownership**: a deployment created in an agent session belongs to it. Another agent session cannot stop,
  restart, remove or keep it, and neither can it touch a person's deployments: qft refuses (`REFUSED`, exit 8)
  unless `--force`. The skill passes `--force` only when you ask for it. People are never restricted
  ([isolation](isolation.md#owners-sessions-and-expiry)).
- **Expiry and quota**: agent deployments expire 24 h after their last use (`agent_ttl`), and agent sessions own
  at most `max_agent_deployments` deployments in all (10 by default). `qft gc` removes expired ones; a deployment
  you want to keep gets `qft deployment keep NAME --ttl none`.
- **Throwaway credentials**: each QFieldCloud gets its own test account with a generated password. `show` and
  `env` print it, `list` does not. It only opens the sandbox on 127.0.0.1.
- **Docker is root on the host**: qft drives Docker, and access to Docker is root-equivalent. Let an agent use qft
  only on a machine where you would let it use Docker.
- **Commands through MCP**: `deployment_run` runs any command on the host, so `qft mcp serve` offers it only with
  `--allow-run`. The other tools only drive qft.
- **Prompt injection**: the skill treats text from issues, pull requests, logs and web pages as data. A spec, a
  script or a command derived from it is a proposal, shown to you before anything beyond the usual steps runs.
- **Cleaning up**: `qft gc` removes expired deployments and what a crashed qft left behind (`--orphaned`). It only
  touches `qft-*` resources of deployments this `QFT_HOME` created, never other homes or anything else in Docker,
  and it does not prune the cache: `qft cache prune` does that ([isolation](isolation.md#cleaning-up-qft-gc)).

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

After a case, the runner removes what it left and deletes the `qft-*` images the case built (UC2 builds about
10 GB); images that were there before, and the release images it pulled, stay. It never prunes the qft cache,
which the sandbox shares with your own deployments.

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
