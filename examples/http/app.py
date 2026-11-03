"""An HTTP deployment: a request is a call, a client is a session, a tenant is a qualifier.

Four lifetimes, and one line each — which is the reason they are in one deployment rather than four:

* the **process** is the assembly: ``start`` and ``stop`` are the lifespan, and what is declared at
  the empty qualifier is one instance for everything the process runs (``Tally``);
* a **tenant** is a qualifier: ``Renderer`` is declared once per tenant, so a request is served by the
  instance its path names;
* a **session** is a qualifier on another identity: ``Session`` is admitted the first time a client
  arrives, and withdrawn when the client says so or when this deployment decides it has gone idle;
* a **request** is a call: ``open_call`` wraps the response, and the ``Rendering`` it owns is closed
  once the last byte has gone out.

That a tenant's instance can read the process-wide one is exactly one line, where the assembly is
built: ``answering_from_the_root(registry, up, only={Tally})``. Nothing else crosses that axis, and
nothing crosses the other way — which is why a session holds state and needs nobody, so a client can
be admitted the moment it arrives. Work that outlives a response is the *instance's* (``effect_scope``
in :meth:`Renderer.persist`), not the call's.

The transport is ASGI, written out by hand: no router, no middleware, no framework. A refusal is
decided before the first byte goes out, because after that the status has been sent and there is no
putting it back. Everything a web framework would add — routes, auth, a response object, a retry on
the streaming path — is the application's own business and is left out on purpose.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping, Sequence
from typing import cast, final

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
from composat.std.root import answering_from_the_root

type Message = MutableMapping[str, object]
"""One ASGI message, as the spec says it: a mapping with a ``type`` and the fields that go with it."""

type Scope = MutableMapping[str, object]
type Headers = Sequence[tuple[bytes, bytes]]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]

TENANTS = ("acme", "globex")
"""The tenants this deployment runs: each one is a qualifier, and each one is declared at start-up."""


class Tally(Component):
    """One for the process: the rows every request rendered, and what became of the work it started."""

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


class Rendering:
    """What one request owns: the rows it rendered, and a close the tally can see."""

    tally: Tally
    rows: list[str]

    def __init__(self, tally: Tally) -> None:
        self.tally = tally
        self.rows = []

    def close(self) -> None:
        """Give the rendering up: called once, by whatever opened it, when the request is over."""
        self.tally.let_go += 1


class Session(Component):
    """One for a client: what its requests added between them, and when it was last seen.

    It asks for nothing, which is what makes it possible to run one per client: a session's address is
    its own qualifier, so a requirement read from here would be looked up at that qualifier too.
    """

    rows: list[str]
    touched_at: float

    def __init__(self) -> None:
        super().__init__()
        self.rows = []
        self.touched_at = 0.0

    def touch(self, now: float) -> None:
        """Say the client is still here: the reading the deployment's idle rule is made of."""
        self.touched_at = now

    def add(self, row: str) -> None:
        self.rows.append(row)


class Renderer(Component):
    """One for a tenant: what a request is rendered by, in that tenant's own terms."""

    tally: Tally = require()

    delay: float = 0.05

    def tenant(self) -> str:
        """Whose instance this is, read off the address it answers at."""
        if self.binding is None:  # pragma: no cover - a renderer driven outside an assembly has no address
            raise RuntimeError("the renderer is not attached to a key")
        return self.binding.key.instance

    def begin(self) -> Rendering:
        """What this request will hold until its response is finished."""
        return Rendering(self.tally)

    def prepare(self, text: str) -> None:
        """Refuse a request that cannot be answered: before the first byte, and before anything is opened."""
        if not text.strip():
            raise ValueError("nothing to render")

    async def render(self, rendering: Rendering, session: Session, text: str) -> AsyncIterator[bytes]:
        """Answer the request one piece at a time, in the tenant's row and the client's session.

        The tally is a requirement of a *tenant* instance, so it is answered by the process-wide
        instance through the rule this deployment installed — a read that would be a miss under the
        framework's own rule, which looks at the asker's qualifier and no further.
        """
        for word in text.split():
            rendering.rows.append(word)
            session.add(word)
            self.tally.written += 1
            yield f"{self.tenant()}:{len(rendering.rows)}/{len(session.rows)}:{word}\n".encode()
            await asyncio.sleep(0)

    def persist(self, rendering: Rendering) -> None:
        """Write the rows down where the next request will find them, off this request's path.

        The job belongs to the *instance*, not to the call: the response is long gone by the time it
        finishes, so registering it on the call would cancel it at the end of the body. ``effect_scope``
        is the lifetime that matches — cancelling the task is what unloading the instance does to it.
        """
        task = asyncio.create_task(self._flush(rendering))
        _ = self.effect_scope.put(task.cancel)

    async def _flush(self, rendering: Rendering) -> None:
        try:
            await asyncio.sleep(self.delay)  # where the write itself would go
            self.tally.flushed += len(rendering.rows)
        except asyncio.CancelledError:
            # Taken down mid-write: what the request read is recorded as swept rather than written.
            self.tally.swept += len(rendering.rows)
            raise


def key_of(cls: type[Component], instance: str = "") -> ComponentKey:
    """The key ``cls`` answers at under ``instance``: an identity plus the qualifier."""
    return ComponentKey(ComponentIdentity.of(cls), instance)


