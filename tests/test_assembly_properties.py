"""The claim and the cancelled path, as a property: a key is never left half way through a move.

A verb speaks for the keys it names from the moment it accepts them, and the two ways it can stop
holding them are the two that are easy to get wrong: it returns a report that says one key failed
(``strict`` gives the batch back), and the task driving it is cancelled while a hook is in flight.
What must hold in both is what a reader reads — nothing is still spoken for, every key the table holds
has an instance behind it, and that instance rests at a level an assembly holds rather than at the one
the table leaves when it stops holding a key.

The shapes are drawn rather than listed: which key refuses, which hook a batch is cancelled in, and
whether it was told to come up serving.
"""

# ``asyncio.Event.wait()`` is typed ``Literal[True]``, and a case that waits for a gate is not asking
# for the answer: the rule is off for this file rather than a row of ``_ =`` a reader walks past.
# pyright: reportUnusedCallResult=false

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import override

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    ComponentState,
    Declaration,
    KeyInTransitError,
)
from composat.state import HELD

NAMES = ("a", "b", "c")


@dataclass(frozen=True)
class Plan:
    """What the hooks of one generation do: stop in one of them, or refuse in one of them."""

    waits_in: str | None = None
    refuses_in: str | None = None


class Gates:
    """One case's two events: the hook that stopped says it arrived, and the case lets it go."""

    reached: asyncio.Event
    go: asyncio.Event

    def __init__(self) -> None:
        self.reached = asyncio.Event()
        self.go = asyncio.Event()


class Step(Component):
    """A component that does what its plan says, and writes down every hook it was driven through."""

    plan: Plan
    gates: Gates
    driven: list[str]

    def __init__(self, plan: Plan, gates: Gates) -> None:
        super().__init__()
        self.plan = plan
        self.gates = gates
        self.driven = []

    @override
    async def load(self) -> None:
        await self._step("load")

    @override
    async def enable(self) -> None:
        await self._step("enable")

    @override
    async def drain(self) -> None:
        await self._step("drain")

    @override
    async def unload(self) -> None:
        await self._step("unload")

    async def _step(self, name: str) -> None:
        self.driven.append(name)
        if self.plan.waits_in == name:
            self.gates.reached.set()
            _ = await self.gates.go.wait()
        if self.plan.refuses_in == name:
            raise RuntimeError(f"{name} refused")


def at(name: str) -> ComponentKey:
    """The key one tenant of ``Step`` answers at."""
    return ComponentKey(ComponentIdentity.of(Step), name)


def building(plans: dict[str, Plan], gates: Gates) -> Assembly:
    """An assembly of one tenant per plan, declared and not started."""
    registry = ComponentRegistry()
    _ = registry.declare_all(Declaration(Step, instance=name, args=(plan, gates)) for name, plan in plans.items())
    return Assembly(registry, up={})


def settled(assembly: Assembly, keys: tuple[ComponentKey, ...]) -> None:
    """What a batch that has come back — returned, or cancelled — leaves behind."""
    for key in keys:
        assert assembly.in_transit(key) is None, f"{key} is still spoken for"
    table = assembly.keys()
    for key in table:
        instance = assembly.instance(key)
        assert instance is not None, f"{key} is in the table with nothing behind it"
        # ``UNLOADED`` is the level the table stops holding a key at, so a key it does hold is at one
        # of the other three: anything else is a move that stopped where the key still answers.
        assert instance.state.state in HELD, f"{key} rests at {instance.state.state.name}"


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


@given(
    failing=st.sampled_from(NAMES),
    enable=st.booleans(),
    strict=st.booleans(),
)
@settings(max_examples=20, deadline=None)
def test_a_batch_that_comes_up_short_holds_nothing_back(failing: str, *, enable: bool, strict: bool) -> None:
    async def main() -> None:
        gates = Gates()
        plans = {name: Plan(refuses_in="load" if name == failing else None) for name in NAMES}
        assembly = building(plans, gates)
        keys = tuple(at(name) for name in NAMES)
        others = {at(name) for name in NAMES if name != failing}

        if strict:
            # The strict reading is the one a host asks for when a half-started batch is worse than
            # no batch: what this call brought up is taken back down, and the failure it stopped at is
            # raised rather than reported.
            with pytest.raises(RuntimeError, match="load refused"):
                _ = await assembly.start(*keys, enable=enable, strict=strict)
            assert assembly.keys() == ()
        else:
            result = await assembly.start(*keys, enable=enable, strict=strict)
            assert set(result.failed) == {at(failing)}
            # The key that refused is not up; the ones this batch did bring up are, at the level it
            # was asked for.
            assert set(assembly.keys()) == others
        settled(assembly, keys)

        _ = await assembly.stop(*keys)
        assert assembly.keys() == ()

    run(main())


