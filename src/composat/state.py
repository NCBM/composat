"""The four states a component is in, the verbs that move it, and the lock they move under.

The machine is two tables rather than a method on each state: a verb says which states it may leave
and which state it arrives at, and a way from one state to another is a search over those two tables.
That is what lets a caller name a *target* — "bring this one to ``LOADED``" — and have the verbs fall
out of it, instead of writing the sequence out at every call site and keeping the sequence right.

One instance, one control. The state is a fact about an instance at its key, so the lock that
serialises the moves made on it is per instance too. Nothing here calls a hook — driving one is the
manager's — so what this module promises is narrower: a verb is allowed or refused before it runs, a
committed state is the one its hook runs in, and a verb that a raising hook did not perform can be put
back.
"""

from __future__ import annotations

import asyncio
import enum
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import final, override


class ComponentState(enum.Enum):
    """Where one instance is between calls.

    ``UNLOADED`` is nothing built yet, ``LOADED`` is built and not serving, ``DRAINING`` is serving
    what it already accepted, ``ENABLED`` is taking new work. Draining is a state rather than a
    moment: a drain that finished is a ``disable`` away from ``LOADED``, and one that was interrupted
    has to be readable as such, because what it still owes is the work it accepted.

    Not an ``IntEnum``, though the old tree's was: these four are not a scale. ``ENABLED`` is not
    "more" than ``LOADED`` for every question a caller asks, so a comparison that happens to
    typecheck would be a reading nobody meant.
    """

    UNLOADED = enum.auto()
    LOADED = enum.auto()
    DRAINING = enum.auto()
    ENABLED = enum.auto()


class Operation(enum.StrEnum):
    """A verb the machine drives: one hook, and the transition that hook is allowed to make.

    The member *is* the hook's name, so the two lists cannot drift: a verb that names no hook, or a
    hook no verb drives, fails the one check that compares them rather than showing up as a component
    whose hook nothing ever calls.
    """

    load = "load"
    unload = "unload"
    reload = "reload"
    enable = "enable"
    disable = "disable"
    drain = "drain"


LEAVING: Mapping[Operation, frozenset[ComponentState]] = {
    Operation.load: frozenset({ComponentState.UNLOADED}),
    Operation.unload: frozenset({ComponentState.LOADED}),
    Operation.reload: frozenset({ComponentState.LOADED, ComponentState.ENABLED}),
    Operation.enable: frozenset({ComponentState.LOADED}),
    Operation.disable: frozenset({ComponentState.DRAINING}),
    Operation.drain: frozenset({ComponentState.ENABLED}),
}
"""The states each verb may be driven from: where it is a move at all.

One row rather than one precondition per call site, so ``load`` cannot be driven on a loaded
component, and a batch that asks for a level is refused where it asked rather than halfway through.
"""

ARRIVING: Mapping[Operation, ComponentState | None] = {
    Operation.load: ComponentState.LOADED,
    Operation.unload: ComponentState.UNLOADED,
    Operation.reload: None,
    Operation.enable: ComponentState.ENABLED,
    Operation.disable: ComponentState.LOADED,
    Operation.drain: ComponentState.DRAINING,
}
"""The state each verb commits, before its hook runs.

``reload`` is the one ``None``: it rebuilds what an instance holds without taking it out of service,
so its arrival is the state it started in. Nothing else about the machine distinguishes the two
verbs, which is why the pair of tables carries the asymmetry instead of a special case in a method.
"""


class ComponentStateError(RuntimeError):
    """A verb was driven from a state it does not leave.

    Each verb leaves the states ``LEAVING`` names and commits the arrival ``ARRIVING`` names, so this
    is a reading of the two tables rather than a rule of its own: a component is never asked to load
    while it is loaded, and a batch asking for a level is refused where it asked instead of halfway
    through. The refusal is raised before the hook runs, which is what makes it safe to report and go
    on to the next key.
    """

    operation: Operation
    leaving: frozenset[ComponentState]
    actual: ComponentState

    def __init__(self, operation: Operation, leaving: frozenset[ComponentState], actual: ComponentState) -> None:
        allowed = ", ".join(sorted(state.name for state in leaving))
        super().__init__(f"cannot {operation} from {actual.name}: that verb leaves {allowed}")
        self.operation = operation
        self.leaving = leaving
        self.actual = actual