@final
class Application:
    """The process: the assembly, the tenants it declared, and the verbs whose time is the process's.

    Admitting a client is here rather than in a component because it *is* an assembly verb, and a
    component never drives one. What the components own is their own epoch; what the application owns
    is which keys exist.
    """

    assembly: Assembly
    tenants: tuple[str, ...]
    roots: tuple[ComponentKey, ...]
    ttl: float
    clock: Callable[[], float]

    def __init__(
        self,
        assembly: Assembly,
        tenants: tuple[str, ...],
        /,
        *,
        ttl: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.assembly = assembly
        self.tenants = tenants
        self.ttl = ttl
        self.clock = clock
        self.roots = (key_of(Tally), *(key_of(Renderer, tenant) for tenant in tenants))

    async def start(self) -> None:
        """Bring up the process and its tenants: what the lifespan's startup is."""
        _ = await self.assembly.start(*self.roots)

    async def stop(self) -> None:
        """Take down everything that is up, the clients admitted on the way included."""
        _ = await self.assembly.stop(*self.assembly.keys())

    def tally(self) -> Tally:
        """The instance every request shares: what outlived all of them, and what survived them."""
        instance = self.assembly.instance(key_of(Tally))
        if not isinstance(instance, Tally):
            raise RuntimeError("nothing is up under the tally")
        return instance

    def renderer(self, tenant: str) -> Renderer | None:
        """The tenant's instance, or ``None`` when this deployment does not run that tenant."""
        instance = self.assembly.instance(key_of(Renderer, tenant))
        return instance if isinstance(instance, Renderer) else None

    async def session(self, client: str) -> Session:
        """The client's session: admitted on arrival, reused after that, replaced once it has idled."""
        key = key_of(Session, client)
        now = self.clock()
        held = self.assembly.instance(key)
        if isinstance(held, Session):
            if now - held.touched_at > self.ttl:
                _ = await self.assembly.evict(key)
            else:
                held.touch(now)
                return held
        result = await self.assembly.admit(Declaration(Session, instance=client))
        admitted = self.assembly.instance(key)
        if not isinstance(admitted, Session):
            raise RuntimeError(f"the client {client} was not admitted: {result.failed}")
        admitted.touch(now)
        return admitted

    async def bye(self, client: str) -> bool:
        """Withdraw what the client held; whether there was anything to withdraw."""
        key = key_of(Session, client)
        if self.assembly.instance(key) is None:
            return False
        _ = await self.assembly.evict(key)
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        match scope.get("type"):
            case "lifespan":
                await self._survive(receive, send)
            case "http":
                await self._answer(scope, receive, send)
            case _:
                # A scope this deployment has nothing to say about — websockets, and whatever a server
                # grows next. Silence is the whole answer.
                return

    async def _survive(self, receive: Receive, send: Send) -> None:
        """Start the deployment, and stop it when the server says to."""
        while True:
            match (await receive()).get("type"):
                case "lifespan.startup":
                    await self.start()
                    await send({"type": "lifespan.startup.complete"})
                case "lifespan.shutdown":
                    await self.stop()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
                case _:
                    # A lifespan message with nothing for this deployment to do: wait for the one that
                    # ends it.
                    pass

    async def _answer(self, scope: Scope, receive: Receive, send: Send) -> None:
        """One request: the tenant from the path, the client from a header, the body from the request."""
        tenant, action = _route_of(scope)
        client = _headers_of(scope).get("x-session", "")
        renderer = self.renderer(tenant)
        if renderer is None:
            await self._reply(send, 404, b"no such tenant")
            return
        if not client:
            await self._reply(send, 400, b"no session")
            return
        if action == "bye":
            await self._reply(send, 204 if await self.bye(client) else 404, b"")
            return
        if action:
            await self._reply(send, 404, b"no such page")
            return

        # Everything that can refuse a request is decided here, while a refusal is still a status:
        # after the first byte it would be a truncation instead.
        text = _body_of(await receive()).decode("utf-8", "replace")
        try:
            renderer.prepare(text)
        except ValueError as refusal:
            await self._reply(send, 400, str(refusal).encode())
            return

        session = await self.session(client)

        async with open_call(key_of(Renderer, tenant), self.assembly.rule) as call:
            rendering = renderer.begin()
            _ = call.put(rendering.close)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            async for chunk in renderer.render(rendering, session, text):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        renderer.persist(rendering)

    async def _reply(self, send: Send, status: int, body: bytes) -> None:
        """Answer with a status and a body: the two messages an ASGI response is."""
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": body, "more_body": False})


def _route_of(scope: Scope) -> tuple[str, str]:
    """The tenant a request names and the page it asks for: the first two segments of the path."""
    parts = [part for part in str(scope.get("path", "")).split("/") if part]
    tenant = parts[0] if parts else ""
    return tenant, parts[1] if len(parts) > 1 else ""


def _headers_of(scope: Scope) -> dict[str, str]:
    """The request headers as text, which the protocol says are pairs of byte strings."""
    pairs = cast("Headers", scope.get("headers") or ())
    return {name.decode().lower(): value.decode() for name, value in pairs}


def _body_of(message: Message) -> bytes:
    """The bytes an ``http.request`` message carried, or nothing when it carried none."""
    body = message.get("body")
    return body if isinstance(body, bytes) else b""


def build() -> Application:
    """Declare the process and its tenants, and say which identities answer for every one of them.

    ``only={Tally}`` is the whole of the crossing: a tenant's renderer reads the process-wide tally,
    and everything else — a session, another tenant's instance — is as far away as it was.
    """
    registry = ComponentRegistry()
    up: dict[ComponentKey, Component] = {}
    rule = answering_from_the_root(registry, up, only={Tally})
    _ = registry.declare_all((Declaration(Tally), *(Declaration(Renderer, instance=tenant) for tenant in TENANTS)))
    return Application(Assembly(registry, up=up, rule=rule), TENANTS)
