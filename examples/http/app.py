"""An HTTP deployment: one request is one call, and the call lasts as long as the response does.

The transport is ASGI, written out by hand, because that is all this deployment needs from it — no
router, no middleware, no framework. What is worth seeing is where the two composat calls sit.
``assembly.start`` and ``assembly.stop`` are the *lifespan*: the process is up while the server is.
And ``open_call`` wraps **the response**, not the handler: a body is produced a piece at a time, after
the handler's own work is finished, so a scope closed when the handler returned would be closed while
the response is still being written.

Two corollaries the cases below pin down. A refusal is decided before the first byte goes out, because
after that the status has been sent and there is no putting it back. And work that outlives the
response is not the call's — a task started inside the call would be cancelled the moment the body
ended — so it is registered on the *instance's* epoch (``effect_scope``), which is the lifetime that
matches it.

What is deliberately absent is everything a web framework would add: routes, middleware, auth, a
response object, a retry on the streaming path. Those are the application's, and each of them would be
a second thing this example is about.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import final

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Declaration,
    require,
)
from composat.context import open_call

type Message = MutableMapping[str, object]
"""One ASGI message, as the spec says it: a mapping with a ``type`` and the fields that go with it."""

type Scope = MutableMapping[str, object]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]


class Store(Component):
    """What outlives every request: the rows it rendered, and what became of the work it started."""

    written: int = 0
    let_go: int = 0
    flushed: int = 0
    swept: int = 0

    def __init__(self) -> None:
        super().__init__()
        self.written = 0
        self.let_go = 0
        self.flushed = 0
        self.swept = 0


class Cart:
    """What one request owns: the rows it rendered, and a close the store can see."""

    store: Store
    rows: list[str]

    def __init__(self, store: Store) -> None:
        self.store = store
        self.rows = []

    def close(self) -> None:
        """Give the cart up: called once, by whatever opened it, when the request is over."""
        self.store.let_go += 1


class Page(Component):
    """The work a request does: one page, produced a piece at a time."""

    store: Store = require()

    delay: float = 0.05

    def open(self) -> Cart:
        """What this request will hold until its response is finished."""
        return Cart(self.store)

    def prepare(self, text: str) -> None:
        """Refuse a request that cannot be answered, before the first byte of the response."""
        if not text.strip():
            raise ValueError("nothing to render")

    async def render(self, cart: Cart, text: str) -> AsyncIterator[bytes]:
        """Answer the request one piece at a time, reading the store again for every piece.

        The read is a requirement read like any other, so it is answered from the call the response is
        running inside: a scope closed under it would make this a refusal rather than a second chunk.
        """
        for word in text.split():
            cart.rows.append(word)
            self.store.written += 1
            yield f"{len(cart.rows)}:{word}\n".encode()
            await asyncio.sleep(0)

    def persist(self, cart: Cart) -> None:
        """Write the rows down where the next request will find them, off this request's path.

        The job belongs to the *instance*, not to the call: the response is long gone by the time it
        finishes, so registering it on the call would cancel it at the end of the body. ``effect_scope``
        is the lifetime that matches — cancelling the task is what unloading the instance does to it.
        """
        task = asyncio.create_task(self._flush(cart))
        _ = self.effect_scope.put(task.cancel)

    async def _flush(self, cart: Cart) -> None:
        try:
            await asyncio.sleep(self.delay)  # where the write itself would go
            self.store.flushed += len(cart.rows)
        except asyncio.CancelledError:
            # Taken down mid-write: what the request read is recorded as swept rather than written.
            self.store.swept += len(cart.rows)
            raise


@final
class Deployment:
    """The ASGI application: a lifespan for the process, and a call for each response.

    Two protocol branches and no more, which is also the honest size of what a deployment owes the
    framework: a place to start and stop, and a place to say whose work a request is.
    """

    assembly: Assembly
    page: ComponentKey
    store: ComponentKey

    def __init__(self, assembly: Assembly) -> None:
        self.assembly = assembly
        self.page = ComponentKey(ComponentIdentity.of(Page))
        self.store = ComponentKey(ComponentIdentity.of(Store))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        match scope.get("type"):
            case "lifespan":
                await self._survive(receive, send)
            case "http":
                await self._answer(receive, send)
            case _:
                # A scope this deployment has nothing to say about — websockets, and whatever a server
                # grows next. Silence is the whole answer.
                return

    def held(self) -> Store:
        """The instance every request shares: what outlived all of them."""
        instance = self.assembly.instance(self.store)
        if not isinstance(instance, Store):
            raise RuntimeError(f"nothing is up under {self.store}")
        return instance

    async def _survive(self, receive: Receive, send: Send) -> None:
        """Start the deployment, and stop it when the server says to."""
        while True:
            match (await receive()).get("type"):
                case "lifespan.startup":
                    _ = await self.assembly.start(self.page)
                    await send({"type": "lifespan.startup.complete"})
                case "lifespan.shutdown":
                    _ = await self.assembly.stop(*self.assembly.keys())
                    await send({"type": "lifespan.shutdown.complete"})
                    return
                case _:
                    # A lifespan message with nothing for this deployment to do: wait for the one that
                    # ends it.
                    pass

    async def _answer(self, receive: Receive, send: Send) -> None:
        """One request: the call is opened around the response, and the response is what it holds."""
        page = self.assembly.instance(self.page)
        if not isinstance(page, Page):
            raise RuntimeError(f"nothing is up under {self.page}")
        text = _body_of(await receive()).decode("utf-8", "replace")

        async with open_call(self.page, self.assembly.rule) as call:
            cart = page.open()
            _ = call.put(cart.close)
            try:
                page.prepare(text)
            except ValueError as refusal:
                # Nothing has been sent yet, so the refusal is still a status rather than a truncation.
                await self._reply(send, 400, str(refusal).encode())
                return
            await send({"type": "http.response.start", "status": 200, "headers": []})
            async for chunk in page.render(cart, text):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        page.persist(cart)

    async def _reply(self, send: Send, status: int, body: bytes) -> None:
        """Answer with a status and a body: the two messages an ASGI response is."""
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": body, "more_body": False})


def _body_of(message: Message) -> bytes:
    """The bytes an ``http.request`` message carried, or nothing when it carried none."""
    body = message.get("body")
    return body if isinstance(body, bytes) else b""


def build() -> Deployment:
    registry = ComponentRegistry()
    _ = registry.declare_all((Declaration(Store), Declaration(Page)))
    return Deployment(Assembly(registry, up={}))
