# Component `qfieldcloud`

The server. Runs QFieldCloud's own Docker Compose files (`docker-compose.yml` +
`docker-compose.override.standalone.yml` of the selected revision) under the compose project
`qft-<deployment>-qfc`, with an `.env` derived from that revision's `.env.example` and a testbed override that
sets per-deployment ports, image names and the shared network.

## Version sources

| Source | What runs |
|---|---|
| `release:vYY.N` | Published images `ghcr.io/opengisch/qfieldcloud-{app,worker-wrapper,createbuckets,qgis3,qgis4}:YY.N`; compose files from the tag's checkout. Fast. |
| `git:<ref>` | Images built from a cached checkout of the ref, tagged `qft-qfieldcloud-<service>:<sha>` and shared by every deployment on that sha. |
| `worktree:<path>` | Images built from your checkout, source bind-mounted for live reload of the Django app and worker; rebuilt on every start. |

The oldest supported release is `v26.0`.

## Endpoint

Clients talk to the Django development server over plain HTTP — the upstream README's recommendation for QField
— on port block offset 0: `http://127.0.0.1:<port>/` from the host, `http://app:8000/` from containers on the
deployment network. `tls: true` additionally starts nginx with mkcert certificates on offsets 1–2 (clients
must then trust the generated root CA, `conf/nginx/certs/rootCA.pem` in the checkout).

## Provisioning

On the first start: `manage.py migrate`, `collectstatic`, then `manage.py createuser` for the **test account**
(a superuser by default). Its credentials are in `qft deployment show`; a token is fetched and exported as
`QFT_QFIELDCLOUD_TOKEN`. With `demo_project: true`, the bundled sample project is created and uploaded through
the API with `qfieldcloud-sdk`.

This **wiring** runs right after `GET /api/v1/status/` answers, while workers may still restart: each call (token,
project lookup or creation, upload, processing job) is retried for about a minute on a dropped connection, a
timeout or a 502/503/504. If it still fails, the start stops with one line naming the step, the component is
`wiring failed` in `qft deployment list`, and `qft deployment start NAME` retries it.

`SENTRY_RELEASE` is set to the revision label; the testbed reads it back from the running `app` container (and
from `GET /api/v1/server/info/` when the release exposes it) as the `version` fact — the bisect engine checks it
after every swap.

The worker's temporary directory is the host `/tmp` (see [isolation](../isolation.md)); the Django `app` and
`worker_wrapper` containers share the deployment network with the QGIS containers the worker spawns.

## Facts (`show`, `QFT_QFIELDCLOUD_*`)

`url`, `url_internal`, `admin_url`, `version` (from `/api/v1/server/info/`), `compose_project`, `checkout`,
`images`.

## Troubleshooting

- `docker compose … up` fails on `createbuckets`: the object storage port of the slot is taken by something that
  escaped the probe; remove and re-add the deployment.
- A `worktree` build is slow: the first build of the QGIS worker images takes minutes; they are cached per
  revision afterwards.
