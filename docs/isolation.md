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

One thing is shared on purpose: QFieldCloud's worker hands `/tmp/<job>` host paths to the QGIS containers it
spawns through the Docker socket (`TMP_FILE` is hardcoded in `worker_wrapper/wrapper.py`), so `TMP_DIRECTORY`
must be the host's `/tmp` for every deployment. Job directories are named randomly and removed after each job.

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
most `cache.max_image_sets` (default 4) sets of built QFieldCloud images.
