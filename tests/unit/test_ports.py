from __future__ import annotations

from qfield_testbed.ports import BLOCK_SIZE, PORT_OFFSETS, PortBlock, find_free_slot, make_prober


def test_offsets_fit_in_a_block_and_are_unique() -> None:
    assert len(set(PORT_OFFSETS.values())) == len(PORT_OFFSETS)
    assert max(PORT_OFFSETS.values()) < BLOCK_SIZE


def test_block_ports() -> None:
    block = PortBlock.for_slot(20000, 3)
    assert block.base == 20300
    assert block.port("app") == 20300
    assert block.port("novnc") == 20320
    assert block.all_ports()["adb"] == 20321


def test_find_free_slot_skips_used_and_busy_blocks() -> None:
    busy = {20100 + PORT_OFFSETS["postgres"]}  # something unrelated listens in slot 1's block
    prober = make_prober(busy, probe=lambda _port: True)
    assert find_free_slot([0], port_base=20000, max_slots=5, prober=prober) == 2


def test_find_free_slot_exhausted() -> None:
    assert find_free_slot([0, 1], port_base=20000, max_slots=2, prober=lambda _p: True) is None
    assert find_free_slot([], port_base=20000, max_slots=3, prober=lambda _p: False) is None
