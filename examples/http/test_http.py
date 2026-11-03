"""Four lifetimes under one handler: the process, a tenant, a client, and the request itself."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import final

import pytest

from examples.http.app import Application, Message, Scope, Session, Tally, build, key_of
from examples.support import run


@final
class Conversation:
    """One request as it went out: the messages, and how many renderings had been closed at each."""

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


async def post(app: Application, path: str, /, *, client: str = "", body: bytes = b"") -> Conversation:
    """One request, driven the way a server drives it: hand it the body, keep what it sent."""
    sent: list[Message] = []
    letting_go: list[int] = []
    tally = app.tally()
    headers: list[tuple[bytes, bytes]] = [] if not client else [(b"x-session", client.encode())]

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)
        letting_go.append(tally.let_go)

    scope: Scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    await app(scope, receive, send)
    return Conversation(sent, letting_go)


async def serve(app: Application, during: Callable[[], Awaitable[None]]) -> list[Message]:
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
        kept: list[Tally] = []

        async def during() -> None:
            tally = app.tally()
            kept.append(tally)
            conversation = await post(app, "/acme", client="s1", body=b"hello world")

            assert conversation.status == 200
            assert conversation.bodies() == [b"acme:1/1:hello\n", b"acme:2/2:world\n", b""]
            # Every message went out while the rendering was still held: the scope ends after the
            # body, not when the handler returned.
            assert conversation.letting_go == [0, 0, 0, 0]
            assert tally.let_go == 1
            # The tenant's renderer counted into the process-wide tally: the one line that crosses.
            assert tally.written == 2
            # The flush started once the response was done, and is still running.
            assert tally.flushed == 0
            assert tally.swept == 0

        sent = await serve(app, during)

        assert [message["type"] for message in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
        assert app.assembly.keys() == ()
        for _ in range(3):  # the cancellation lands on a tick of its own
            await asyncio.sleep(0)
        # Stopping the deployment cut the flush off, and what the request read is what it was holding.
        assert kept[0].flushed == 0
        assert kept[0].swept == 2

    run(main())


def test_two_requests_at_once_hold_two_renderings_and_share_one_tally() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            tally = app.tally()
            first, second = await asyncio.gather(
                post(app, "/acme", client="s1", body=b"one"),
                post(app, "/acme", client="s2", body=b"two three"),
            )

            # Each client's session counts its own rows, and both wrote to the one process-wide tally.
            assert first.bodies() == [b"acme:1/1:one\n", b""]
            assert second.bodies() == [b"acme:1/1:two\n", b"acme:2/2:three\n", b""]
            assert [first.status, second.status] == [200, 200]
            assert tally.written == 3
            assert tally.let_go == 2

        _ = await serve(app, during)

    run(main())


def test_one_handler_serves_every_tenant_from_its_own_instance() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            tally = app.tally()
            acme = await post(app, "/acme", client="s1", body=b"a")
            globex = await post(app, "/globex", client="s2", body=b"b")
            nobody = await post(app, "/nobody", client="s3", body=b"c")

            # The body says which instance answered: one handler, one path segment, one tenant each.
            assert acme.bodies() == [b"acme:1/1:a\n", b""]
            assert globex.bodies() == [b"globex:1/1:b\n", b""]
            assert nobody.status == 404
            assert nobody.bodies() == [b"no such tenant"]
            # Two renderers, one tally: what is per tenant stays per tenant, what is shared stays shared.
            assert tally.written == 2

        _ = await serve(app, during)

    run(main())


def test_a_session_lives_across_requests_until_it_is_gone() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            tally = app.tally()
            first = await post(app, "/acme", client="s1", body=b"a")
            again = await post(app, "/acme", client="s1", body=b"b")
            other = await post(app, "/acme", client="s2", body=b"c")
            bye = await post(app, "/acme/bye", client="s1")
            after = await post(app, "/acme", client="s1", body=b"d")

            # The session is the client's: the same one across requests, its own for another client.
            assert first.bodies() == [b"acme:1/1:a\n", b""]
            assert again.bodies() == [b"acme:1/2:b\n", b""]
            assert other.bodies() == [b"acme:1/1:c\n", b""]
            # Saying goodbye withdraws it, and the next request starts the client over.
            assert bye.status == 204
            assert after.bodies() == [b"acme:1/1:d\n", b""]
            # An idle session is replaced by the next request, on this deployment's rule, not the clock's.
            app.clock = lambda: 10**6
            idle = await post(app, "/acme", client="s2", body=b"e")
            assert idle.bodies() == [b"acme:1/1:e\n", b""]
            assert tally.written == 5

        _ = await serve(app, during)

    run(main())


def test_two_requests_from_one_client_count_into_one_session() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            tally = app.tally()
            # Admitted first, because two requests arriving for a client nobody has admitted both ask
            # to admit it, and the assembly refuses the second rather than queueing it: ordering two
            # callers is the application's, so this one does it before either arrives.
            _ = await app.session("s1")
            left, right = await asyncio.gather(
                post(app, "/acme", client="s1", body=b"one"),
                post(app, "/acme", client="s1", body=b"two"),
            )

            # One session answers both, so the rows are counted between them — in whichever order the
            # loop let the two write.
            assert sorted([left.bodies()[0], right.bodies()[0]]) == [b"acme:1/1:one\n", b"acme:1/2:two\n"]
            session = app.assembly.instance(key_of(Session, "s1"))
            assert isinstance(session, Session)
            assert sorted(session.rows) == ["one", "two"]
            assert tally.written == 2
            assert tally.let_go == 2

        _ = await serve(app, during)

    run(main())


def test_a_client_that_leaves_mid_body_keeps_writing_into_what_nobody_holds() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            tally = app.tally()
            sent: list[Message] = []
            said_bye: list[Conversation] = []

            async def receive() -> Message:
                return {"type": "http.request", "body": b"one two", "more_body": False}

            async def send(message: Message) -> None:
                sent.append(message)
                if len(sent) == 2:  # the first piece of the body: the client goes, in another request
                    said_bye.append(await post(app, "/acme/bye", client="s1"))

            scope: Scope = {"type": "http", "method": "POST", "path": "/acme", "headers": [(b"x-session", b"s1")]}
            await app(scope, receive, send)

            assert [bye.status for bye in said_bye] == [204]
            # The response still finished, on the rendering the call owns and the counts it kept.
            assert [_bytes_of(message["body"]) for message in sent if message["type"] == "http.response.body"] == [
                b"acme:1/1:one\n",
                b"acme:2/2:two\n",
                b"",
            ]
            # Withdrawing a key does not wait for a reader, and a read does not reserve the key: the
            # second word went into a session nothing holds any more, and nobody is told it is gone.
            assert app.assembly.instance(key_of(Session, "s1")) is None
            after = await post(app, "/acme", client="s1", body=b"three")
            assert after.bodies() == [b"acme:1/1:three\n", b""]
            assert tally.written == 3

        _ = await serve(app, during)

    run(main())


def test_a_request_that_cannot_be_answered_is_refused_before_anything_is_opened() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            tally = app.tally()
            refused = await post(app, "/acme", client="s1", body=b"   ")

            assert refused.status == 400
            assert refused.bodies() == [b"nothing to render"]
            # Nothing was opened for a request that could not be answered: no rendering, no client.
            assert tally.let_go == 0
            assert tally.written == 0
            assert len(app.assembly.keys()) == len(app.roots)
            assert refused.letting_go == [0, 0]

        _ = await serve(app, during)

    run(main())


def test_a_client_that_goes_away_mid_body_still_loses_nothing() -> None:
    async def main() -> None:
        app = build()

        async def during() -> None:
            tally = app.tally()
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

            scope: Scope = {"type": "http", "method": "POST", "path": "/acme", "headers": [(b"x-session", b"s1")]}
            with pytest.raises(asyncio.CancelledError):
                await app(scope, receive, send)

            # The response never finished, and the rendering was still given up: a teardown that has
            # to be remembered on the way out is the leak this scope exists to make impossible.
            assert tally.let_go == 1
            assert tally.written == 1
            assert tally.flushed == 0
            assert tally.swept == 0

        _ = await serve(app, during)

    run(main())
