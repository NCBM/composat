"""A request is a call: the body is written inside it, and the store it read is the deployment's."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import final

import pytest

from examples.http.app import Deployment, Message, Store, build
from examples.support import run


@final
class Conversation:
    """One request as it went out: the messages, and how many carts had been closed at each of them."""

    sent: list[Message]
    letting_go: list[int]

    def __init__(self, sent: list[Message], letting_go: list[int]) -> None:
        self.sent = sent
        self.letting_go = letting_go

    @property
    def status(self) -> object:
        """The status the deployment answered with, or ``None`` when it answered with nothing."""
        return next((message["status"] for message in self.sent if message["type"] == "http.response.start"), None)

    def bodies(self) -> list[bytes]:
        """The body messages in the order they went out; the last one is the empty end of the response."""
        return [_bytes_of(message["body"]) for message in self.sent if message["type"] == "http.response.body"]


def _bytes_of(value: object) -> bytes:
    return value if isinstance(value, bytes) else b""


async def request(app: Deployment, body: bytes, store: Store) -> Conversation:
    """One request, driven the way a server drives it: hand it the body, keep what it sent."""
    sent: list[Message] = []
    letting_go: list[int] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)
        letting_go.append(store.let_go)

    await app({"type": "http", "method": "POST", "path": "/"}, receive, send)
    return Conversation(sent, letting_go)


async def serve(app: Deployment, during: Callable[[], Awaitable[None]]) -> list[Message]:
    """Start the deployment, run ``during``, then stop it: the shape a server's lifespan gives."""
    sent: list[Message] = []
    inbox: asyncio.Queue[Message] = asyncio.Queue()
    started = asyncio.Event()

    async def receive() -> Message:
        return await inbox.get()

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "lifespan.startup.complete":
            started.set()

    await inbox.put({"type": "lifespan.startup"})
    life = asyncio.create_task(app({"type": "lifespan"}, receive, send))
    _ = await started.wait()
    await during()
    await inbox.put({"type": "lifespan.shutdown"})
    await life
    return sent


def test_the_call_outlives_the_body_it_is_writing() -> None:
    async def main() -> None:
        app = build()
        kept: list[Store] = []

        async def during() -> None:
            store = app.held()
            kept.append(store)
            conversation = await request(app, b"hello world", store)

            assert conversation.status == 200
            assert conversation.bodies() == [b"1:hello\n", b"2:world\n", b""]
            # Every message went out while the cart was still held: the scope ends after the body, not
            # when the handler returned.
            assert conversation.letting_go == [0, 0, 0, 0]
            assert store.let_go == 1
            assert store.written == 2
            # The flush started once the response was done, and is still running.
            assert store.flushed == 0
            assert store.swept == 0

        sent = await serve(app, during)

        assert [message["type"] for message in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert app.assembly.keys() == ()
        for _ in range(3):  # the cancellation lands on a tick of its own
            await asyncio.sleep(0)
        # Stopping the deployment cut the flush off, and what the request read is what it was holding.
        assert kept[0].flushed == 0
        assert kept[0].swept == 2

    run(main())


def test_two_requests_at_once_hold_two_carts_and_share_one_store() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            store = app.held()
            first, second = await asyncio.gather(request(app, b"one", store), request(app, b"two three", store))

            # Each response counts its own rows, and both wrote to the one instance behind the store.
            assert first.bodies() == [b"1:one\n", b""]
            assert second.bodies() == [b"1:two\n", b"2:three\n", b""]
            assert [first.status, second.status] == [200, 200]
            assert store.written == 3
            assert store.let_go == 2

        _ = await serve(app, during)

    run(main())


def test_a_request_that_cannot_be_answered_is_refused_before_the_body_starts() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            store = app.held()
            refused = await request(app, b"   ", store)

            assert refused.status == 400
            assert refused.bodies() == [b"nothing to render"]
            assert store.written == 0
            # The call was released anyway, and no flush was started for a request that did no work.
            assert refused.letting_go == [0, 0]
            assert store.let_go == 1
            assert store.flushed == 0
            assert store.swept == 0

        _ = await serve(app, during)

    run(main())


def test_a_client_that_goes_away_mid_body_still_loses_nothing() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            store = app.held()
            sent: list[Message] = []

            async def receive() -> Message:
                return {"type": "http.request", "body": b"one two", "more_body": False}

            async def send(message: Message) -> None:
                sent.append(message)
                if len(sent) == 2:  # the first piece of the body: the client is gone
                    task = asyncio.current_task()
                    assert task is not None
                    _ = task.cancel()
                    await asyncio.sleep(0)

            with pytest.raises(asyncio.CancelledError):
                await app({"type": "http", "method": "POST", "path": "/"}, receive, send)

            # The response never finished, and the cart was still given up: a teardown that has to be
            # remembered on the way out is the leak this scope exists to make impossible.
            assert store.let_go == 1
            assert store.written == 1
            assert store.flushed == 0
            assert store.swept == 0

        _ = await serve(app, during)

    run(main())
