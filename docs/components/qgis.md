# Component `qgis`

QGIS Desktop with an isolated **profile** per deployment, `<deployment>/qgis/profiles/testbed`. The profile is
where QFieldSync and extra plugins are installed and where the QFieldCloud connection is configured.

## Version sources

| Source | Backend | Notes |
|---|---|---|
| `host` (default) | `qgis-host` | `/usr/bin/qgis --profiles-path <deployment>/qgis --profile testbed`. Profile seeding uses the system Python with `PYTHONPATH=/usr/share/qgis/python`. |
| `docker:<tag>` | `qgis-docker` | `qgis/qgis:<tag>` on the deployment network, X11 socket shared, the profile and cache bind-mounted. Reaches QFieldCloud at `http://app:8000/`. |

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
profile's `QGIS3.ini`.

## Facts

`launcher`, `profile`, `version`, `backend`.
