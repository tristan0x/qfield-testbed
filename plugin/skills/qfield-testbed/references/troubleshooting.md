# Troubleshooting

- **Start with `qft --json doctor`**: each failed check has a `fix_hint`. Missing Docker, KVM, QGIS, `gh` login or
  `xvfb-run` are for the human to install.
- **No display** (SSH, CI): QGIS on the host runs under `xvfb-run -a` (package `xvfb`); `qgis: docker:<tag>` works
  headless as well. `qft deployment launch` needs a real display.
- **`TIMEOUT` / `COMPONENT_FAILED`**: `error.details` names the component and step and carries `log_tail`; then
  `qft --json deployment logs NAME --component C --tail 200`. A first `git:`/`worktree:` start builds images and
  is slow: give `--timeout 30m` before concluding.
- **`RESOURCE_EXHAUSTED`** (no free slot, port or disk): remove your own finished deployments,
  `qft gc --expired --yes`, `qft cache prune --dry-run` (ask the user before pruning: the cache is shared).
- **Leftovers** after a crash (containers, networks, files with no deployment): `qft gc --orphaned --dry-run`, then
  show the user before running it without `--dry-run`.
- **Name taken** (`ALREADY_EXISTS`): `--name auto`, or `--replace` on a deployment of yours.
- **Someone else's deployment** (`REFUSED` on ownership): leave it alone and create your own.

Details: `qft docs isolation`, `qft docs json-output`.
