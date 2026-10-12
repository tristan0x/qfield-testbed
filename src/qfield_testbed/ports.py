"""Port blocks: each slot owns 100 contiguous host ports, one fixed offset per service."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass

BLOCK_SIZE = 100

PORT_OFFSETS: dict[str, int] = {
    "app": 0,  # QFieldCloud Django dev server (plain HTTP, the client endpoint)
    "https": 1,  # nginx (only with tls: true)
    "http": 2,  # nginx redirect (only with tls: true)
    "postgres": 3,
    "s3_api": 4,
    "s3_console": 5,
    "smtp_web": 6,
    "smtp": 7,
    "imap": 8,
    "webdav": 9,
    "debugpy_app": 10,
    "debugpy_worker": 11,
    "novnc": 20,  # Android emulator web UI
    "adb": 21,
    "qfield_desktop": 30,
}

PortProber = Callable[[int], bool]
"""Returns True when the host port is free."""


@dataclass(frozen=True)
class PortBlock:
    base: int

    def port(self, name: str) -> int:
        return self.base + PORT_OFFSETS[name]

    def all_ports(self) -> dict[str, int]:
        return {name: self.base + offset for name, offset in PORT_OFFSETS.items()}

    @classmethod
    def for_slot(cls, port_base: int, slot: int) -> PortBlock:
        return cls(base=port_base + BLOCK_SIZE * slot)


def is_port_free(port: int) -> bool:
    """Try to bind the port on all interfaces (what Docker does when publishing)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))  # noqa: S104 - probing, not serving
        except OSError:
            return False
        return True


def make_prober(busy_ports: Iterable[int] = (), *, probe: PortProber = is_port_free) -> PortProber:
    """Combine a known-busy list (e.g. ports published by other containers) with a live bind probe."""
    busy = set(busy_ports)
    return lambda port: port not in busy and probe(port)


def find_free_slot(used_slots: Iterable[int], *, port_base: int, max_slots: int, prober: PortProber) -> int | None:
    """Lowest slot not in use whose whole port block is free; None when every slot is exhausted."""
    used = set(used_slots)
    for slot in range(max_slots):
        if slot in used:
            continue
        block = PortBlock.for_slot(port_base, slot)
        if all(prober(port) for port in block.all_ports().values()):
            return slot
    return None
