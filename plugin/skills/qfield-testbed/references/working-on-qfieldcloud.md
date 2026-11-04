# Working on QFieldCloud

A QFieldCloud checkout runs with its own Docker Compose files, images built from the checkout, the Django app and
the worker bind-mounted for live reload.

```sh
qft --json spec suggest --path . [--need worker] --output /tmp/qft-SESSION/spec.yaml   # qfieldcloud: worktree:<checkout>, qgis + qfieldsync release
qft --json deployment ensure /tmp/qft-SESSION/spec.yaml --timeout 30m
```

- The first `worktree:` start builds the images (several minutes); every start rebuilds them, so code changes to
  the Docker images need `qft deployment restart NAME --component qfieldcloud`. Python edits in the app reload by
  themselves.
- The test account (superuser) and its API token are in `qft --json deployment show NAME` and in
  `QFT_QFIELDCLOUD_USERNAME`, `QFT_QFIELDCLOUD_PASSWORD`, `QFT_QFIELDCLOUD_TOKEN`.
- Jobs (packaging, delta apply, processing) run in the worker: `--need worker` (`worker_replicas: 1`), then
  `qft --json deployment logs NAME --component qfieldcloud --service worker_wrapper --since 10m`. The Django app is
  `--service app`.
- `demo_project: true` uploads a sample project for the test account (`QFT_QFIELDCLOUD_DEMO_PROJECT_ID`).
- Raw compose access: `docker compose -p "$QFT_QFIELDCLOUD_COMPOSE_PROJECT" …` inside `qft deployment run`.
- From other containers of the deployment the server is `$QFT_QFIELDCLOUD_URL_INTERNAL` (`http://app:8000/`).

Details: `qft docs components/qfieldcloud`, `qft docs environment`.
