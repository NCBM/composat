"""A request answered inside a call: whose work it is, where its effects go, what it resolves through.

Two things are the application's here, and neither is a subclass: the object one call owns — made by
whoever opens the call — and the plain function underneath that reaches the assembly without being
handed anything.
"""

from __future__ import annotations

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Declaration,
    require,
)
from composat.context import current


class Store(Component):
    """The component's own instance: one per key, holding what outlives a call."""

    written: int = 0

    def __init__(self) -> None:
        super().__init__()
        self.written = 0


class Session:
    """What one call opens and closes: owned by the caller that opened the call, not by a component."""

    rows: list[str]
    closed: bool

    def __init__(self) -> None:
        self.rows = []
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Page(Component):
    store: Store = require()

    async def render(self, session: Session, text: str) -> str:
        session.rows.append(text)
        self.store.written += 1
        return f"{len(session.rows)}:{text}"


def writer() -> Store:
    """Plain code underneath the component: the call is what says which instance it means."""
    return current().resolve(Store)


def build() -> tuple[Assembly, ComponentKey]:
    registry = ComponentRegistry()
    _ = registry.declare_all((Declaration(Store), Declaration(Page)))
    page_key = ComponentKey(ComponentIdentity.of(Page))
    return Assembly(registry, up={}), page_key
