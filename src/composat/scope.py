import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial

type Disposable = Callable[[], object | Awaitable[object]]
type Revoke = Callable[[], Awaitable[None]]


def name_of(effect: Disposable) -> str:
    """How one effect is named in a view: what it is called, never what it is.

    A function or a bound method answers with its own name; a ``partial`` takes the name of the
    callable it wraps, since that is where the work happens; anything else callable answers with its
    type's name, which for a lambda is ``<lambda>``. Names are reported rather than the effects
    themselves because an effect is a promise that something gets released exactly once — handing it
    out would let a reader release it a second time.
    """
    target: object = effect
    while isinstance(target, partial):
        target = target.func
    name = getattr(target, "__name__", None)
    if isinstance(name, str) and name:
        return name
    return type(target).__qualname__


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

        "Still holds" is the whole of what it answers: a segment released by a :class:`Revoke` is
        gone from the answer, an effect a cancellation left behind is still in it, and a scope that
        has finished recycling answers with nothing. That makes the mid-disposal reading useful in
        the one case it matters — what a failure left undone.

        It reports names rather than the effects themselves on purpose (:func:`name_of`): an effect
        is a promise that something is released exactly once, so a view that handed one out would be
        handing out a second chance to release it.
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
            raise ExceptionGroup("Error occurred when recycling effects: ", gathered)

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

    async def dispose(self) -> None:
        """Recycle every effect, last first; from the first call on the scope is closed to ``put()``.

        Sealing belongs to *asking* for disposal rather than to finishing it. A cancellation
        releases the disposal so that a later call can finish it (the effects a cancellation left
        behind are still recycled then), and the scope stays closed to registration all the same:
        ``put()`` raises from the moment this is called, whether the disposal goes through or is
        cancelled and left for a retry. That is what an epoch needs — a scope on its way out stops
        collecting effects, so a retry has the whole of the leftover work in view.
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
        return self._dispose_task is not None and self._dispose_task.done()
