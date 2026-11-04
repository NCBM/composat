"""The instances this process has up, the rule a requirement is answered by, and what a batch reports.

Driving a component through its states is the manager's job, and deciding what is declared is the
registry's; what is here is the table the two meet in — one instance per key — the answer a declared
requirement reads out of it, and the verbs that fill the table and empty it.

Two readings run through every verb. A batch that comes up short is *reported*
(:class:`AssemblyResult`), because one tenant's broken configuration stays that tenant's problem;
what is wrong with the call itself — a key nothing declares, a key listed twice, a key already up —
is raised before the first instance exists. And a verb speaks for the keys it names, and for the ones
it derived, until it is done with them, so no second call moves one of them: the claim is one region
with no ``await`` in it (:meth:`Assembly._claim`), which the event loop already refuses to interleave,
so nothing here is locked and tenants that have nothing to do with each other go on running side by
side.

The rule is a function of the table rather than a method of the thing that fills it
(:func:`answering_from`), and that is a reading rather than a style. A requirement is answered from
whatever is up *now*, so the rule has to see the table as it fills; taking the table as its argument
is what lets a rule be built before there is anything to answer with, and what lets a deployment say
*where* a requirement may be found without owning the rest of the answer::

    up: dict[ComponentKey, Component] = {}
    rule = answering_from(registry, up, some_candidates)
    assembly = Assembly(registry, up=up, rule=rule)

:func:`default_rule` is that mechanism over the narrowest list there is — the asker's own qualifier —
and ``composat.std.root`` is another list over the same mechanism. So an assembly does not own a rule
slot to assign into — it is handed one — and the table it was handed is the table it fills: ``up`` is
mutated, never rebound, or the rule would go on reading a table nothing writes to.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import final

from .component import Component, RequirementResolver, attach, requirements_of
from .errors import KeyInTransitError, ServiceNotUpError
from .identity import ComponentIdentity, ComponentKey
from .manager import ComponentManager
from .registry import ComponentRegistry, Declaration
from .scope import Reclaimable
from .spec import RequirementSpec
from .state import HELD, READABLE, Committed, ComponentState, Operation, Unsubscribe


@final
@dataclass(frozen=True, slots=True)
class Event:
    """One move, as this assembly committed it: which key, which instance, and the two states.

    ``instance`` is the generation, and that is why it is carried. A swap leaves the retired
    instance's teardown arriving at the same key it left, so ``assembly.instance(event.key) is
    event.instance`` is the whole reading of "is this still the one the key answers with" — a number
    would be a second table to look it up in.

    ``undone`` is the move that did not hold: nothing else about the record distinguishes a rollback,
    and the states say which way it went.
    """

    key: ComponentKey
    instance: Component
    operation: Operation
    origin: ComponentState
    arrival: ComponentState
    undone: bool = False


type Observer = Callable[[Event], None]
"""What an assembly calls for each event: the record, once, in subscription order, between moves."""

_STAGED: contextvars.ContextVar[list[Event] | None] = contextvars.ContextVar("composat_staged_events", default=None)
"""What the call now running has staged and not yet delivered.

A ``ContextVar`` rather than an attribute, so two batches running at once hand out their own events
and report their own failures: a task inherits a copy of the context, and the copy a verb makes is
made when the verb starts. ``None`` outside a verb — a move this assembly did not order is not its
to report, and an instance driven straight through the manager is exactly that case.
"""


@final
@dataclass(frozen=True, slots=True)
class AssemblyResult:
    """What a batch did: the keys it carried through, the ones that failed, and what an observer said.

    A key the batch left alone is in neither tuple — stopping a key that was never up has an empty
    report about it, and a batch told to do nothing reports exactly that.

    ``observer_failures`` is deliberately not ``failed``: that map is the errors of hooks, keyed by
    the component they belong to, and a handler that could not write its line down is not a component
    that failed. Each entry is the event it was told about and what it said, so a sink can be blamed
    for exactly what it saw — and ``ok`` stays a reading about the batch rather than about the
    reporting, because a batch whose keys all came up did come up.

    A key that failed is in ``failed`` and in neither of the other two, and the moves it made on the
    way are still in the event stream: a step a hook took back is the one reported with ``undone``
    set, where ``origin`` is where the instance ended up. A projection that reads ``arrival`` without
    looking at ``undone`` writes a state the instance never rested in.
    """

    completed: tuple[ComponentKey, ...] = ()
    failed: Mapping[ComponentKey, BaseException] = field(default_factory=dict[ComponentKey, BaseException])
    observer_failures: tuple[tuple[Event, BaseException], ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every key the batch touched went through."""
        return not self.failed


