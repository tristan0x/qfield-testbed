# Component `qgis`

QGIS Desktop with an isolated **profile** per deployment: `<deployment>/qgis` is the deployment's profiles path
and `<deployment>/qgis/profiles/default` the profile (QGIS always maps `QGIS_CUSTOM_CONFIG_PATH` to `default`,
which keeps the headless seeding and the desktop application on the same files). The profile is where
QFieldSync and extra plugins are installed and where the QFieldCloud connection is configured.

## Version sources

| Source | Backend | Notes |
|---|---|---|
| `host` (default) | `qgis-host` | `/usr/bin/qgis --profiles-path <deployment>/qgis --profile default`. Profile seeding uses the system Python with `PYTHONPATH=/usr/share/qgis/python`. |
| `docker:<tag>` | `qgis-docker` | `qft-qgis:<tag>`, built once from `qgis/qgis:<tag>` plus `libxcb-cursor0` (the published image targets CI and cannot open a display as is). The container shares the host network — so an SSH-forwarded `localhost:N.0` display and the deployment's QFieldCloud URL work unchanged — runs as your user, and mounts your home, the testbed home, the cache and any worktree at their own paths. |

## Headless runs

Scripts drive QGIS with `--code`, from an SSH session or CI where there is no display:

```sh
QT_QPA_PLATFORM=offscreen <deployment>/bin/qgis --nologo --code check.py < /dev/null
```

- `docker:<tag>`: works as is; the launcher allocates a TTY only when it has one.
- `host`: QGIS Desktop refuses to start without a display, whatever `QT_QPA_PLATFORM` says. When neither
  `DISPLAY` nor `WAYLAND_DISPLAY` is set, the launcher runs QGIS under `xvfb-run -a` (package `xvfb`); without
  it, the launcher stops with a hint. `qft doctor` reports `xvfb-run`.

## Launcher

`qft deployment launch <name> qgis` runs `<deployment>/bin/qgis`, which sets `QGIS_AUTH_PASSWORD_FILE` (so the
authentication database opens without a prompt) and the extra `env` from the spec.

## Extra plugins

```yaml
qgis:
  plugins:
    - zip: https://plugins.qgis.org/plugins/qgis_swiss_locator/version/4.0.3/download/
    - path: ~/src/some-plugin/plugin_dir
```

Zips are downloaded to the cache and extracted into the profile; paths are symlinked. Both are enabled in the
profile's settings file — `QGIS/QGIS3.ini` for QGIS 3, `QGIS/QGIS4.ini` for QGIS 4, which is why the seed script
is told the QGIS version.

## Facts

`launcher`, `profile`, `version`, `backend`.