@dataclass(frozen=True, slots=True)
class Migration:
    """A move between two states, as the verbs that walk it.

    The path is worked out rather than written down, and breadth first, so it is the fewest verbs that
    get there and the same pair always answers the same tuple. That is the whole of what a target buys
    a caller: ``QUIESCED`` is ``migrate(component, to=LOADED)`` and a shutdown is the same call with
    another target, and neither one spells out the order it happens in.

    Every pair of states is reachable today — the machine is strongly connected, ``disabling`` being
    how a drained component is resumed — so the refusal at the end of the search is about the tables
    staying that way, not about a case anyone can reach.
    """

    origin: ComponentState
    target: ComponentState

    def steps(self) -> tuple[Operation, ...]:
        """The fewest verbs that take ``origin`` to ``target``, or none when they are the same."""
        if self.origin is self.target:
            return ()
        routes: dict[ComponentState, tuple[Operation, ...]] = {self.origin: ()}
        searching: list[ComponentState] = [self.origin]
        while searching:
            current = searching.pop(0)
            for operation in Operation:
                arrival = ARRIVING[operation]
                if arrival is None or current not in LEAVING[operation] or arrival in routes:
                    continue
                routes[arrival] = (*routes[current], operation)
                if arrival is self.target:
                    return routes[arrival]
                searching.append(arrival)
        raise ValueError(f"{self.origin.name} cannot reach {self.target.name}")


type Undo = Callable[[], None]
"""What a verb answers: the call that puts the state the verb committed back where it was."""

type Advance = Callable[[Operation], Undo]
"""The one writer a move is given: commit a verb, and be answered with its undo."""


@final
class ComponentStateControl:
    """One instance's state, the lock its moves run under, and the migration in progress.

    ``pending`` is what makes "what is happening to this component" answerable while it happens. The
    state alone reads ``LOADED`` halfway through a shutdown, and a component resting in ``DRAINING``
    is not the same as one on its way down; the name of the move says which.

    The state is read-only from outside: :meth:`transaction` hands out the only writer, and it is in
    force only while the lock is. A state assigned from anywhere else would be a transition nothing
    drove and nothing can order.

    Final, like the other objects that hold state: there is nothing here an application has a reason
    to replace, and a subclass would be a second answer to where an instance is.
    """

    __slots__ = ("_pending", "_state", "lock")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self._pending = None
        self._state = ComponentState.UNLOADED

    @override
    def __repr__(self) -> str:
        driving = "" if self._pending is None else f" in {self._pending}"
        return f"<{type(self).__name__} {self._state.name}{driving}>"

    @property
    def state(self) -> ComponentState:
        """Where the instance is now — and, during a hook, the state that hook is producing."""
        return self._state

    @property
    def pending(self) -> str | None:
        """The move being run, or ``None`` when nothing is driving this instance."""
        return self._pending

    def _commit(self, operation: Operation) -> Undo:
        """Commit one verb, and answer the call that takes it back.

        The arrival is committed *before* the hook runs, so a hook reads the state it is producing:
        a ``load`` runs inside ``LOADED``, and a component asking "am I up" from its own hook is
        answered about the world that hook is making, not the one it is leaving. A refused verb
        raises :class:`ComponentStateError` and commits nothing.
        """
        if self._state not in LEAVING[operation]:
            raise ComponentStateError(operation, LEAVING[operation], self._state)
        origin = self._state
        arrival = ARRIVING[operation]
        if arrival is not None:
            self._state = arrival

        def undo() -> None:
            self._state = origin

        return undo

    @asynccontextmanager
    async def transaction(self, move: str) -> AsyncGenerator[Advance]:
        """Hold the lock for one move, and say what is being run while it is.

        One lock for the whole move rather than one per verb, because a composite is one operation on
        the instance: a caller that saw ``LOADED`` in the middle of a shutdown would be reading a true
        state to the wrong question, which is what ``pending`` is for. Nothing here puts a state back
        — what a raising hook needs undone is its own verb, and :meth:`_commit` is what answers that.
        """
        async with self.lock:
            self._pending = move
            try:
                yield self._commit
            finally:
                self._pending = None
