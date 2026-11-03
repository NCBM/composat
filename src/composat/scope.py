"""Effects and the scope that holds them: what a lifetime registers, and how it is released.

An effect is a callable that releases something — a socket, a directory, a lease — registered with a
scope that promises to call it exactly once, last-registered first. Closing the scope releases
everything it still holds; a :class:`Revoke` releases everything registered after a checkpoint and
leaves what came before.

A *timed* effect is the same promise the other way round: :meth:`EffectScope.after` registers what
should happen later, so the scope can take it back — a session that expires in ten minutes is a
registration that a disconnect cancels, not a timer a caller has to remember.

The scope also owns an epoch's ends: :meth:`EffectScope.dispose` closes it for good and
:meth:`EffectScope.reopen` answers the scope a new epoch starts with.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Protocol

type Disposable = Callable[[], object | Awaitable[object]]
type Revoke = Callable[[], Awaitable[None]]


class Reclaimable(Protocol):
    """Somewhere an effect can be registered: the seam a resolver receives.

    A resolver that hands back something with a lifetime shorter than the assembly's says who
    releases it by registering the release here, and ``put`` is the whole of what it may ask for.
    The seam stays this narrow so that it holds whatever the scope is eventually swapped for.
    """

    def put(self, effect: Disposable, /) -> Revoke:
        """Register ``effect``, and answer the call that releases everything from here on."""
        ...


def name_of(effect: Disposable) -> str:
    """How one effect is named in a view: what it is called, never what it is.

    A function or a bound method answers with its own name; a ``partial`` takes the name of the
    callable it wraps, since that is where the work happens; anything else callable answers with its
    type's name, which for a lambda is ``<lambda>``. Views report names because an effect is a
    promise that something gets released exactly once, and a reader holding the callable would hold a
    second chance to release it.
    """
    target: object = effect
    while isinstance(target, partial):
        target = target.func
    name = getattr(target, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return type(target).__qualname__


def _fire(loop: asyncio.AbstractEventLoop, effect: Disposable) -> None:
    """Run one timed effect, and report whatever it raises where a background failure belongs."""
    try:
        result = effect()
    except Exception as error:  # noqa: BLE001 - nobody is waiting for this effect's answer
        _report(loop, effect, error)
        return
    if inspect.isawaitable(result):
        _ = loop.create_task(_settle(loop, effect, result))


async def _settle(loop: asyncio.AbstractEventLoop, effect: Disposable, awaiting: Awaitable[object]) -> None:
    """Wait for an awaitable timed effect, and report its failure the same way."""
    try:
        await awaiting
    except Exception as error:  # noqa: BLE001 - same
        _report(loop, effect, error)


def _report(loop: asyncio.AbstractEventLoop, effect: Disposable, error: BaseException) -> None:
    """Hand a timed effect's failure to the loop, which is where a call nobody awaits reports.

    The alternative is a task nobody retrieves, whose exception surfaces whenever it is collected — at
    a time and a place that have nothing to do with the effect. Naming it is what makes the report say
    which of a deployment's timers failed.
    """
    loop.call_exception_handler({"message": f"a timed effect ({name_of(effect)}) failed", "exception": error})


@dataclass(eq=False, slots=True)
class EffectScope:
    _stack: list[Disposable] = field(default_factory=list)
    _dispose_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    #: Whether this scope is still taking effects. It only ever goes one way, and it goes that way
    #: the moment disposal is *asked for* — see :meth:`dispose` for why the ask is the boundary.
    _closed: bool = field(default=False, init=False, repr=False)

    def __len__(self) -> int:
        return len(self._stack)

    def effects(self) -> tuple[str, ...]:
        """The name of every effect this scope still holds, oldest first.

        "Still holds" is the whole of the answer: a segment released by a :class:`Revoke` is gone
        from it, an effect a cancellation left behind is still in it, and a scope that finished
        recycling answers with nothing. That makes the mid-disposal reading useful in the one case it
        matters — what a failure left undone.
        """
        return tuple(name_of(effect) for effect in self._stack)

    def _test_open(self) -> None:
        if self._closed:
            raise RuntimeError("the scope is closed, so it takes no more effects")

    async def _recycle_until(self, checkpoint: int) -> None:
        gathered: list[Exception] = []
        while len(self._stack) > checkpoint:
            h = self._stack.pop()
            try:
                res = h()
                if inspect.isawaitable(res):
                    await res
            except Exception as e:  # noqa: BLE001
                gathered.append(e)
        if gathered:
            raise ExceptionGroup("Error occurred when recycling effects", gathered)

    def put(self, effect: Disposable) -> Revoke:
        self._test_open()
        checkpoint = len(self._stack)
        self._stack.append(effect)
        _revoked = False

        async def revoke() -> None:
            nonlocal _revoked
            if _revoked:
                return
            _revoked = True
            await self._recycle_until(checkpoint)

        return revoke

    def after(self, delay: float, effect: Disposable) -> Revoke:
        """Register ``effect`` to run once, ``delay`` seconds from now, unless this scope goes first.

        A timed effect is an ordinary registration: what goes on the stack is the timer's cancellation,
        so the scope's own bookkeeping stays the whole story — ``revoke`` pre-empts it, :meth:`dispose`
        cancels it along with everything else, and a scope that is already closed refuses it. That is
        what a session expiry or a timeout needs, and what a bare ``loop.call_later`` leaves the caller
        to remember.

        It fires on the loop running now, which is the loop the scope's effects belong to, and nobody
        is waiting for it when it does: a failure has no caller to raise to, so it goes to the loop's
        exception handler, named by :func:`name_of`. An awaitable effect is awaited on a task of its
        own, which makes an ``async def`` expiry as ordinary as a plain function.

        The registration stays in :meth:`effects` after it fires — the scope still holds it, and
        releasing it then is a no-op — so the view says what *may* still happen rather than what has.
        """
        loop = asyncio.get_running_loop()
        timer: asyncio.TimerHandle | None = None

        def preempt() -> None:
            """Call the timer off: this effect is not going to run."""
            if timer is not None:
                timer.cancel()

        preempt.__name__ = name_of(effect)
        # Registered first, so a closed scope refuses the effect before anything is scheduled.
        revoke = self.put(preempt)
        timer = loop.call_later(delay, partial(_fire, loop, effect))
        return revoke

    async def dispose(self) -> None:
        """Recycle every effect, last first; from the first call on the scope is closed to ``put()``.

        Sealing belongs to *asking* for disposal rather than to finishing it, which is what gives a
        cancelled disposal a retry: the effects it left behind are still recycled by the next call,
        and the scope has been closed to registration since the first one. An epoch on its way out
        stops collecting effects, so a retry has the whole of the leftover work in view.
        """
        self._closed = True
        if self._dispose_task is not None:
            await self._dispose_task
            return
        self._dispose_task = asyncio.create_task(self._recycle_until(0))
        try:
            await self._dispose_task
        except asyncio.CancelledError:
            self._dispose_task = None
            raise

    @property
    def dead(self) -> bool:
        """Whether this scope has nothing left to give: its disposal finished."""
        return self._dispose_task is not None and self._dispose_task.done()

    async def reopen(self) -> EffectScope:
        """Close this scope, and answer the fresh one the next epoch starts with.

        A new epoch starts a new scope, so anything still holding the old one keeps reading the truth
        about it: closed, and drained. A cleared scope would let that holder collect into another
        epoch's bookkeeping and release an effect twice.

        This is the whole of what reloading does to an instance's effects.
        """
        await self.dispose()
        return EffectScope()
