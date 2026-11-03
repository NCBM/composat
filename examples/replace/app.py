"""Swapping one key for a new generation: the readers wait it out, and the dependants are told.

The class stays the same here and only its construction changes — a pool pointed at another host. A
change of *class* is the reload shape (``examples/reload``), where the module is what moves first.
"""

from __future__ import annotations

from typing import override

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Declaration,
    RequirementSpec,
    require,
)


class Store(Component):
    dsn: str

    def __init__(self, dsn: str = "memory") -> None:
        super().__init__()
        self.dsn = dsn


class Client(Component):
    store: Store = require()

    def __init__(self) -> None:
        super().__init__()
        self.noticed: list[str] = []

    @override
    async def provider_changed(self, requirement: RequirementSpec) -> None:
        """What a dependant does about being told its provider moved: here, write it down."""
        self.noticed.append(requirement.name)


def key_of(cls: type[Component]) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls))


def build() -> Assembly:
    registry = ComponentRegistry()
    _ = registry.declare_all((Declaration(Store, args=("host-a",)), Declaration(Client)))
    return Assembly(registry, up={})
