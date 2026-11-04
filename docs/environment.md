# The `QFT_*` environment

`qft deployment run NAME -- CMD`, bisect test scripts and `eval "$(qft deployment env NAME)"` all see the same
variables, which describe the deployment: where it lives, how to reach each component, and the test account.

```sh
eval "$(qft deployment env demo)"                 # export K='v' lines for a shell
qft deployment env demo --format dotenv > .env    # K="v" lines for docker compose, python-dotenv…
qft deployment env demo --format json             # {"variables": {...}} in the JSON envelope
```

## Deployment

| Variable | Content |
|---|---|
| `QFT_DEPLOYMENT` | deployment name |
| `QFT_DEPLOYMENT_DIR` | `$QFT_HOME/deployments/<name>` (generated files, launchers in `bin/`) |
| `QFT_NETWORK` | the Docker network of the deployment |
| `QFT_QFIELDCLOUD_USERNAME`, `QFT_QFIELDCLOUD_PASSWORD` | the test account, when there is a QFieldCloud |
| `QFT_QFIELDCLOUD_TOKEN` | its API token, once the account is provisioned |
| `QFT_REVISION_<COMPONENT>` | the revision each component runs (`QFT_REVISION_QFIELDCLOUD`, `QFT_REVISION_QGIS`…) |

## Components

Every fact a component reports in `qft deployment show` is exported as `QFT_<COMPONENT>_<FACT>`: the component
name upper-cased with `-` turned into `_`, then the fact upper-cased. A fact the component does not know yet
(before the first start, for example) is exported as `None`.

### qfieldcloud

| Variable | Content |
|---|---|
| `QFT_QFIELDCLOUD_URL` | base URL from the host, **ending with `/`** (`http://127.0.0.1:20000/`): `${QFT_QFIELDCLOUD_URL}api/v1/status/` |
| `QFT_QFIELDCLOUD_URL_INTERNAL` | base URL from the deployment's Docker network, ending with `/` (`http://app:8000/`) |
| `QFT_QFIELDCLOUD_ADMIN_URL` | Django admin |
| `QFT_QFIELDCLOUD_VERSION` | the revision label it runs |
| `QFT_QFIELDCLOUD_COMPOSE_PROJECT` | Docker Compose project name (`docker compose -p …`) |
| `QFT_QFIELDCLOUD_CHECKOUT` | the source checkout it runs from |
| `QFT_QFIELDCLOUD_DEMO_PROJECT_ID`, `QFT_QFIELDCLOUD_DEMO_PROJECT_STATUS` | the demo project of the test account |
| `QFT_QFIELDCLOUD_OBJECT_STORAGE_CONSOLE` | MinIO console |
| `QFT_QFIELDCLOUD_MAIL_UI` | the mail catcher's web UI |

### qgis

| Variable | Content |
|---|---|
| `QFT_QGIS_LAUNCHER` | QGIS with the deployment's profile; pass `--code script.py` to run a script |
| `QFT_QGIS_BACKEND` | `qgis-host` or `qgis-docker` |
| `QFT_QGIS_VERSION` | QGIS version |
| `QFT_QGIS_PROFILES_PATH`, `QFT_QGIS_PROFILE`, `QFT_QGIS_PROFILE_NAME` | the isolated profile |
| `QFT_QGIS_PLATFORM` | the Qt platform QGIS starts with |
| `QFT_QGIS_EXTRA_PLUGINS` | extra plugins installed in the profile |

With `qgis: host` and no display (SSH session, CI, an agent's shell), the launcher runs QGIS under
`xvfb-run`; `qft doctor` reports whether it is installed (`qgis:headless`).

### qfieldsync

| Variable | Content |
|---|---|
| `QFT_QFIELDSYNC_PLUGIN_DIR` | the plugin folder in the profile |
| `QFT_QFIELDSYNC_PLUGIN_VERSION` | its version |
| `QFT_QFIELDSYNC_LIBQFIELDSYNC` | the libqfieldsync wheel it uses |
| `QFT_QFIELDSYNC_QFIELDCLOUD_URL` | the server it is configured for |
| `QFT_QFIELDSYNC_AUTHCFG` | the QGIS authentication configuration holding the test account |
| `QFT_QFIELDSYNC_LIVE_EDITS` | `True` for a `worktree:` source linked into the profile |

### qfield-android

| Variable | Content |
|---|---|
| `QFT_QFIELD_ANDROID_ADB` | `adb` command line against the emulator |
| `QFT_QFIELD_ANDROID_ADB_CONNECT` | `host:port` for `adb connect` |
| `QFT_QFIELD_ANDROID_NOVNC_URL` | the emulator screen in a browser |
| `QFT_QFIELD_ANDROID_QFIELDCLOUD_URL` | the server URL as seen from Android |
| `QFT_QFIELD_ANDROID_APK`, `QFT_QFIELD_ANDROID_INSTALLED_VERSION` | the installed build |
| `QFT_QFIELD_ANDROID_SETTINGS_FILE` | the pre-seeded app settings |
| `QFT_QFIELD_ANDROID_DEVICE`, `QFT_QFIELD_ANDROID_IMAGE` | emulated device and emulator image |
| `QFT_QFIELD_ANDROID_LAUNCHER` | opens the emulator UI |

### qfield-ios

| Variable | Content |
|---|---|
| `QFT_QFIELD_IOS_HOST` | the Mac (`ios_hosts` entry) |
| `QFT_QFIELD_IOS_UDID`, `QFT_QFIELD_IOS_DEVICE`, `QFT_QFIELD_IOS_RUNTIME` | the simulator |
| `QFT_QFIELD_IOS_APP` | the installed app bundle |
| `QFT_QFIELD_IOS_QFIELDCLOUD_URL` | the server URL as seen from the simulator |
| `QFT_QFIELD_IOS_SETTINGS_FILE` | the pre-seeded app settings |
| `QFT_QFIELD_IOS_LAUNCHER` | opens the simulator |

### qfield-desktop

| Variable | Content |
|---|---|
| `QFT_QFIELD_DESKTOP_LAUNCHER` | QField desktop with its own XDG directories |
| `QFT_QFIELD_DESKTOP_APPIMAGE` | the AppImage |
| `QFT_QFIELD_DESKTOP_QFIELDCLOUD_URL` | the server it is configured for |
| `QFT_QFIELD_DESKTOP_SETTINGS_FILE` | its settings file |

## Set by you

| Variable | Effect |
|---|---|
| `QFT_AGENT_SESSION` | same as `qft --agent-session ID`: deployments added are owned by that agent session, which may only stop or remove its own ([isolation](isolation.md)) |
| `QFT_OUTPUT=json` | same as `qft --json` |
| `QFT_NONINTERACTIVE=1` | same as `qft --non-interactive` |
| `QFT_MCP_CALL_CEILING` | longest an MCP tool call is held open before it answers with a job, e.g. `50s` (default), `2m`, `30` ([mcp](mcp.md)) |

## Bisect test scripts

On top of the above, a bisect test script gets `QFT_BISECT_COMPONENT`, `QFT_BISECT_INDEX`,
`QFT_BISECT_CANDIDATE`, `QFT_BISECT_SHA`, `QFT_SESSION` and `QFT_SESSION_DIR`; see [Bisect](bisect.md).
