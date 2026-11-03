"""The seam and the batches: what a declared requirement reads, and what filling the table does.

The rule is exercised as a function over a table rather than through a running assembly, which is
the whole point of it being one: what a field read gets is decided by the qualifier and by what is
up, and neither needs a manager to test. What the assembly adds above that is the batch — which keys
a call covers, the order they come up in, and what a key that fails leaves behind — and the report it
hands out while it does any of it; so that is what its own cases are about.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import ClassVar, Protocol

import pytest

from composat import (
    Assembly,
    AssemblyResult,
    Component,
    ComponentKey,
    ComponentManager,
    ComponentRegistry,
    ComponentState,
    Declaration,
    EffectScope,
    Event,
    Operation,
    Replacement,
    RequirementResolver,
    ServiceNotUpError,
    UnboundServiceError,
    require,
)
from composat.assembly import default_rule
from composat.scope import Disposable, Revoke
from composat.spec import RequirementSpec


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


class Store(Component):
    """A leaf: other components ask for it."""


class Engine(Component):
    store: Store = require()


class Vault(Component):
    """A step in a chain, so a closure has something to be transitive about."""

    store: Store = require()


class Hall(Component):
    """Two edges away from a leaf: what ``start`` should reach without being told."""

    vault: Vault = require()


class Wired(Component):
    """A component that reads a requirement from its own hook, which is what closure makes possible."""

    store: Store = require()
    read: Store | None = None

    async def load(self) -> None:
        self.read = self.store


class Wobbly(Component):
    """A component that will not load, having registered an effect first."""

    released: ClassVar[list[str]] = []

    async def load(self) -> None:
        _ = self.effect_scope.put(lambda: Wobbly.released.append("released"))
        raise RuntimeError("load refused")


class Sticky(Component):
    """A component that will not let go."""

    async def unload(self) -> None:
        raise RuntimeError("unload refused")


class Quitter(Component):
    """A component whose hook is cancelled before it comes back."""

    async def load(self) -> None:
        raise asyncio.CancelledError


class Watching(Component):
    """A dependant that writes down what it was told."""

    store: Store = require()

    def __init__(self) -> None:
        super().__init__()
        self.told: list[RequirementSpec] = []

    async def provider_changed(self, requirement: RequirementSpec) -> None:
        self.told.append(requirement)


class Objecting(Watching):
    """A dependant for whom being told is itself a failure."""

    async def provider_changed(self, requirement: RequirementSpec) -> None:
        raise RuntimeError("not now")


class Steady(Component):
    """A leaf whose replacement generation is the one that will not come up."""


class Bashful(Component):
    """A component that will not be put into service."""

    async def enable(self) -> None:
        raise RuntimeError("enable refused")


async def refusing_load(self: Component) -> None:
    """A lifecycle hook that refuses, for a generation written up as a class dict."""
    raise RuntimeError("load refused")


def generation(name: str, **body: object) -> type[Component]:
    """A class wearing the identity of the module-level one called ``name``: a reload, written down.

    The identity of a type is the path it was written at, so the only way to have a second generation
    of one is to build a class that claims the same path — which is what a module reloading under a
    running assembly looks like from in here.
    """
    return type(name, (Component,), {"__module__": __name__, **body})


class WeatherService(Protocol):
    """A service contract written as an interface: whoever answers it stays behind the name."""

    def forecast(self, city: str) -> str: ...


class Weather(Component):
    def forecast(self, city: str) -> str:
        return city


class RefusingScope:
    """A scope a rule must not register into: an instance it hands out is not the caller's effect."""

    def put(self, _effect: Disposable, /) -> Revoke:
        raise AssertionError("the assembly's own rule registers nothing into a call's scope")


def at(component: type[Component], instance: str = "") -> ComponentKey:
    """The key ``component`` answers at under ``instance``."""
    return ComponentKey(component.identity(), instance)


def asking(service: type[object], name: str = "service") -> RequirementSpec:
    """A requirement a caller made on ``service``."""
    return RequirementSpec(name, service)


