"""The `qfieldsync` component: the QGIS plugin, installed in the deployment's profile and wired to QFieldCloud.

Releases are the published plugin zips (wheel included). Git refs and worktrees are plugin folders from a
checkout, completed with the ``libqfieldsync`` wheel pinned in ``requirements.txt`` — the same steps as the
plugin's own release workflow. The profile is then seeded with the server URL and a credentials auth config.
"""

from __future__ import annotations

import configparser
import logging
import re
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any, ClassVar

import requests

from qfield_testbed.components import register
from qfield_testbed.components.base import Component
from qfield_testbed.components.qgis import Qgis, _replace_with_symlink
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError
from qfield_testbed.provision.qgis_profile import AuthConfig
from qfield_testbed.revisions import Revision
from qfield_testbed.revisions.git import Mirror, ensure_submodules, resolve_code_source
from qfield_testbed.revisions.github import release_download
from qfield_testbed.spec import QFieldSyncSpec, ReleaseSource, WorktreeSource

log = logging.getLogger(__name__)

PLUGIN_ID = "qfieldsync"
AUTH_CONFIG_NAME = "qfieldcloud"
_LIBQFIELDSYNC_PIN = re.compile(
    r"libqfieldsync\s*@\s*(https://github\.com/opengisch/libqfieldsync/archive/([0-9a-f]{7,40})\.tar\.gz)"
)


def libqfieldsync_pin(requirements: str) -> tuple[str, str]:
    """``(tarball url, sha)`` of the libqfieldsync pinned by a QFieldSync checkout."""
    match = _LIBQFIELDSYNC_PIN.search(requirements)
    if not match:
        raise TestbedError("requirements.txt does not pin libqfieldsync to a GitHub archive; cannot build its wheel")
    return match.group(1), match.group(2)


