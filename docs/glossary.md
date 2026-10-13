# Glossary

The terms below are used consistently in the documentation, the command line and the code. The third column
gives the identifier that carries the concept in the code base.

| Term | Definition | In the code |
|---|---|---|
| **Testbed** | The tool itself and its on-disk home, `$QFT_HOME` (default `~/.local/share/qfield-testbed`). | package `qfield_testbed`, class `Testbed` |
| **Deployment** | A named, sandboxed set of running components wired together: at most one QFieldCloud and zero or one of each client. Lives in `$QFT_HOME/deployments/<name>/`. | `Deployment`, CLI noun `deployment` |
| **Spec** | The YAML document describing a deployment: its components, their version sources and options. Immutable once the deployment is added, except through a *swap*. | `DeploymentSpec` |
| **Component** | One product taking part in a deployment: `qfieldcloud`, `qgis`, `qfieldsync`, `qfield-android`, `qfield-ios`, `qfield-desktop`. | `Component`, `components/<name>.py` |
| **Backend** | The runtime strategy a component uses to run: `compose` (QFieldCloud), `qgis-host`, `qgis-docker`, `android-emulator`, `ios-simulator`, `appimage`. | `backends/<name>.py` |
| **Version source** | Where a component's code or binary comes from: `release:<tag>`, `git:<ref>`, `worktree:<path>`, `artifact:<ref>`, `build:<path>`; for QGIS, `host` or `docker:<tag>`. | `VersionSource` |
| **Revision** | The resolved, immutable identity of a version source: a commit sha when known, a label, and whether the source is dirty. Content-addresses the cache. | `Revision` |
| **Materialization** | The cached, revision-keyed result of fetching or building a revision: a checkout, an APK, a plugin folder, a set of image tags. | `Component.materialize()` |
| **Cache** | The shared store of materializations, `~/.cache/qfield-testbed`, independent of deployments. | `Cache`, CLI noun `cache` |
| **Slot** | The integer identifying a deployment's isolation unit: its port block and the names of its Docker resources. | `slot` |
| **Port block** | The 100 contiguous host ports `port_base + 100 × slot` owned by a slot, with a fixed offset per service. | `PortBlock`, `PORT_OFFSETS` |
| **Profile** | The isolated QGIS user profile created for a deployment (`<deployment>/qgis/profiles/default`, under the deployment's own profiles path), holding QFieldSync, its settings and the authentication database. | `ProfileLayout` |
| **Test account** | The QFieldCloud user provisioned in every deployment, used by all clients; optionally owns a demo project. | `TestAccountSpec`, `Credentials` |
| **Wiring** | The provisioning step that points a client at the deployment's QFieldCloud with the test account. | `Component.wire()` |
| **Health** | A component's readiness check, awaited after start. | `Component.health()` |
| **Qt platform** | The Qt platform plugin QGIS starts with (`xcb` for X11, `wayland`, `offscreen`), chosen by `qgis.platform`; `session` follows the desktop session on the host and means X11 in Docker. | `QgisSpec.platform`, `qt_platform()` |
| **Launcher** | The script in `<deployment>/bin/` that opens an interactive client (QGIS with the profile, the emulator UI…). | `Deployment.launcher()` |
| **Scenario** | The YAML document describing a bisect: the fixed components, the one that varies with its range and candidate rule, and the test. | `BisectScenario` |
| **Bisect session** | A resumable execution of a scenario, with its dedicated deployment, candidates, verdicts and log, in `$QFT_HOME/bisect/<session>/`. | `BisectSession`, CLI noun `bisect` |
| **Candidate** | One revision in the ordered list a session searches. | `Candidate` |
| **Verdict** | The outcome of testing a candidate: `good`, `bad` or `skip`. | `Verdict` |
| **Test** | What produces a verdict: a `script` (exit code) or a `manual` prompt. | `ScriptTest`, `ManualTest` |
| **Swap** | Replacing one component's revision inside a running deployment: stop, reset, materialize, render, start, health, wire. The bisect primitive. | `Deployment.swap()` |

Conventions: component names are lowercase with hyphens (`qfield-android`), the same in specs, CLI output,
environment variables (`QFT_QFIELD_ANDROID_*`), directories and documentation. "Server" means QFieldCloud;
"client" means QFieldSync or any QField.
