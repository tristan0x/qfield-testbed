# JSON output

`qft --json COMMAND …` (or `QFT_OUTPUT=json`) prints exactly one JSON document on stdout, the *envelope*;
everything meant for humans goes to stderr. The envelope, the error codes and the exit codes are defined in
[ADR 0001](adr/0001-agent-interface.md):

```json
{"schema_version": 1, "ok": true, "data": {}, "warnings": []}
{"schema_version": 1, "ok": false, "error": {"code": "NOT_FOUND", "message": "…", "hint": "…", "docs": null, "details": {}}, "warnings": []}
```

This page lists `data` for each command. Paths are absolute strings, dates ISO 8601, statuses the words of
`qft deployment list` (`created`, `stopped`, `starting`, `running`, `failed`, `wiring failed`). New fields may
appear in any release; removing or renaming one bumps `schema_version`.


The MCP server returns the same `data` as structured content of its tools, and the same `error` object on failure
([mcp](mcp.md)).
## Deployments

**Deployment info** — returned by `deployment add`, `show`, `start`, `stop` and `restart`:

| Field | Content |
|---|---|
| `name`, `status`, `slot` | identity and overall status |
| `owner`, `agent_session` | `human`, `agent` (with its session) or `bisect` |
| `created_at`, `last_used_at`, `ttl_s`, `expires_at` | the sliding expiry; `ttl_s` and `expires_at` are `null` when it never expires |
| `directory` | `$QFT_HOME/deployments/<name>` |
| `network` | the Docker network of the deployment |
| `ports` | service name → host port, in the slot's port block |
| `test_account` | `{username, password, token}` of the QFieldCloud test account, or `null` |
| `components` | component name → `{status, version, revision, source, …}`, plus the facts the component reports (URLs, profile path, QGIS version, emulator serial…); `source` is the spec's version (`release:v26.29`, `worktree:…`) while `version` may be what the component reports (`v26.29`, the host QGIS version) |

| Command | `data` |
|---|---|
| `deployment ensure SPEC [--name N\|auto] [--replace]` | deployment info plus `{created, started, spec_hash}`; another spec under that name is an `ALREADY_EXISTS` error with `details.diff` |
| `deployment list [--owner O] [--agent-session S]` | `{deployments: [{name, slot, status, port_base, port_last, components: {name: status}, session, owner, agent_session, created_at, last_used_at, ttl_s, expires_at}]}` — never credentials; `session` is the bisect session owning it |
| `deployment keep NAME --ttl T` | the same summary, with the new expiry |
| `deployment remove NAME --yes` | `{name, removed: true}` |
| `deployment env NAME` | `{variables: {QFT_…: value}}`, also with `--format json`; see [The QFT_* environment](environment.md) |
| `deployment launch NAME TARGET` | `{launcher, exit_code}` once the client is closed |
| `deployment logs NAME [--tail N] [--since T] [--service S]` | `{components: {name: text}}` for the components that have logs; `--follow` is refused |
| `deployment run NAME [--timeout T] [--tail-lines N] -- CMD` | `{exit_code, duration_s, timed_out, stdout_tail, stderr_tail}`: `ok` is true whenever the command ran, its own failure is `exit_code`; past `--timeout` the whole process group is killed and the answer is a `TIMEOUT` error (exit 6) carrying the same fields in `details` |

### Readiness and failed starts

`deployment start`, `restart` and `add --start` return only once every component is ready:

| Component | Ready when |
|---|---|
| `qfieldcloud` | `api/v1/status/` answers 200, the test account logs in (its token is in `test_account`), the demo project exists |
| `qgis`, `qfieldsync` | the profile is seeded with QFieldSync, its settings and the test account |
| `qfield-android` | the emulator has booted; app settings are seeded (or a warning gives the manual steps) |
| `qfield-ios`, `qfield-desktop` | the app is installed and its settings point at the deployment's QFieldCloud |

