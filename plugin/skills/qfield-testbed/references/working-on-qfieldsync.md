# Working on QFieldSync

A QFieldSync checkout runs inside QGIS with an isolated profile, connected to a QFieldCloud of the deployment.

```sh
qft --json spec suggest --path . --output /tmp/qft-SESSION/spec.yaml   # qfieldsync: worktree:<checkout>, qgis: host, qfieldcloud: latest release
qft --json deployment ensure /tmp/qft-SESSION/spec.yaml --timeout 20m
```

- `worktree:` symlinks the plugin folder into the profile: edits are live **after a QGIS restart**. A headless
  script started with `qft deployment run` starts a fresh QGIS, so it always sees the current code.
- The profile already holds an authentication configuration for the test account
  (`$QFT_QFIELDSYNC_AUTHCFG`) and the server URL; `remember_me: true` logs the plugin in at startup.
- Drive QGIS headless: `qft --json deployment run NAME --timeout 10m -- LAUNCHER --code DIR/scratch/check.py`
  (`QFT_QGIS_LAUNCHER` is set inside `run`; without a display it uses `xvfb-run`). The script can import
  `qfieldsync` and `libqfieldsync`, and read `QFT_QFIELDCLOUD_URL`, `QFT_QFIELDCLOUD_TOKEN`.
- Against QFieldCloud master instead of the latest release: `--need master`, or set
  `qfieldcloud: {version: git:master}`.
- A human wants to look: `qft deployment launch NAME qgis` (needs a display).

Details: `qft docs components/qfieldsync`, `qft docs components/qgis`, `qft docs environment`.
