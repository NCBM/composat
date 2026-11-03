"""The stream of moves an assembly reports: one record per committed step, delivered as it happens.

A sink is a plain callable, and it runs between moves rather than inside one — which is what makes it
a place to write a line down rather than a place to do work.
"""

from __future__ import annotations

from composat import Component, require


class Store(Component):
    """A leaf, so a batch has two keys to report."""


class Engine(Component):
    store: Store = require()


class Rude:
    """A sink that raises: what it says is reported apart from the batch's own outcome."""

    def __call__(self, event: object) -> None:
        raise RuntimeError("this sink is broken")
