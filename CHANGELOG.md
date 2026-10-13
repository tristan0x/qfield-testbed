# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `qft doctor` reports `xvfb-run`, which `qgis: host` needs to run without a display (SSH session, CI),
  and says what to install or use instead when it is missing (#1).
- `qfield-ios` component: QField in the iOS Simulator of a remote macOS host driven over SSH (`ios_hosts` in
  the global config), from a simulator build made on the Mac; QFieldCloud is reached through a reverse SSH
  tunnel and pre-filled in the app's preferences. Unconfigured or unreachable hosts are refused at `add`.
- `qfield-desktop` component: the Linux AppImage of a QField release with its own XDG directories and the
  deployment's QFieldCloud pre-filled.
- `qgis: docker:<tag>` backend: `qgis/qgis:<tag>` from Docker Hub instead of the host QGIS, sharing the host
  network and display, with the profile, cache and worktrees mounted at their own paths.
- `qfield-android` component: QField in a `budtmo/docker-android` emulator on the deployment network, with
  the APK from a GitHub release, a CI artifact of any ref, or a local `scripts/build.sh` build; QFieldCloud is
  reachable from the app at `http://10.0.2.2:8000/` and pre-filled in its settings; noVNC and adb are exposed
  on the slot's ports.
- Bisect sessions (`qft bisect start|run|next|good|bad|skip|status|log|list|reset|remove|example`): a
  scenario fixes every component but one, whose revisions between a good and a bad ref are searched with a
  script (`git bisect run` exit codes) or a manual prompt; sessions resume after an interruption and end
  with a report naming the first bad revision.
- `qgis` component (host backend): an isolated profile per deployment, a launcher
  (`qft deployment launch NAME qgis`), extra plugins from zips or local folders.
- `qfieldsync` component: the plugin from a release zip, a git ref or a local worktree (live edits), with
  the pinned `libqfieldsync` wheel built once per revision; the profile is seeded so the plugin logs in to
  the deployment's QFieldCloud with the test account at QGIS startup.
- `qfieldcloud` component: runs QFieldCloud's own compose stack per deployment from an official release
  (ghcr.io images), any git ref (images built and shared by revision) or a local worktree (live reload);
  provisions the test account and a processed demo project.
- `qft` command line with the deployment lifecycle (`add`, `list`, `show`, `start`, `stop`, `restart`,
  `remove`, `logs`, `launch`, `run`, `env`), `doctor`, `spec example|validate`, `cache list|prune` and
  `config show|path`.
- Deployment specs with version sources `release`, `git`, `worktree`, `artifact`, `build`, `host` and `docker`.
- Slot allocation: each deployment owns a block of 100 host ports and a Docker network.
- Glossary and documentation skeleton.

### Fixed

- `qgis: host`: QGIS started through `uv run qft` (launch, run, bisect scripts) no longer inherits the testbed's
  virtualenv, which disabled its Python (`No module named 'PyQt6'`) (#4).
- `qgis: docker:<tag>`: killing the launcher (a `timeout`, Ctrl-C in a script) now stops QGIS instead of leaving
  its container running (#3).
- `qft deployment stop|remove` also remove the QGIS containers a killed Docker launcher left behind; they are
  now labelled with their deployment (#3).
- `qgis: docker:<tag>`: `--code` scripts now see the caller's `QFT_*` variables, plus those named in
  `QFT_FORWARD_ENV=FOO,BAR` (#2).
- `qgis: docker:<tag>`: the directory of a `--code` script is mounted at its own path, so scripts kept outside
  your home (e.g. in `/tmp`) run in the container instead of failing silently (#2).
- `qgis: host` without a display (SSH session, CI): the launcher runs QGIS under `xvfb-run -a` when it is
  installed, and otherwise stops with a hint instead of QGIS's own error, so scripted `--code` runs work
  headless (#1).
