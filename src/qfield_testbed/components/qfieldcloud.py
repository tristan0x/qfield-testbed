"""The `qfieldcloud` component: QFieldCloud's own compose stack, namespaced per deployment.

The compose files come from the selected revision's checkout; the testbed contributes an ``.env`` derived
from that revision's ``.env.example`` and an override that pins images, ports and the network. Clients use
the Django development port over plain HTTP (upstream's recommendation for QField); ``tls: true`` adds nginx.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any, ClassVar

import yaml
from jinja2 import Environment, PackageLoader, StrictUndefined

from qfield_testbed.components import register
from qfield_testbed.components.base import Component
from qfield_testbed.errors import TestbedError
from qfield_testbed.provision import qfieldcloud_account
from qfield_testbed.revisions import Revision
from qfield_testbed.revisions.git import Mirror, resolve_code_source
from qfield_testbed.runtimes import docker
from qfield_testbed.runtimes.http import wait_for_http
from qfield_testbed.spec import QFieldCloudSpec, ReleaseSource, WorktreeSource

log = logging.getLogger(__name__)

GHCR = "ghcr.io/opengisch/qfieldcloud-{image}:{tag}"
OLDEST_RELEASE = (26, 1)

# compose service → (ghcr image name, build context directory)
BUILT_SERVICES: dict[str, tuple[str, str]] = {
    "app": ("app", "docker-app"),
    "worker_wrapper": ("worker-wrapper", "docker-app"),
    "createbuckets": ("createbuckets", "docker-createbuckets"),
    "qgis": ("qgis", "docker-qgis"),
    "qgis3": ("qgis3", "docker-qgis"),
    "qgis4": ("qgis4", "docker-qgis"),
}
QGIS_SERVICES = ("qgis", "qgis3", "qgis4")
TLS_SERVICES = ("nginx", "mkcert", "certbot")

# .env key → port block offset name; only the keys present in the revision's .env.example are patched
PORT_KEYS: dict[str, str] = {
    "DJANGO_DEV_PORT": "app",
    "WEB_HTTPS_PORT": "https",
    "WEB_HTTP_PORT": "http",
    "HOST_POSTGRES_PORT": "postgres",
    "OBJECT_STORAGE_API_PORT": "s3_api",
    "MINIO_API_PORT": "s3_api",
    "OBJECT_STORAGE_BROWSER_PORT": "s3_console",
    "MINIO_BROWSER_PORT": "s3_console",
    "SMTP4DEV_WEB_PORT": "smtp_web",
    "SMTP4DEV_SMTP_PORT": "smtp",
    "SMTP4DEV_IMAP_PORT": "imap",
    "WEBDAV_PUBLIC_PORT": "webdav",
    "DEBUG_APP_DEBUGPY_PORT": "debugpy_app",
    "DEBUG_WORKER_WRAPPER_DEBUGPY_PORT": "debugpy_worker",
}
DROPPED_KEYS = ("COMPOSE_FILE", "COMPOSE_PATH_SEPARATOR")

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_TEMPLATES = Environment(
    loader=PackageLoader("qfield_testbed", "assets/templates"),
    undefined=StrictUndefined,
    autoescape=False,  # noqa: S701 - renders compose YAML and shell snippets, never HTML
    keep_trailing_newline=True,
)


# ---------------------------------------------------------------------------------------------------------
# .env derivation (pure, unit-tested)
# ---------------------------------------------------------------------------------------------------------


def parse_env_blocks(text: str) -> list[tuple[str | None, str]]:
    """Split a dotenv file into blocks ``(key, raw_text)``; comments/blank lines have ``key=None``.

    Single-quoted values spanning several lines (QFieldCloud's ``STORAGES`` JSON) stay in one block.
    """
    blocks: list[tuple[str | None, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _ENV_LINE.match(line)
        if not match:
            blocks.append((None, line))
            i += 1
            continue
        key, value = match.group(1), match.group(2)
        block = [line]
        if value.startswith("'") and value.count("'") % 2 == 1:
            i += 1
            while i < len(lines):
                block.append(lines[i])
                if "'" in lines[i]:
                    break
                i += 1
        blocks.append((key, "\n".join(block)))
        i += 1
    return blocks


def env_value(blocks: list[tuple[str | None, str]], key: str) -> str | None:
    """Raw value of ``key`` (quotes stripped) or None when the key is not set."""
    for block_key, raw in blocks:
        if block_key == key:
            value = raw.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":  # noqa: PLR2004
                value = value[1:-1]
            return value
    return None


def patch_env(text: str, values: dict[str, str], *, drop: tuple[str, ...] = DROPPED_KEYS) -> str:
    """Rewrite ``text`` so every key of ``values`` is set; keys absent from the example are appended."""
    blocks = parse_env_blocks(text)
    present = {key for key, _ in blocks if key}
    out: list[str] = []
    for key, raw in blocks:
        if key is None:
            out.append(raw)
        elif key in drop:
            out.append(f"# {key} is set by qfield-testbed on the command line")
        elif key in values:
            out.append(f"{key}={values[key]}")
        else:
            out.append(raw)
    missing = [key for key in values if key not in present]
    if missing:
        out.append("")
        out.append("# --- added by qfield-testbed ---")
        out.extend(f"{key}={values[key]}" for key in missing)
    return "\n".join(out) + "\n"


def storages_with_endpoint(raw_json: str, endpoint_url: str) -> str:
    """The example ``STORAGES`` JSON with every S3 ``endpoint_url`` pointed at this deployment's port."""
    storages = json.loads(raw_json)
    for storage in storages.values():
        options = storage.get("OPTIONS", {})
        if "endpoint_url" in options:
            options["endpoint_url"] = endpoint_url
    return "'" + json.dumps(storages, separators=(",", ":")) + "'"


def release_tuple(tag: str) -> tuple[int, int]:
    match = re.fullmatch(r"v(\d+)\.(\d+)", tag)
    if not match:
        raise TestbedError(f"QFieldCloud release tags look like vYY.N, got '{tag}'")
    return int(match.group(1)), int(match.group(2))


def compose_services(checkout: Path) -> set[str]:
    services: set[str] = set()
    for name in ("docker-compose.yml", "docker-compose.override.standalone.yml"):
        file = checkout / name
        if file.is_file():
            data = yaml.safe_load(file.read_text()) or {}
            services.update((data.get("services") or {}).keys())
    return services


# ---------------------------------------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------------------------------------


@register
class QFieldCloud(Component):
    name: ClassVar[str] = "qfieldcloud"
    spec: QFieldCloudSpec

    # --- helpers ----------------------------------------------------------------------------------------

    @property
    def repo_url(self) -> str:
        return self.spec.repo or self.ctx.config.repositories.qfieldcloud

    @property
    def mirror(self) -> Mirror:
        return Mirror(self.ctx.runner, self.ctx.paths, self.repo_url)

    @property
    def compose_project(self) -> str:
        return f"qft-{self.ctx.name}-qfc"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.ctx.ports.port('app')}/"

    @property
    def url_internal(self) -> str:
        return "http://app:8000/"

    @property
    def checkout(self) -> Path:
        path = self.state.facts.get("checkout")
        if not path:
            raise TestbedError("qfieldcloud is not rendered yet; run `qft deployment start`")
        return Path(path)

    def compose(self) -> docker.Compose:
        checkout = self.checkout
        files = [
            checkout / "docker-compose.yml",
            checkout / "docker-compose.override.standalone.yml",
            self.directory / "compose.override.yml",
        ]
        profiles = ["tls"] if self.spec.tls else []
        return docker.Compose(
            runner=self.ctx.runner,
            project=self.compose_project,
            files=files,
            env_file=self.directory / ".env",
            project_directory=checkout,
            profiles=profiles,
        )

    def check_host(self) -> list[str]:
        problems = []
        if not docker.daemon_available(self.ctx.runner):
            problems.append("docker daemon not reachable")
        elif docker.compose_version(self.ctx.runner) is None:
            problems.append("docker compose plugin missing")
        if self.ctx.runner.which("git") is None:
            problems.append("git missing")
        if isinstance(self.spec.version, ReleaseSource) and release_tuple(self.spec.version.tag) < OLDEST_RELEASE:
            problems.append(
                f"release {self.spec.version.tag} predates v{OLDEST_RELEASE[0]}.{OLDEST_RELEASE[1]}, the oldest supported"
            )
        return problems

    # --- lifecycle --------------------------------------------------------------------------------------

    def resolve(self) -> Revision:
        return resolve_code_source(self.ctx.runner, self.mirror, self.spec.version)

    def materialize(self, revision: Revision) -> Path:
        if isinstance(revision.source, WorktreeSource):
            return revision.source.path
        assert revision.sha is not None  # noqa: S101 - release/git revisions always carry a sha
        checkout = self.mirror.checkout(revision.sha)
        self.ctx.cache.touch(f"qfieldcloud/checkout/{revision.sha[:12]}", checkout, user=self.ctx.name)
        if isinstance(revision.source, ReleaseSource):
            images = self.images(revision, checkout)
            for image in images.values():
                if not docker.image_exists(self.ctx.runner, image):
                    log.info("pulling %s", image)
                    self.ctx.runner.run(["docker", "pull", "--quiet", image], timeout=1800)
            self.ctx.cache.touch(
                f"qfieldcloud/images/{revision.key}", checkout, user=self.ctx.name, images=list(images.values())
            )
        return checkout

    def images(self, revision: Revision, checkout: Path) -> dict[str, str]:
        """Image per built compose service: ghcr tags for releases, context-keyed local tags otherwise."""
        services = compose_services(checkout) & set(BUILT_SERVICES)
        images: dict[str, str] = {}
        for service in sorted(services):
            ghcr_name, context = BUILT_SERVICES[service]
            if isinstance(revision.source, ReleaseSource):
                images[service] = GHCR.format(image=ghcr_name, tag=revision.source.tag.removeprefix("v"))
            else:
                images[service] = f"qft-qfieldcloud-{ghcr_name}:{self.context_key(revision, checkout, context)}"
        return images

    def context_key(self, revision: Revision, checkout: Path, context: str) -> str:
        """Tree sha of the build context, so identical Dockerfiles across revisions share one image.

        A worktree with uncommitted changes under the context gets a per-deployment tag rebuilt on every start.
        """
        git = ["git", "-C", str(checkout)]
        if isinstance(revision.source, WorktreeSource):
            status = self.ctx.runner.run([*git, "status", "--porcelain", "--", context], check=False).stdout.strip()
            if status:
                return f"wt-{self.ctx.name}"
        tree = self.ctx.runner.run([*git, "rev-parse", f"HEAD:{context}"], check=False).stdout.strip()
        return tree[:12] if tree else f"wt-{self.ctx.name}"

    def render(self, revision: Revision, materialized: Path) -> None:
        checkout = materialized
        example = checkout / ".env.example"
        if not example.is_file():
            raise TestbedError(f"{checkout} has no .env.example; is it a QFieldCloud checkout?")
        services = compose_services(checkout)
        images = self.images(revision, checkout)
        (self.directory / ".env").write_text(self.derive_env(example.read_text(), revision, images, checkout))
        template = _TEMPLATES.get_template("qfieldcloud.compose.override.yml.j2")
        (self.directory / "compose.override.yml").write_text(
            template.render(
                name=self.ctx.name,
                revision=str(revision),
                images=images,
                ports=self.ctx.ports.all_ports(),
                worktree=isinstance(revision.source, WorktreeSource),
                checkout=str(checkout),
                qgis_services=[s for s in QGIS_SERVICES if s in images],
                tls_services=[s for s in TLS_SERVICES if s in services],
                services=services,
                network=self.ctx.network,
            )
        )
        self.state.facts.update(
            {
                "checkout": str(checkout),
                "compose_project": self.compose_project,
                "images": images,
                "worktree": isinstance(revision.source, WorktreeSource),
            }
        )

    def derive_env(self, example: str, revision: Revision, images: dict[str, str], checkout: Path) -> str:
        blocks = parse_env_blocks(example)
        ports = self.ctx.ports
        values: dict[str, str] = {
            "COMPOSE_PROJECT_NAME": self.compose_project,
            "QFIELDCLOUD_DEFAULT_NETWORK": self.ctx.network,
            "QFIELDCLOUD_HOST": "localhost",
            "DJANGO_ALLOWED_HOSTS": '"localhost 127.0.0.1 0.0.0.0 app nginx 10.0.2.2 172.17.0.1 host.docker.internal"',
            "SECRET_KEY": secrets.token_urlsafe(48),
            "SALT_KEY": secrets.token_urlsafe(48),
            "DEBUG": "1",
            # one ENVIRONMENT per deployment: the worker labels the QGIS containers it spawns with
            # `app=<ENVIRONMENT>_worker` and kills every container carrying that label whose job it does not
            # know — with a shared value, one deployment's worker would cancel the jobs of the others.
            # (The only thing lost versus "development" is the Django Debug Toolbar.)
            "ENVIRONMENT": f"qft-{self.ctx.name}",
            "SENTRY_RELEASE": revision.label,
            # the worker hands `/tmp/<job>` host paths to the QGIS containers it spawns (TMP_FILE is hardcoded
            # to /tmp in worker_wrapper/wrapper.py), so the host directory must be /tmp — shared by deployments
            "TMP_DIRECTORY": "/tmp",  # noqa: S108
            "QFIELDCLOUD_WORKER_REPLICAS": str(self.spec.worker_replicas),
            "DOCKER_DEFAULT_PLATFORM": "linux/amd64",
        }
        for key, port_name in PORT_KEYS.items():
            if env_value(blocks, key) is not None:
                values[key] = str(ports.port(port_name))
        if env_value(blocks, "QFIELDCLOUD_WORKER_DEQUEUE_INTERVAL_S") is not None:
            values["QFIELDCLOUD_WORKER_DEQUEUE_INTERVAL_S"] = "1"
        storages = env_value(blocks, "STORAGES")
        if storages:
            values["STORAGES"] = storages_with_endpoint(storages, f"http://172.17.0.1:{ports.port('s3_api')}")
        for service, image in images.items():
            if service in QGIS_SERVICES:
                values[f"QFIELDCLOUD_{service.upper()}_IMAGE_NAME"] = image
        if isinstance(revision.source, WorktreeSource):
            values["DEBUG_QGIS_WORKER_HOST_PATH"] = str(checkout / "docker-qgis")
        return patch_env(example, values)

    def start(self) -> None:
        compose = self.compose()
        images = self.state.facts.get("images", {})
        release = not any(":" in image and image.startswith("qft-") for image in images.values())
        if not release:
            to_build = [
                s
                for s, image in images.items()
                if image.endswith(f":wt-{self.ctx.name}") or not docker.image_exists(self.ctx.runner, image)
            ]
            if to_build:
                log.info(
                    "building images for %s (first time for this revision may take several minutes)",
                    ", ".join(to_build),
                )
                compose.run("build", *to_build, capture=False, timeout=7200)
        compose.up()
        if not self.state.initialized:
            self.initialize(compose)

    def initialize(self, compose: docker.Compose) -> None:
        account = self.spec.test_account
        credentials = self.ctx.state.credentials
        if credentials is None:
            raise TestbedError("no test account credentials in the deployment state")
        log.info("applying migrations")
        compose.exec_run("app", "python", "manage.py", "migrate", "--no-input")
        compose.exec_run("app", "python", "manage.py", "collectstatic", "--no-input", timeout=600)
        log.info("creating test account '%s'", credentials.username)
        cmd = [
            "python",
            "manage.py",
            "createuser",
            f"--username={credentials.username}",
            f"--password={credentials.password}",
            f"--email={account.email}",
        ]
        if account.superuser:
            cmd.append("--superuser")
        compose.exec_run("app", *cmd)
        self.state.initialized = True

    def health(self, timeout: float = 240) -> None:
        wait_for_http(self.url + "api/v1/status/", timeout=timeout)
        self.state.facts["version"] = self.running_version()

    def running_version(self) -> str | None:
        """The revision label the app container was started with (``SENTRY_RELEASE``); bisect asserts it."""
        info = qfieldcloud_account.server_info(self.url)
        if info.get("version"):
            return str(info["version"])
        result = self.ctx.runner.run(
            [
                "docker",
                "inspect",
                f"{self.compose_project}-app-1",
                "--format",
                "{{range .Config.Env}}{{println .}}{{end}}",
            ],
            check=False,
        )
        for line in result.stdout.splitlines():
            if line.startswith("SENTRY_RELEASE="):
                return line.split("=", 1)[1] or None
        return None

    def wire(self) -> None:
        credentials = self.ctx.state.credentials
        if credentials is None:
            return
        credentials.token = qfieldcloud_account.login(self.url, credentials.username, credentials.password)
        if self.spec.test_account.demo_project:
            project_id, status = qfieldcloud_account.ensure_demo_project(
                self.url, credentials.token, credentials.username
            )
            self.state.facts["demo_project_id"] = project_id
            self.state.facts["demo_project_status"] = status

    def stop(self) -> None:
        if "checkout" in self.state.facts:
            self.compose().stop()

    def reset(self) -> None:
        if "checkout" in self.state.facts:
            self.compose().down(volumes=True)
        self.state.initialized = False
        self.state.facts.pop("demo_project_id", None)

    def remove(self) -> None:
        if "checkout" in self.state.facts:
            self.compose().down(volumes=True)
            for image in self.state.facts.get("images", {}).values():
                if image.endswith(f":wt-{self.ctx.name}"):
                    self.ctx.runner.run(["docker", "image", "rm", image], check=False)

    def logs(self, *, follow: bool = False) -> None:
        self.compose().logs(follow=follow)

    def describe(self) -> dict[str, Any]:
        facts = self.state.facts
        return {
            "url": self.url,
            "url_internal": self.url_internal,
            "admin_url": self.url + "admin/",
            "version": facts.get("version"),
            "compose_project": self.compose_project,
            "checkout": facts.get("checkout"),
            "images": facts.get("images"),
            "demo_project_id": facts.get("demo_project_id"),
            "demo_project_status": facts.get("demo_project_status"),
            "object_storage_console": f"http://127.0.0.1:{self.ctx.ports.port('s3_console')}/",
            "mail_ui": f"http://127.0.0.1:{self.ctx.ports.port('smtp_web')}/",
        }

    def env(self) -> dict[str, str]:
        variables = super().env()
        variables.pop("QFT_QFIELDCLOUD_IMAGES", None)
        return variables
