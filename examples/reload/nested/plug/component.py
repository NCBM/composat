"""What this drop-in is: one component, two levels down from the entry an edit rewrites.

The example copies the directory somewhere writable before it starts, because a case that edits the
file in the tree would be editing the example itself.
"""

from __future__ import annotations

from typing import override

from composat import Component


class Gear(Component):
    version: int = 1

    seen: list[str]

    def __init__(self, seen: list[str]) -> None:
        super().__init__()
        self.seen = seen

    @override
    async def load(self) -> None:
        self.seen.append(f"load {self.version}")

    @override
    async def unload(self) -> None:
        self.seen.append(f"unload {self.version}")
