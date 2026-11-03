"""A drop-in unit: the version this module declares is what an edit rewrites.

The example copies this directory somewhere writable before it starts, because a case that edits the
file in the tree would be editing the example itself.
"""

from __future__ import annotations

from composat import Component


class Widget(Component):
    version: int = 1
