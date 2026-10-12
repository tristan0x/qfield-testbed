# Component `qfield-ios`

QField in the iOS Simulator. The Simulator only exists in Xcode, so this component drives a **remote macOS
host** over SSH; nothing runs locally.

## Configuration

```yaml
# ~/.config/qfield-testbed/config.yaml
ios_hosts:
  mac-mini:
    ssh: tristan@mac-mini.local
    cache_dir: ~/.cache/qfield-testbed
```

`qft doctor` checks that each host answers `xcrun simctl list`. A spec naming an unknown or unreachable host is
refused at `add` time with an explicit message.

## Version sources

| Source | Notes |
|---|---|
| `build:<path on the Mac>` | A simulator build of `QField.app` made on the Mac (Xcode, `-sdk iphonesimulator`). |

CI artifacts are not accepted: the `ios.yml` workflow produces a device `.ipa`, which the Simulator cannot run.

## Runtime and wiring

`simctl create` / `boot` the requested `device` (and `runtime`), `simctl install` the app. QFieldCloud is reached
through a reverse SSH tunnel (`ssh -R <port>:127.0.0.1:<port>`) kept alive for the deployment, so the URL inside
the simulator is `http://localhost:<port>/`. The app's preferences plist (`QFieldCloud.url`, `username`) is
seeded with `plutil` when possible; otherwise the manual steps are printed.

## Facts

`host`, `udid`, `device`, `qfieldcloud_url`, `app`.
