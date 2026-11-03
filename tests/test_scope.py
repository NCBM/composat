"""Effects: what a scope promises, how a revoke releases a segment, and how an epoch ends.

The scope is a promise that an effect is released exactly once, last-registered first. These cases
pin the promise from the side that matters — the ending — because that is where an epoch's rule
lives: closing is not clearing, and a new epoch is a new scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest

from composat import EffectScope


def run[T](awaitable: Awaitable[T], /) -> T:
    return asyncio.run(_awaited(awaitable))


async def _awaited[T](awaitable: Awaitable[T]) -> T:
    """``asyncio.run`` wants a coroutine; a revoke is only promised to be awaitable."""
    return await awaitable


class TestRegistration:
    def test_effects_are_released_last_in_first_out(self) -> None:
        released: list[str] = []
        scope = EffectScope()
        _ = scope.put(lambda: released.append("first"))
        _ = scope.put(lambda: released.append("second"))

        run(scope.dispose())

        assert released == ["second", "first"]

    def test_an_async_effect_is_awaited(self) -> None:
        released: list[str] = []
        scope = EffectScope()

        async def release() -> None:
            released.append("awaited")

        _ = scope.put(release)
        run(scope.dispose())

        assert released == ["awaited"]

    def test_a_released_effect_is_no_longer_held(self) -> None:
        def close_socket() -> None:
            pass

        scope = EffectScope()
        _ = scope.put(lambda: None)
        revoke = scope.put(close_socket)
        assert "close_socket" in scope.effects()

        run(revoke())

        assert "close_socket" not in scope.effects()

    def test_a_failing_effect_does_not_stop_the_rest(self) -> None:
        released: list[str] = []
        scope = EffectScope()

        def boom() -> None:
            raise ValueError("boom")

        _ = scope.put(lambda: released.append("first"))
        _ = scope.put(boom)

        with pytest.raises(ExceptionGroup, match="recycling effects"):
            run(scope.dispose())
        assert released == ["first"]


class TestClosing:
    def test_putting_after_disposal_is_refused(self) -> None:
        scope = EffectScope()
        run(scope.dispose())

        with pytest.raises(RuntimeError, match="closed"):
            _ = scope.put(lambda: None)

    def test_disposal_is_idempotent(self) -> None:
        released: list[str] = []
        scope = EffectScope()
        _ = scope.put(lambda: released.append("once"))

        run(scope.dispose())
        run(scope.dispose())

        assert released == ["once"]
        assert scope.dead

    def test_a_revoke_releases_the_checkpoint_and_everything_after_it(self) -> None:
        released: list[str] = []
        scope = EffectScope()
        _ = scope.put(lambda: released.append("before"))
        revoke = scope.put(lambda: released.append("from-here"))
        _ = scope.put(lambda: released.append("after"))

        run(revoke())
        run(scope.dispose())

        assert released == ["after", "from-here", "before"]


class TestReopening:
    def test_it_releases_what_the_old_epoch_held_and_answers_a_fresh_scope(self) -> None:
        released: list[str] = []
        scope = EffectScope()
        _ = scope.put(lambda: released.append("old"))

        fresh = run(scope.reopen())

        assert released == ["old"]
        assert scope.dead
        assert len(fresh) == 0

    def test_the_old_scope_stays_closed_so_a_straggler_cannot_join_the_new_epoch(self) -> None:
        scope = EffectScope()
        fresh = run(scope.reopen())

        with pytest.raises(RuntimeError, match="closed"):
            _ = scope.put(lambda: None)
        assert len(fresh) == 0

    def test_reopening_twice_is_harmless(self) -> None:
        scope = EffectScope()
        first = run(scope.reopen())
        second = run(scope.reopen())

        assert first is not second
        assert scope.dead


class TestTimedEffects:
    def test_an_effect_runs_once_after_its_delay(self) -> None:
        ran: list[str] = []
        scope = EffectScope()

        async def scenario() -> None:
            _ = scope.after(0, lambda: ran.append("ran"))
            await asyncio.sleep(0.01)

        run(scenario())

        assert ran == ["ran"]

    def test_an_awaitable_effect_is_awaited(self) -> None:
        ran: list[str] = []
        scope = EffectScope()

        async def expire() -> None:
            await asyncio.sleep(0)
            ran.append("expired")

        async def scenario() -> None:
            _ = scope.after(0, expire)
            await asyncio.sleep(0.02)

        run(scenario())

        assert ran == ["expired"]

    def test_it_is_named_in_the_view(self) -> None:
        def expire() -> None:
            pass

        scope = EffectScope()

        async def scenario() -> None:
            _ = scope.after(1, expire)

        run(scenario())

        assert scope.effects() == ("expire",)

    def test_a_cleared_scope_never_runs_it(self) -> None:
        ran: list[str] = []
        scope = EffectScope()

        async def scenario() -> None:
            _ = scope.after(0.05, lambda: ran.append("ran"))
            await scope.dispose()
            await asyncio.sleep(0.06)

        run(scenario())

        assert ran == []

    def test_a_revoked_segment_never_runs_it(self) -> None:
        ran: list[str] = []
        scope = EffectScope()

        async def scenario() -> None:
            revoke = scope.after(0.05, lambda: ran.append("ran"))
            await revoke()
            await asyncio.sleep(0.06)

        run(scenario())

        assert ran == []

    def test_a_closed_scope_refuses_a_timer(self) -> None:
        scope = EffectScope()

        async def scenario() -> None:
            await scope.dispose()
            with pytest.raises(RuntimeError, match="closed"):
                _ = scope.after(1, lambda: None)

        run(scenario())

    def test_nothing_a_closed_scope_was_holding_runs_afterwards(self) -> None:
        ran: list[str] = []
        scope = EffectScope()

        async def scenario() -> None:
            _ = scope.after(0, lambda: ran.append("ran"))
            assert not scope.closed
            await scope.dispose()  # closed before the timer got its turn
            assert scope.closed
            await asyncio.sleep(0.01)

        run(scenario())

        assert ran == []

    def test_an_awaitable_effect_is_taken_down_with_the_scope(self) -> None:
        started: list[str] = []
        ended: list[str] = []

        async def expire() -> None:
            started.append("started")
            try:
                await asyncio.sleep(10)
            finally:
                ended.append("over")

        scope = EffectScope()

        async def scenario() -> None:
            _ = scope.after(0, expire)
            for _ in range(3):  # the timer lands, then the task it started
                await asyncio.sleep(0)
            assert started == ["started"]
            await scope.dispose()
            for _ in range(3):  # and the cancellation lands
                await asyncio.sleep(0)
            # The work of a timed effect is the scope's, not a task that outlives it: it is over here,
            # before the loop this ran on is gone.
            assert ended == ["over"]

        run(scenario())

    def test_a_failure_goes_to_the_loop_handler(self) -> None:
        seen: list[str] = []
        scope = EffectScope()

        def boom() -> None:
            raise ValueError("boom")

        def handler(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
            seen.append(str(context["message"]))

        async def scenario() -> None:
            asyncio.get_running_loop().set_exception_handler(handler)
            _ = scope.after(0, boom)
            await asyncio.sleep(0.01)

        run(scenario())

        assert seen == ["a timed effect (boom) failed"]
