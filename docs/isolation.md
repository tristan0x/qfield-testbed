# Isolation: slots, ports, names, cache

Several deployments run at the same time on one host. Each one owns a **slot**, and everything it creates
derives from the slot or the deployment name.

## Slots and port blocks

Slot `n` owns the host ports `port_base + 100 × n` … `+ 99` (`port_base` defaults to 20000, see
`qft config show`). Offsets are fixed:

| Offset | Service |
|---|---|
| 0 | QFieldCloud Django dev server — the client endpoint (plain HTTP) |
| 1, 2 | nginx https / http (only with `tls: true`) |
| 3 | PostgreSQL |
| 4, 5 | object storage API / console |
| 6, 7, 8 | smtp4dev web / SMTP / IMAP |
| 9 | WebDAV |
| 10, 11 | debugpy (app, worker) |
| 20, 21 | Android emulator noVNC / adb |
| 30 | QField desktop |

`qft deployment add` takes the lowest free slot whose entire block is free: ports are probed by binding them
and by reading the ports published by existing containers, so unrelated services on the machine are avoided.
The slot is recorded in `$QFT_HOME/registry.json` and released by `remove`.

## Names

| Resource | Name |
|---|---|
| Docker network shared by all containers of the deployment | `qft-<name>` |
| QFieldCloud compose project (upstream compose files) | `qft-<name>-qfc` |
| Testbed compose project (emulator, proxies, Docker QGIS) | `qft-<name>-tb` |
| Images built from a revision | `qft-qfieldcloud-<service>:<revision key>` — shared by every deployment on that revision |
| Deployment directory | `$QFT_HOME/deployments/<name>/` |

Volumes inherit their compose project prefix, so `remove` (which runs `compose down -v`) never touches another
deployment.

Each deployment also gets its own `ENVIRONMENT` value (`qft-<name>`): QFieldCloud's worker labels the QGIS
containers it spawns with `app=<ENVIRONMENT>_worker` and kills any container with that label whose job it does
not know, so a shared value would let one deployment cancel another's jobs. The only side effect is that the
Django Debug Toolbar, enabled upstream for `ENVIRONMENT=development`, stays off.

One thing is shared on purpose: QFieldCloud's worker hands `/tmp/<job>` host paths to the QGIS containers it
spawns through the Docker socket (`TMP_FILE` is hardcoded in `worker_wrapper/wrapper.py`), so `TMP_DIRECTORY`
must be the host's `/tmp` for every deployment. Job directories are named randomly and removed after each job.

## Owners, sessions and expiry

Every deployment records who owns it:

| Owner | When | Expires |
|---|---|---|
| `human` | added without an agent session | never, unless `--ttl` |
| `agent` | added with `qft --agent-session ID …` or `QFT_AGENT_SESSION=ID` | `agent_ttl` after its last use (24h by default) |
| `bisect` | the deployment of a bisect session | never |

The TTL slides: `start`, `restart`, `show`, `env`, `run` and `logs` count as a use and push the expiry back;
`list`, `stop` and `doctor` don't. `qft deployment add --ttl 8h|none` sets it at creation,
`qft deployment keep NAME --ttl 3d|none` later. `qft deployment list` shows OWNER and EXPIRES, and filters on
`--owner` and `--agent-session`.

An agent session may only stop, restart, remove or keep **its own** deployments: anything a person, a bisect
session or another agent session owns is refused (exit 8) unless `--force`. People are never restricted.
Agent sessions own at most `max_agent_deployments` deployments in all (10 by default; exit 4 beyond).

## Cleaning up: `qft gc`

`qft gc` removes what nobody uses any more; `--dry-run` shows it first, and it asks before removing unless
`--yes`:

- `--expired`: deployments whose TTL ran out since their last use;
- `--agent-session ID`: every deployment of one agent session (from another agent session: `--force`);
- `--orphaned`: what a killed or crashed `qft` left behind — compose projects `qft-<name>-qfc` /
  `qft-<name>-tb`, networks `qft-<name>`, containers labelled `qft.deployment=<name>`, deployment directories
  without a registry entry, and registry entries whose directory is gone (their slot is freed).

Without option it does `--expired --orphaned`. A resource is orphaned only when no registered deployment has its
exact name; nothing outside `qft-*` is ever touched. A deployment in use (being started, for example) is skipped.
With `gc_on_start: true` in the config, every `qft` invocation first removes expired deployments.

## Locks

Registry mutations and slot allocation take `$QFT_HOME/registry.lock`; `start`, `stop`, `remove` and `swap` take
`<deployment>/deployment.lock`. Two terminals can drive different deployments freely.

## Cache

`~/.cache/qfield-testbed/` holds what is independent of any deployment and expensive to recreate:

| Path | Content |
|---|---|
| `git/<repo>.git` | bare mirrors of the component repositories |
| `checkouts/<repo>/<sha>/` | detached worktrees of the mirrors, one per revision |
| `artifacts/<component>/<revision>/` | downloaded APKs, plugin zips, AppImages, CI artifacts |
| `wheels/` | `libqfieldsync` wheels built for QFieldSync revisions |
| `index.json` | which deployment uses which entry, last-used timestamps, image tags |

`qft cache list` shows the entries and their size. `qft cache prune [--older-than 30d] [--dry-run]` removes
entries no existing deployment references and that were not used recently, together with their Docker images;
`--docker-build-cache` also runs `docker builder prune`.

Disk guards: `qft doctor` warns under 10 GiB free; `build:` sources are refused under 15 GiB; a bisect keeps at
most `cache.max_image_sets` (default 4) sets of built QFieldCloud images. Built sets are cache entries
(`qfieldcloud/images/<revision>`); after each swap the bisect forgets the least recently used ones beyond the limit,
never the running revision's, and removes their images except those a kept set still uses (an unchanged build
context shares one image across revisions).
