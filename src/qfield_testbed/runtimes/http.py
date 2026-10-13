"""Small HTTP helpers: wait for a URL, fetch JSON, retry what a server still warming up drops."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from qfield_testbed.errors import HealthTimeoutError

log = logging.getLogger(__name__)

TRANSIENT_STATUSES = frozenset({502, 503, 504})
"""Gateway and availability errors a server answers while its workers restart."""


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


def is_transient(exc: BaseException) -> bool:
    """A dropped connection, a timeout or a 502/503/504, also when wrapped by a client library (``__cause__``)."""
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, (requests.ConnectionError, requests.Timeout)):
            return True
        response = getattr(current, "response", None)
        if isinstance(response, requests.Response) and response.status_code in TRANSIENT_STATUSES:
            return True
        current = current.__cause__
    return False


def retrying[T](action: Callable[[], T], *, what: str, timeout: float = 60, interval: float = 2) -> T:
    """Run ``action``, retrying transient errors (see :func:`is_transient`) until ``timeout``; others pass through.

    ``action`` must be safe to repeat: a request whose response was lost may have taken effect.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return action()
        except Exception as exc:
            if not is_transient(exc) or time.monotonic() >= deadline:
                raise
            log.info("%s: %s, retrying", what, exc.__class__.__name__)
        time.sleep(interval)


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 10) -> Any:
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout: float = 30) -> Any:
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()
