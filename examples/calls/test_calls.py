"""One component serving many calls: each call owns what it opened, and loses it when it ends."""

from __future__ import annotations

import asyncio

from composat import NoCallContextError, current, open_call
from examples.calls.app import Page, Session, build, writer
from examples.support import run


def test_a_call_owns_what_it_opened_and_loses_it_afterwards() -> None:
    async def main() -> None:
        assembly, page_key = build()
        _ = await assembly.start(page_key)
        page = assembly.instance(page_key)
        assert isinstance(page, Page)

        async with open_call(page_key, assembly.rule) as call:
            session = Session()
            _ = call.put(session.close)

            assert await page.render(session, "a") == "1:a"
            assert await page.render(session, "b") == "2:b"
            assert not session.closed

        assert session.closed
        assert page.store.written == 2

    run(main())


def test_two_calls_do_not_share_what_they_opened() -> None:
    async def main() -> None:
        assembly, page_key = build()
        _ = await assembly.start(page_key)
        page = assembly.instance(page_key)
        assert isinstance(page, Page)

        opened: list[Session] = []

        async def one(text: str) -> str:
            async with open_call(page_key, assembly.rule) as call:
                session = Session()
                _ = call.put(session.close)
                opened.append(session)
                answer = await page.render(session, text)
                await asyncio.sleep(0)
                return answer

        assert await asyncio.gather(one("a"), one("b")) == ["1:a", "1:b"]
        first, second = opened
        assert first is not second
        assert [first.closed, second.closed] == [True, True]

    run(main())


def test_code_underneath_reads_the_same_instance_the_field_does() -> None:
    async def main() -> None:
        assembly, page_key = build()
        _ = await assembly.start(page_key)
        page = assembly.instance(page_key)
        assert isinstance(page, Page)

        async with open_call(page_key, assembly.rule):
            assert writer() is page.store

        try:
            _ = writer()
        except NoCallContextError:
            pass
        else:  # pragma: no cover - the assertion below is the point
            raise AssertionError("asking outside a call should have been refused")

    run(main())


def test_a_call_nests_and_its_effects_go_when_it_does() -> None:
    async def main() -> None:
        assembly, page_key = build()
        _ = await assembly.start(page_key)
        released: list[str] = []

        async with open_call(page_key, assembly.rule) as outer:
            _ = outer.put(lambda: released.append("outer"))
            async with open_call(page_key, assembly.rule) as inner:
                assert current() is inner
                _ = inner.put(lambda: released.append("inner"))
            assert current() is outer

        assert released == ["inner", "outer"]

    run(main())