class TestDefaultRule:
    def test_a_requirement_is_answered_at_the_caller_s_own_qualifier(self) -> None:
        tenant = at(Store, "tenant-a")
        instances = {tenant: Store(), at(Store): Store()}
        rule = default_rule(ComponentRegistry(), instances)

        assert rule(at(Engine, "tenant-a"), asking(Store), EffectScope()) is instances[tenant]

    def test_another_qualifier_s_instance_is_not_an_answer(self) -> None:
        rule = default_rule(ComponentRegistry(), {at(Store): Store()})

        with pytest.raises(ServiceNotUpError) as raised:
            _ = rule(at(Engine, "tenant-a"), asking(Store), EffectScope())
        assert raised.value.key == at(Store, "tenant-a")

    def test_the_rule_reads_the_table_as_it_fills(self) -> None:
        instances: dict[ComponentKey, Component] = {}
        rule = default_rule(ComponentRegistry(), instances)
        instances[at(Store)] = Store()

        assert rule(at(Engine), asking(Store), EffectScope()) is instances[at(Store)]

    def test_an_interface_is_answered_by_the_provider_it_is_bound_to(self) -> None:
        registry = ComponentRegistry()
        registry.bind(WeatherService, Weather)
        weather = Weather()
        rule = default_rule(registry, {at(Weather): weather})

        assert rule(at(Engine), asking(WeatherService), EffectScope()) is weather

    def test_an_interface_nothing_is_bound_to_is_refused(self) -> None:
        rule = default_rule(ComponentRegistry(), {at(Weather): Weather()})

        with pytest.raises(UnboundServiceError) as raised:
            _ = rule(at(Engine), asking(WeatherService), EffectScope())
        assert raised.value.service is WeatherService

    def test_nothing_is_registered_on_the_scope_it_was_handed(self) -> None:
        rule = default_rule(ComponentRegistry(), {at(Store): Store()})

        assert rule(at(Engine), asking(Store), RefusingScope()) is not None


class TestAssembly:
    def test_the_rule_in_force_is_the_default_rule_over_the_table_it_fills(self) -> None:
        instances: dict[ComponentKey, Component] = {}
        assembly = Assembly(ComponentRegistry(), up=instances)
        instances[at(Store)] = Store()

        assert assembly.rule(at(Engine), asking(Store), EffectScope()) is instances[at(Store)]

    def test_a_rule_handed_in_is_the_one_in_force(self) -> None:
        def chain(key: ComponentKey, requirement: RequirementSpec, effects: object, /) -> object:
            _ = key, requirement, effects
            return "from the chain"

        rule: RequirementResolver = chain
        assembly = Assembly(ComponentRegistry(), rule=rule)

        assert assembly.rule is rule
        assert assembly.rule(at(Engine), asking(Store), EffectScope()) == "from the chain"

    def test_an_assembly_that_was_handed_nothing_is_answering_with_nothing(self) -> None:
        assembly = Assembly(ComponentRegistry())

        with pytest.raises(ServiceNotUpError):
            _ = assembly.rule(at(Engine), asking(Store), EffectScope())

    def test_nothing_is_up_in_an_assembly_that_was_handed_nothing(self) -> None:
        assembly = Assembly(ComponentRegistry())

        assert assembly.keys() == ()
        assert assembly.instances() == ()
        assert assembly.instance(at(Store)) is None

    def test_what_is_up_comes_back_in_the_order_it_came_up(self) -> None:
        store, engine = Store(), Engine()
        instances = {at(Store, "tenant-a"): store, at(Engine, "tenant-a"): engine}
        assembly = Assembly(ComponentRegistry(), up=instances)

        assert assembly.keys() == tuple(instances)
        assert assembly.instances() == (store, engine)
        assert assembly.instance(at(Engine, "tenant-a")) is engine
        assert assembly.instance(at(Engine, "tenant-b")) is None


