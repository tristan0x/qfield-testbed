"""The `qfield-android` component: QField in an Android emulator (budtmo/docker-android) on the deployment network.

APKs come from GitHub releases (``qfield-vX.Y.Z-android-x64.apk``), from the ``android.yml`` CI artifacts of a
ref, or from a local ``scripts/build.sh`` build. The app is installed after the emulator boots and its
QFieldCloud URL is pre-filled when the emulator allows root access.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, ClassVar

from qfield_testbed.backends.android_emulator import QFIELDCLOUD_URL_FROM_ANDROID, AndroidEmulatorBackend
from qfield_testbed.components import register
from qfield_testbed.components.base import Component
from qfield_testbed.errors import MaterializationUnavailableError, TestbedError
from qfield_testbed.provision import android_settings
from qfield_testbed.revisions import Revision
from qfield_testbed.revisions.git import worktree_head
from qfield_testbed.revisions.github import artifact_download, owner_repo, release_download, workflow_run_id
from qfield_testbed.runtimes import docker
from qfield_testbed.spec import ArtifactSource, BuildSource, QFieldAndroidSpec, ReleaseSource

log = logging.getLogger(__name__)

APK_NAME = "qfield-android-x64.apk"
ARTIFACT_PATTERN = "qfield-*-android-x64"
RELEASE_PATTERN = "qfield-*-android-x64.apk"
BUILD_TRIPLET = "x64-android"
MIN_FREE_GIB_FOR_BUILD = 15
KVM_DEVICE = Path("/dev/kvm")


@register
class QFieldAndroid(Component):
    name: ClassVar[str] = "qfield-android"
    spec: QFieldAndroidSpec

    @property
    def repo(self) -> str:
        return owner_repo(self.spec.repo or self.ctx.config.repositories.qfield)

    def backend(self) -> AndroidEmulatorBackend:
        return AndroidEmulatorBackend(
            runner=self.ctx.runner,
            deployment=self.ctx.name,
            directory=self.directory,
            network=self.ctx.network,
            ports=self.ctx.ports,
        )

    def check_host(self) -> list[str]:
        problems = []
        if not docker.daemon_available(self.ctx.runner):
            problems.append("docker daemon not reachable")
        if not KVM_DEVICE.exists():
            problems.append(f"{KVM_DEVICE} missing: the emulator needs hardware virtualization")
        if isinstance(self.spec.version, ReleaseSource | ArtifactSource) and self.ctx.runner.which("gh") is None:
            problems.append("gh missing (needed to download releases and CI artifacts)")
        if isinstance(self.spec.version, BuildSource):
            if not (self.spec.version.path / "scripts" / "build.sh").is_file():
                problems.append(f"{self.spec.version.path} is not a QField checkout (no scripts/build.sh)")
            free_gib = (
                shutil.disk_usage(self.ctx.paths.cache if self.ctx.paths.cache.exists() else Path.home()).free // 2**30
            )
            if free_gib < MIN_FREE_GIB_FOR_BUILD:
                problems.append(
                    f"building QField needs at least {MIN_FREE_GIB_FOR_BUILD} GiB free, {free_gib} available"
                )
        return problems

    # --- lifecycle --------------------------------------------------------------------------------------

    def commit_sha(self, ref: str) -> str | None:
        """Resolve a ref through the GitHub API (no clone of the large QField repository needed)."""
        result = self.ctx.runner.run(["gh", "api", f"repos/{self.repo}/commits/{ref}", "--jq", ".sha"], check=False)
        sha = result.stdout.strip()
        return sha if result.returncode == 0 and len(sha) == 40 else None  # noqa: PLR2004

    def resolve(self) -> Revision:
        source = self.spec.version
        if isinstance(source, ReleaseSource):
            return Revision(source=source, label=source.tag, sha=self.commit_sha(source.tag))
        if isinstance(source, ArtifactSource):
            sha = self.commit_sha(source.ref)
            if sha is None:
                raise TestbedError(f"unknown ref '{source.ref}' in {self.repo}")
            label = source.ref if sha.startswith(source.ref) else f"{source.ref}@{sha[:12]}"
            return Revision(source=source, label=label, sha=sha)
        if isinstance(source, BuildSource):
            sha, dirty = worktree_head(self.ctx.runner, source.path)
            return Revision(source=source, label=f"build@{sha[:12]}", sha=sha, dirty=dirty)
        raise TestbedError(f"unsupported version source for qfield-android: {source}")

    def materialize(self, revision: Revision) -> Path:
        """The x86_64 APK for the revision, cached by revision key."""
        target_dir = self.ctx.paths.artifacts / "qfield" / revision.key
        apk = target_dir / APK_NAME
        if apk.exists() and not revision.dirty:
            self.ctx.cache.touch(f"qfield/apk/{revision.key}", target_dir, user=self.ctx.name)
            return apk
        target_dir.mkdir(parents=True, exist_ok=True)
        source = revision.source
        if isinstance(source, ReleaseSource):
            downloaded = release_download(self.ctx.runner, self.repo, source.tag, RELEASE_PATTERN, target_dir)
            downloaded.replace(apk)
        elif isinstance(source, ArtifactSource):
            assert revision.sha is not None  # noqa: S101
            run_id = workflow_run_id(self.ctx.runner, self.repo, "android.yml", revision.sha)
            if run_id is None:
                raise MaterializationUnavailableError(
                    f"no successful android.yml run for {revision.label} in {self.repo}; push the ref to a repo "
                    "whose CI builds it, or use a release"
                )
            artifact_download(self.ctx.runner, self.repo, run_id, ARTIFACT_PATTERN, target_dir / "artifact")
            apks = sorted((target_dir / "artifact").rglob("*.apk"))
            if not apks:
                raise MaterializationUnavailableError(f"artifact of run {run_id} contains no APK")
            shutil.copy(apks[0], apk)
        elif isinstance(source, BuildSource):
            self.build(source.path, apk)
        else:
            raise TestbedError(f"unsupported version source for qfield-android: {source}")
        self.ctx.cache.touch(f"qfield/apk/{revision.key}", target_dir, user=self.ctx.name)
        return apk

    def build(self, checkout: Path, apk: Path) -> None:
        """QField's own Docker build (``scripts/build.sh``) for the x86_64 triplet; slow the first time."""
        cache_dir = self.ctx.paths.cache / "qfield-build"
        cache_dir.mkdir(parents=True, exist_ok=True)
        log.info("building QField for %s in %s (this takes a long time the first time)", BUILD_TRIPLET, checkout)
        self.ctx.runner.run(
            ["./scripts/build.sh"],
            cwd=checkout,
            env={"triplet": BUILD_TRIPLET, "CACHE_DIR": str(cache_dir)},
            capture=False,
            timeout=6 * 3600,
        )
        apks = sorted((checkout / "build-docker" / "out").rglob("*.apk"), key=lambda p: p.stat().st_mtime)
        if not apks:
            raise MaterializationUnavailableError(
                f"scripts/build.sh produced no APK under {checkout / 'build-docker/out'}"
            )
        shutil.copy(apks[-1], apk)

    def render(self, revision: Revision, materialized: Path) -> None:
        backend = self.backend()
        backend.render(image=self.spec.image, device=self.spec.device)
        launcher = self.ctx.directory / "bin" / "android"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(
            "#!/usr/bin/env bash\n# generated by qfield-testbed: open the emulator's noVNC page\n"
            f'URL={backend.novnc_url!r}\necho "$URL"\ncommand -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1 || true\n'
        )
        launcher.chmod(0o755)
        self.state.facts.update(
            {
                "apk": str(materialized),
                "apk_sha256": _sha256(materialized),
                "revision_label": revision.label,
                "image": self.spec.image,
                "device": self.spec.device,
                "container": backend.container,
                "launcher": str(launcher),
            }
        )

    def start(self) -> None:
        backend = self.backend()
        backend.start()
        adb = backend.adb()
        log.info("waiting for the Android emulator to boot")
        adb.wait_for_boot()
        apk = Path(self.state.facts["apk"])
        if self.state.facts.get("installed_sha256") != self.state.facts.get("apk_sha256") or not adb.installed_version(
            android_settings.PACKAGE
        ):
            log.info("installing %s", apk.name)
            adb.copy_in(apk, "/tmp/qfield.apk")  # noqa: S108 - path inside the emulator container
            adb.install("/tmp/qfield.apk")  # noqa: S108
            self.state.facts["installed_sha256"] = self.state.facts.get("apk_sha256")
        self.state.facts["installed_version"] = adb.installed_version(android_settings.PACKAGE)

    def health(self, timeout: float = 600) -> None:
        self.backend().adb().wait_for_boot(timeout)

    def wire(self) -> None:
        credentials = self.ctx.state.credentials
        if credentials is None:
            return
        url = QFIELDCLOUD_URL_FROM_ANDROID
        self.state.facts["qfieldcloud_url"] = url
        if not self.spec.seed_settings:
            return
        try:
            conf = android_settings.seed_qfield_settings(self.backend().adb(), url, credentials.username)
        except TestbedError as exc:
            log.warning("could not seed QField settings: %s", exc)
            conf = None
        self.state.facts["settings_file"] = conf
        if conf is None:
            log.warning("%s", android_settings.manual_steps(url, credentials.username))

    def stop(self) -> None:
        self.backend().stop()

    def reset(self) -> None:
        self.backend().down()
        self.state.facts.pop("installed_sha256", None)

    def remove(self) -> None:
        self.backend().down()

    def logs(self, *, follow: bool = False) -> None:
        self.backend().compose().logs(follow=follow)

    def describe(self) -> dict[str, Any]:
        backend = self.backend()
        facts = self.state.facts
        return {
            "novnc_url": backend.novnc_url,
            "adb": backend.adb().command(),
            "adb_connect": backend.adb_connect,
            "qfieldcloud_url": facts.get("qfieldcloud_url", QFIELDCLOUD_URL_FROM_ANDROID),
            "apk": facts.get("apk"),
            "installed_version": facts.get("installed_version"),
            "settings_file": facts.get("settings_file"),
            "device": self.spec.device,
            "image": self.spec.image,
            "launcher": facts.get("launcher"),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
