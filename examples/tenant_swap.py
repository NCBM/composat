"""One account's credential swapped under a running endpoint, and a tenant taken out of service.

The deployment addresses two levels: an endpoint is ``{account}/{endpoint}``, and the credential that
answers completions is the account's — ``{account}/{credential}`` — so every endpoint of one account
reads one instance. One policy says both. ``answering_from_the_root`` answers a requirement at the
asker's own qualifier, then wherever ``below`` derives, then at the root; ``joined(routing,
ancestors)`` puts the deployment's routing table and the address itself in that single slot.

The walk below is three things an application does: a swap under a key that is serving, the read that
lands inside the swap's window, and a tenant taken out of service without being thrown away. Each step
asserts what the reader is promised, so the assertions are the explanation; ``test_tenant_swap.py``
beside it drives ``main()`` in the suite.
"""

from __future__ import annotations

import asyncio
from typing import override

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    ComponentState,
    Declaration,
    ServiceNotUpError,
    require,
)
from composat.std.root import ancestors, answering_from_the_root, joined
from composat.std.wait import serving

ACCOUNT = "acme"
ENDPOINT = f"{ACCOUNT}/one"


class Ledger(Component):
    """The process-wide record: one instance for every account, which is what ``only=`` is for."""

    entries: list[str]

    def __init__(self) -> None:
        super().__init__()
        self.entries = []

    def record(self, said: str) -> None:
        self.entries.append(said)


class Credential(Component):
    """A model credential: one generation behind one key.

    ``drain`` is what a swap waits on. The pool closes in ``unload``, so everything already accepted
    has to finish first — and a generation whose drain is slow is a swap window that is long. Here the
    two gates are the example's, so the window can be held open while a request arrives in it.
    """

    name: str
    holds: bool
    asked_to_drain: asyncio.Event
    finished: asyncio.Event
    closed: bool

    def __init__(self, name: str, *, holds: bool = False) -> None:
        super().__init__()
        self.name = name
        self.holds = holds
        self.asked_to_drain = asyncio.Event()
        self.finished = asyncio.Event()
        self.closed = False

    @override
    async def drain(self) -> None:
        # Only the generation the example holds open waits: this is where a real one would wait for
        # the work it already accepted, so it is also where the swap's window is as long as it is.
        if not self.holds:
            return
        self.asked_to_drain.set()
        _ = await self.finished.wait()

    @override
    async def unload(self) -> None:
        self.closed = True


class Endpoint(Component):
    """One endpoint of one account: it reads the account's credential and the shared ledger."""

    credential: Credential = require()
    ledger: Ledger = require()

    def complete(self, prompt: str) -> str:
        # The read comes first: a credential that is not serving refuses the work before it is done,
        # rather than being found out half way through.
        name = self.credential.name
        self.ledger.record(prompt)
        return f"{name}:{prompt}"


async def main() -> None:
    """Bring the tenant up, swap its credential, take it out of service, and stop the process."""
    registry = ComponentRegistry()
    up: dict[ComponentKey, Component] = {}
    routed: dict[str, str] = {ACCOUNT: "credential-a"}

    def routing(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
        """The credential this deployment picked, and no opinion about any other identity."""
        if identity != ComponentIdentity.of(Credential):
            return ()
        account, _, _endpoint = key.instance.partition("/")
        chosen = routed.get(account)
        return () if chosen is None else (ComponentKey(identity, f"{account}/{chosen}"),)

    rule = answering_from_the_root(registry, up, only={Ledger}, below=joined(routing, ancestors))
    assembly = Assembly(registry, up=up, rule=rule)

    endpoint_key = ComponentKey(ComponentIdentity.of(Endpoint), ENDPOINT)
    credential_key = ComponentKey(ComponentIdentity.of(Credential), f"{ACCOUNT}/credential-a")
    ledger_key = ComponentKey(ComponentIdentity.of(Ledger))

    _ = registry.declare_all(
        (
            Declaration(Ledger),
            Declaration(Credential, instance=f"{ACCOUNT}/credential-a", args=("a",), kwargs={"holds": True}),
            Declaration(Endpoint, instance=ENDPOINT),
        )
    )

    # The endpoint is the only root. The credential and the ledger are not named: a batch derives what
    # a listed key declares, at the first place the rule names, so they come up with it.
    started = await assembly.start(endpoint_key)
    assert started.ok, started.failed
    endpoint = assembly.instance(endpoint_key)
    assert isinstance(endpoint, Endpoint)
    assert endpoint.complete("hello") == "a:hello"

    # A swap: a new generation behind the key that is serving, declared the same way.
    retired = endpoint.credential
    assert isinstance(retired, Credential)
    swapping = asyncio.create_task(
        assembly.replace(credential_key, Declaration(Credential, instance=f"{ACCOUNT}/credential-a", args=("b",)))
    )
    _ = await retired.asked_to_drain.wait()

    # The window: the key answers a generation that is built and not serving, because the move that
    # would publish it is still running.
    assert assembly.in_transit(credential_key) is not None
    refused: ServiceNotUpError | None = None
    try:
        _ = endpoint.complete("during the swap")
    except ServiceNotUpError as caught:
        refused = caught
    assert refused is not None, "the key answered with a generation that was not serving"
    assert refused.key == credential_key
    assert refused.level is ComponentState.LOADED

    # So the request waits the window out instead of retrying: `serving` subscribes first and reads
    # second, which is what makes a move that lands between the two visible either way.
    waiting = asyncio.create_task(serving(assembly, credential_key))
    await asyncio.sleep(0)
    retired.finished.set()
    published = await waiting
    assert isinstance(published, Credential)
    assert published.name == "b"
    swapped = await swapping
    assert isinstance(swapped, Credential)
    assert retired.closed
    assert endpoint.complete("after the swap") == "b:after the swap"

    # Out of service and still built: `quiesce` is the move to LOADED, and it names the keys.
    _ = await assembly.quiesce(credential_key)
    resting = assembly.instance(credential_key)
    assert isinstance(resting, Credential) and resting.state.state is ComponentState.LOADED
    stopped: ServiceNotUpError | None = None
    try:
        _ = endpoint.complete("while quiesced")
    except ServiceNotUpError as caught:
        stopped = caught
    # The same read, the opposite answer: this key rests below serving and nothing is moving it, so
    # it is refused at once rather than waited out to a deadline.
    assert stopped is not None, "the endpoint answered with a credential that is out of service"
    assert stopped.key == credential_key
    assert stopped.level is ComponentState.LOADED
    assert assembly.in_transit(stopped.key) is None

    _ = await assembly.enable(credential_key)
    assert endpoint.complete("back in service") == "b:back in service"

    # What the requests left behind, read while the key that holds the ledger is still up: a read is
    # answered from the table as it is now, so this is the last moment it can be asked.
    assert endpoint.ledger.entries == ["hello", "after the swap", "back in service"]

    # The process goes: the table down in the registry's order, then the declarations withdrawn.
    _ = await assembly.stop(*assembly.keys())
    assert assembly.keys() == ()
    for key in (endpoint_key, credential_key, ledger_key):
        _ = await assembly.evict(key)
    assert not any(key in registry for key in (endpoint_key, credential_key, ledger_key))
