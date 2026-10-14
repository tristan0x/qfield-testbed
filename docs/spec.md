# Deployment spec

A spec is a YAML mapping with a `name`, an optional `description`, and one key per **component** to include.
A component that is absent is simply not deployed. `qft spec example` prints a full example,
`qft spec example --minimal` the smallest one, and `qft spec validate FILE` checks a file.

```yaml
name: demo                 # [a-z][a-z0-9-]{0,19}; used in container and network names
description: free text
qfieldcloud: { … }
qgis: { … }
qfieldsync: { … }
qfield-android: { … }
qfield-ios: { … }
qfield-desktop: { … }
```

Dependency rules: `qfieldsync` needs `qgis`; every client (`qfieldsync`, `qfield-*`) needs `qfieldcloud`.

## Version sources

Every component has a `version`, written as `kind:value` or as a mapping (`{kind: git, ref: master}`).

| Source | Meaning | Allowed for |
|---|---|---|
| `release:<tag>` | An official release: GitHub release assets (APK, AppImage, plugin zip) or published images (`ghcr.io/opengisch/qfieldcloud-*`). | qfieldcloud, qfieldsync, qfield-android, qfield-desktop |
| `git:<ref>` | Any ref (tag, branch, sha) of the component repository, built from a cached checkout. | qfieldcloud, qfieldsync |
| `worktree:<path>` | Your local checkout, used as-is; edits are live where the component allows it. | qfieldcloud, qfieldsync |
| `artifact:<ref>` | The binary built by the component's own CI for that ref (GitHub Actions artifact). | qfield-android |
| `build:<path>` | Built locally with the component's own scripts (`scripts/build.sh` for QField). | qfield-android, qfield-ios (simulator build made on the Mac) |
| `host` | The version installed on this machine. | qgis (default) |
| `docker:<tag>` | `qgis/qgis:<tag>` from Docker Hub (`4.2.3`, `3.44.15`, `ltr`, `stable`, `latest`…). | qgis |

## Components

### `qfieldcloud`

```yaml
qfieldcloud:
  version: release:v26.28
  repo: https://github.com/opengisch/QFieldCloud   # defaults to the configured upstream
  tls: false                 # true also starts nginx with mkcert certificates on the https/http ports
  worker_replicas: 1
  test_account:
    username: testbed
    email: testbed@example.com
    password: auto           # generated at add time; or a literal
    superuser: true
    demo_project: true       # upload the bundled sample project through the API
```

### `qgis`

```yaml
qgis:
  version: host              # or docker:<tag>
  plugins:                   # extra plugins installed in the deployment's profile
    - zip: https://plugins.qgis.org/plugins/<name>/version/<v>/download/
    - path: ~/src/some-plugin/plugin_dir
  env: { QGIS_DEBUG: "0" }   # extra environment for the QGIS process
  platform: session          # Qt platform: session | xcb | wayland | offscreen (see components/qgis.md)
```

### `qfieldsync`

```yaml
qfieldsync:
  version: worktree:~/src/github.com/opengisch/qfieldsync
  repo: https://github.com/opengisch/qfieldsync
  remember_me: true          # log in to QFieldCloud when QGIS starts
```

### `qfield-android`

```yaml
qfield-android:
  version: release:v4.3.5    # artifact:<ref> | build:/path/to/QField
  repo: opengisch/QField     # owner/name or URL for releases and CI artifacts; defaults to repositories.qfield
  image: budtmo/docker-android:emulator_14.0
  device: "Samsung Galaxy S10"   # EMULATOR_DEVICE of the image
  seed_settings: true        # pre-fill the QFieldCloud URL and username in the app
```

### `qfield-ios`

```yaml
qfield-ios:
  version: build:/Users/me/QField/build-ios-simulator   # a simulator .app on the Mac
  host: mac-mini             # entry of `ios_hosts` in the global config
  device: "iPhone 16"
  runtime: iOS-18-2          # optional simctl runtime id
```

### `qfield-desktop`

```yaml
qfield-desktop:
  version: release:v4.3.5    # Linux AppImage
```

## Overrides

`qft deployment add SPEC --set qfieldsync.version=release:v4.22.3 --set qfieldcloud.tls=true --name other`
changes values before validation; values are parsed as YAML scalars.
