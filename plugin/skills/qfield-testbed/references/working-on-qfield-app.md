# Working on QField (the app)

| Target | Component | Needs |
|---|---|---|
| Android emulator | `qfield-android` | Docker and `/dev/kvm` (`qft doctor --require qfield-android`) |
| Linux desktop | `qfield-desktop` | a display; release AppImages only |
| iOS Simulator | `qfield-ios` | a macOS host configured in `ios_hosts` (`qft docs components/qfield-ios`) |

```sh
qft --json doctor --require qfield-android
qft --json spec suggest --path . --output /tmp/qft-SESSION/spec.yaml   # qfield-android: build:<checkout> or artifact:<branch>, plus qfieldcloud
qft --json spec suggest --path . --need release --output /tmp/qft-SESSION/spec.yaml   # QField's latest release instead
qft --json deployment ensure /tmp/qft-SESSION/spec.yaml --timeout 30m
```

- Version sources: `release:vX.Y.Z` (fast), `artifact:<ref>` (the `android.yml` CI build of a pushed ref; needs
  `gh` logged in), `build:<checkout>` (QField's Docker build: **hours and tens of GiB** the first time — ask the
  user before choosing it).
- The app is installed and pointed at the deployment's QFieldCloud (`http://10.0.2.2:8000/` inside the
  emulator) with the test account's username; the password is typed in the app.
- `data.components.qfield-android.adb` is the `adb` command line for the emulator: run it as
  `qft --json deployment run NAME -- docker exec <container> adb shell pidof ch.opengis.qfield`, words written out
  (not `$QFT_QFIELD_ANDROID_ADB`, which only exists inside the command). Install, `logcat`, screenshots
  (`exec-out screencap -p`), input events; `adb shell su 0 cat <settings_file>` shows the app's settings.
- A human can watch and drive it: `qft deployment launch NAME android` opens noVNC.

Details: `qft docs components/qfield-android`, `components/qfield-desktop`, `components/qfield-ios`.
