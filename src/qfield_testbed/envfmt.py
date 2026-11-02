"""Render a ``QFT_*`` environment for a shell (`eval "$(qft deployment env NAME)"`) or a dotenv file."""

from __future__ import annotations

from collections.abc import Mapping

FORMATS = ("shell", "dotenv", "json")


def shell_quote(value: str) -> str:
    """Single-quote ``value`` for POSIX shells: nothing inside is expanded."""
    return "'" + value.replace("'", "'\\''") + "'"


def dotenv_quote(value: str) -> str:
    """Double-quote ``value`` the way dotenv readers (docker compose, python-dotenv) unescape it."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("\n", "\\n")
    return f'"{escaped}"'


def render(variables: Mapping[str, str], fmt: str) -> str:
    """``variables`` as `export K='v'` lines (``shell``) or `K="v"` lines (``dotenv``)."""
    if fmt == "shell":
        lines = [f"export {key}={shell_quote(value)}" for key, value in variables.items()]
    elif fmt == "dotenv":
        lines = [f"{key}={dotenv_quote(value)}" for key, value in variables.items()]
    else:
        raise ValueError(f"no text rendering for format '{fmt}'")
    return "".join(line + "\n" for line in lines)
