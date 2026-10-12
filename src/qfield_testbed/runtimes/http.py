"""Small HTTP helpers: wait for a URL, fetch JSON."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from qfield_testbed.errors import HealthTimeoutError

log = logging.getLogger(__name__)


def wait_for_http(url: str, *, timeout: float, interval: float = 2.0, expect: int = 200) -> None:
    """Poll ``url`` until it answers with ``expect`` or the timeout elapses."""
    deadline = time.monotonic() + timeout
    last = "no response yet"
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == expect:
                return
            last = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            last = exc.__class__.__name__
        time.sleep(interval)
    raise HealthTimeoutError(f"{url} not healthy after {timeout:.0f}s ({last})")


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 10) -> Any:
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()
