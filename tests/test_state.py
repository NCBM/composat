"""The state machine: what the two tables say, what a path through them looks like, and what a move
promises while it runs.

The machine is data, so most of it is asked as data — a pair of states in, the verbs that join them
out, no component and no event loop. What needs the loop is the control, and there the cases are the
three promises a move makes: the state a hook runs in, the verb a hook that raised can put back, and
the record each committed move hands to whoever is watching.
"""

# ``asyncio.Event.wait()`` is typed ``Literal[True]``, and a case that waits for a gate is not asking
# for the answer: the rule is off for this file rather than a row of ``_ =`` a reader walks past.
# pyright: reportUnusedCallResult=false

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

import pytest

from composat import LIFECYCLE_HOOKS, Committed, ComponentState, ComponentStateControl, ComponentStateError, Operation
from composat.state import ARRIVING, LEAVING, Migration


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


class TestTheTables:
    def test_the_verbs_are_the_hooks(self) -> None:
        # One vocabulary: a verb nothing drives, or a hook no verb drives, is a component whose hook
        # is quietly never called — so the two lists are compared rather than trusted to match.
        assert tuple(operation.value for operation in Operation) == LIFECYCLE_HOOKS

    def test_every_verb_says_where_it_starts_and_where_it_arrives(self) -> None:
        assert set(LEAVING) == set(ARRIVING) == set(Operation)

    def test_reload_is_the_verb_that_arrives_nowhere(self) -> None:
        assert ARRIVING[Operation.reload] is None

    def test_a_verb_is_always_a_move(self) -> None:
        # Nothing arrives where it may start, so no verb is a self-loop dressed up as a transition:
        # a state change is always something that happened.
        assert not [operation for operation, arrival in ARRIVING.items() if arrival in LEAVING[operation]]


class TestPaths:
    def test_the_way_up_loads_before_it_enables(self) -> None:
        assert Migration(ComponentState.UNLOADED, ComponentState.ENABLED).steps() == (
            Operation.load,
            Operation.enable,
        )

    def test_the_way_down_drains_before_it_disables(self) -> None:
        assert Migration(ComponentState.ENABLED, ComponentState.UNLOADED).steps() == (
            Operation.drain,
            Operation.disable,
            Operation.unload,
        )

    def test_coming_out_of_service_keeps_what_was_loaded(self) -> None:
        assert Migration(ComponentState.ENABLED, ComponentState.LOADED).steps() == (
            Operation.drain,
            Operation.disable,
        )

    def test_a_drained_component_is_resumed_by_disabling_first(self) -> None:
        assert Migration(ComponentState.DRAINING, ComponentState.ENABLED).steps() == (
            Operation.disable,
            Operation.enable,
        )

    def test_a_state_is_reached_from_itself_without_a_verb(self) -> None:
        assert Migration(ComponentState.LOADED, ComponentState.LOADED).steps() == ()

    def test_every_pair_of_states_is_joined_and_walking_it_lands(self) -> None:
        for origin in ComponentState:
            for target in ComponentState:
                state = origin
                for step in Migration(origin, target).steps():
                    assert state in LEAVING[step], f"{step} leaves {LEAVING[step]}, not {state.name}"
                    state = ARRIVING[step] or state
                assert state is target, f"{origin.name} to {target.name} landed on {state.name}"


class TestTheControl:
    def test_an_instance_begins_unloaded_and_idle(self) -> None:
        control = ComponentStateControl()

        assert control.state is ComponentState.UNLOADED
        assert control.pending is None

    def test_a_committed_verb_is_readable_before_its_hook_returns(self) -> None:
        control = ComponentStateControl()

        async def drive() -> None:
            async with control.transaction("migrate to LOADED") as advance:
                _ = advance(Operation.load)
                assert control.state is ComponentState.LOADED

        run(drive())

    def test_a_verb_answers_the_call_that_takes_it_back(self) -> None:
        control = ComponentStateControl()

        async def drive() -> None:
            async with control.transaction("migrate to LOADED") as advance:
                undo = advance(Operation.load)
                undo()
                assert control.state is ComponentState.UNLOADED

        run(drive())

    def test_a_reload_commits_nothing(self) -> None:
        control = ComponentStateControl()

        async def drive() -> None:
            async with control.transaction("migrate to ENABLED") as advance:
                _ = advance(Operation.load)
                _ = advance(Operation.enable)
            async with control.transaction("reload") as advance:
                _ = advance(Operation.reload)
                assert control.state is ComponentState.ENABLED

        run(drive())

    def test_a_verb_from_a_state_it_does_not_leave_is_refused_and_commits_nothing(self) -> None:
        control = ComponentStateControl()

        async def drive() -> None:
            async with control.transaction("migrate to ENABLED") as advance:
                with pytest.raises(ComponentStateError, match=r"cannot enable from UNLOADED.*leaves LOADED"):
                    _ = advance(Operation.enable)
                assert control.state is ComponentState.UNLOADED

        run(drive())

    def test_what_is_running_is_readable_while_it_runs_and_cleared_after(self) -> None:
        control = ComponentStateControl()

        async def drive() -> None:
            async with control.transaction("migrate to LOADED"):
                assert control.pending == "migrate to LOADED"
            assert control.pending is None

        run(drive())

    def test_a_move_that_raises_still_stops_being_the_one_in_progress(self) -> None:
        control = ComponentStateControl()

        async def drive() -> None:
            with pytest.raises(ValueError, match="boom"):
                async with control.transaction("migrate to LOADED"):
                    raise ValueError("boom")
            assert control.pending is None

        run(drive())

    def test_one_move_at_a_time(self) -> None:
        control = ComponentStateControl()
        entered = asyncio.Event()
        leaving = asyncio.Event()
        order: list[str] = []

        async def first() -> None:
            async with control.transaction("first"):
                order.append("first in")
                entered.set()
                await leaving.wait()
                order.append("first out")

        async def second() -> None:
            await entered.wait()
            async with control.transaction("second"):
                order.append("second in")

        async def drive() -> None:
            awaiting = asyncio.gather(first(), second())
            await entered.wait()
            leaving.set()
            _ = await awaiting

        run(drive())

        assert order == ["first in", "first out", "second in"]


