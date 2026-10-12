# Component `qfieldsync`

The QGIS plugin, installed in the deployment's QGIS **profile** and wired to the deployment's QFieldCloud.

## Version sources

| Source | Materialization |
|---|---|
| `release:vX.Y.Z` | `qfieldsync.X.Y.Z.zip` from the GitHub release, extracted; it already contains the `libqfieldsync` wheel. |
| `git:<ref>` | Cached checkout + `git submodule update --init` (`qfieldsync/setting_manager`), then the `libqfieldsync` wheel pinned in `requirements.txt` is built once into the cache and copied into the plugin folder — the same steps as the plugin's release workflow. |
| `worktree:<path>` | Your checkout: submodule initialized if needed, wheel copied into `qfieldsync/`, plugin folder symlinked into the profile so edits are live after a QGIS restart. |

## Wiring

A headless PyQGIS script seeds the profile once:

- an authentication configuration `qfieldcloud` (method *Basic*, the server URL as URI, the test account as
  username/password) in the profile's `qgis-auth.db`, unlocked by the master password stored next to it and
  passed through `QGIS_AUTH_PASSWORD_FILE`;
- `QFieldSync/qfieldCloudServerUrl`, `qfieldCloudServerUrlsHistory`, `qfieldCloudAuthcfg`, `qfieldCloudAuthMethod`
  (credentials) and `qfieldCloudRememberMe` in the profile's `QGIS/QGIS3.ini` or `QGIS4.ini`, plus `cloudDirectory`,
  `exportDirectory` and `importDirectory` under the deployment;
- `PythonPlugins/qfieldsync=true`.

With `remember_me: true` the plugin logs in when QGIS starts. The server URL field of the login dialog is hidden;
double-click the QFieldCloud logo to reveal it if you need to change it by hand.

## Compatibility

`metadata.txt` of the selected revision is read: a warning is printed when `qgisMinimumVersion` is above the
QGIS in use or when the revision predates Qt 6 support while QGIS 4 is used. For bisect ranges that cross the
Qt 6 transition, pin `qgis: {version: docker:3.44.15}`.

## Facts

`plugin_dir`, `version` (from `metadata.txt`), `libqfieldsync` (sha of the pinned wheel).