class TestReport:
    def test_a_batch_that_left_every_key_alone_is_ok(self) -> None:
        result = AssemblyResult()

        assert result.ok
        assert result.completed == ()
        assert result.failed == {}

    def test_a_batch_that_came_up_short_is_not(self) -> None:
        failed = {at(Store): RuntimeError("no dsn")}
        result = AssemblyResult((at(Engine),), failed)

        assert not result.ok
        assert result.completed == (at(Engine),)
        assert result.failed == failed

    def test_a_swap_nobody_complained_about_is_ok(self) -> None:
        replacement = Replacement(Engine())

        assert replacement.ok
        assert replacement.failed == {}

    def test_a_complaining_dependant_is_reported_apart_from_the_instance(self) -> None:
        instance = Engine()
        failed = {at(Store): RuntimeError("would not re-read")}
        replacement = Replacement(instance, failed)

        # The swap landed either way: ``failed`` is about the dependants, not about the key.
        assert not replacement.ok
        assert replacement.instance is instance
        assert replacement.failed == failed


def declaring(*declarations: Declaration) -> ComponentRegistry:
    """A registry holding ``declarations``: what every batch case needs before it can start one."""
    registry = ComponentRegistry()
    _ = registry.declare_all(declarations)
    return registry


def state_of(assembly: Assembly, key: ComponentKey) -> ComponentState:
    """Where the instance under ``key`` is; a case that asks about a key that is down is a bad case."""
    instance = assembly.instance(key)
    assert instance is not None, f"nothing is up under {key}"
    return instance.state.state


def watching(assembly: Assembly) -> list[Event]:
    """Subscribe, and answer the list the events land in as they are delivered."""
    seen: list[Event] = []
    _ = assembly.observe(seen.append)
    return seen


class TestClosure:
    def test_a_start_reaches_what_its_key_needs_two_edges_away(self) -> None:
        assembly = Assembly(declaring(Declaration(Hall), Declaration(Vault), Declaration(Store)))

        result = run(assembly.start(at(Hall)))

        assert result.completed == (at(Store), at(Vault), at(Hall))
        assert assembly.keys() == (at(Store), at(Vault), at(Hall))

    def test_a_dependency_comes_up_at_the_qualifier_of_the_key_that_named_it(self) -> None:
        registry = declaring(Declaration(Engine, instance="tenant-a"), Declaration(Store, instance="tenant-a"))
        assembly = Assembly(registry)

        result = run(assembly.start(at(Engine, "tenant-a")))

        assert result.completed == (at(Store, "tenant-a"), at(Engine, "tenant-a"))

    def test_a_dependency_the_caller_named_is_left_where_it_was_named(self) -> None:
        # ``Engine`` at a tenant needs a store there, and the only one declared sits at the empty
        # qualifier: naming it in the call is what says where that store is meant to live.
        assembly = Assembly(declaring(Declaration(Engine, instance="tenant-a"), Declaration(Store)))

        result = run(assembly.start(at(Engine, "tenant-a"), at(Store)))

        assert result.completed == (at(Store), at(Engine, "tenant-a"))

    def test_a_requirement_that_is_already_up_is_not_started_twice(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine), Declaration(Store)))
        _ = run(assembly.start(at(Store)))

        result = run(assembly.start(at(Engine)))

        assert result.completed == (at(Engine),)
        assert assembly.keys() == (at(Store), at(Engine))

    def test_an_implied_key_nothing_declares_names_the_requirement(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine, instance="tenant-a"), Declaration(Store)))

        with pytest.raises(KeyError) as raised:
            _ = run(assembly.start(at(Engine, "tenant-a")))

        message = str(raised.value)
        assert "declares store: Store" in message
        assert str(at(Store, "tenant-a")) in message
        assert assembly.keys() == ()


class TestAdmission:
    def test_a_key_nothing_declares_is_refused(self) -> None:
        assembly = Assembly(ComponentRegistry())

        with pytest.raises(KeyError):
            _ = run(assembly.start(at(Store)))
        assert assembly.keys() == ()

    def test_a_key_listed_twice_is_refused(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))

        with pytest.raises(ValueError, match="listed twice"):
            _ = run(assembly.start(at(Store), at(Store)))
        assert assembly.keys() == ()

    def test_a_key_that_is_already_up_is_refused(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))

        with pytest.raises(ValueError, match="already up"):
            _ = run(assembly.start(at(Store)))