def plugin_metadata(plugin_dir: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(plugin_dir / "metadata.txt")
    return dict(parser["general"]) if parser.has_section("general") else {}


def compatibility_warnings(metadata: dict[str, str], qgis_version: str | None) -> list[str]:
    if not qgis_version:
        return []
    warnings = []
    qgis = tuple(int(p) for p in qgis_version.split(".")[:2])
    minimum = metadata.get("qgisminimumversion")
    if minimum:
        needed = tuple(int(p) for p in minimum.split(".")[:2])
        if qgis < needed:
            warnings.append(f"plugin needs QGIS >= {minimum}, deployment runs {qgis_version}")
    if qgis[0] >= 4 and metadata.get("supportsqt6", "false").lower() != "true":  # noqa: PLR2004
        warnings.append(
            f"plugin revision does not declare supportsQt6 but QGIS {qgis_version} is Qt 6 — expect import errors"
        )
    return warnings


@register
class QFieldSync(Component):
    name: ClassVar[str] = "qfieldsync"
    spec: QFieldSyncSpec

    @property
    def repo_url(self) -> str:
        return self.spec.repo or self.ctx.config.repositories.qfieldsync

    @property
    def mirror(self) -> Mirror:
        return Mirror(self.ctx.runner, self.ctx.paths, self.repo_url)

    def qgis(self) -> Qgis:
        spec = self.ctx.spec.qgis
        if spec is None:
            raise TestbedError("qfieldsync requires the qgis component")
        return Qgis(self.ctx, spec)

    def check_host(self) -> list[str]:
        problems = []
        if self.ctx.runner.which("git") is None:
            problems.append("git missing")
        if isinstance(self.spec.version, ReleaseSource) and self.ctx.runner.which("gh") is None:
            problems.append("gh missing (needed to download release zips)")
        if self.ctx.runner.which("uv") is None and self.ctx.runner.which("pip") is None:
            problems.append("neither uv nor pip available to build the libqfieldsync wheel")
        return problems

    # --- lifecycle --------------------------------------------------------------------------------------

    def resolve(self) -> Revision:
        return resolve_code_source(self.ctx.runner, self.mirror, self.spec.version)

    def materialize(self, revision: Revision) -> Path:
        """The ready-to-install plugin folder (named ``qfieldsync``), wheel included."""
        if isinstance(revision.source, ReleaseSource):
            return self.materialize_release(revision)
        if isinstance(revision.source, WorktreeSource):
            checkout = revision.source.path
        else:
            assert revision.sha is not None  # noqa: S101
            checkout = self.mirror.checkout(revision.sha)
            self.ctx.cache.touch(f"qfieldsync/checkout/{revision.sha[:12]}", checkout, user=self.ctx.name)
        ensure_submodules(self.ctx.runner, checkout)
        plugin_dir = checkout / PLUGIN_ID
        if not (plugin_dir / "metadata.txt").is_file():
            raise TestbedError(f"{checkout} does not look like a QFieldSync checkout (no {PLUGIN_ID}/metadata.txt)")
        wheel = self.libqfieldsync_wheel(checkout)
        for stale in plugin_dir.glob("libqfieldsync*.whl"):
            if stale.name != wheel.name:
                stale.unlink()
        if not (plugin_dir / wheel.name).exists():
            shutil.copy(wheel, plugin_dir / wheel.name)
        return plugin_dir

    def materialize_release(self, revision: Revision) -> Path:
        assert isinstance(revision.source, ReleaseSource)  # noqa: S101
        tag = revision.source.tag
        target = self.ctx.paths.artifacts / "qfieldsync" / tag
        plugin_dir = target / PLUGIN_ID
        if not (plugin_dir / "metadata.txt").is_file():
            archive = release_download(self.ctx.runner, self.repo_url, tag, "qfieldsync.*.zip", target)
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(target)  # noqa: S202 - official release asset
            if not (plugin_dir / "metadata.txt").is_file():
                raise MaterializationUnavailableError(f"release {tag}: zip has no {PLUGIN_ID}/metadata.txt")
        self.ctx.cache.touch(f"qfieldsync/release/{tag}", target, user=self.ctx.name)
        return plugin_dir

    def libqfieldsync_wheel(self, checkout: Path) -> Path:
        """Build (once) the libqfieldsync wheel pinned by the checkout; cached by sha."""
        url, sha = libqfieldsync_pin((checkout / "requirements.txt").read_text())
        wheel = self.ctx.paths.wheels / f"libqfieldsync_{sha[:12]}.whl"
        cache_key = f"libqfieldsync/wheel/{sha[:12]}"
        if wheel.exists():
            # reused, not rebuilt: still a user, so `qft cache prune` keeps it while this deployment exists
            self.ctx.cache.touch(cache_key, wheel, user=self.ctx.name)
            return wheel
        workdir = self.ctx.paths.artifacts / "libqfieldsync" / sha[:12]
        source_dir = workdir / "src"
        if not source_dir.exists():
            workdir.mkdir(parents=True, exist_ok=True)
            log.info("downloading libqfieldsync %s", sha[:12])
            response = requests.get(url, timeout=120)
            response.raise_for_status()
            tarball = workdir / "src.tar.gz"
            tarball.write_bytes(response.content)
            with tarfile.open(tarball) as tf:
                tf.extractall(source_dir, filter="data")
        folders = sorted(p for p in source_dir.iterdir() if p.is_dir())
        if len(folders) != 1:
            found = ", ".join(p.name for p in folders) or "none"
            raise TestbedError(f"libqfieldsync archive {url} should unpack to one project folder, found {found}")
        project_dir = folders[0]
        out_dir = workdir / "dist"
        out_dir.mkdir(exist_ok=True)
        log.info("building libqfieldsync wheel %s", sha[:12])
        if self.ctx.runner.which("uv"):
            self.ctx.runner.run(["uv", "build", "--wheel", "--out-dir", str(out_dir), str(project_dir)], timeout=600)
        else:
            self.ctx.runner.run(
                ["python3", "-m", "pip", "wheel", "--no-deps", "-w", str(out_dir), str(project_dir)], timeout=600
            )
        built = sorted(out_dir.glob("libqfieldsync-*.whl"))
        if not built:
            raise TestbedError(f"no wheel produced in {out_dir}")
        wheel.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(built[-1], wheel)
        self.ctx.cache.touch(cache_key, wheel, user=self.ctx.name)
        return wheel

    def render(self, revision: Revision, materialized: Path) -> None:
        layout = self.qgis().layout
        layout.ensure()
        _replace_with_symlink(layout.plugins / PLUGIN_ID, materialized)
        metadata = plugin_metadata(materialized)
        wheels = sorted(p.name for p in materialized.glob("libqfieldsync*.whl"))
        self.state.facts.update(
            {
                "plugin_dir": str(materialized),
                "plugin_version": metadata.get("version"),
                "libqfieldsync": wheels[-1] if wheels else None,
                "live": isinstance(revision.source, WorktreeSource),
            }
        )
        qgis_version = self.ctx.facts("qgis").get("version")
        for warning in compatibility_warnings(metadata, qgis_version):
            log.warning("[qfieldsync] %s", warning)

    def start(self) -> None:
        """Loaded by QGIS at launch; nothing runs on its own."""

    def wire(self) -> None:
        cloud = self.ctx.facts("qfieldcloud")
        credentials = self.ctx.state.credentials
        url = cloud.get("url")
        if not url or credentials is None:
            raise TestbedError("qfieldsync: the qfieldcloud component must be running and provisioned first")
        settings: dict[str, Any] = {
            f"PythonPlugins/{PLUGIN_ID}": True,
            "QFieldSync/qfieldCloudServerUrl": url,
            "QFieldSync/qfieldCloudServerUrlsHistory": [url],
            "QFieldSync/qfieldCloudAuthMethod": 1,  # username / password
            "QFieldSync/qfieldCloudRememberMe": self.spec.remember_me,
            "QFieldSync/cloudDirectory": str(self.directory / "cloud"),
            "QFieldSync/exportDirectory": str(self.directory / "export"),
            "QFieldSync/importDirectory": str(self.directory / "import"),
            "QFieldSync/firstRun": False,
        }
        for key in ("cloud", "export", "import"):
            (self.directory / key).mkdir(parents=True, exist_ok=True)
        auth = AuthConfig(
            name=AUTH_CONFIG_NAME,
            method="Basic",
            uri=url,
            config={"username": credentials.username, "password": credentials.password},
            id_settings=["QFieldSync/qfieldCloudAuthcfg"],
        )
        report = self.qgis().seed(settings, auth)
        self.state.facts["authcfg"] = report.get("authcfg")
        self.state.facts["qfieldcloud_url"] = url

    def stop(self) -> None:
        """Nothing to stop."""

    def remove(self) -> None:
        """The symlink lives in the deployment's profile, removed with the deployment."""

    def describe(self) -> dict[str, Any]:
        facts = self.state.facts
        return {
            "plugin_dir": facts.get("plugin_dir"),
            "plugin_version": facts.get("plugin_version"),
            "libqfieldsync": facts.get("libqfieldsync"),
            "qfieldcloud_url": facts.get("qfieldcloud_url"),
            "authcfg": facts.get("authcfg"),
            "live_edits": facts.get("live"),
        }
