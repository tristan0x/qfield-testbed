# Component `qfield-android`

QField running in an Android emulator inside Docker (`budtmo/docker-android`), on the deployment network.

## Version sources

| Source | APK |
|---|---|
| `release:vX.Y.Z` | `qfield-vX.Y.Z-android-x64.apk` from the GitHub release (x86_64 image, no ARM translation). |
| `artifact:<ref>` | The `android-x64` artifact of the `android.yml` workflow run for that ref on `repo` (upstream, or your fork after pushing). Artifacts expire after 90 days; a missing one is a skipped bisect candidate. |
| `build:<path>` | `triplet=x64-android ./scripts/build.sh` in that QField checkout (Docker; hours and tens of GiB the first time). Refused under 15 GiB of free disk. |

## Runtime

A compose project `qft-<deployment>-tb` starts:

- `android`: the emulator image with `EMULATOR_DEVICE=<device>` and `WEB_VNC=true`, `/dev/kvm` passed through,
  noVNC on port offset 20 and adb on offset 21 (both bound to 127.0.0.1);
- `qfc-proxy`: a `socat` relay sharing the emulator container's network namespace and forwarding `:8000` to
  QFieldCloud's `app:8000`.

Inside Android, `10.0.2.2` is the emulator host — here the container — so QField reaches QFieldCloud at
`http://10.0.2.2:8000/`. No `adb reverse`, no dependence on the host IP.

## Wiring

After boot (`sys.boot_completed` and a focused launcher), the APK is installed with `adb install -r -g`; the
same APK is not reinstalled on the next start. With `seed_settings: true` the app is launched once so its data
directory exists, then `QField.conf` (QSettings, INI format, `OPENGIS.ch/QField.conf` under the app's private
`files/.config`) gets `[QFieldCloud] url=…`, `username=…` written as root and chowned back to the app user. When
seeding is impossible (no root, unexpected paths) the manual steps are printed: open QFieldCloud in QField,
double-tap the logo, enter the URL, log in with the test account.

Revisions are resolved through the GitHub API (`gh api repos/<repo>/commits/<ref>`) — the QField repository is
never cloned for releases and artifacts.

## Using it

`qft deployment launch <name> android` prints and opens the noVNC URL. `QFT_QFIELD_ANDROID_ADB` is a command
prefix (`docker exec … adb`) for scripts. Device profiles accepted by the image include `Samsung Galaxy S10`,
`Samsung Galaxy S6`, `Nexus 5`, `Nexus 7`, `Pixel C`.

## Facts

`novnc_url`, `adb`, `qfieldcloud_url` (as seen from Android), `apk`, `device`, `image`.