class TestComingUp:
    def test_a_batch_comes_up_to_serving(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine), Declaration(Store)))

        result = run(assembly.start(at(Engine)))

        assert result.ok
        assert state_of(assembly, at(Store)) is ComponentState.ENABLED
        assert state_of(assembly, at(Engine)) is ComponentState.ENABLED

    def test_a_batch_that_asked_for_no_more_than_loaded_stops_there(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))

        _ = run(assembly.start(at(Store), enable=False))

        assert state_of(assembly, at(Store)) is ComponentState.LOADED

    def test_a_hook_reads_the_requirement_the_batch_brought_up(self) -> None:
        assembly = Assembly(declaring(Declaration(Wired), Declaration(Store)))

        _ = run(assembly.start(at(Wired)))

        wired = assembly.instance(at(Wired))
        assert isinstance(wired, Wired)
        assert wired.read is assembly.instance(at(Store))


class TestGoingDown:
    def test_a_batch_goes_down_dependants_first(self) -> None:
        assembly = Assembly(declaring(Declaration(Hall), Declaration(Vault), Declaration(Store)))
        _ = run(assembly.start(at(Hall)))

        result = run(assembly.stop(*assembly.keys()))

        assert result.completed == (at(Hall), at(Vault), at(Store))
        assert assembly.keys() == ()

    def test_a_key_that_is_not_up_is_passed_over(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))

        result = run(assembly.stop(at(Store)))

        assert result.ok
        assert result.completed == ()

    def test_an_empty_batch_leaves_everything_where_it_was(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine), Declaration(Store)))
        _ = run(assembly.start(at(Engine)))

        result = run(assembly.stop())

        assert result.completed == ()
        assert assembly.keys() == (at(Store), at(Engine))

    def test_stopping_a_component_leaves_its_requirement_up(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine), Declaration(Store)))
        _ = run(assembly.start(at(Engine)))

        _ = run(assembly.stop(at(Engine)))

        assert assembly.keys() == (at(Store),)

    def test_a_component_that_will_not_unload_is_reported_and_stops_answering(self) -> None:
        assembly = Assembly(declaring(Declaration(Sticky)))

        _ = run(assembly.start(at(Sticky)))
        result = run(assembly.stop(at(Sticky)))

        assert not result.ok
        assert isinstance(result.failed[at(Sticky)], RuntimeError)
        assert assembly.keys() == ()


class TestReLevelling:
    def test_a_batch_built_first_is_put_into_service_later(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine), Declaration(Store)))
        built = run(assembly.start(at(Engine), enable=False))
        assert built.completed == (at(Store), at(Engine))

        result = run(assembly.enable(*built.completed))

        assert result.completed == (at(Store), at(Engine))
        assert state_of(assembly, at(Engine)) is ComponentState.ENABLED
        assert state_of(assembly, at(Store)) is ComponentState.ENABLED

    def test_quiescing_keeps_the_instance_it_was_holding(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))
        held = assembly.instance(at(Store))

        _ = run(assembly.quiesce(at(Store)))

        assert assembly.instance(at(Store)) is held
        assert assembly.keys() == (at(Store),)
        assert state_of(assembly, at(Store)) is ComponentState.LOADED

    def test_a_batch_coming_up_is_walked_requirements_first(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine), Declaration(Store)))
        _ = run(assembly.start(at(Engine), enable=False))
        seen = watching(assembly)

        _ = run(assembly.enable(at(Engine), at(Store)))

        assert [event.key for event in seen] == [at(Store), at(Engine)], "the batch was named the other way round"
        assert [event.operation for event in seen] == [Operation.enable, Operation.enable]

    def test_a_batch_going_down_is_walked_dependants_first(self) -> None:
        assembly = Assembly(declaring(Declaration(Engine), Declaration(Store)))
        _ = run(assembly.start(at(Engine)))
        seen = watching(assembly)

        result = run(assembly.quiesce(*assembly.keys()))

        assert result.completed == (at(Engine), at(Store))
        assert [event.key for event in seen if event.operation is Operation.drain] == [at(Engine), at(Store)]
        assert state_of(assembly, at(Store)) is ComponentState.LOADED

    def test_a_drained_key_is_settled_by_the_same_verb(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))

        _ = run(assembly.migrate(at(Store), to=ComponentState.DRAINING))
        assert state_of(assembly, at(Store)) is ComponentState.DRAINING

        _ = run(assembly.quiesce(at(Store)))
        assert state_of(assembly, at(Store)) is ComponentState.LOADED

    def test_a_key_already_at_the_level_is_carried_through(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))
        seen = watching(assembly)

        result = run(assembly.enable(at(Store)))

        assert result.completed == (at(Store),)
        assert seen == []

    def test_a_key_that_is_not_up_is_passed_over(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))

        result = run(assembly.enable(at(Store)))

        assert result.ok
        assert result.completed == ()

    def test_unloading_is_not_a_level_a_batch_moves_to(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))

        with pytest.raises(ValueError, match="not a level"):
            _ = run(assembly.migrate(at(Store), to=ComponentState.UNLOADED))

        assert state_of(assembly, at(Store)) is ComponentState.ENABLED

    def test_a_key_that_will_not_come_up_is_reported_and_the_rest_still_move(self) -> None:
        assembly = Assembly(declaring(Declaration(Bashful), Declaration(Store)))
        _ = run(assembly.start(at(Bashful), at(Store), enable=False))

        result = run(assembly.enable(at(Store), at(Bashful)))

        assert result.completed == (at(Store),)
        assert isinstance(result.failed[at(Bashful)], RuntimeError)
        assert state_of(assembly, at(Bashful)) is ComponentState.LOADED


