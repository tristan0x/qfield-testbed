"""Components: one module per product, each implementing the lifecycle protocol of :mod:`.base`."""

from __future__ import annotations

from qfield_testbed.components.base import Component, ComponentContext

REGISTRY: dict[str, type[Component]] = {}
"""Component name → implementation. Populated by the component modules below as they land."""


def register(cls: type[Component]) -> type[Component]:
    REGISTRY[cls.name] = cls
    return cls


__all__ = ["REGISTRY", "Component", "ComponentContext", "register"]

# importing the component modules registers them (each module decorates its class with @register)
from qfield_testbed.components import qfieldcloud as _qfieldcloud  # noqa: E402, F401
from qfield_testbed.components import qfieldsync as _qfieldsync  # noqa: E402, F401
from qfield_testbed.components import qgis as _qgis  # noqa: E402, F401
