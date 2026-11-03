"""Driving one component: the lock a move runs under, the guard against reentry, and the hooks.

Components declare what they read; the assembly decides what comes up; a component never drives a
lifecycle — its own or anyone else's. That last one is a decision about who owns the plan, and this
module is where it is enforced rather than merely documented: a hook that reaches for the manager is
refused where it asked (:class:`~composat.errors.LifecycleReentryError`), because a hook is already
inside the critical section of the component it would be moving.

The unit of work is a :class:`~composat.state.Migration` — a target rather than a verb — so bringing
an instance to a level is one call and the verbs fall out of the machine's tables. Only one move runs
on an instance at a time, and every step of it commits its arrival before the hook runs.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import final

from .component import Component, reopen_epoch
from .errors import LifecycleReentryError
from .state import ComponentState, Migration, Operation

#: The move running in this task, if any. A ``ContextVar`` rather than an attribute on the manager,
#: so that it follows the hook across every ``await``: a task a hook starts inherits a copy and is
#: refused too, while an operation running in another task stays independent of it.
_IN_LIFECYCLE: ContextVar[str | None] = ContextVar("composat_lifecycle_move", default=None)


@asynccontextmanager
async def _guard(component: Component, operation: str) -> AsyncGenerator[None]:
    """Refuse a component that reaches for the manager while it is already being driven."""
    current = _IN_LIFECYCLE.get()
    if current is not None:
        raise LifecycleReentryError(component, operation, current)
    token = _IN_LIFECYCLE.set(operation)
    try:
        yield
    finally:
        _IN_LIFECYCLE.reset(token)


async def _release(component: Component) -> None:
    """Drive ``unload``, then close the epoch — and close it the same way when the hook raises.

    A failed unload leaves what it held in an unknown state, so the epoch closes with it: whatever is
    still registered is released, and nothing lives on under a component that no longer claims to be
    loaded. The hook's error is what the caller sees; a failure *while* closing replaces it, chained
    so that both are in the report.
    """
    try:
        await component.unload()
    except BaseException as error:
        try:
            await reopen_epoch(component)
        except Exception as closing:
            raise closing from error
        raise
    await reopen_epoch(component)


async def _drive(component: Component, operation: Operation) -> None:
    """Run the hook ``operation`` names.

    Written out rather than reflected, so a verb the machine grew without a hook to drive it is the
    checker's error here instead of a component that quietly is not driven.
    """
    match operation:
        case Operation.load:
            await component.load()
        case Operation.unload:
            await _release(component)
        case Operation.reload:
            await component.reload()
        case Operation.enable:
            await component.enable()
        case Operation.disable:
            await component.disable()
        case Operation.drain:
            await component.drain()


@final
class ComponentManager:
    """The moves that can be made on one component, and the hooks each of them drives.

    Two verbs, because a target is the whole of what a caller knows: bring this instance to a level,
    or rebuild what it holds. One of them is a migration and the other is not — a reload has
    preconditions and no arrival — and between them they cover the nine verbs the old tree wrote by
    hand.

    Which components move, and in what order, is not here. Ordering is the registry's, the decision to
    move is the assembly's, and doing either from here would put a second plan next to the first.
    """

    async def migrate(self, component: Component, to: ComponentState) -> None:
        """Drive ``component`` to ``to``, by the fewest verbs that get there.

        One critical section for the whole move, and each step commits its arrival before its hook
        runs, so a hook reads the state it is producing. A hook that raises takes *its own* step back:
        what already happened stays happened, because it did — a component that failed while being
        enabled is left ``LOADED``, which is true, rather than one claiming ``UNLOADED`` while its
        ``load`` sits on resources nothing will release.

        ``unload`` is the one step that is not taken back. A component that could not release what it
        held is still down, and the epoch closes with it, so a state saying otherwise would be the lie.

        A move whose step fails and puts the instance back at ``UNLOADED`` closes the epoch too: what
        the hook registered belongs to a lifetime that never started, and a failed ``load``'s sockets
        are nobody's to find later. A cancelled move is the one case left open — releasing an epoch
        means awaiting a release, and awaiting while being cancelled would not finish — so the epoch
        of a cancelled ``load`` is released by whatever holds the instance next.

        The instance's state is read here, inside the lock, so a move always starts from where the
        instance actually is.
        """
        move = f"migrate to {to.name}"
        async with _guard(component, move), component.state.transaction(move) as advance:
            for step in Migration(component.state.state, to).steps():
                undo = advance(step)
                try:
                    await _drive(component, step)
                except BaseException:
                    if step is not Operation.unload:
                        undo()
                        if component.state.state is ComponentState.UNLOADED:
                            await reopen_epoch(component)
                    raise

    async def reload(self, component: Component) -> None:
        """Rebuild what a loaded instance holds, without taking it out of service.

        The verb that is not a migration: committing it only checks that the instance is at a level
        where reloading means something, and it arrives where it started, so there is no path to
        search and nothing to put back.
        """
        async with _guard(component, Operation.reload), component.state.transaction(Operation.reload) as advance:
            _ = advance(Operation.reload)
            await _drive(component, Operation.reload)
