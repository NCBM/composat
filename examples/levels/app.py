"""A batch built but not serving, and the three verbs that move it the rest of the way.

A declaration says how to build a component; the call that starts it says whether it should be
serving. Splitting those two is what lets a deployment build a batch, check it, and put it into
service on its own schedule — and take it back out without unloading it.
"""

from __future__ import annotations

from typing import override

from composat import Assembly, Component, ComponentRegistry, Declaration


class Store(Component):
    """Writes down the hook that ran last, so a case has something an instance remembers."""

    stage: str = "unloaded"

    @override
    async def load(self) -> None:
        self.stage = "loaded"

    @override
    async def enable(self) -> None:
        self.stage = "enabled"

    @override
    async def disable(self) -> None:
        self.stage = "disabled"


class Refuser(Component):
    """A component whose own ``enable`` fails: it stays built, and the batch says so."""

    @override
    async def enable(self) -> None:
        raise RuntimeError("no port")


def build(*components: type[Component]) -> Assembly:
    registry = ComponentRegistry()
    _ = registry.declare_all(Declaration(cls) for cls in components)
    return Assembly(registry)