class TestFailures:
    def test_a_component_that_will_not_load_is_reported_and_the_rest_come_up(self) -> None:
        assembly = Assembly(declaring(Declaration(Store), Declaration(Wobbly)))

        result = run(assembly.start(at(Store), at(Wobbly)))

        assert result.completed == (at(Store),)
        assert isinstance(result.failed[at(Wobbly)], RuntimeError)
        assert assembly.keys() == (at(Store),)

    def test_strict_takes_back_what_this_call_brought_up(self) -> None:
        assembly = Assembly(declaring(Declaration(Store), Declaration(Wobbly)))

        with pytest.raises(RuntimeError, match="load refused"):
            _ = run(assembly.start(at(Store), at(Wobbly), strict=True))

        assert assembly.keys() == ()

    def test_a_failed_load_releases_what_it_registered(self) -> None:
        # The epoch of a lifetime that never started goes with the failure: what the hook put there
        # is released rather than left for a next reader who will never come.
        Wobbly.released.clear()
        assembly = Assembly(declaring(Declaration(Wobbly)))

        result = run(assembly.start(at(Wobbly)))

        assert isinstance(result.failed[at(Wobbly)], RuntimeError)
        assert Wobbly.released == ["released"]

    def test_a_cancelled_batch_takes_back_what_it_brought_up(self) -> None:
        assembly = Assembly(declaring(Declaration(Store), Declaration(Quitter)))

        with pytest.raises(asyncio.CancelledError):
            _ = run(assembly.start(at(Store), at(Quitter)))

        assert assembly.keys() == ()


