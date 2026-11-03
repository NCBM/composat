"""A watch loop: a burst of edits becomes one reload, and the scope ends both when it closes.

Neither half is the framework's — deciding *when* to look is deployment policy — but both halves are
ordinary uses of what the framework does expose: a timer registered on the instance's own scope, and
a task registered there so that unloading cancels it.
"""

from __future__ import annotations

import asyncio
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

    async def watch(self, source: list[str]) -> None:
        """Follow ``source`` in the background; unloading the instance cancels the loop."""
        seen = 0

        async def poll() -> None:
            nonlocal seen
            while True:
                while seen < len(source):
                    await self.note(source[seen])
                    seen += 1
                await asyncio.sleep(self.delay / 2)

        _ = self.effect_scope.put(asyncio.create_task(poll()).cancel)
