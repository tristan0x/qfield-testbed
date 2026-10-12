"""The `qfield-desktop` component: QField for Linux (AppImage) on the host, pointed at the deployment's QFieldCloud.

The cheapest way to exercise QField against a deployment: no emulator, the release AppImage runs with its own
XDG directories so its settings never mix with a QField you use yourself.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, ClassVar

from qfield_testbed.backends.appimage import AppImageBackend
from qfield_testbed.components import register
from qfield_testbed.components.base import Component
from qfield_testbed.errors import TestbedError
from qfield_testbed.provision.qfield_settings import CONF_RELATIVE_PATH, merge_qfieldcloud_section
from qfield_testbed.revisions import Revision
from qfield_testbed.revisions.github import owner_repo, release_download
from qfield_testbed.spec import QFieldDesktopSpec, ReleaseSource

log = logging.getLogger(__name__)

APPIMAGE_PATTERN = "qfield-*-linux-x64.AppImage"
APPIMAGE_NAME = "qfield-linux-x64.AppImage"


@register
class QFieldDesktop(Component):
    name: ClassVar[str] = "qfield-desktop"
    spec: QFieldDesktopSpec

    @property
    def repo(self) -> str:
        return owner_repo(self.spec.repo)

    def backend(self) -> AppImageBackend:
        return AppImageBackend(directory=self.directory)

    @property
    def launcher(self) -> Path:
        return self.ctx.directory / "bin" / "qfield-desktop"

    def check_host(self) -> list[str]:
        problems = []
        if self.ctx.runner.which("gh") is None:
            problems.append("gh missing (needed to download the release AppImage)")
        return problems

    def resolve(self) -> Revision:
        source = self.spec.version
        if not isinstance(source, ReleaseSource):
            raise TestbedError(f"unsupported version source for qfield-desktop: {source}")
        return Revision(source=source, label=source.tag)

    def materialize(self, revision: Revision) -> Path:
        assert isinstance(revision.source, ReleaseSource)  # noqa: S101
        target_dir = self.ctx.paths.artifacts / "qfield-desktop" / revision.key
        appimage = target_dir / APPIMAGE_NAME
        if not appimage.exists():
            downloaded = release_download(self.ctx.runner, self.repo, revision.source.tag, APPIMAGE_PATTERN, target_dir)
            downloaded.replace(appimage)
            appimage.chmod(0o755)
        self.ctx.cache.touch(f"qfield-desktop/appimage/{revision.key}", target_dir, user=self.ctx.name)
        return appimage

    def render(self, revision: Revision, materialized: Path) -> None:  # noqa: ARG002 - one AppImage per revision
        backend = self.backend()
        backend.ensure()
        self.launcher.parent.mkdir(parents=True, exist_ok=True)
        self.launcher.write_text("\n".join(backend.launcher_lines(materialized)) + "\n")
        self.launcher.chmod(0o755)
        self.state.facts.update(
            {"appimage": str(materialized), "launcher": str(self.launcher), "config_dir": str(backend.config_home)}
        )

    def start(self) -> None:
        """Interactive: nothing runs until `qft deployment launch <name> qfield-desktop`."""

    def wire(self) -> None:
        cloud = self.ctx.facts("qfieldcloud")
        credentials = self.ctx.state.credentials
        url = cloud.get("url")
        if not url or credentials is None:
            raise TestbedError("qfield-desktop: the qfieldcloud component must be running and provisioned first")
        conf = self.backend().config_home / CONF_RELATIVE_PATH
        conf.parent.mkdir(parents=True, exist_ok=True)
        existing = conf.read_text() if conf.exists() else ""
        conf.write_text(merge_qfieldcloud_section(existing, url, credentials.username))
        self.state.facts["qfieldcloud_url"] = url
        self.state.facts["settings_file"] = str(conf)

    def stop(self) -> None:
        """Nothing to stop: QField is launched by the user."""

    def remove(self) -> None:
        """Settings live in the deployment directory, removed with it."""

    def describe(self) -> dict[str, Any]:
        facts = self.state.facts
        return {
            "launcher": facts.get("launcher", str(self.launcher)),
            "appimage": facts.get("appimage"),
            "qfieldcloud_url": facts.get("qfieldcloud_url"),
            "settings_file": facts.get("settings_file"),
        }
