"""The smallest host: declare what exists, start the roots, stop everything on the way out.

Three decisions are the application's — what is declared, which keys are roots, and what a failure
means. Everything else here is the library's.
"""

from __future__ import annotations

from typing import override

from composat import Assembly, Component, ComponentRegistry, Declaration, require


class Store(Component):
    """A resource with a life of its own: it opens on ``load`` and closes on ``unload``."""

    opened: bool = False

    @override
    async def load(self) -> None:
        self.opened = True

    @override
    async def unload(self) -> None:
        self.opened = False


class Engine(Component):
    store: Store = require()


class App(Component):
    engine: Engine = require()


class Broken(Component):
    """A component whose own start-up fails, so a batch can show what one failure costs."""

    @override
    async def load(self) -> None:
        raise RuntimeError("nothing to read")


def build() -> Assembly:
    registry = ComponentRegistry()
    _ = registry.declare_all((Declaration(Store), Declaration(Engine), Declaration(App), Declaration(Broken)))
    return Assembly(registry, up={})
