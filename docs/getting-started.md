# Getting started

## Install

```sh
uv tool install git+https://github.com/opengisch/qfield-testbed
# or, from a checkout:
uv sync && alias qft="uv run qft"
```

`qft doctor` tells you what this host can run:

```
✓  docker                 daemon reachable           all
✓  docker compose         5.6.0                      qfieldcloud, qfield-android
✓  /dev/kvm               usable                     qfield-android
✓  gh                     authenticated              release and artifact sources
✓  qgis (host)            QGIS 4.2.3                 qgis: host
✓  display                :0                         qgis, qfield-desktop
✗  xvfb-run               missing: needed only …     qgis: host without a display (--code)
✗  ios hosts              none configured            qfield-ios
✓  free disk              34 GiB                     cache, builds
```

Only the rows needed by the components you use have to be green.

## First deployment

```sh
qft deployment add examples/minimal.yaml --start
```

This registers a deployment named `smoke`, allocates a **slot** (a block of 100 host ports and a Docker
network), then brings the components up in order: QFieldCloud v26.28 from the official images, a QGIS profile
on the host QGIS, QFieldSync v4.23.1 installed in that profile and logged in to the deployment's QFieldCloud with
the test account.

```sh
qft deployment show smoke        # URLs, ports, credentials, revisions
qft deployment launch smoke qgis # QGIS with the profile; QFieldSync is already connected
qft deployment stop smoke
qft deployment start smoke
qft deployment remove smoke
```

## Your own change

Point a component at your checkout with a `worktree:` source. Edits to the checkout are visible on the next
start (QFieldCloud rebuilds its images; QFieldSync is symlinked so a QGIS restart is enough):

```yaml
name: fix-806
qfieldcloud:
  version: release:v26.28
qgis:
  version: host
qfieldsync:
  version: worktree:~/src/github.com/opengisch/qfieldsync-fix-cloud-rm-806
```

Several deployments can run at once — each has its own slot — so keep a `release` one next to your `worktree`
one and compare.

## Scripting

`qft deployment run NAME -- CMD` runs a command with the deployment exposed as environment variables
(`QFT_QFIELDCLOUD_URL`, `QFT_QFIELDCLOUD_USERNAME`, `QFT_QFIELDCLOUD_PASSWORD`, `QFT_QFIELDCLOUD_TOKEN`,
`QFT_QGIS_LAUNCHER`, `QFT_QGIS_PROFILE`, `QFT_REVISION_<COMPONENT>`…); `qft deployment env NAME` prints them.
These are the same variables a bisect [test script](bisect.md) receives.

## Where things live

| What | Where |
|---|---|
| Deployments (spec, state, rendered files, QGIS profile, launchers) | `~/.local/share/qfield-testbed/deployments/<name>/` |
| Bisect sessions | `~/.local/share/qfield-testbed/bisect/<session>/` |
| Shared cache (git mirrors, checkouts, downloads, wheels) | `~/.cache/qfield-testbed/` |
| Global configuration | `~/.config/qfield-testbed/config.yaml` (`qft config show`) |

`QFT_HOME`, `QFT_CACHE_DIR` and `QFT_CONFIG` override these.
