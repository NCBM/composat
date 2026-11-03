"""Draining an instance's work: what is nearly done is finished, and the rest is cut and waited for."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from composat import EffectScope
from composat.std.drain import InFlight


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


class TestDraining:
    def test_nothing_in_flight_is_nothing_to_wait_for(self) -> None:
        async def drive() -> int:
            return await InFlight(EffectScope()).drain(1.0)

        assert run(drive()) == 0

    def test_work_that_finishes_within_the_grace_is_kept(self) -> None:
        done: list[str] = []

        async def work() -> None:
            await asyncio.sleep(0)
            done.append("finished")

        async def drive() -> int:
            flying = InFlight(EffectScope())
            flying.add(work())
            return await flying.drain(1.0)

        assert run(drive()) == 0
        assert done == ["finished"]

    def test_work_that_outlives_the_grace_is_cut_off_and_waited_for(self) -> None:
        ended: list[str] = []

        async def work() -> None:
            try:
                await asyncio.sleep(10)
            finally:
                ended.append("over")

        async def drive() -> int:
            flying = InFlight(EffectScope())
            flying.add(work())
            return await flying.drain(0.0)

        assert run(drive()) == 1
        # The cut was waited for, so the work is over by the time the drain answers.
        assert ended == ["over"]

    def test_what_is_still_running_is_what_a_drain_would_wait_for(self) -> None:
        async def drive() -> tuple[int, int]:
            flying = InFlight(EffectScope())
            flying.add(asyncio.sleep(10))
            held = len(flying)
            _ = await flying.drain(0.0)
            return held, len(flying)

        assert run(drive()) == (1, 0)

    def test_finished_work_is_not_counted_again(self) -> None:
        async def drive() -> int:
            flying = InFlight(EffectScope())
            flying.add(asyncio.sleep(0))
            for _ in range(3):  # the task finishes on one tick and reports back on the next
                await asyncio.sleep(0)
            return len(flying)

        assert run(drive()) == 0

    def test_the_epoch_cancels_work_nobody_drained(self) -> None:
        ended: list[str] = []

        async def work() -> None:
            try:
                await asyncio.sleep(10)
            finally:
                ended.append("over")

        async def drive() -> None:
            scope = EffectScope()
            InFlight(scope).add(work())
            await scope.dispose()
            await asyncio.sleep(0)  # the cancellation is delivered on a tick of its own

        run(drive())

        assert ended == ["over"]
