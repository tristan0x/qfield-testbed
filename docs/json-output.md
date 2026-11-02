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

## Deployments

**Deployment info** — returned by `deployment add`, `show`, `start`, `stop` and `restart`:

| Field | Content |
|---|---|
| `name`, `status`, `slot` | identity and overall status |
| `directory` | `$QFT_HOME/deployments/<name>` |
| `network` | the Docker network of the deployment |
| `ports` | service name → host port, in the slot's port block |
| `test_account` | `{username, password, token}` of the QFieldCloud test account, or `null` |
| `components` | component name → `{status, version, revision, …}`, plus the facts the component reports (URLs, profile path, QGIS version, emulator serial…) |

| Command | `data` |
|---|---|
| `deployment list` | `{deployments: [{name, slot, status, port_base, port_last, components: {name: status}, session}]}` — never credentials; `session` is the bisect session owning it |
| `deployment remove NAME --yes` | `{name, removed: true}` |
| `deployment env NAME` | `{variables: {QFT_…: value}}`, also with `--format json`; see [The QFT_* environment](environment.md) |
| `deployment launch NAME TARGET` | `{launcher, exit_code}` once the client is closed |
| `deployment logs`, `deployment run` | not available in JSON mode yet: `USAGE` error |

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
| `spec validate FILE` | `{name, components: [names]}` |
| `doctor` | `{checks: [{name, ok, detail, needed_by: [keys], fix_hint}], capabilities: {key: {can_run, missing: [check names], fix_hints}}, host: {free_disk_gib, slots: {used, max}, deployments, home, cache, config}}`; `--require KEY` makes a missing capability a `PREREQ_MISSING` error whose `details.missing` maps each key to its failing checks |
| `--version` | `{version}` |
