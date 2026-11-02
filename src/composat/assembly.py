"""The instances this process has up, the rule a requirement is answered by, and what a batch reports.

Driving a component through its states is the manager's job, and deciding what is declared is the
registry's; what is here is the table the two meet in — one instance per key — the answer a declared
requirement reads out of it, and the verbs that fill the table and empty it.

A batch that comes up short is reported (:class:`AssemblyResult`) rather than raised: one tenant's
broken configuration stays that tenant's problem, and the call site reads which keys failed with
what. Asking for the other reading — a batch that is atomic — is a request, and that one raises. A
swap reports the same way (:class:`Replacement`), because a replacement that landed with a dependant
complaining is neither an unchanged key nor a failed swap.

**A batch is closed over what its keys need.** ``start(App:tenant-a)`` brings up what ``App``
declares, transitively, each dependency at the qualifier of the key that named it: instances of one
identity are told apart by the qualifier, so a reader served by one tenant is answered by that
tenant's provider. An identity the caller listed itself is left where it was listed — naming a
dependency is how a deployment says where that thing sits — and an implied key that is already up is
passed over rather than started a second time. ``stop`` is deliberately not the mirror of that: it
takes down the keys it was given and nothing else, because a dependency may be serving keys the
caller did not name.

**A batch is admitted before anything is built.** A key nothing declares, a key listed twice, a key
already up: all three are refused as exceptions, because they are facts about the call rather than a
component failing on its own, and half a batch already up with nothing to report it would be the
worst of both readings.

The rule is a function of the table rather than a method of the thing that fills it
(:func:`default_rule`), and that is a reading rather than a style. A requirement is answered from
whatever is up *now*, so the rule has to see the table as it fills; taking the table as its argument
is what lets a rule be built before there is anything to answer with, and what lets an application
build a chain whose tail is the framework's own rule without either of them being installed later::

    up: dict[ComponentKey, Component] = {}
    chain = Chain(PerRequest(...), tail=default_rule(registry, up))
    assembly = Assembly(registry, up=up, rule=chain)

So an assembly does not own a rule slot to assign into — it is handed one — and the table it was
handed is the table it fills: ``up`` is mutated, never rebound, or the rule would go on reading a
table nothing writes to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .component import Component, RequirementResolver, attach
from .errors import ServiceNotUpError
from .identity import ComponentIdentity, ComponentKey
from .manager import ComponentManager
from .registry import ComponentRegistry
from .scope import Reclaimable
from .spec import RequirementSpec
from .state import ComponentState


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    """What a batch did: the keys it carried through, and the ones that failed, with their errors.

    A key the batch left alone is in neither tuple — stopping a key that was never up has an empty
    report about it, and a batch told to do nothing reports exactly that.

    Reporting rather than raising is the default reading: one tenant's broken configuration stays
    that tenant's problem. A batch that asks to be atomic is the other reading, and it raises instead
    of answering with this.
    """

    completed: tuple[ComponentKey, ...] = ()
    failed: Mapping[ComponentKey, BaseException] = field(default_factory=dict[ComponentKey, BaseException])

    @property
    def ok(self) -> bool:
        """Whether every key the batch touched went through."""
        return not self.failed


@dataclass(frozen=True, slots=True)
class Replacement:
    """What a swap left behind: the instance the key answers with, and the dependants that complained.

    The two are reported together because a swap has two outcomes that are not each other's
    negation: ``failed`` being non-empty does *not* mean the replacement did not land. The key
    answers with ``instance`` either way — the old instance is gone by the time this is handed
    back — and what a dependant's ``provider_changed`` did about being told is a separate fact.

    ``failed`` is keyed by the *dependant*, not by the replaced key: one hook that raises is not a
    reason for the others to stay uninformed, so the map is what all of them said.
    """

    instance: Component
    failed: Mapping[ComponentKey, BaseException] = field(default_factory=dict[ComponentKey, BaseException])

    @property
    def ok(self) -> bool:
        """Whether the swap landed and every dependant was told."""
        return not self.failed


def default_rule(
    registry: ComponentRegistry,
    instances: Mapping[ComponentKey, Component],
) -> RequirementResolver:
    """The framework's own answer: what is up under the asker's qualifier, or a refusal.

    Built from two things it does not own — the registry says which identity a contract names, the
    table says which instances are up — and neither is read at build time, so the closure goes on
    answering from the table as it fills: a rule made before anything is up is still the rule in
    force once something is.

    The qualifier is the whole lookup. Instances of one identity are told apart by it, so a caller
    served by the provider at ``tenant-a`` reads its requirements from ``tenant-a`` as well; a slot
    that is declared and is not up is a miss (``ServiceNotUpError``), never a reason to answer from
    another instance, which would be answering a question the caller did not ask.

    A ``RequirementSpec`` is accepted as the whole requirement rather than as its service, so an
    annotation that carries extras arrives intact; this rule has no vocabulary to read them with, and
    resolving by key alone is what "the requirement says nothing beyond its service" means.

    ``effects`` is where a resolver hands back something shorter-lived than the assembly, and this
    one registers nothing into it: an instance the assembly brought up is not the caller's resource,
    and releasing one from a call's scope would take it down under everyone else.
    """

    def resolve(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable, /) -> object:
        _ = effects
        provider = ComponentKey(registry.provider_identity(requirement.service), key.instance)
        instance = instances.get(provider)
        if instance is None:
            raise ServiceNotUpError(provider)
        return instance

    return resolve


def _in_order(keys: Iterable[ComponentKey], order: Sequence[ComponentIdentity]) -> tuple[ComponentKey, ...]:
    """``keys`` in the order ``order`` gives their identities; the caller's order within one.

    Sorting is stable, so several instances of one identity keep the order they were handed in, and a
    key whose identity has left the order sinks to the end rather than being dropped — a key whose
    declaration was withdrawn on the way is still one this assembly holds.
    """
    rank = {identity: index for index, identity in enumerate(order)}
    fallback = len(rank)
    return tuple(sorted(keys, key=lambda key: rank.get(key.identity, fallback)))


class Assembly:
    """The instances this process has up, the rule they reach each other by, and the two batch verbs.

    One instance per key: an identity may have several (a tenant each), and each answers at its own
    qualifier. :meth:`start` fills the table against the registry's order and :meth:`stop` empties it
    against the reverse, and both report what happened rather than raising — except for what is wrong
    with the call itself, which is refused before the first instance exists.

    Which instance answers a requirement is not decided here but in the rule this assembly was handed,
    and that rule reads this same table: an instance that came up a moment ago is what the next field
    read gets, with nothing refreshed.
    """

    _registry: ComponentRegistry
    _manager: ComponentManager
    _up: dict[ComponentKey, Component]
    _rule: RequirementResolver

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
        self._rule = default_rule(registry, self._up) if rule is None else rule

    @property
    def rule(self) -> RequirementResolver:
        """The rule in force, which is also what an instance is attached with.

        Reading it back is how an application asks whether its chain was the one installed, and how a
        test asks what a field read is going to go through; there is no setter, because a rule is
        given once and a second one would leave the instances attached earlier pointing at the first.
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

    async def start(self, *keys: ComponentKey, enable: bool = True, strict: bool = False) -> AssemblyResult:
        """Build and bring ``keys`` up, requirements first, and report what happened.

        The batch is these keys closed over what they require (:meth:`_closed`), in the registry's
        order rather than the caller's, so a requirement is up before the instance that reads it.
        ``enable`` is the level: ``False`` leaves the batch ``LOADED``, which is what a deployment that
        gates each key wants — a declaration says how to build a component, and the call says whether
        it should be serving.

        A component that fails is left where the failure left it, reported, and the rest still come
        up. ``strict`` asks for the other reading: the keys *this call* brought up are taken back
        down, in reverse, and the original failure is re-raised. A cancelled batch has no result to
        report, so it takes back what it brought up and lets the ``CancelledError`` go on.
        """
        batch = _in_order(self._closed(self._require_startable(keys)), self._registry.order())
        completed: list[ComponentKey] = []
        failed: dict[ComponentKey, BaseException] = {}
        try:
            for key in batch:
                try:
                    await self._bring_up(key, enable=enable)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # One component's failure is the batch's business, not the call's: it is reported
                    # and the rest are still brought up.
                    failed[key] = error
                    if strict:
                        raise
                    continue
                completed.append(key)
        except (asyncio.CancelledError, Exception):
            await self._retract_all(completed)
            raise
        return AssemblyResult(tuple(completed), failed)

    async def stop(self, *keys: ComponentKey) -> AssemblyResult:
        """Take ``keys`` back down, the instances that need them going first.

        Keys that are not up are passed over — nothing is done to them, so they are in neither tuple of
        the result — and an empty batch reports exactly that, which is what makes ``finally: await
        stop(*assembly.keys())`` the teardown a caller can run without asking first. Ordering comes
        from the registry, and everything else from the instances this assembly holds, so a key whose
        declaration was withdrawn on the way is still taken down.
        """
        completed: list[ComponentKey] = []
        failed: dict[ComponentKey, BaseException] = {}
        for key in _in_order((key for key in keys if key in self._up), self._registry.reverse_order()):
            error = await self._retract(key)
            if error is None:
                completed.append(key)
            else:
                failed[key] = error
        return AssemblyResult(tuple(completed), failed)

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

    async def _bring_up(self, key: ComponentKey, *, enable: bool) -> None:
        """Build one instance, point its requirements at ``key``, and drive it to the level asked for.

        The instance is in the table before ``load`` runs, so a requirement read from a hook finds
        what is up — this instance at this key included, which is what lets a component read its own
        field instead of missing. A failure takes it back down and forgets it, so a key that did not
        come up is reported as failed rather than as up.
        """
        instance = self._registry.declaration(key).build()
        attach(instance, key, self._rule)
        self._up[key] = instance
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
