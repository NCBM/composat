"""A watch loop: a burst of edits becomes one reload, and closing the instance ends both.

Neither half is the framework's — what counts as an edit, and how long a burst is — but both halves are
ordinary uses of what the framework does expose: a timer registered on the instance's own scope, and a
task registered there so that unloading cancels it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import override

from composat import Component
from composat.scope import Revoke


class Watcher(Component):
    """Edits arrive as they are noticed; what comes out is one reload per burst."""

    delay: float = 0.01

    reloads: list[str]
    _pending: Revoke | None

    def __init__(self) -> None:
        super().__init__()
        self.reloads = []
        self._pending = None

    @override
    async def load(self) -> None:
        self._pending = None

    @override
    async def unload(self) -> None:
        self._pending = None

    async def note(self, name: str) -> None:
        """Say an edit landed: the reload happens once the burst stops."""
        if self._pending is not None:
            await self._pending()
        self._pending = self.effect_scope.after(self.delay, lambda: self.reloads.append(name))

    async def watch(self, source: AsyncIterator[str]) -> None:
        """Follow ``source`` in the background; unloading the instance cancels the loop."""

        async def follow() -> None:
            async for name in source:
                await self.note(name)

        _ = self.effect_scope.put(asyncio.create_task(follow()).cancel)
