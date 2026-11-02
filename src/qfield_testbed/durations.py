"""Durations on the command line: `90s`, `10m`, `12h`, `30d`."""

from __future__ import annotations

import re
from datetime import timedelta

from qfield_testbed.errors import UsageError

_DURATION = re.compile(r"^(\d+)([dhms])$")
_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}


def parse_duration(value: str) -> timedelta:
    match = _DURATION.match(value)
    if not match:
        raise UsageError(f"invalid duration '{value}'; use e.g. 30d, 12h, 90m, 45s")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
