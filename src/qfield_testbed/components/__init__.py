"""Components: one module per product, each implementing the lifecycle protocol of :mod:`.base`."""

from __future__ import annotations

from qfield_testbed.components.base import Component, ComponentContext

REGISTRY: dict[str, type[Component]] = {}
"""Component name → implementation. Populated by the component modules below as they land."""


def register(cls: type[Component]) -> type[Component]:
    REGISTRY[cls.name] = cls
    return cls


__all__ = ["REGISTRY", "Component", "ComponentContext", "register"]