class TestWhatIsReported:
    def test_a_committed_verb_is_reported_with_the_two_states_it_spans(self) -> None:
        control = ComponentStateControl()
        seen: list[Committed] = []
        _ = control.observe(seen.append)

        async def drive() -> None:
            async with control.transaction("migrate to ENABLED") as advance:
                _ = advance(Operation.load)
                _ = advance(Operation.enable)

        run(drive())

        assert [(move.operation, move.origin, move.arrival) for move in seen] == [
            (Operation.load, ComponentState.UNLOADED, ComponentState.LOADED),
            (Operation.enable, ComponentState.LOADED, ComponentState.ENABLED),
        ]
        assert not any(move.undone for move in seen)

    def test_a_step_a_hook_put_back_is_reported_as_undone(self) -> None:
        control = ComponentStateControl()
        seen: list[Committed] = []
        _ = control.observe(seen.append)

        async def drive() -> None:
            async with control.transaction("migrate to LOADED") as advance:
                undo = advance(Operation.load)
                undo()

        run(drive())

        # The arrival was reported when it was committed, so the state it did not hold has to be
        # corrected rather than quietly left standing.
        assert seen == [
            Committed(Operation.load, ComponentState.UNLOADED, ComponentState.LOADED),
            Committed(Operation.load, ComponentState.LOADED, ComponentState.UNLOADED, undone=True),
        ]

    def test_a_verb_that_is_refused_is_reported_to_nobody(self) -> None:
        control = ComponentStateControl()
        seen: list[Committed] = []
        _ = control.observe(seen.append)

        async def drive() -> None:
            async with control.transaction("migrate to ENABLED") as advance:
                with pytest.raises(ComponentStateError):
                    _ = advance(Operation.enable)

        run(drive())

        assert seen == []

    def test_a_reload_is_reported_where_it_started(self) -> None:
        control = ComponentStateControl()
        seen: list[Committed] = []
        _ = control.observe(seen.append)

        async def drive() -> None:
            async with control.transaction("migrate to ENABLED") as advance:
                _ = advance(Operation.load)
                _ = advance(Operation.enable)
            async with control.transaction("reload") as advance:
                _ = advance(Operation.reload)

        run(drive())

        assert seen[-1] == Committed(Operation.reload, ComponentState.ENABLED, ComponentState.ENABLED)

    def test_an_observer_is_called_while_the_move_it_reports_is_still_pending(self) -> None:
        # What an observer may rely on: the move it is told about has not finished, so what it reads
        # is the instance mid-move rather than the resting state a later read would see.
        control = ComponentStateControl()
        seen: list[str | None] = []

        def watch(_move: Committed) -> None:
            seen.append(control.pending)

        _ = control.observe(watch)

        async def drive() -> None:
            async with control.transaction("migrate to LOADED") as advance:
                _ = advance(Operation.load)

        run(drive())

        assert seen == ["migrate to LOADED"]

    def test_unsubscribing_stops_that_one_and_leaves_the_others_alone(self) -> None:
        control = ComponentStateControl()
        first: list[Committed] = []
        second: list[Committed] = []
        unsubscribe = control.observe(first.append)
        _ = control.observe(second.append)

        unsubscribe()

        async def drive() -> None:
            async with control.transaction("migrate to LOADED") as advance:
                _ = advance(Operation.load)

        run(drive())

        assert first == []
        assert [move.operation for move in second] == [Operation.load]

    def test_unsubscribing_twice_does_nothing(self) -> None:
        control = ComponentStateControl()
        seen: list[Committed] = []
        unsubscribe = control.observe(seen.append)

        unsubscribe()
        unsubscribe()

        async def drive() -> None:
            async with control.transaction("migrate to LOADED") as advance:
                _ = advance(Operation.load)

        run(drive())

        assert seen == []
