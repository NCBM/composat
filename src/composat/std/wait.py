"""Waiting for a key to serve: what a reader does instead of retrying through a swap.

Built on what the core already exposes — :meth:`Assembly.observe` for the moves and
:meth:`Assembly.instance` for the answer — because the assembly keeps no queue of who is waiting for
what, and a waiter is a reading of those two rather than a table of its own.
"""

from __future__ import annotations

import asyncio

from composat import Assembly, Component, ComponentKey, ComponentState, Event
from composat.errors import ServiceNotUpError


async def serving(assembly: Assembly, key: ComponentKey) -> Component:
    """The instance behind ``key``, once it is serving; the wait is the point, not a retry loop.

    A reader that arrives in the middle of a swap is answered with a generation that is built and not
    yet serving, because ``replace`` publishes what it installed only after the generation it
    replaced is gone. That window is the old generation's drain and teardown, so it can be long, and
    waiting it out is cheaper and more honest than polling until it looks over.

    The subscription comes first and the table second, because the stream does not replay: a move
    that lands between the two is caught by the reading, and one that lands after it by the
    subscription. A move that lands *before* the reading is the case the reading exists for.

    A key that is not in the table is refused rather than waited for. Whether it is about to appear
    is a question the caller can answer better than this can — ``start`` answers it by returning —
    and a wait that never finishes is worse than a miss that names the key. A caller that knows it
    may be waiting gives the call a deadline with ``asyncio.timeout``.

    Nothing is registered on the instance or on any scope: what this holds is one subscription, and
    it goes when the wait does — answered, refused, or cancelled.
    """
    loop = asyncio.get_running_loop()
    moved: asyncio.Future[None] = loop.create_future()

    def notice(event: Event) -> None:
        if event.key == key and not moved.done():
            moved.set_result(None)

    unsubscribe = assembly.observe(notice)
    try:
        while (instance := assembly.instance(key)) is not None:
            if instance.state.state is ComponentState.ENABLED:
                return instance
            await moved
            moved = loop.create_future()
        raise ServiceNotUpError(key)
    finally:
        unsubscribe()
