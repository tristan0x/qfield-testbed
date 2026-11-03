# Working on a QGIS plugin

Any QGIS plugin checkout (a `metadata.txt` with a `[general]` section at the root or one level down) is installed
from its folder into an isolated QGIS profile; the user's own QGIS profile is never touched.

```sh
qft --json spec suggest --path . [--need sync] --output "$TMPDIR/qft-spec.yaml"   # qgis: {version: host, plugins: [{path: …}]}
qft --json deployment ensure "$TMPDIR/qft-spec.yaml" --timeout 15m
```

- `--need sync` adds QFieldCloud and QFieldSync, for plugins that work with QField projects.
- `qgis: {version: docker:ltr}` (or another `qgis/qgis` tag) tests against a QGIS release the host does not have.
- Run a check headless: `qft --json deployment run NAME --timeout 10m -- "$QFT_QGIS_LAUNCHER" --code check.py`.
  Write outputs next to the script: with `docker:` QGIS only sees your home, the testbed home and the worktrees.
- `QFT_QGIS_EXTRA_PLUGINS` lists the plugins installed in the profile.

Details: `qft docs components/qgis`.
