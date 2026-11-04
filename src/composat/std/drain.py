"""Draining what an instance started: wait for it, then cut it off, and wait for the cut.

Built on what the core already exposes — the instance's epoch scope, which is what cancels work a
drain never reached — because a deadline is policy and the framework keeps a scope rather than a
registry of tasks. What this adds is the one thing a scope cannot say: *finish what is nearly done*
before the epoch is released, and say how much was given up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import final

from composat import EffectScope


@final
class InFlight:
    """The work one instance started and has not finished.

    Started work is registered here rather than with a bare ``create_task`` because two things have to
    be able to happen to it: this instance waits for it, and its epoch cancels it when nobody does.

    ``scope`` is the instance's epoch scope, taken when the tracker is made. A scope that has been
    closed takes no more effects, so a tracker is not carried across an ``unload``: the next epoch
    gets a new one.

    A failure is not the drain's to report. A task that raises belongs to whoever started the work,
    and :meth:`drain` leaves it alone: it is collected by the loop, which is where a background
    failure has always gone in this tree (:meth:`~composat.EffectScope.after` says the same).
    """

    __slots__ = ("_scope", "_started")

    def __init__(self, scope: EffectScope, /) -> None:
        self._scope = scope
        self._started: set[asyncio.Task[object]] = set()

    def __len__(self) -> int:
        """How much is still running: what a drain would be waiting for."""
        return len(self._started)

    def add(self, work: Coroutine[object, object, object], /) -> None:
        """Start ``work`` as this instance's, and let the epoch cancel it if nobody drains it first.

        The handle is not handed back on purpose: the caller this belongs to is the instance, and a
        caller holding a task is a caller who can await it, cancel it, or forget it a second way.
        """
        task = asyncio.create_task(work)
        self._started.add(task)
        _ = task.add_done_callback(self._started.discard)
        _ = self._scope.put(task.cancel)

    async def drain(self, after: float, /) -> int:
        """Let what is running finish, for up to ``after`` seconds; cut off the rest and wait for it.

        Answers how many were cut off, which is the number a deployment reports: the work that was
        nearly done is done, and the work that was not is gone now rather than at some point after the
        instance stopped. Waiting for the cancellation to land is the half of this that a bare
        ``cancel()`` leaves out — an instance that stops answering while its tasks still run is the
        state this exists to make impossible.

        A drain that is itself cut off leaves the rest to the epoch, which cancels them; that is the
        floor, and it is why the tasks are registered there as well.
        """
        running = set(self._started)
        if not running:
            return 0
        _finished, left = await asyncio.wait(running, timeout=after)
        if not left:
            return 0
        for task in left:
            _ = task.cancel()
        _ = await asyncio.gather(*left, return_exceptions=True)
        return len(left)
