"""Effects: what a scope promises, how a revoke releases a segment, and how an epoch ends.

The scope is a promise that an effect is released exactly once, last-registered first. These cases
pin the promise from the side that matters — the ending — because that is where an epoch's rule
lives: closing is not clearing, and a new epoch is a new scope.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

import pytest

from composat import EffectScope


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


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

    def test_the_view_reports_names_rather_than_the_effects(self) -> None:
        def close_socket() -> None:
            pass

        scope = EffectScope()
        _ = scope.put(lambda: None)
        _ = scope.put(close_socket)

        assert scope.effects() == ("<lambda>", "close_socket")

    def test_a_released_effect_leaves_the_view(self) -> None:
        def close_socket() -> None:
            pass

        scope = EffectScope()
        _ = scope.put(lambda: None)
        revoke = scope.put(close_socket)

        run(revoke())

        assert scope.effects() == ("<lambda>",)

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
