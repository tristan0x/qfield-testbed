# Component `qfield-desktop`

QField for Linux (AppImage) running on the host — the cheapest way to exercise QField against a deployment,
without an emulator.

## Version sources

| Source | Notes |
|---|---|
| `release:vX.Y.Z` | `qfield-vX.Y.Z-linux-x64.AppImage` from the GitHub release, cached. |

## Runtime and wiring

`<deployment>/bin/qfield-desktop` runs the AppImage with `XDG_CONFIG_HOME=<deployment>/qfield-desktop/config`,
so its settings are isolated from any QField you use yourself. `OPENGIS.ch/QField.conf` in that directory is
pre-filled with the deployment's QFieldCloud URL (`http://127.0.0.1:<port>/`) and the test account's username.

## Facts

`launcher`, `appimage`, `qfieldcloud_url`.
