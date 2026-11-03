"""One unit of work per call: asked for as often as it likes, released once, gone with the call."""

from __future__ import annotations

import asyncio

import pytest

from composat import NoCallContextError, open_call, resolve
from examples.support import run
from examples.units.app import Ledger, Page, audit, build


def test_a_call_opens_one_unit_of_work_however_often_it_asks() -> None:
    async def main() -> None:
        assembly, page_key = build()
        _ = await assembly.start(page_key)
        page = assembly.instance(page_key)
        assert isinstance(page, Page)

        async with open_call(page_key, assembly.rule):
            assert await page.render("a") == "1:a"
            # Plain code underneath asks for itself: the same ledger, not a second one.
            assert audit() == 1
            assert resolve(Ledger) is resolve(Ledger)
            assert await page.render("b") == "2:b"
            # Nothing has been released: the unit of work outlives the calls that read it.
            assert page.store.released == 0

        # One close, for however many asks, and the next call starts a new one.
        assert page.store.released == 1
        assert page.store.written == 2

        async with open_call(page_key, assembly.rule):
            assert await page.render("c") == "1:c"

        assert page.store.released == 2

    run(main())


def test_two_calls_are_two_units_of_work() -> None:
    async def main() -> None:
        assembly, page_key = build()
        _ = await assembly.start(page_key)
        page = assembly.instance(page_key)
        assert isinstance(page, Page)
        opened: list[Ledger] = []

        async def one(text: str) -> str:
            async with open_call(page_key, assembly.rule):
                opened.append(resolve(Ledger))
                answer = await page.render(text)
                await asyncio.sleep(0)
                return answer

        assert await asyncio.gather(one("a"), one("b")) == ["1:a", "1:b"]
        first, second = opened
        assert first is not second
        assert [first.rows, second.rows] == [["a"], ["b"]]
        assert page.store.released == 2

    run(main())


def test_the_unit_of_work_belongs_to_no_key() -> None:
    async def main() -> None:
        assembly, page_key = build()
        _ = await assembly.start(page_key)
        page = assembly.instance(page_key)
        assert isinstance(page, Page)

        # The assembly holds what it brought up, and the ledger is not among them: the unit of work
        # never enters the table, so no reader of a key can be handed one by accident.
        up = assembly.keys()
        assert {key.identity.qualname for key in up} == {"Page", "Store"}

        with pytest.raises(NoCallContextError):
            _ = audit()

        assert page.store.released == 0

    run(main())
