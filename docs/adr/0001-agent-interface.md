# ADR 0001 — Machine interface for agents

- Status: accepted
- Date: 2026-11-02
- Context issue: #25 (agent interoperability), implemented from #26 on

## Context

`qft` was designed for a person at a terminal: human-formatted output, prompts, errors as prose, and exit code 1
for almost every failure. #25 makes it something an AI agent (Claude Code first, then any MCP client) can
operate on its own, and a script can drive without parsing text. Two adapters will sit on the same core:

- the **CLI**, which an agent with a shell already has, made machine-friendly (this ADR);
- an **MCP server** (`qft mcp serve`, stdio), added later for clients without a shell.

The core already exists: `Testbed` and `Deployment` (`deployment.py`), `BisectSession` (`bisect/session.py`),
`Cache` and the components. CLI handlers in `cli/` only parse arguments and render results. Two pieces of logic
still live in handlers and move to the core with their own sub-issues: the host checks of `qft doctor`, and the
logs / run helpers of `qft deployment`.

## Decision

### 1. One JSON envelope

`qft --json …` or `QFT_OUTPUT=json` switches to JSON mode, for every command. Stdout then carries **exactly one
JSON document**; progress, `✓` lines, log records and warnings for humans go to stderr. `deployment show --json`
and `bisect status --json` are aliases of the global flag.

```json
{"schema_version": 1, "ok": true, "data": {}, "warnings": []}
{"schema_version": 1, "ok": false, "error": {"code": "NOT_FOUND", "message": "…", "hint": "…", "docs": "spec", "details": {}}, "warnings": []}
```

- `schema_version` changes only on an incompatible change of the envelope or of a command's `data`.
- `data` is the command's result; a command that has none answers `"data": null`.
- `error.hint` is the next thing to try, `error.docs` a topic of the bundled documentation, `error.details` a
  free-form object of structured facts (failing probe, log tail…). Each may be empty.
- `warnings` lists the warnings emitted on the way, in both outcomes.

### 2. Error codes and exit codes

Every deliberate failure is a subclass of `TestbedError` (`errors.py`) carrying a stable `code` and `exit_code`.
Raising `TestbedError` itself is forbidden (a unit test enforces it).

| Exit | Meaning | Codes |
|---|---|---|
| 0 | success | |
| 1 | unexpected error; bisect session stopped by its test or paused by the user | `ERROR`, `ABORTED` |
| 2 | usage error, invalid spec or scenario, name already taken | `USAGE`, `INVALID_SPEC`, `ALREADY_EXISTS` |
| 3 | prerequisite missing (host, configuration, previous step), or a human must act | `PREREQ_MISSING`, `NEEDS_HUMAN` |
| 4 | resource exhausted: no free slot, port or disk | `RESOURCE_EXHAUSTED` |
| 5 | not found: deployment, session, ref, path, launcher | `NOT_FOUND` |
| 6 | timeout: something did not become ready in time | `TIMEOUT` |
| 7 | component failure: container unhealthy, wiring, build, external command | `COMPONENT_FAILED` |
| 8 | refused: confirmation or authorization missing, confirmation declined | `REFUSED` |
| 130 | interrupted (Ctrl-C) | `INTERRUPTED` |

`125` is **not** a `qft` exit code: it stays the `git bisect run` convention for bisect *test scripts* ("skip
this candidate"). A revision that cannot be materialized is caught inside the bisect session and recorded as a
skip; outside a bisect it exits 7.

In human mode the message is printed as `error: …`, followed by `hint: …` when there is one.

### 3. Decisions taken for the rest of #25

Recorded here so every sub-issue applies the same rules.

- **Confirmation and override**: `--yes` skips a confirmation; `--force` overrides ownership guards. Until
  ownership exists, `--force` keeps its current meaning (skip the confirmation) with a deprecation warning.
  Without a terminal, a command needing confirmation refuses (exit 8) instead of waiting on stdin.
- **Readiness**: `start`, `restart` and `add --start` return only when the deployment is ready (this is already
  the case); a `--timeout` bounds them. No `--wait` flag.
- **Ownership and TTL**: every deployment records an owner (`human`, `agent`, `bisect`) and, for agents, a
  session. Agent deployments get a **sliding** TTL of 24 h by default: `expires_at = last_used_at + ttl`, where
  `last_used_at` is refreshed by `ensure`, `start`, `restart`, `show`, `env`, `run` and `logs`, not by `list`,
  `gc` or `doctor`. Human deployments have no TTL unless asked.
- **Identity for `ensure`**: the hash of the normalized spec (`git:<ref>` resolved to a sha, paths made
  absolute). The version of a host QGIS is not part of it; a change since creation is reported as a warning.
- **No `qft session` command group**: `--session` filters on `deployment list` and `gc` cover it.
- **MCP server** in the optional extra `qfield-testbed[mcp]`, stdio only, as thin as the CLI over the same core.
- **`qft agent install`** copies the skill and writes no snippet into the user's `CLAUDE.md`.

## Consequences

- Scripts and agents branch on exit codes and `error.code` instead of messages; messages may be reworded freely.
- Adding an error class means choosing its intermediate class and adding it to the table test in
  `tests/unit/test_errors.py`, which fails otherwise.
- Breaking for existing consumers: `deployment show --json` and `bisect status --json` now return the envelope
  (read `.data`), most failures no longer exit 1, and declining a confirmation exits 8 instead of 130.
- Each command gets a typed result model for its `data`, shared with the MCP server.