@final
@dataclass(frozen=True, slots=True)
class Replacement:
    """What a swap left behind: the instance the key answers with, and what went wrong on the way.

    The two are reported together because a swap has outcomes that are not each other's negation:
    ``failed`` being non-empty does *not* mean the replacement did not land. The key answers with
    ``instance`` either way — the old instance is gone by the time this is handed back — and what a
    dependant's ``provider_changed`` did about being told is a separate fact.

    ``failed`` is keyed by whoever complained: a dependant whose ``provider_changed`` raised, and the
    replaced key itself when the instance that was taken out would not let go. One hook that raises is
    not a reason for the others to stay uninformed, so the map is what all of them said. Those two
    cannot meet in the map, because a dependant is never the key that was swapped.

    ``published`` is a third fact, and it is held apart rather than keyed by the same key: the new
    generation's ``enable`` refused, so the key answers an instance that is built and not serving.
    Folding that into ``failed`` would lose one of the two errors whenever the old instance also
    refused to let go, and the two want different things done about them — a leak to look at, versus
    a generation to try putting into service again.

    ``observer_failures`` carries what an observer said, for the same reason a batch carries it: a
    swap that landed with the reporting broken is neither a clean swap nor a failed one, and the
    caller has nowhere else to read it from.
    """

    instance: Component
    failed: Mapping[ComponentKey, BaseException] = field(default_factory=dict[ComponentKey, BaseException])
    published: BaseException | None = None
    observer_failures: tuple[tuple[Event, BaseException], ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the swap landed, the old generation let go, and the new one is serving."""
        return not self.failed and self.published is None


type Candidates = Callable[[ComponentKey, ComponentIdentity], Iterable[ComponentKey]]
"""Where a requirement may be looked up, given the asker and the identity it names.

The asker's own qualifier first is not something this shape can enforce, and it is the one thing
every policy has to keep: a list that starts anywhere else answers a caller from another instance
while its own is up. Everything after that first place is what a deployment is choosing.

A qualifier with two levels in it — an account and one of its endpoints — is two levels only to the
deployment that wrote it, so the places come out of its own function::

    def places(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
        account, _, _endpoint = key.instance.partition("/")
        return (ComponentKey(identity, key.instance), ComponentKey(identity, account))

Every place is a key of ``identity``: one of another identity is refused, because the instance it
names is not what the annotation asks for. And this decides reads only — ``Assembly.start`` derives
each requirement at the qualifier of the key that needs it and consults no rule, so a place that is
not the asker's own has to be named in the batch as well.
"""


def answering_from(
    registry: ComponentRegistry,
    instances: Mapping[ComponentKey, Component],
    candidates: Candidates,
    /,
) -> RequirementResolver:
    """The framework's answer, over the places ``candidates`` says a requirement may be found.

    Built from two things it does not own — the registry says which identity a contract names, the
    table says which instances are up — and neither is read at build time, so the closure goes on
    answering from the table as it fills: a rule made before anything is up is still the rule in
    force once something is.

    The list is looked through in order and the first place with something up answers. Where nothing
    is found, the *first* place is what the refusal names: that is where the caller's own qualifier
    put the requirement, and pointing at a further stop would say the caller asked for that one.

    A level is part of the answer, and whose level it is, is the caller's. An instance that is serving
    needs a provider that is serving too; one that is loading or unloading is preparing or letting go
    and reaches whatever the assembly holds (:data:`~composat.state.READABLE`). So the table is read
    twice — once for the provider, once for the asker — and a caller the table does not place is not
    policed at all: the assembly cannot say such a caller is doing work, and refusing on a guess would
    be worse than answering with something it does hold. Both refusals say which they were, and who
    asked: an instance that is up and not serving is not the same incident as a hole in the table. The
    policing is the caller's whichever place answered, so a wider list cannot loosen it.

    A ``RequirementSpec`` is accepted as the whole requirement rather than as its service, so an
    annotation that carries extras arrives intact; this rule has no vocabulary to read them with, and
    resolving by key alone is what "the requirement says nothing beyond its service" means.

    ``effects`` is where a resolver hands back something shorter-lived than the assembly, and this
    one registers nothing into it: an instance the assembly brought up is not the caller's resource,
    and releasing one from a call's scope would take it down under everyone else.
    """

    def resolve(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable, /) -> object:
        _ = effects
        identity = registry.provider_identity(requirement.service)
        places = tuple(candidates(key, identity))
        for place in places:
            if place.identity != identity:
                raise ValueError(f"{place} is not an instance of {identity}")
        found = next(((place, instances[place]) for place in places if place in instances), None)
        if found is None:
            raise ServiceNotUpError(places[0] if places else ComponentKey(identity, key.instance), asked_from=key)
        provider, instance = found
        asker = instances.get(key)
        readable = None if asker is None else READABLE.get(asker.state.state)
        level = instance.state.state
        if readable is not None and level not in readable:
            raise ServiceNotUpError(provider, asked_from=key, level=level)
        return instance

    return resolve


def _own_qualifier(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
    """The one place :func:`default_rule` looks: the asker's own qualifier, and nothing past it."""
    return (ComponentKey(identity, key.instance),)


def default_rule(
    registry: ComponentRegistry,
    instances: Mapping[ComponentKey, Component],
) -> RequirementResolver:
    """The framework's own answer: what is up under the asker's qualifier, or a refusal.

    The qualifier is the whole lookup. Instances of one identity are told apart by it, so a caller
    served by the provider at ``tenant-a`` reads its requirements from ``tenant-a`` as well; a slot
    that is declared and is not up is a miss (``ServiceNotUpError``), never a reason to answer from
    another instance, which would be answering a question the caller did not ask.

    A deployment that wants a second place — one instance at the root answering for every qualifier,
    or a version pinned per tenant — hands its own candidates to :func:`answering_from`, and keeps
    the level policing and the refusals that are written down there.
    """
    return answering_from(registry, instances, _own_qualifier)


def _in_order(keys: Iterable[ComponentKey], order: Sequence[ComponentIdentity]) -> tuple[ComponentKey, ...]:
    """``keys`` in the order ``order`` gives their identities; the caller's order within one.

    Sorting is stable, so several instances of one identity keep the order they were handed in, and a
    key whose identity has left the order sinks to the end rather than being dropped — a key whose
    declaration was withdrawn on the way is still one this assembly holds.
    """
    rank = {identity: index for index, identity in enumerate(order)}
    fallback = len(rank)
    return tuple(sorted(keys, key=lambda key: rank.get(key.identity, fallback)))


def _level_of(state: ComponentState) -> ComponentState:
    """The level a replacement is *published* at: what the instance it takes over was serving at.

    A drained instance counts as loaded rather than as draining — the replacement is a fresh
    instance, and asking it to finish somebody else's work is asking it to be born halfway down.

    Anything below ``ENABLED`` is reached by the move that builds the generation, so what this
    decides is not only where a swap ends up but whether it has a publish segment at all.
    """
    return ComponentState.ENABLED if state is ComponentState.ENABLED else ComponentState.LOADED


@final
class Assembly:
    """The instances this process has up, the rule they reach each other by, and the two batch verbs.

    One instance per key: an identity may have several (a tenant each), and each answers at its own
    qualifier. :meth:`start` fills the table against the registry's order and :meth:`stop` empties it
    against the reverse. :meth:`migrate` moves the keys already in it between the levels an instance
    rests at, and :meth:`enable` and :meth:`quiesce` are the two of those a deployment names.
    :meth:`replace` puts a new generation behind a key, and :meth:`admit` and :meth:`evict` are what
    changes the declarations the table is filled from.

    Which instance answers a requirement is not decided here but in the rule this assembly was handed,
    and that rule reads this same table: an instance that came up a moment ago is what the next field
    read gets, with nothing refreshed.

    What it does to the table is watched as well as reported: :meth:`observe` subscribes to the
    stream, and every event names the key, the generation, and the two states the move spans.
    """

    _registry: ComponentRegistry
    _manager: ComponentManager
    _up: dict[ComponentKey, Component]
    _moving: dict[ComponentKey, str]
    _rule: RequirementResolver
    _subscribers: list[Observer]

    def __init__(
        self,
        registry: ComponentRegistry,
        up: dict[ComponentKey, Component] | None = None,
        rule: RequirementResolver | None = None,
        manager: ComponentManager | None = None,
    ) -> None:
        """Hold ``registry``'s instances in ``up``, answered by ``rule`` or by the default rule.

        ``up`` is the table this assembly fills, and a caller that passes one gets to build a rule
        over it before the assembly exists (:func:`default_rule`). It is held as given and never
        rebound, so a rule built from it keeps reading what the assembly writes.

        ``manager`` drives the instances this assembly brings up. It is handed in so that a process
        already driving components keeps one set of per-instance locks rather than two.
        """
        self._registry = registry
        self._manager = ComponentManager() if manager is None else manager
        self._up = {} if up is None else up
        self._moving = {}
        self._rule = default_rule(registry, self._up) if rule is None else rule
        self._subscribers = []

    @property
    def rule(self) -> RequirementResolver:
        """The rule in force, which is also what an instance is attached with.

        Reading it back is how an application asks whether its chain was the one installed, and how a
        test asks what a field read is going to go through; there is no setter, because a rule is
        given once and a second one would leave the instances attached earlier pointing at the first.
        One rule serves the whole assembly, so a policy that varies by identity branches inside the
        candidates function, which is handed the identity it is answering for.
        """
        return self._rule

    def instance(self, key: ComponentKey) -> Component | None:
        """The instance up under ``key``, or ``None`` when nothing is.

        This is the reading that answers "what does that key answer *now*", which is a read rather
        than a history: subscribing to a running assembly replays nothing, and the current picture is
        this method together with :meth:`keys`.
        """
        return self._up.get(key)

    def instances(self) -> tuple[Component, ...]:
        """Every instance that is up, in the order it came up."""
        return tuple(self._up.values())

    def keys(self) -> tuple[ComponentKey, ...]:
        """Every key that is up, in the order it came up."""
        return tuple(self._up)

    def declaration(self, key: ComponentKey) -> Declaration:
        """How ``key`` was declared: the class, the qualifier, and what it is built with.

        A read of what the process *declared*, not of what is up — the two are separate facts, and this
        is the one a caller needs to build another instance under the same key: a swap takes a new
        class with the same construction, and a caller that had to keep its own copy of every
        declaration would be a second record that can disagree with the registry's.

        The registry is where declarations live, so the answer is its own (:meth:`ComponentRegistry.
        declaration`); ``KeyError`` when nothing is declared under the key, which is a question about
        the call rather than about what is running.
        """
        return self._registry.declaration(key)

    def observe(self, observer: Observer) -> Unsubscribe:
        """Notice what this assembly does to the keys it holds, as it does it.

        The one place an application subscribes. An instance's own control reports its moves but
        knows no key, so a handler that needs to know *which* key moved — and which generation of it
        — is served here.

        What a handler is handed is what happened, once, in subscription order, and between moves
        rather than inside one: it may therefore block the batch but never a hook. It is a report and
        not a request, so it cannot refuse a move — and it must not drive the assembly either, because
        a handler runs inside the batch it is reporting and a move started from one would be racing
        its own subject. One that has to await has a queue to put the event in: ``put_nowait`` is what
        a handler can do, and the task that drains the queue is the application's to own.

        A handler that raises does not stop the others: the failure is reported to whoever ran the
        batch (:attr:`AssemblyResult.observer_failures`).
        """
        self._subscribers.append(observer)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(observer)

        return unsubscribe

    async def start(self, *keys: ComponentKey, enable: bool = True, strict: bool = False) -> AssemblyResult:
        """Build and bring ``keys`` up, requirements first, and report what happened.

        The batch is these keys closed over what they require (:meth:`_closed`), in the registry's
        order rather than the caller's, so a requirement is up before the instance that reads it.
        ``enable`` is the level: ``False`` leaves the batch ``LOADED``, which is what a deployment that
        gates each key wants — a declaration says how to build a component, and the call says whether
        it should be serving. A requirement that is already up is passed over where it rests: a
        provider an earlier call left ``LOADED`` is not raised by this batch, and a batch that comes up
        serving reads it as a miss.

        A component that fails is left where the failure left it, reported, and the rest still come
        up. ``strict`` asks for the other reading: the keys *this call* brought up are taken back
        down, in reverse, and the original failure is re-raised. A cancelled batch has no result to
        report, so it takes back what it brought up and lets the ``CancelledError`` go on.

        Events are delivered per key, as each one finishes coming up, so a batch that is still running
        is already readable. On the two paths that raise there is no result to carry a handler's
        failure, and it goes the way of the exception.

        The keys are spoken for before the first instance exists and until the last of them is done,
        the ones this call derived included, so a second call that would build one of them is refused
        rather than racing this one (:class:`~composat.errors.KeyInTransitError`).
        """
        batch = _in_order(self._closed(self._require_startable(keys)), self._registry.order())
        self._claim(batch, "being brought up")
        try:
            return await self._bring_up_each(batch, enable=enable, strict=strict)
        finally:
            self._release(batch)

    async def stop(self, *keys: ComponentKey) -> AssemblyResult:
        """Take ``keys`` back down, the instances that need them going first.

        Keys that are not up are passed over — nothing is done to them, so they are in neither tuple of
        the result — and an empty batch reports exactly that, which is what makes ``finally: await
        stop(*assembly.keys())`` the teardown a caller can run without asking first. Ordering comes
        from the registry, and everything else from the instances this assembly holds, so a key whose
        declaration was withdrawn on the way is still taken down.

        Events are delivered per key, as each one finishes going down, so a shutdown that is still
        running is already readable.

        A key another call is still moving is refused rather than passed over
        (:class:`~composat.errors.KeyInTransitError`): it is not "nothing is up" — a batch is building
        it, and tearing it down halfway is the one thing the claim exists to prevent.
        """
        self._claim(keys, "being taken down")
        try:
            return await self._take_down_each(keys)
        finally:
            self._release(keys)

    async def migrate(self, *keys: ComponentKey, to: ComponentState) -> AssemblyResult:
        """Move ``keys`` to the level ``to`` and report what each of them did.

        ``to`` is a level an instance rests at while this assembly holds it (:data:`~composat.state.HELD`),
        never ``UNLOADED``: that one is :meth:`stop`, which also takes the key out of the table, and a
        key left there with an unloaded instance would answer a requirement with something that is not
        serving.

        What the batch does not do is decide which keys it should have been given. Naming a key is
        naming where that thing sits, so a provider left serving under a dependant that was put back
        into service is the caller's instruction rather than an oversight here — the same reading
        :meth:`stop` takes of a dependency it was not told about.

        The direction decides the order, because the order is what makes the level reachable: a batch
        coming up is walked requirements first, so a provider is serving before the dependant that
        reads it is asked to be, and a batch going down the other way, so whatever would hand a
        provider new work stops first. ``DRAINING`` counts as going down — a batch told to finish what
        it accepted is a batch being taken out of service.

        A key already at ``to`` is carried through without a move: the call is about the level the
        batch should be at, and a key already there is that postcondition holding. That is also why a
        batch here has no ``strict``, unlike :meth:`start` — a key that could not be moved rests at
        some true level of its own, and it is reported and left there while the rest are still moved.

        Events arrive per key, as each one finishes moving, so a re-level is visible in a running
        ledger with no second mechanism. A key another call is moving is refused rather than moved
        twice, the direction being part of what the caller is told.
        """
        if to not in HELD:
            raise ValueError(f"{to.name} is not a level a held instance is at: that is stop()")
        batch = _in_order(self._require_held(keys), self._order_for(to))
        self._claim(batch, f"being moved to {to.name}")
        try:
            return await self._move_each(batch, to)
        finally:
            self._release(batch)

    async def enable(self, *keys: ComponentKey) -> AssemblyResult:
        """Put ``keys`` into service: the batch ``migrate(*keys, to=ComponentState.ENABLED)`` names.

        Worth a name because it is the second half of ``start(..., enable=False)``: a deployment that
        builds a tenant before it serves it says so at two call sites, and the one that finishes the
        job should read as the level it means rather than as a state name.
        """
        return await self.migrate(*keys, to=ComponentState.ENABLED)

    async def quiesce(self, *keys: ComponentKey) -> AssemblyResult:
        """Take ``keys`` out of service and leave them built: ``to=ComponentState.LOADED``.

        The multi-tenant verb. What it keeps is what :meth:`stop` gives up — the instance, its caches,
        its pools, whatever its ``load`` registered — so putting the tenant back is an :meth:`enable`
        rather than a build.
        """
        return await self.migrate(*keys, to=ComponentState.LOADED)

    async def admit(self, *declarations: Declaration, enable: bool = True) -> AssemblyResult:
        """Declare what has just arrived and bring it up: one connection, admitted once.

        What it saves a caller from is a declaration and a batch that can come apart —
        :meth:`~composat.registry.ComponentRegistry.declare_all` followed by :meth:`start` leaves a
        declaration behind whenever the batch fails, and nothing would remember to withdraw it. What
        ``enable`` means is :meth:`start`'s.

        Several declarations arrive at once because their edges may only be acyclic together, and the
        batch is checked as a set before any of it is committed. A key already declared is refused;
        a key another call is moving is refused before the registry is touched at all, and one that
        is already spoken for by the time the batch is closed over leaves the declarations standing —
        as a component that failed to come up does.

        A key that did not come up is reported rather than raised, and it stays declared: what clears
        it is :meth:`evict`, and what retries it is :meth:`start`. So a caller that admitted something
        the network then dropped still has one call to make, in each direction.
        """
        keys = tuple(declaration.key for declaration in declarations)
        self._require_idle(keys)
        _ = self._registry.declare_all(declarations)
        return await self.start(*keys, enable=enable)

    async def evict(self, *keys: ComponentKey) -> AssemblyResult:
        """Take ``keys`` down and withdraw what declares them: the other end of :meth:`admit`.

        The report is :meth:`stop`'s, and so is the teardown: a hook that refused to let go is
        reported and the instance is down regardless. The withdrawal follows, because a key still up
        under a declaration that is gone would answer requirements nothing orders anything by.

        The batch is checked whole before any of it moves — every key declared, none named twice, and
        none of them still needed by a declaration that stays put (``DependencyInUseError`` names
        who) — so a batch that would be refused halfway through has taken nothing down.

        A key that was declared and never came up is withdrawn with no teardown in between, which is
        what makes a failed :meth:`admit` cleanable; a key nothing declared is a fact about the call,
        and is refused before anything is.
        """
        seen: set[ComponentKey] = set()
        for key in keys:
            if key in seen:
                raise ValueError(f"key is listed twice in one batch: {key}")
            seen.add(key)
            if key not in self._registry:
                raise KeyError(key)
        self._registry.require_undeclarable(*keys)
        self._claim(keys, "being taken down")
        try:
            result = await self._take_down_each(keys)
            self._registry.undeclare(*keys)
            return result
        finally:
            self._release(keys)

    async def replace(self, key: ComponentKey, declaration: Declaration) -> Component | Replacement:
        """Put a new instance behind ``key``, without a gap in what the key answers.

        ``declaration`` has to name ``key``, and something has to be up under it: the slot is what is
        being replaced, and a declaration for somewhere else would start a second instance where this
        one is meant to take over.

        The declaration moves first, so a generation the registry will not take costs nothing: it is
        refused before an instance is built, and the key keeps answering what it answered.

        The new generation is then built and brought to ``LOADED`` — ``load`` has run, so its fields
        read and its requirements resolve, and nothing it does on ``enable`` has happened — and the key
        moves to it before the old generation is torn down: a key that answered nothing while the old
        one unloaded would be the gap this verb exists to avoid. What a reader sees during that
        teardown is the new generation at ``LOADED``, built and not yet taking work — a reader that
        has to read *now* waits it out with ``composat.std.wait.serving`` rather than retrying. Only
        after the old generation has let go is the new one brought to the level that one *served* at,
        which keeps a registration made on the way up (a matcher in a shared dispatcher, a row in a
        table) from being live twice. A key that rested at ``LOADED`` has no publish segment: nothing
        it replaced was serving, so the swap is the one move it always was.

        Everything that raises happens before the key is touched — a declaration the registry will not
        take, and a generation that will not come up, which is taken back down and leaves the old
        declaration and the old instance exactly as they were found. Everything after the swap is
        *reported* (:class:`Replacement`).

        Both generations are reported at this key, in the order they happened: the new one's as it
        comes up, the quiet retreat of the one it replaced, and the new one's ``enable`` last of all.
        ``event.instance`` is what tells the two apart, so a ledger sees the hand-over instead of a
        gap, and a handler that only wants the current answer asks the table
        (``instance(key) is event.instance``).

        The key is spoken for from before the registry is touched until the swap is done, so a stop or
        a batch that named it is refused rather than finding it half replaced.
        """
        if key not in self._up:
            raise ValueError(f"nothing is up under {key}")
        if declaration.key != key:
            raise ValueError(f"{declaration.key} cannot take over {key}")
        self._claim((key,), "being replaced")
        try:
            return await self._swap(key, declaration)
        finally:
            self._release((key,))

    def _require_idle(self, keys: Iterable[ComponentKey]) -> None:
        """Refuse a batch that names a key another call is already moving; claim nothing.

        The question without the claim is what a verb asks before it changes anything it would have to
        undo: an admission declares first, and a key it cannot have should not have left a declaration
        behind.
        """
        for key in keys:
            moving = self._moving.get(key)
            if moving is not None:
                raise KeyInTransitError(key, moving)

    def _claim(self, keys: Iterable[ComponentKey], what: str) -> None:
        """Speak for ``keys`` until :meth:`_release`, so no second call moves one of them.

        Checked as a set before any of it is written, so a batch refused for one key takes none of
        them — half a claim would be worse than none, since the refused call leaves no way to find out
        which half.

        ``what`` is the phrase a refused caller is told, and it is the verb's own: "being brought up"
        and "being taken down" are different answers to "can I wait for it".
        """
        claimed = tuple(keys)
        self._require_idle(claimed)
        for key in claimed:
            self._moving[key] = what

    def _release(self, keys: Iterable[ComponentKey]) -> None:
        """Give up the claim on ``keys``; a key nobody holds is not an error, so this runs in ``finally``."""
        for key in keys:
            _ = self._moving.pop(key, None)

    @contextlib.contextmanager
    def _staging(self) -> Generator[list[Event], None, None]:
        """Collect the events this call stages, and stop collecting when it is done.

        One list per call rather than one per assembly, so two batches running at once deliver their
        own events and report their own handler failures. The list is what a verb hands to
        :meth:`_deliver` as it goes, so a batch that is still running has already reported what it
        finished.
        """
        staged: list[Event] = []
        token = _STAGED.set(staged)
        try:
            yield staged
        finally:
            _STAGED.reset(token)

    def _watch(self, key: ComponentKey, instance: Component) -> None:
        """Report this instance's moves as events at ``key``, for as long as the instance exists.

        Nothing ever unsubscribes: the subscription lives on the instance's own control and goes when
        the instance does. That is what makes a swap reportable in full — while the old generation is
        being taken down the key may already answer with the new one, and both are in the list, so
        both are reported and ``event.instance`` says which is which.
        """
        _ = instance.state.observe(partial(self._stage, key, instance))

    def _stage(self, key: ComponentKey, instance: Component, committed: Committed) -> None:
        """Take one committed move as an event.

        Called under the instance's lock, so it only appends: nothing here may await, and nothing here
        may fail on the instance's account. Outside a verb nothing is being collected, and an event
        that no verb ordered is dropped — a move made straight through the manager is not this
        assembly's doing.
        """
        staged = _STAGED.get()
        if staged is not None:
            staged.append(
                Event(
                    key,
                    instance,
                    committed.operation,
                    committed.origin,
                    committed.arrival,
                    committed.undone,
                )
            )

    def _deliver(self, staged: list[Event]) -> tuple[tuple[Event, BaseException], ...]:
        """Hand the staged events to the subscribers, outside every lock, and empty the stage.

        Subscription order and one event at a time, because a handler may keep a running account of
        the batch and that account has an order. A handler that raises does not stop the rest: one
        broken sink is not a reason for the others to stay uninformed, and what it said comes back to
        the caller rather than being raised into the batch.
        """
        events = tuple(staged)
        staged.clear()
        failures: list[tuple[Event, BaseException]] = []
        for event in events:
            for observer in tuple(self._subscribers):
                try:
                    observer(event)
                except Exception as error:  # noqa: BLE001 - a handler's failure is reported, not raised
                    failures.append((event, error))
        return tuple(failures)

    async def _notify(
        self,
        identity: ComponentIdentity,
        *,
        excluding: ComponentKey | None,
    ) -> dict[ComponentKey, BaseException]:
        """Tell the instances that declared ``identity`` that whatever answers it has changed.

        Re-read from the classes rather than remembered, and mapped through the registry the same way a
        field read is, so an interface dependant is told when the provider behind its binding changes
        and an instance that merely imported the provider is not told at all — importing is not
        depending.

        Direct dependants only: a transitive dependant's own provider did not change, and deciding what
        a change propagates to is not the framework's call. ``excluding`` is the key that was just
        swapped — its new instance did not depend on anything that changed.

        What failed comes back per key rather than raising on the first: one hook that raises is not a
        reason for the others to stay uninformed.

        The table is read as it was when the swap landed: a dependant that starts or stops while the
        walk runs is not part of this round, which keeps a hook that manages the assembly from breaking
        the walk it is part of.
        """
        failed: dict[ComponentKey, BaseException] = {}
        for key, instance in tuple(self._up.items()):
            if key == excluding:
                continue
            for spec in requirements_of(type(instance)):
                if self._registry.provider_identity(spec.service) != identity:
                    continue
                try:
                    await instance.provider_changed(spec)
                except Exception as error:  # noqa: BLE001 - one dependant's failure is reported, not raised
                    failed[key] = error
                break
        return failed

    def _closed(self, keys: tuple[ComponentKey, ...]) -> tuple[ComponentKey, ...]:
        """The batch ``keys`` is, closed over what they require, still in the caller's order.

        A listed key brings up what its type declares, transitively, each dependency at the qualifier
        of the key that named it. An identity the caller listed itself is not derived: the batch
        already says where that thing sits, and the derivation only fills the gaps. An implied key
        that is already up is passed over — this call has nothing to do for it, and taking it down
        with its dependant later is not this call's business either.

        An implied key nothing declares is a ``KeyError`` naming the requirement that implied it,
        because the alternative — starting the declared half and failing at the first field read —
        puts the cause as far from the call as it can be. The walk carries the parent that reached the
        key, which is what makes the message name a field rather than an identity nobody wrote down.
        """
        listed = {key.identity for key in keys}
        batch: dict[ComponentKey, None] = dict.fromkeys(keys)
        expanding = list(keys)
        while expanding:
            parent = expanding.pop(0)
            for spec in self._registry.requirements_of(parent.identity):
                provider = self._registry.provider_identity(spec.service)
                if provider in listed:
                    continue
                implied = ComponentKey(provider, parent.instance)
                if implied in batch or implied in self._up:
                    continue
                if implied not in self._registry:
                    needed = f"{parent} declares {spec.name}: {spec.service.__qualname__}"
                    raise KeyError(f"{needed}, so it needs {implied}, and nothing is declared there")
                batch[implied] = None
                expanding.append(implied)
        return tuple(batch)

    def _require_startable(self, keys: tuple[ComponentKey, ...]) -> tuple[ComponentKey, ...]:
        """Refuse a batch that is not addressed at real, fresh keys, before anything is built.

        An undeclared key is a ``KeyError``; a key listed twice, or one already up, is a
        ``ValueError``. Those are facts about the call rather than one component failing on its own,
        so they are exceptions rather than a result — and catching them halfway through the batch
        would leave the first half up with nothing to report it.
        """
        seen: set[ComponentKey] = set()
        for key in keys:
            if key not in self._registry:
                raise KeyError(key)
            if key in seen:
                raise ValueError(f"key is listed twice in one batch: {key}")
            if key in self._up:
                raise ValueError(f"already up: {key}")
            seen.add(key)
        return keys

    def _require_held(self, keys: tuple[ComponentKey, ...]) -> tuple[ComponentKey, ...]:
        """The keys of ``keys`` this assembly is holding, refusing one that is named twice.

        A key that is not up is passed over rather than raised on, which is :meth:`stop`'s reading of
        the same fact: a deployment says "these keys should be at this level", and a tenant torn down
        between the read and the call would otherwise make every caller guard its own batch. A key
        named twice is still a fact about the call, and is refused before the first move.
        """
        held: list[ComponentKey] = []
        seen: set[ComponentKey] = set()
        for key in keys:
            if key in seen:
                raise ValueError(f"key is listed twice in one batch: {key}")
            seen.add(key)
            if key in self._up:
                held.append(key)
        return tuple(held)

    def _order_for(self, to: ComponentState) -> tuple[ComponentIdentity, ...]:
        """The order a batch is walked in to arrive at ``to``: requirements first when coming up."""
        return self._registry.order() if to is ComponentState.ENABLED else self._registry.reverse_order()

    async def _bring_up_each(
        self,
        batch: tuple[ComponentKey, ...],
        *,
        enable: bool,
        strict: bool,
    ) -> AssemblyResult:
        """Walk ``batch`` requirements first, reporting each key as it finishes coming up."""
        completed: list[ComponentKey] = []
        failed: dict[ComponentKey, BaseException] = {}
        watched: list[tuple[Event, BaseException]] = []
        with self._staging() as staged:
            try:
                for key in batch:
                    try:
                        await self._bring_up(key, enable=enable)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        # One component's failure is the batch's business, not the call's: it is
                        # reported and the rest are still brought up.
                        failed[key] = error
                        if strict:
                            raise
                    else:
                        completed.append(key)
                    finally:
                        watched.extend(self._deliver(staged))
            except (asyncio.CancelledError, Exception):
                await self._retract_all(completed)
                _ = self._deliver(staged)
                raise
        return AssemblyResult(tuple(completed), failed, tuple(watched))

    async def _take_down_each(self, keys: Iterable[ComponentKey]) -> AssemblyResult:
        """Walk ``keys`` dependants first, reporting each as it finishes going down."""
        completed: list[ComponentKey] = []
        failed: dict[ComponentKey, BaseException] = {}
        watched: list[tuple[Event, BaseException]] = []
        with self._staging() as staged:
            for key in _in_order((key for key in keys if key in self._up), self._registry.reverse_order()):
                try:
                    error = await self._retract(key)
                finally:
                    watched.extend(self._deliver(staged))
                if error is None:
                    completed.append(key)
                else:
                    failed[key] = error
        return AssemblyResult(tuple(completed), failed, tuple(watched))

    async def _move_each(self, batch: tuple[ComponentKey, ...], to: ComponentState) -> AssemblyResult:
        """Walk ``batch`` in the direction ``to`` gives it, reporting each key as it settles."""
        completed: list[ComponentKey] = []
        failed: dict[ComponentKey, BaseException] = {}
        watched: list[tuple[Event, BaseException]] = []
        with self._staging() as staged:
            for key in batch:
                try:
                    await self._manager.migrate(self._up[key], to)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - a key that would not move is reported, not raised
                    # A key that would not move is at some true level of its own, and the rest of
                    # the batch is still the caller's instruction.
                    failed[key] = error
                else:
                    completed.append(key)
                finally:
                    watched.extend(self._deliver(staged))
        return AssemblyResult(tuple(completed), failed, tuple(watched))

    async def _swap(self, key: ComponentKey, declaration: Declaration) -> Component | Replacement:
        """The swap itself, the key already spoken for: declaration, generation, publication."""
        superseded = self._registry.declaration(key)
        self._registry.redeclare(declaration)
        old = self._up[key]
        publish = _level_of(old.state.state)
        instance = declaration.build()
        attach(instance, key, self._rule)
        self._watch(key, instance)
        failed: dict[ComponentKey, BaseException] = {}
        published: BaseException | None = None
        watched: list[tuple[Event, BaseException]] = []
        with self._staging() as staged:
            try:
                try:
                    await self._manager.migrate(instance, ComponentState.LOADED)
                except (asyncio.CancelledError, Exception):
                    # Nothing has been published and the key has not moved: put the declaration back
                    # and take the generation that never happened down again.
                    self._registry.redeclare(superseded)
                    _ = await self._shutdown(instance)
                    raise
                self._up[key] = instance
                failed = await self._notify(key.identity, excluding=key)
                error = await self._shutdown(old)
                if error is not None:
                    failed[key] = error
                if publish is ComponentState.ENABLED:
                    try:
                        await self._manager.migrate(instance, ComponentState.ENABLED)
                    except asyncio.CancelledError:
                        raise
                    except Exception as refusal:  # noqa: BLE001 - reported, not raised
                        # The key answers this generation either way: what the refusal leaves is the
                        # level it rests at, which is a fact about the swap rather than about it.
                        published = refusal
            finally:
                watched.extend(self._deliver(staged))
        if not failed and published is None and not watched:
            return instance
        return Replacement(instance, failed, published, tuple(watched))

    async def _bring_up(self, key: ComponentKey, *, enable: bool) -> None:
        """Build one instance, point its requirements at ``key``, and drive it to the level asked for.

        The instance is in the table before ``load`` runs, so a requirement read from a hook finds
        what is up — this instance at this key included, which is what lets a component read its own
        field instead of missing. A failure takes it back down and forgets it, so a key that did not
        come up is reported as failed rather than as up.

        It is watched before it loads, so the moves that bring it up are part of the report rather
        than a silent preamble to it.
        """
        instance = self._registry.declaration(key).build()
        attach(instance, key, self._rule)
        self._up[key] = instance
        self._watch(key, instance)
        try:
            await self._manager.migrate(instance, ComponentState.ENABLED if enable else ComponentState.LOADED)
        except (asyncio.CancelledError, Exception):
            _ = await self._retract(key)
            raise

    async def _retract(self, key: ComponentKey) -> BaseException | None:
        """Forget ``key`` and take its instance down; the error when a hook refused to let it go.

        The key stops answering before the hooks run, so a dependant reading it during the teardown
        sees a miss rather than an instance on its way out. The error is *returned* rather than
        raised, because this also runs on failure paths, where the error that brought us here is the
        caller's business and a second one would replace it.
        """
        instance = self._up.pop(key, None)
        if instance is None:
            return None
        return await self._shutdown(instance)

    async def _shutdown(self, instance: Component) -> BaseException | None:
        """Drive one instance all the way down; the error when a hook refused."""
        try:
            await self._manager.migrate(instance, ComponentState.UNLOADED)
        except Exception as error:  # noqa: BLE001 - an error is the answer here, not a re-raise
            return error
        return None

    async def _retract_all(self, keys: list[ComponentKey]) -> None:
        """Take an abandoned batch back down, in reverse.

        This runs when the batch has nothing to report with — it was cancelled, or ``strict`` gave up
        on it — so the error that got us here keeps propagating, and the walk carries on past an
        instance whose hook refuses because there is no one left to tell.
        """
        for key in reversed(keys):
            if key in self._up:
                _ = await self._retract(key)
