"""QField's QSettings file (``OPENGIS.ch/QField.conf``, INI format) — shared by the Android and desktop wiring."""

from __future__ import annotations

import configparser
import io

ORGANIZATION = "OPENGIS.ch"
APPLICATION = "QField"
CONF_RELATIVE_PATH = f"{ORGANIZATION}/{APPLICATION}.conf"


class _CaseSensitiveParser(configparser.ConfigParser):
    """QSettings keys are case-sensitive (``url`` vs ``Url``); configparser lowercases them by default."""

    def optionxform(self, optionstr: str) -> str:
        return optionstr


def merge_qfieldcloud_section(existing: str, url: str, username: str) -> str:
    """Return ``existing`` INI text with the ``[QFieldCloud]`` url/username/urls keys set."""
    parser = _CaseSensitiveParser(interpolation=None, delimiters=("=",), strict=False)
    parser.read_string(existing)
    if not parser.has_section("QFieldCloud"):
        parser.add_section("QFieldCloud")
    parser.set("QFieldCloud", "url", url)
    parser.set("QFieldCloud", "username", username)
    parser.set("QFieldCloud", "urls", url)
    out = io.StringIO()
    parser.write(out, space_around_delimiters=False)
    return out.getvalue()


def manual_steps(url: str, username: str) -> str:
    return (
        "In QField: open QFieldCloud from the dashboard, double-tap the QFieldCloud logo to reveal the server\n"
        f"field, enter {url} then log in as {username} (password: `qft deployment show`)."
    )
