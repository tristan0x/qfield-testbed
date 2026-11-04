# Working on a QGIS plugin

Any QGIS plugin checkout (a `metadata.txt` with a `[general]` section at the root or one level down) is installed
from its folder into an isolated QGIS profile; the user's own QGIS profile is never touched.

```sh
qft --json spec suggest --path . [--need sync] --output /tmp/qft-SESSION/spec.yaml   # qgis: {version: host, plugins: [{path: …}]}
qft --json deployment ensure /tmp/qft-SESSION/spec.yaml --timeout 15m
```

- `--need sync` adds QFieldCloud and QFieldSync, for plugins that work with QField projects.
- `qgis: {version: docker:ltr}` (or another `qgis/qgis` tag) tests against a QGIS release the host does not have.
- Run a check headless: `qft --json deployment run NAME --timeout 10m -- LAUNCHER --code DIR/scratch/check.py`
  (LAUNCHER: `data.components.qgis.launcher`). The enabled plugins are loaded when the script runs:
  `qgis.utils.active_plugins` tells whether yours started. **QGIS stays open after a `--code` script**: end it with
  `print(…, flush=True)` then `os._exit(0)` (non-zero on failure), or the run lasts until its timeout.
  Write outputs next to the script: with `docker:` QGIS only sees the testbed home, its cache and the worktrees.
- `QFT_QGIS_EXTRA_PLUGINS` lists the plugins installed in the profile.

Details: `qft docs components/qgis`.
