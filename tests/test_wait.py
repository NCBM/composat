"""Waiting for a key to serve: the handle a reader takes instead of retrying through a swap.

The case worth testing is the one the wait exists for — a swap whose old generation is still being
torn down — so these hold the teardown open rather than hope the window is wide enough to be seen.
"""

# A case here waits on gates and reads a handle; both answer values the wait is not asking for.
# pyright: reportUnusedCallResult=false

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import ClassVar, override

import pytest

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    ComponentState,
    Declaration,
    ServiceNotUpError,
    require,
)
from composat.std.wait import serving


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


def at(component: type[Component], instance: str = "") -> ComponentKey:
    """The key ``component`` answers at under ``instance``."""
    return ComponentKey(ComponentIdentity.of(component), instance)


def declaring(*declarations: Declaration) -> ComponentRegistry:
    """A registry holding ``declarations``: what a wait needs before there is anything to wait for."""
    registry = ComponentRegistry()
    _ = registry.declare_all(declarations)
    return registry


def generation(name: str, **body: object) -> type[Component]:
    """A class wearing the identity of the module-level one called ``name``: a reload, written down."""
    return type(name, (Component,), {"__module__": __name__, **body})


class Store(Component):
    """A leaf: the key a wait is about."""


class Slow(Component):
    """A generation whose teardown is held open, which is the window a wait exists for."""

    reached: ClassVar[asyncio.Event]
    gate: ClassVar[asyncio.Event]

    @classmethod
    def hold(cls) -> None:
        """Start a fresh pair of events, so one case cannot open another case's gate."""
        cls.reached = asyncio.Event()
        cls.gate = asyncio.Event()

    @override
    async def unload(self) -> None:
        Slow.reached.set()
        await Slow.gate.wait()


class Reading(Component):
    """A serving component whose provider is the key a swap is held open on."""

    slow: Slow = require()


class TestServing:
    def test_a_key_that_is_not_up_is_refused(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))

        with pytest.raises(ServiceNotUpError) as raised:
            _ = run(serving(assembly, at(Store)))

        assert raised.value.key == at(Store)

    def test_a_key_that_leaves_the_table_while_it_waits_is_refused(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store), enable=False))

        async def scenario() -> None:
            waiter = asyncio.create_task(serving(assembly, at(Store)))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter), 0.01)
            _ = await assembly.stop(at(Store))
            _ = await waiter

        with pytest.raises(ServiceNotUpError) as raised:
            run(scenario())

        assert raised.value.key == at(Store)

    def test_it_waits_out_a_swap_and_answers_the_generation_that_serves(self) -> None:
        Slow.hold()
        assembly = Assembly(declaring(Declaration(Slow)))
        _ = run(assembly.start(at(Slow)))

        async def scenario() -> tuple[Component, Component | object]:
            swap = asyncio.create_task(assembly.replace(at(Slow), Declaration(generation("Slow"))))
            await Slow.reached.wait()

            waiter = asyncio.create_task(serving(assembly, at(Slow)))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waiter), 0.01)

            Slow.gate.set()
            return await waiter, await swap

        waited, swapped = run(scenario())

        # The key answered the new generation the moment it was built; what the wait waited for is
        # that generation serving, which is what the end of the old one's teardown releases.
        assert waited is swapped
        assert waited is assembly.instance(at(Slow))
        assert waited.state.state is ComponentState.ENABLED

    def test_a_key_that_rests_below_serving_is_refused_at_once(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store), enable=False))

        with pytest.raises(ServiceNotUpError) as raised:
            _ = run(serving(assembly, at(Store)))

        # Nothing is moving it, so waiting would be waiting for nothing: the level it rests at is the
        # answer, and the caller's own deadline is not what finds that out.
        assert raised.value.key == at(Store)
        assert raised.value.level is ComponentState.LOADED

    def test_a_key_that_settles_below_serving_while_it_waits_is_refused(self) -> None:
        Slow.hold()
        assembly = Assembly(declaring(Declaration(Slow)))
        _ = run(assembly.start(at(Slow), enable=False))

        async def scenario() -> ServiceNotUpError:
            swap = asyncio.create_task(assembly.replace(at(Slow), Declaration(generation("Slow"))))
            await Slow.reached.wait()

            waiter = asyncio.create_task(serving(assembly, at(Slow)))
            Slow.gate.set()
            with pytest.raises(ServiceNotUpError) as refused:
                _ = await asyncio.wait_for(waiter, 1)
            _ = await swap
            return refused.value

        refused = run(scenario())

        # The old generation was not serving, so the swap has no publish segment: the new one settles
        # at ``LOADED``, and the wait ends with that level rather than with a deadline.
        assert refused.key == at(Slow)
        assert refused.level is ComponentState.LOADED

    def test_a_serving_caller_is_refused_the_generation_that_does_not_serve_yet(self) -> None:
        Slow.hold()
        assembly = Assembly(declaring(Declaration(Reading), Declaration(Slow)))
        _ = run(assembly.start(at(Reading)))
        reading = assembly.instance(at(Reading))
        assert isinstance(reading, Reading)

        async def scenario() -> tuple[ServiceNotUpError, Component]:
            swap = asyncio.create_task(assembly.replace(at(Slow), Declaration(generation("Slow"))))
            await Slow.reached.wait()

            with pytest.raises(ServiceNotUpError) as refused:
                _ = reading.slow
            # ``serving`` is what a caller that has to read *now* reaches for instead of retrying.
            waited = asyncio.create_task(serving(assembly, at(Slow)))
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(waited), 0.01)
            Slow.gate.set()
            instance = await waited
            _ = await swap
            return refused.value, instance

        refused, waited = run(scenario())

        assert refused.level is ComponentState.LOADED
        assert refused.asked_from == at(Reading)
        assert waited is assembly.instance(at(Slow))
        assert waited.state.state is ComponentState.ENABLED
