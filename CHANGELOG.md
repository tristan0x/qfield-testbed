# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

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