`--timeout 10m` bounds the whole start. A start that fails answers with `TIMEOUT` (exit 6, not ready in time)
or `COMPONENT_FAILED` (exit 7), and `error.details` says where:

| Field | Content |
|---|---|
| `component` | the component that failed |
| `step` | `resolve`, `materialize`, `render`, `start`, `health` or `wire` |
| `elapsed_s` | seconds since the start began |
| `log_tail` | the component's last log lines, when it has logs |
| `probe`, `last` | for health timeouts: what was polled and its last answer |

## Clean-up

| Command | `data` |
|---|---|
| `gc [--expired] [--orphaned] [--agent-session S] [--dry-run]` | `{dry_run, plan: {expired, orphaned_projects, orphaned_containers, orphaned_networks, orphaned_directories, stale_entries}, report: {removed, skipped, failed}}` (`report` once applied) |

## Bisect

**Session summary** — returned by `bisect start`, `run`, `good`, `bad`, `skip`, `status` and `reset`:
`{session, status, component, good, bad, deployment, candidates, tested, skipped, remaining_steps, current,
next, first_bad, ambiguous, verdicts: [{index, label, subject, verdict, exit_code}]}`, where `first_bad` is
`{index, label, sha, subject, date, url}` or `null`.

| Command | `data` |
|---|---|
| `bisect example` | `{yaml}` |
| `bisect next` | `{prepared, outcome, session}`: `outcome` is `prepared`, `already-prepared`, `skipped` (not materializable) or `done`; `prepared` the candidate index or `null` |
| `bisect list` | `{sessions: [summary…]}` |
| `bisect log` | `{outcomes: [{index, label, verdict, duration_s, log_file}]}`; with `--index N`, `{index, log}` |
| `bisect remove NAME --yes` | `{session, removed: true}` |

A session stopped by its test (exit ≥ 128) or paused by the user answers with the `ABORTED` error (exit 130).
A manual test cannot run without a terminal: `NEEDS_HUMAN` (exit 3).

## Cache, configuration, host

| Command | `data` |
|---|---|
| `cache list` | `{cache_dir, entries: [{key, path, size_bytes, last_used, users, images}], total_bytes}` |
| `cache prune` | `{dry_run, removed: [{key, size_bytes}]}` (`size_bytes` is `null` when the files were already gone) |
| `config show` | the effective configuration, as `qft config show` prints it |
| `config path` | `{path, exists}` |
| `spec example` | `{yaml}` |
| `spec validate --scenario FILE` | `{name, component, good, bad, deployment, script, problems: [...]}`; an invalid scenario is an `INVALID_SPEC` error; `problems` (also in `warnings`) is what `bisect run` would refuse, such as a missing or non-executable script |
| `spec validate FILE\|-` | `{name, components: [names]}`; an invalid spec is an `INVALID_SPEC` error whose `details.issues` lists `{path, message, line, column}` (dotted path in the spec, 1-based YAML position, `null` when unknown) |
| `spec suggest [--path P] [--need N…]` | `{project, path, spec, spec_yaml, rationale: [...], notes: [...]}`; an unrecognized checkout is a `USAGE` error, an unmet `--need` a `PREREQ_MISSING` one |
| `spec schema [--scenario]` | `{schema}`: the JSON Schema of specs (or scenarios) |
| `doctor` | `{checks: [{name, ok, detail, needed_by: [keys], fix_hint}], capabilities: {key: {can_run, missing: [check names], fix_hints}}, host: {free_disk_gib, slots: {used, max}, deployments, home, cache, config}}`; `--require KEY` makes a missing capability a `PREREQ_MISSING` error whose `details.missing` maps each key to its failing checks |
| `docs [TOPIC]` | `{topic, title, markdown}`; without a topic (or with `--list`), `{topics: [{topic, title}]}` |
| `agent install [--scope user\|project] [--dry-run]` | `{scope, target, files: [paths], replaced, modified: [paths changed by hand], dry_run}`; a copy edited by hand or not made by qft is a `REFUSED` error without `--yes` |
| `--version` | `{version}` |