class TestSwapping:
    def test_a_replacement_takes_over_the_key_and_the_old_one_goes_down(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))
        old = assembly.instance(at(Store))
        assert old is not None

        result = run(assembly.replace(at(Store), Declaration(generation("Store"))))

        assert not isinstance(result, Replacement)
        assert assembly.instance(at(Store)) is result
        assert old.state.state is ComponentState.UNLOADED

    def test_the_new_instance_comes_up_to_the_level_the_old_one_served_at(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))

        _ = run(assembly.replace(at(Store), Declaration(generation("Store"))))

        assert state_of(assembly, at(Store)) is ComponentState.ENABLED

    def test_a_loaded_one_is_not_made_to_serve_by_being_replaced(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store), enable=False))

        _ = run(assembly.replace(at(Store), Declaration(generation("Store"))))

        assert state_of(assembly, at(Store)) is ComponentState.LOADED

    def test_a_dependant_is_told_what_changed(self) -> None:
        assembly = Assembly(declaring(Declaration(Watching), Declaration(Store)))
        _ = run(assembly.start(at(Watching)))

        _ = run(assembly.replace(at(Store), Declaration(generation("Store"))))

        watching = assembly.instance(at(Watching))
        assert isinstance(watching, Watching)
        assert watching.told == [RequirementSpec("store", Store)]

    def test_a_dependant_that_minds_is_reported_apart_from_the_swap(self) -> None:
        assembly = Assembly(declaring(Declaration(Objecting), Declaration(Store)))
        _ = run(assembly.start(at(Objecting)))

        result = run(assembly.replace(at(Store), Declaration(generation("Store"))))

        assert isinstance(result, Replacement)
        assert isinstance(result.failed[at(Objecting)], RuntimeError)
        assert assembly.instance(at(Store)) is result.instance

    def test_a_declaration_that_names_another_key_is_refused(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))

        with pytest.raises(ValueError, match="cannot take over"):
            _ = run(assembly.replace(at(Store), Declaration(Engine)))

    def test_replacing_a_key_nothing_is_up_under_is_refused(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))

        with pytest.raises(ValueError, match="nothing is up"):
            _ = run(assembly.replace(at(Store), Declaration(generation("Store"))))

    def test_a_generation_the_registry_will_not_take_costs_nothing(self) -> None:
        registry = declaring(Declaration(Store))
        assembly = Assembly(registry)
        _ = run(assembly.start(at(Store)))
        old = assembly.instance(at(Store))
        greedy = generation("Store", hall=require(Hall))

        with pytest.raises(ValueError, match="already has requirements"):
            _ = run(assembly.replace(at(Store), Declaration(greedy)))

        assert assembly.instance(at(Store)) is old
        assert registry.declaration(at(Store)).component_class is Store

    def test_a_new_instance_that_will_not_come_up_leaves_the_key_as_it_was(self) -> None:
        registry = declaring(Declaration(Steady))
        assembly = Assembly(registry)
        _ = run(assembly.start(at(Steady)))
        old = assembly.instance(at(Steady))

        with pytest.raises(RuntimeError, match="load refused"):
            _ = run(assembly.replace(at(Steady), Declaration(generation("Steady", load=refusing_load))))

        assert assembly.instance(at(Steady)) is old
        assert state_of(assembly, at(Steady)) is ComponentState.ENABLED
        assert registry.declaration(at(Steady)).component_class is Steady

    def test_an_old_instance_that_will_not_let_go_is_reported_against_its_key(self) -> None:
        assembly = Assembly(declaring(Declaration(Sticky)))
        _ = run(assembly.start(at(Sticky)))

        result = run(assembly.replace(at(Sticky), Declaration(generation("Sticky"))))

        assert isinstance(result, Replacement)
        assert isinstance(result.failed[at(Sticky)], RuntimeError)
        assert assembly.instance(at(Sticky)) is result.instance


