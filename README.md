# qfield-testbed

Sandboxed, reproducible deployments of the QField stack — [QFieldCloud](https://github.com/opengisch/QFieldCloud),
QGIS with [QFieldSync](https://github.com/opengisch/qfieldsync), and [QField](https://github.com/opengisch/QField)
on Android, iOS or the desktop — at any combination of versions, wired together through a test account. Several
deployments run side by side. A **bisect** mode swaps one component's revision, `git bisect`-style, until it finds
the one that introduced a regression.

Typical questions it answers:

- *Does my QFieldSync worktree still sync against QFieldCloud `master`?*
- *Which QFieldCloud release broke project packaging in QFieldSync 4.22?*
- *Does QField 4.3.5 on Android log in to a QFieldCloud built from this pull request?*

The vocabulary used in the docs, the CLI and the code is defined once in the [glossary](docs/glossary.md).

## Quick start

Requirements: Linux, Docker with Compose v2, `git`, the [`gh`](https://cli.github.com/) CLI logged in (for
release and CI-artifact downloads), QGIS on the host (or Docker for `qgis: docker:<tag>`), `/dev/kvm` for the
Android emulator.

```sh
uv tool install git+https://github.com/opengisch/qfield-testbed      # or: uv sync && uv run qft …
qft doctor                                                           # what this host can run
qft deployment add examples/minimal.yaml --start                     # QFieldCloud v26.28 + host QGIS + QFieldSync v4.23.1
qft deployment show smoke                                            # URLs, ports, test account
qft deployment launch smoke qgis                                     # QGIS with the deployment's profile, logged in
qft deployment remove smoke
```

A deployment is described by a small YAML **spec** — see `qft spec example` and [docs/spec.md](docs/spec.md):

```yaml
name: fix-806
qfieldcloud:
  version: git:master
qgis:
  version: host
qfieldsync:
  version: worktree:~/src/github.com/opengisch/qfieldsync-fix-cloud-rm-806
qfield-android:
  version: release:v4.3.5
  device: "Samsung Galaxy S10"
```

Version sources: `release:<tag>` (official binaries/images), `git:<ref>` (built from a checkout),
`worktree:<path>` (your local checkout, edits picked up live), `artifact:<ref>` (the component's own CI
artifact), `build:<path>` (built locally with the component's scripts); QGIS uses `host` or `docker:<tag>`.

## Bisect

```sh
qft bisect start examples/bisect-qfieldcloud.yaml   # fixed components + the one that varies + the test
qft bisect run                                      # swaps revisions until the first bad one is found
qft bisect status                                   # verdicts so far; the report names the first bad revision
```

The test is a script (exit 0 = good, 125 = skip, anything else = bad — `git bisect run` semantics) receiving the
deployment as `QFT_*` environment variables, or `manual: true` to be prompted after each swap. See
[docs/bisect.md](docs/bisect.md).

## Commands

| Command | Purpose |
|---|---|
| `qft doctor` | Check Docker, KVM, `gh`, QGIS, display, iOS hosts, free disk |
| `qft spec example [--minimal]`, `qft spec validate FILE` | Help writing specs |
| `qft deployment add SPEC [--name N] [--set k=v] [--start]` | Register a deployment and allocate its slot |
| `qft deployment list \| show \| start \| stop \| restart \| remove \| logs NAME` | Lifecycle |
| `qft deployment launch NAME qgis\|android\|ios\|qfield-desktop` | Open an interactive client |
| `qft deployment run NAME -- CMD`, `qft deployment env NAME` | Use the deployment from scripts |
| `qft bisect start \| run \| next \| good \| bad \| skip \| status \| log \| reset` | Bisect sessions |
| `qft cache list \| prune` | The shared cache of checkouts, downloads and images |
| `qft config show \| path` | Global configuration (`~/.config/qfield-testbed/config.yaml`) |

## Documentation

- [Glossary](docs/glossary.md) — the terms, used consistently everywhere
- [Getting started](docs/getting-started.md)
- [Deployment spec](docs/spec.md)
- [Bisect](docs/bisect.md)
- [Isolation: slots, ports, names, cache](docs/isolation.md)
- Components: [QFieldCloud](docs/components/qfieldcloud.md), [QGIS](docs/components/qgis.md),
  [QFieldSync](docs/components/qfieldsync.md), [QField on Android](docs/components/qfield-android.md),
  [QField on iOS](docs/components/qfield-ios.md), [QField desktop](docs/components/qfield-desktop.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under the [MIT License](LICENSE).