@given(enable=st.booleans(), strict=st.booleans())
@settings(max_examples=10, deadline=None)
def test_a_batch_cancelled_while_a_key_is_built_leaves_no_claim_behind(*, enable: bool, strict: bool) -> None:
    async def main() -> None:
        gates = Gates()
        plans = {name: Plan(waits_in="load" if name == "b" else None) for name in NAMES}
        assembly = building(plans, gates)
        keys = tuple(at(name) for name in NAMES)

        batch = asyncio.create_task(assembly.start(*keys, enable=enable, strict=strict))
        _ = await gates.reached.wait()
        batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            _ = await batch

        settled(assembly, keys)

        # The table the cancelled batch left is one a host can still shut down with one call: the
        # cleanup is ``stop`` over what is up, and it is idempotent because a key that is not up is
        # passed over rather than refused.
        _ = await assembly.stop(*assembly.keys())
        assert assembly.keys() == ()
        settled(assembly, keys)

    run(main())


@given(strict=st.booleans())
@settings(max_examples=10, deadline=None)
def test_a_batch_cancelled_while_a_key_is_put_into_service_leaves_no_claim_behind(*, strict: bool) -> None:
    async def main() -> None:
        gates = Gates()
        plans = {name: Plan(waits_in="enable" if name == "b" else None) for name in NAMES}
        assembly = building(plans, gates)
        keys = tuple(at(name) for name in NAMES)

        batch = asyncio.create_task(assembly.start(*keys, strict=strict))
        _ = await gates.reached.wait()
        batch.cancel()
        with pytest.raises(asyncio.CancelledError):
            _ = await batch

        # The key the hook was interrupted in was committed as serving before the hook ran, so it may
        # be up or gone: what it may not be is up with nothing behind it, or spoken for by a call that
        # is over.
        settled(assembly, keys)
        _ = await assembly.stop(*assembly.keys())
        assert assembly.keys() == ()

    run(main())


@given(slow=st.sampled_from(NAMES))
@settings(max_examples=10, deadline=None)
def test_a_quiesce_cancelled_while_a_key_drains_leaves_it_where_the_table_says(slow: str) -> None:
    async def main() -> None:
        gates = Gates()
        plans = {name: Plan(waits_in="drain" if name == slow else None) for name in NAMES}
        assembly = building(plans, gates)
        keys = tuple(at(name) for name in NAMES)
        _ = await assembly.start(*keys)

        taking_down = asyncio.create_task(assembly.quiesce(*keys))
        _ = await gates.reached.wait()
        taking_down.cancel()
        # The gate is the case's stand-in for a drain that finishes: letting it go here means the
        # shutdown below waits for nothing this case invented, whatever level the cancelled move left.
        gates.go.set()
        with pytest.raises(asyncio.CancelledError):
            _ = await taking_down

        # A drain that was interrupted is a level rather than a half-state: the key is still held, and
        # what it rests at is what the table says, so the next verb has a place to be driven from.
        settled(assembly, keys)
        assert set(assembly.keys()) == set(keys)

        _ = await assembly.stop(*keys)
        assert assembly.keys() == ()

    run(main())


def test_a_second_verb_on_a_claimed_key_is_refused_and_moves_nothing() -> None:
    async def main() -> None:
        gates = Gates()
        assembly = building({"a": Plan(waits_in="enable")}, gates)
        key = at("a")

        batch = asyncio.create_task(assembly.start(key))
        _ = await gates.reached.wait()

        # The claim is readable but not takeable: the second call is refused before it touches
        # anything, and what it would have moved is exactly where the first call left it.
        claim = assembly.in_transit(key)
        assert claim is not None
        assert claim.arrival is ComponentState.ENABLED
        with pytest.raises(KeyInTransitError):
            _ = await assembly.quiesce(key)
        instance = assembly.instance(key)
        assert instance is not None

        gates.go.set()
        result = await batch
        assert result.completed == (key,)
        assert assembly.instance(key) is instance
        settled(assembly, (key,))

        _ = await assembly.stop(key)

    run(main())