class TestWhatIsReported:
    def test_a_key_coming_up_is_reported_move_by_move(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        seen = watching(assembly)

        result = run(assembly.start(at(Store)))

        assert result.completed == (at(Store),)
        assert [(event.key, event.operation, event.origin, event.arrival) for event in seen] == [
            (at(Store), Operation.load, ComponentState.UNLOADED, ComponentState.LOADED),
            (at(Store), Operation.enable, ComponentState.LOADED, ComponentState.ENABLED),
        ]
        assert all(event.instance is assembly.instance(at(Store)) for event in seen)
        assert not any(event.undone for event in seen)

    def test_what_a_key_needs_is_reported_at_the_key_it_came_up_at(self) -> None:
        assembly = Assembly(
            declaring(
                Declaration(Hall, "tenant-a"),
                Declaration(Vault, "tenant-a"),
                Declaration(Store, "tenant-a"),
            )
        )
        seen = watching(assembly)

        _ = run(assembly.start(at(Hall, "tenant-a")))

        # The closure comes up requirements first, and every instance is reported at its own key: a
        # reader served at one qualifier is answered by the provider at that qualifier.
        loads = [event.key for event in seen if event.operation is Operation.load]
        assert loads == [at(Store, "tenant-a"), at(Vault, "tenant-a"), at(Hall, "tenant-a")]

    def test_the_way_down_is_reported_as_it_happens(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))
        seen = watching(assembly)

        _ = run(assembly.stop(at(Store)))

        assert [event.operation for event in seen] == [Operation.drain, Operation.disable, Operation.unload]
        assert [event.arrival for event in seen] == [
            ComponentState.DRAINING,
            ComponentState.LOADED,
            ComponentState.UNLOADED,
        ]

    def test_a_swap_reports_both_generations_at_the_one_key(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))
        old = assembly.instance(at(Store))
        seen = watching(assembly)

        _ = run(assembly.replace(at(Store), Declaration(generation("Store"))))

        new = assembly.instance(at(Store))
        assert [event.instance for event in seen] == [
            new,
            new,
            old,
            old,
            old,
        ], "the new generation comes up first, and the old one retreats after the key has moved"
        assert {event.key for event in seen} == {at(Store)}
        assert [event.operation for event in seen if event.instance is old] == [
            Operation.drain,
            Operation.disable,
            Operation.unload,
        ]

    def test_a_step_a_hook_put_back_is_reported_as_undone(self) -> None:
        assembly = Assembly(declaring(Declaration(Wobbly)))
        seen = watching(assembly)

        result = run(assembly.start(at(Wobbly)))

        assert not result.ok
        assert seen[-1].undone
        assert (seen[-1].origin, seen[-1].arrival) == (ComponentState.LOADED, ComponentState.UNLOADED)

    def test_a_handler_runs_between_moves_rather_than_inside_one(self) -> None:
        # The promise a handler is entitled to: no instance lock is held, and nothing is being
        # driven, so a handler cannot deadlock on the move that reported itself to it.
        assembly = Assembly(declaring(Declaration(Store)))
        observed: list[tuple[str | None, bool]] = []

        def handler(event: Event) -> None:
            observed.append((event.instance.state.pending, event.instance.state.lock.locked()))

        _ = assembly.observe(handler)

        _ = run(assembly.start(at(Store)))

        assert observed == [(None, False), (None, False)]

    def test_unsubscribing_stops_that_handler(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        seen = watching(assembly)
        unsubscribe = assembly.observe(seen.append)

        unsubscribe()
        _ = run(assembly.start(at(Store)))

        assert [event.operation for event in seen] == [Operation.load, Operation.enable]

    def test_a_handler_that_raises_is_reported_apart_from_the_batch(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        seen: list[Event] = []

        def complaining(event: Event) -> None:
            raise RuntimeError("cannot write it down")

        _ = assembly.observe(complaining)
        _ = assembly.observe(seen.append)

        result = run(assembly.start(at(Store)))

        assert result.ok
        assert result.completed == (at(Store),)
        assert [event for event, _ in result.observer_failures] == seen
        assert all(isinstance(error, RuntimeError) for _, error in result.observer_failures)

    def test_a_swap_only_a_handler_complained_about_is_still_a_replacement(self) -> None:
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))

        def complaining(event: Event) -> None:
            raise RuntimeError("cannot write it down")

        _ = assembly.observe(complaining)

        result = run(assembly.replace(at(Store), Declaration(generation("Store"))))

        assert isinstance(result, Replacement)
        assert result.ok, "nothing a component was asked to do failed"
        assert result.instance is assembly.instance(at(Store))
        assert result.observer_failures

    def test_a_move_this_assembly_did_not_order_is_not_reported(self) -> None:
        # A subscription is to what the assembly does. An application that drives an instance itself
        # is not asking for it to be reported, and nothing about that call is staged.
        assembly = Assembly(declaring(Declaration(Store)))
        _ = run(assembly.start(at(Store)))
        seen = watching(assembly)
        instance = assembly.instance(at(Store))
        assert instance is not None

        run(ComponentManager().migrate(instance, ComponentState.LOADED))

        assert seen == []
