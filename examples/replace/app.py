"""Swapping one key for a new generation: the readers wait it out, and the dependants are told.

The class stays the same here and only its construction changes — a pool pointed at another host. A
change of *class* is the reload shape (``examples/reload``), where the module is what moves first.

What a reader opens on its provider is registered on the reader's **own** epoch, so it is the
reader's lifetime that releases it. A provider swapped underneath changes *what* the reader talks to,
never *when* the handle it holds is let go — and that is why being told matters: nothing on the
provider's side will end the registration for it.
"""

from __future__ import annotations

from functools import partial
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
from composat.scope import Revoke


class Store(Component):
    dsn: str

    def __init__(self, dsn: str = "memory") -> None:
        super().__init__()
        self.dsn = dsn


class Client(Component):
    """A reader that holds one handle per provider generation, on its own epoch."""

    store: Store = require()

    noticed: list[str]
    attached: list[str]
    released: list[str]
    _open: str | None
    _drop: Revoke | None

    def __init__(self) -> None:
        super().__init__()
        self.noticed = []
        self.attached = []
        self.released = []
        self._open = None
        self._drop = None

    def use(self) -> str:
        """Talk to the provider that answers now, opening a handle only if none is held.

        ``put`` registers the release on this instance's epoch, which is the whole of what makes the
        handle the client's: it is let go when the client lets go of it — on being told its provider
        moved, which is :meth:`provider_changed` — or when the client itself goes down. The
        provider's generation appears nowhere in that bookkeeping, so a swap cannot leak it and cannot
        release it twice.
        """
        if self._open is None:
            store = self.store
            self._open = store.dsn
            self.attached.append(store.dsn)
            self._drop = self.effect_scope.put(partial(self._let_go, store.dsn))
        return self._open

    def _let_go(self, dsn: str) -> None:
        self.released.append(dsn)

    @override
    async def provider_changed(self, requirement: RequirementSpec) -> None:
        """What a dependant does about being told its provider moved: drop the handle it holds.

        Reading the field here would be a miss rather than an answer — the new generation is built and
        not yet serving — so what this does is give up the old handle and leave the next :meth:`use`
        to open one on whatever answers then.
        """
        self.noticed.append(requirement.name)
        await self._drop_handle()

    async def _drop_handle(self) -> None:
        drop, self._drop = self._drop, None
        self._open = None
        if drop is not None:
            await drop()


def key_of(cls: type[Component]) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls))


def build() -> Assembly:
    registry = ComponentRegistry()
    _ = registry.declare_all((Declaration(Store, args=("host-a",)), Declaration(Client)))
    return Assembly(registry, up={})
