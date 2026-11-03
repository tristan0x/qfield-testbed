# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [Semantic Versioning](https://semver.org/).

## Unreleased

### Added

- `qft --json …` (or `QFT_OUTPUT=json`) answers with one JSON envelope on stdout — `schema_version`, `ok`,
  `data` or `error`, `warnings` — for every command, success or failure; progress and logs go to stderr (#26).
- Errors carry a stable code (`NOT_FOUND`, `PREREQ_MISSING`, `TIMEOUT`…), a hint printed after the message and
  a documentation topic; [docs/adr/0001-agent-interface.md](docs/adr/0001-agent-interface.md) records the
  contract (#26).
- With `--json`, every command returns its result as `data`: deployment info and summaries, environment
  variables, bisect session summaries, cache entries, configuration and doctor checks, listed in
  [docs/json-output.md](docs/json-output.md) (#27).
- `qft --non-interactive` (or `QFT_NONINTERACTIVE=1`) never prompts: confirmations are refused (exit 8) and a
  manual bisect test answers `NEEDS_HUMAN` (exit 3); JSON mode implies it (#27).
- `--yes` on `qft deployment remove` and `qft bisect remove` skips the confirmation (#27).
- `qft doctor` ends with a capability matrix — which components, QGIS modes and version sources this host can
  run, what each one is missing and how to fix it — and `qft doctor --require KEY` exits 3 when a capability
  is missing; `--json` returns the matrix and the slots in use (#28).
- `qft deployment env NAME --format shell|dotenv|json`; [docs/environment.md](docs/environment.md) documents
  every `QFT_*` variable, and a test keeps it in step with what the components export (#29).
- `qft deployment logs` takes `--tail N`, `--since T` and `--service S`; with `--json` it returns the lines
  instead of printing them, and never follows (#29).
- `qft deployment run NAME --timeout 10m -- CMD` kills the command and its children when it runs too long
  (exit 6); with `--json` it captures the output and returns the exit code, duration and output tails (#29).
- `--timeout 10m` on `qft deployment start`, `restart` and `add --start` bounds the whole start: it fails with
  exit 6 (`TIMEOUT`) naming the component and the step it had reached (#30).
- A failed start names its component in the message and, in the error's `details`, the step (`health`,
  `wire`…), the time spent, the component's last log lines and, for health timeouts, the probe (#30).
- `qft spec schema [--scenario]` prints the JSON Schema of deployment specs (or bisect scenarios), shorthands
  such as `release:v26.28` included, for editors and agents (#31).
- Spec and scenario errors give the YAML line of each problem, and `--json` lists them as
  `{path, message, line, column}`; `qft spec validate -` reads stdin (#31).
- Deployments record their owner — a person, an agent session (`qft --agent-session ID`, `QFT_AGENT_SESSION`)
  or a bisect session — and a sliding expiry: agent deployments expire 24h after their last use
  (`agent_ttl`), `deployment add --ttl` and `qft deployment keep NAME --ttl` change it, `deployment list` shows
  and filters both (#32).
- An agent session may only stop, restart, remove or keep its own deployments unless `--force`, and agent
  sessions own at most `max_agent_deployments` (10) deployments (#32).
- `qft spec suggest` proposes a spec for the QFieldSync, QFieldCloud, QField or QGIS plugin checkout it runs in
  (forks and worktrees included): that checkout live, the other components at their latest release, `--need`
  for more (`android`, `desktop`, `sync`, `worker`, `tls`, `master`), fit to what `qft doctor` says this host can
  run (#35).
- `qft deployment ensure SPEC` creates and starts a deployment of a spec, or reuses the one that exists: the
  identity of a spec is its options plus the commits its `git:`/`artifact:` refs point to; another spec under
  the same name is refused with a diff unless `--replace`, and `--name auto` names it after the spec (#34).
- `qft gc` removes expired deployments (`--expired`), every deployment of an agent session (`--agent-session`)
  and the `qft-*` containers, compose projects, networks, files and registry entries no deployment owns
  (`--orphaned`); `--dry-run` shows the plan, busy deployments are skipped, and `gc_on_start: true` runs the
  expired part at every invocation (#33).
- `qft docs [TOPIC]` prints the documentation shipped in the package, matching the installed version; the
  `docs` field of JSON errors names one of its topics (#31).

- `qgis.platform` (`session`, `xcb`, `wayland`, `offscreen`) chooses the Qt platform of QGIS for both backends;
  window-manager-dependent bugs (dialogs, focus, `showEvent`) can differ between X11 and Wayland (#5).
- `qft deployment show` and `qft deployment launch NAME qgis` report the Qt platform QGIS uses (#5).
- `qft doctor` shows the desktop session (X11 or Wayland) and, on Wayland, whether XWayland is available (#5).
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

- A Claude Code plugin, installed with `/plugin install qfield-testbed --marketplace tristan0x/qfield-testbed`:
  the `qfield-testbed` skill teaches an agent to check the host, propose a spec, create or reuse a deployment,
  use it and clean up, in its own agent session; a `testbed-operator` sub-agent runs that lifecycle for a task
  (#36).
- In a QFieldCloud, QFieldSync, QField or QGIS plugin checkout, the plugin's SessionStart hook tells Claude Code
  that the skill applies and exports `QFT_AGENT_SESSION` for the conversation (#36).
- `AGENTS.md` and `CLAUDE.md` for agents working on this repository, `llms.txt` indexing the documentation,
  [docs/agents.md](docs/agents.md), and a pointer text for upstream projects (#36).

### Changed

- The project lives at [tristan0x/qfield-testbed](https://github.com/tristan0x/qfield-testbed): install
  with `uv tool install git+https://github.com/tristan0x/qfield-testbed` (#36).
- Breaking: failures exit with a code per kind instead of 1 — 2 usage or invalid spec, 3 missing prerequisite,
  4 exhausted resource, 5 not found, 6 timeout, 7 component failure, 8 refused (declining a confirmation used to
  exit 130) (#26).
- Breaking: `qft deployment show --json` and `qft bisect status --json` wrap their data in the JSON envelope;
  read `.data` (#26).

### Deprecated

- `--force` to skip a confirmation: use `--yes` (#27). Since #32 `--force` only overrides ownership checks and
  no longer skips confirmations.

### Fixed

- `qft gc --orphaned` from one `QFT_HOME` took the Docker resources of another home's deployments for
  orphans; it now only looks for leftovers of deployments the same home created (#33).
- In JSON mode, the output of commands qft runs without capturing it (compose up, database migrations, a
  launched client) went to stdout and broke the JSON document; it goes to stderr now (#35).
- `QFT_QFIELDCLOUD_TOKEN` stays valid when a script logs in to QFieldCloud as the test account: the testbed
  now logs in as a `cli` client, whereas QFieldCloud keeps a single token for clients it does not recognise.
- QFieldCloud images built for `git:` and `worktree:` sources are recorded in the cache, so `qft cache list`
  shows them and `qft cache prune` removes them (#13).
- A bisect over QFieldCloud keeps at most `cache.max_image_sets` built image sets, as documented: older sets
  lose their images after each swap, except images a kept set still uses (#13).
- `repositories.qfield` in the global configuration is now the default `repo` of `qfield-android`,
  `qfield-desktop` and QField bisects, so pointing it at a fork takes effect (#13).
- A libqfieldsync archive that does not unpack to one project folder is reported with its URL and contents
  instead of an internal `ValueError` (#12).
- The libqfieldsync wheel's cache entry lists every deployment that uses it, built or reused, so
  `qft cache prune` no longer deletes it under running deployments (#12).
- Two QGIS plugins at the same version no longer share one cached zip (the second deployment installed the first
  plugin); cached plugin zips are named after the plugin and version, or a hash of their URL (#11).
- `qft` now exits with the code the command chose: `qft deployment run NAME -- CMD` returns `CMD`'s status and an
  aborted `qft bisect run` 130, where both exited 0 (#10).
- Usage errors (missing argument, unknown option, `qft` alone) print the usage message and exit 2 instead of a
  traceback (#10).
- `qft deployment remove` and `qft bisect remove` without a terminal fail at once with a message pointing to
  `--force`, instead of a bare `aborted` (#7).
- `qft bisect run` with a `manual: true` test and no terminal is refused before the first swap, pointing to
  `qft bisect next` and `qft bisect good|bad|skip` (#7).
- QFieldCloud wiring (token, demo project) retries for a minute when the freshly started server drops a
  connection or answers 502/503/504, instead of failing the start (#6).
- A QFieldCloud wiring that still fails is reported in one line naming the step and `qft deployment start NAME`
  to retry, instead of a traceback (#6).
- A component whose start, health check or wiring fails is now shown as `failed` or `wiring failed` in
  `qft deployment list` instead of staying `starting` (#6).
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
