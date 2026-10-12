"""Seeding a deployment's QGIS profile: settings in ``QGIS3.ini``, an entry in the authentication database.

The work happens in a generated PyQGIS script run headless by the backend (host interpreter or container), so
the profile ends up exactly as QGIS itself would write it, whatever the QGIS version.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from qfield_testbed.backends.qgis_host import PROFILE_NAME
from qfield_testbed.errors import TestbedError

log = logging.getLogger(__name__)

_TEMPLATES = Environment(
    loader=PackageLoader("qfield_testbed", "assets/templates"),
    undefined=StrictUndefined,
    autoescape=False,  # noqa: S701 - renders a Python script, never HTML
    keep_trailing_newline=True,
)


@dataclass
class AuthConfig:
    """A QGIS authentication configuration, looked up by name so re-seeding updates rather than duplicates."""

    name: str
    method: str
    uri: str
    config: dict[str, str]
    id_settings: list[str] = field(default_factory=list)
    """Settings keys that must receive the configuration id (e.g. ``QFieldSync/qfieldCloudAuthcfg``)."""


@dataclass
class ProfileLayout:
    """Where a deployment keeps its QGIS profile."""

    profiles_path: Path

    @property
    def profile(self) -> Path:
        return self.profiles_path / "profiles" / PROFILE_NAME

    @property
    def plugins(self) -> Path:
        return self.profile / "python" / "plugins"

    @property
    def master_password_file(self) -> Path:
        return self.profiles_path / "master-password"

    @property
    def seed_script(self) -> Path:
        return self.profiles_path / "seed_profile.py"

    def ini(self, qgis_version: str) -> Path:
        return self.profile / "QGIS" / f"{settings_app_name(qgis_version)}.ini"

    def ensure(self) -> None:
        self.plugins.mkdir(parents=True, exist_ok=True)
        if not self.master_password_file.exists():
            self.master_password_file.write_text(secrets.token_urlsafe(16) + "\n")
            self.master_password_file.chmod(0o600)

    def launcher_env(self) -> dict[str, str]:
        return {"QGIS_AUTH_PASSWORD_FILE": str(self.master_password_file)}


def settings_app_name(qgis_version: str) -> str:
    """``QGIS3`` or ``QGIS4``: the application name QGIS Desktop uses, hence the name of its settings file."""
    major = qgis_version.split(".", 1)[0]
    return f"QGIS{major}"


def render_seed_script(
    name: str, layout: ProfileLayout, settings: dict[str, Any], auth: AuthConfig | None, *, qgis_version: str
) -> str:
    template = _TEMPLATES.get_template("seed_profile.py.j2")
    auth_payload = (
        {
            "name": auth.name,
            "method": auth.method,
            "uri": auth.uri,
            "config": auth.config,
            "id_settings": auth.id_settings,
        }
        if auth
        else {}
    )
    return template.render(
        name=name,
        profiles_path=str(layout.profiles_path),
        master_password_file=str(layout.master_password_file),
        settings_json=json.dumps(settings),
        auth_json=json.dumps(auth_payload),
        qgis_app_name=settings_app_name(qgis_version),
    )


def parse_seed_report(stdout: str) -> dict[str, Any]:
    """The seed script prints one JSON line last; QGIS may print warnings before it."""
    for raw in reversed(stdout.strip().splitlines()):
        if (line := raw.strip()).startswith("{"):
            return dict(json.loads(line))
    raise TestbedError(f"the profile seed script printed no report:\n{stdout[-500:]}")
