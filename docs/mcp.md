# MCP server

`qft mcp serve` offers the operations of qft as [MCP](https://modelcontextprotocol.io/) tools over stdio, for
clients without a shell (Claude Desktop) or that prefer typed tools. The tools call the same code as the CLI: their
results are the `data` of `qft --json` (see [json-output](json-output.md)), and their errors carry the same
`code`, `hint`, `docs` and `details` ([ADR 0001](adr/0001-agent-interface.md)).

## Install

The server is in the optional extra `mcp`:

```sh
uv tool install 'qfield-testbed[mcp] @ git+https://github.com/tristan0x/qfield-testbed'
qft mcp serve            # speaks MCP on stdin/stdout; logs go to stderr
```

- **Claude Code with the plugin** (`/plugin install qfield-testbed --marketplace tristan0x/qfield-testbed`): the
  plugin declares the server, started with `uvx`, nothing else to do.
- **Claude Code without the plugin**:
  `claude mcp add qfield-testbed -- uvx --from 'qfield-testbed[mcp] @ git+https://github.com/tristan0x/qfield-testbed' qft mcp serve`
- **Claude Desktop** and other clients, in their MCP configuration:

  ```json
  {
    "mcpServers": {
      "qfield-testbed": {
        "command": "uvx",
        "args": ["--from", "qfield-testbed[mcp] @ git+https://github.com/tristan0x/qfield-testbed", "qft", "mcp", "serve"]
      }
    }
  }
  ```

Without the extra, `qft mcp serve` fails with `PREREQ_MISSING` and the install command as hint.

## Agent session

The server is **one agent session** for its whole life: `--agent-session ID` (or `QFT_AGENT_SESSION`), else a
generated `mcp-<8 hex>`. Deployments it creates are owned by that session, expire 24 h after their last use, count
against `max_agent_deployments`, and deployments of people or other sessions are refused (`REFUSED`) unless a tool
gets `force: true` — which an agent should only pass when the user asked for it.

## Tools

| Tool | Does | Hints |
|---|---|---|
| `testbed_doctor(require?)` | host checks and capabilities; `require` fails with `PREREQ_MISSING` | read-only |
| `spec_suggest(path, needs?)` | a spec putting a checkout live (`spec_yaml` to pass on) | read-only, open-world (`git ls-remote`) |
| `spec_validate(spec_yaml \| spec_path)` | validate a spec | read-only |
| `deployment_ensure(spec_yaml \| spec_path, name?, replace?, timeout?, ttl?, force?, wait?)` | create or reuse, and start (a job when long) | idempotent, open-world |
| `deployment_list(agent_session?, owner?)`, `deployment_show(name)`, `deployment_logs(name, …)` | inspect | read-only |
| `deployment_start`, `deployment_restart` (jobs when long), `deployment_stop`, `deployment_keep(name, ttl)` | lifecycle | idempotent |
| `deployment_remove(name, force?)` | tear down | destructive |
| `deployment_run(name, command, timeout?)` | run a command on the host with the `QFT_*` environment | destructive, open-world; only with `--allow-run` |
| `bisect_start(scenario_path, session?)` | create a bisect session | open-world |
| `bisect_step(session?, verdict?)` | record the verdict of the prepared candidate, prepare the next | destructive (swaps the deployment) |
| `bisect_run(session?)` | the whole automatic search (scripted test), as a job | destructive |
| `bisect_status(session?)` | candidates, verdicts, first bad revision | read-only |
| `job_status(job_id)`, `job_logs(job_id, tail?)`, `job_list()` | follow long operations | read-only |
| `cache_list`, `cache_prune(older_than?, docker_build_cache?, dry_run = true)` | the shared cache | `cache_prune` destructive |
| `gc(expired?, orphaned?, agent_session?, dry_run = true, force?)` | clean up | destructive |

Destructive tools ask no confirmation themselves: the client does, from the annotation. `gc` and `cache_prune`
only report what they would remove unless `dry_run: false`.

**`deployment_run` is off by default.** It runs any program on the host, with your user's rights; a client that
has no shell should not get one through qft by accident. Start the server with `qft mcp serve --allow-run` to offer
it.

## Long operations

A first `deployment_ensure` pulls or builds images and can take many minutes, longer than MCP clients wait for an
answer. The long tools — `deployment_ensure`, `deployment_start`, `deployment_restart`, `bisect_step` and
`bisect_run` — therefore run their operation as a **job** and hold the call open at most the **call ceiling**
(`mcp_call_ceiling` in the configuration, `QFT_MCP_CALL_CEILING`; 50 s by default). They answer with:

```json
{"job": null, "result": {…the usual result…}}
{"job": {"job_id": "job-1a2b3c4d", "tool": "deployment_ensure", "status": "running", "progress": {"steps_done": 4, "step": "qfieldcloud: start"}}, "result": null}
```

— the first when the operation ended within the ceiling, the second otherwise, or at once with `wait: false`.
`job_status(job_id)` then follows it: `status` becomes `succeeded` with the `result` the tool would have answered,
or `failed` with the `error` object. `job_logs` gives what qft logged meanwhile. While waiting, the server sends
**progress notifications**, one per step (`qfieldcloud: materialize`, `qgis: wire`…), to clients that ask for them.

At most one job runs per deployment: calling `deployment_ensure` again while one runs answers with that job. Jobs
live in the server process: when it stops, they are gone, and the deployment keeps whatever state it reached
(`deployment_show`).

## Errors

A failed call is an MCP error result (`isError: true`) whose structured content is the CLI's error object:

```json
{"error": {"code": "NOT_FOUND", "message": "No deployment named 'demo'.", "hint": "run `qft deployment list`", "docs": null, "details": {}}}
```

The text content holds the same JSON. Unexpected failures have the code `ERROR`; their traceback goes to the
server's stderr.

## Limits

stdio only; one process per client; jobs end with the process.
