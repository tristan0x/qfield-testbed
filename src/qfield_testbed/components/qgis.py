"""The `qgis` component: QGIS Desktop with an isolated profile per deployment.

The host backend uses the installed QGIS with ``--profiles-path <deployment>/qgis``; the profile is seeded
headless (settings, authentication database) and extra plugins are installed into it. The launcher
``<deployment>/bin/qgis`` opens it.
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, ClassVar

import requests

from qfield_testbed.backends.qgis_host import PROFILE_NAME, QgisHostBackend
from qfield_testbed.components import register
from qfield_testbed.components.base import Component
from qfield_testbed.errors import TestbedError, UnsupportedComponentError
from qfield_testbed.provision import qgis_profile
from qfield_testbed.provision.qgis_profile import ProfileLayout
from qfield_testbed.revisions import Revision
from qfield_testbed.spec import DockerSource, HostSource, PluginSpec, QgisSpec

log = logging.getLogger(__name__)


@register
class Qgis(Component):
    name: ClassVar[str] = "qgis"
    spec: QgisSpec

    # --- helpers ----------------------------------------------------------------------------------------

    @property
    def layout(self) -> ProfileLayout:
        return ProfileLayout(profiles_path=self.directory)

    @property
    def launcher(self) -> Path:
        return self.ctx.directory / "bin" / "qgis"

    def backend(self) -> QgisHostBackend:
        if isinstance(self.spec.version, DockerSource):
            raise UnsupportedComponentError("qgis: the docker backend is not available yet; use `version: host`")
        return QgisHostBackend.detect(self.ctx.runner)

    def check_host(self) -> list[str]:
        if isinstance(self.spec.version, DockerSource):
            return ["the docker backend is not available yet; use `version: host`"]
        try:
            backend = self.backend()
            backend.version()
        except TestbedError as exc:
            return [str(exc)]
        if not backend.python.exists():
            return [f"{backend.python} missing (needed to seed the profile)"]
        for plugin in self.spec.plugins:
            if plugin.path is not None and not (plugin.path / "metadata.txt").is_file():
                return [f"plugin path {plugin.path} has no metadata.txt"]
        return []

    # --- lifecycle --------------------------------------------------------------------------------------

    def resolve(self) -> Revision:
        if isinstance(self.spec.version, HostSource):
            return Revision(source=self.spec.version, label=f"host-{self.backend().version()}")
        raise UnsupportedComponentError("qgis: the docker backend is not available yet; use `version: host`")

    def materialize(self, revision: Revision) -> Path:  # noqa: ARG002 - the host install is the materialization
        return self.backend().binary

    def render(self, revision: Revision, materialized: Path) -> None:  # noqa: ARG002 - host QGIS has one revision
        layout = self.layout
        layout.ensure()
        backend = self.backend()
        env = {**layout.launcher_env(), **self.spec.env}
        self.launcher.parent.mkdir(parents=True, exist_ok=True)
        self.launcher.write_text("\n".join(backend.launcher_lines(layout.profiles_path, env)) + "\n")
        self.launcher.chmod(0o755)
        installed = [self.install_plugin(plugin, layout) for plugin in self.spec.plugins]
        self.state.facts.update(
            {
                "backend": "qgis-host",
                "binary": str(materialized),
                "profiles_path": str(layout.profiles_path),
                "profile": str(layout.profile),
                "launcher": str(self.launcher),
                "extra_plugin_ids": installed,
            }
        )

    def install_plugin(self, plugin: PluginSpec, layout: ProfileLayout) -> str:
        """Put an extra plugin into the profile and return its folder name (the plugin id)."""
        if plugin.path is not None:
            target = layout.plugins / plugin.path.name
            _replace_with_symlink(target, plugin.path)
            return plugin.path.name
        assert plugin.zip is not None  # noqa: S101 - PluginSpec guarantees one of the two
        archive = self.fetch_plugin_zip(plugin.zip)
        with zipfile.ZipFile(archive) as zf:
            roots = {name.split("/", 1)[0] for name in zf.namelist() if "/" in name}
            if len(roots) != 1:
                raise TestbedError(
                    f"plugin zip {plugin.zip} must contain exactly one top-level folder, found {sorted(roots)}"
                )
            (root,) = roots
            target = layout.plugins / root
            if target.is_symlink() or target.exists():
                shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink()
            zf.extractall(layout.plugins)
        return root

    def fetch_plugin_zip(self, source: str) -> Path:
        if not source.startswith(("http://", "https://")):
            path = Path(source).expanduser()
            if not path.is_file():
                raise TestbedError(f"plugin zip not found: {source}")
            return path
        key = source.rstrip("/").rsplit("/", 2)
        name = "-".join(part for part in key[-2:] if part) or "plugin"
        target = self.ctx.paths.artifacts / "qgis-plugins" / f"{name}.zip"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            log.info("downloading plugin %s", source)
            response = requests.get(source, timeout=120)
            response.raise_for_status()
            target.write_bytes(response.content)
        self.ctx.cache.touch(f"qgis/plugin/{name}", target, user=self.ctx.name)
        return target

    def start(self) -> None:
        """QGIS is interactive: nothing runs until `qft deployment launch <name> qgis`."""

    def wire(self) -> None:
        """Enable the extra plugins in the profile (QFieldSync seeds its own settings)."""
        plugins = self.state.facts.get("extra_plugin_ids", [])
        if plugins:
            self.seed({f"PythonPlugins/{plugin_id}": True for plugin_id in plugins}, None)

    def seed(self, settings: dict[str, Any], auth: qgis_profile.AuthConfig | None) -> dict[str, Any]:
        """Run the headless seed script with the backend's interpreter; other components call this too."""
        layout = self.layout
        layout.ensure()
        backend = self.backend()
        script = qgis_profile.render_seed_script(self.ctx.name, layout, settings, auth, qgis_version=backend.version())
        layout.seed_script.write_text(script)
        stdout = backend.run_python(layout.seed_script)
        return qgis_profile.parse_seed_report(stdout)

    def stop(self) -> None:
        """Nothing to stop: QGIS is launched by the user."""

    def remove(self) -> None:
        """The profile lives in the deployment directory, removed with it."""

    def describe(self) -> dict[str, Any]:
        facts = self.state.facts
        return {
            "backend": facts.get("backend", "qgis-host"),
            "version": self.state.revision.label.removeprefix("host-") if self.state.revision else None,
            "launcher": facts.get("launcher", str(self.launcher)),
            "profiles_path": facts.get("profiles_path", str(self.layout.profiles_path)),
            "profile": facts.get("profile", str(self.layout.profile)),
            "profile_name": PROFILE_NAME,
            "extra_plugins": ", ".join(facts.get("extra_plugin_ids", [])) or None,
        }


def _replace_with_symlink(target: Path, source: Path) -> None:
    if target.is_symlink() or target.exists():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source, target_is_directory=True)
