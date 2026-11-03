"""The registry: declarations, the keys they answer to, and the plan the edges imply.

The graph is reached *through* the registry rather than exercised on its own: the order a deployment
comes up in, the reverse order it goes down in, and the refusals that keep it orderable are what a
caller reads off the registry, so that is where these cases read them too. The graph's own invariants
have their own module.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from composat import (
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Declaration,
    DependencyCycleError,
    DependencyInUseError,
    UnboundServiceError,
    require,
)


class Store(Component):
    """A leaf: other components ask for it."""


class Dsn(Component):
    """A component that takes its own construction argument, which the framework only passes on."""

    def __init__(self, dsn: str = "memory") -> None:
        super().__init__()
        self.dsn: str = dsn


class Engine(Component):
    store: Store = require()


class Mirror(Component):
    store: Store = require()


class Facade(Component):
    engine: Engine = require()
    mirror: Mirror = require()


class Twin(Component):
    """Two fields, one contract: two reads of the same instance rather than two edges."""

    left: Store = require()
    right: Store = require()


class WeatherService(Protocol):
    """A service contract written as an interface: whoever answers it stays behind the name."""

    def forecast(self, city: str) -> str: ...


class WeatherTool(Component):
    def forecast(self, city: str) -> str:
        return city


class Forecaster(Component):
    """Asks for the *interface*: the annotation names nothing until a provider is bound to it."""

    weather: WeatherService = require()


class LeftService(Protocol):
    def from_left(self) -> str: ...


class RightService(Protocol):
    def from_right(self) -> str: ...


class Left(Component):
    """Half of a cycle: it asks for what :class:`Right` provides."""

    right: RightService = require()


class Right(Component):
    left: LeftService = require()


STORAGE = ComponentIdentity.of(Store)
ENGINE = ComponentIdentity.of(Engine)
MIRROR = ComponentIdentity.of(Mirror)
FACADE = ComponentIdentity.of(Facade)
TWIN = ComponentIdentity.of(Twin)
WEATHER = ComponentIdentity.of(WeatherTool)
FORECASTER = ComponentIdentity.of(Forecaster)


def generation(name: str = "Generation", **requirements: type[object]) -> type[Component]:
    """One generation of a component at this module's ``name``; two calls differ by class object.

    Same path, same identity, different class — which is the shape a reload produces. It builds on
    :class:`Dsn` so the two generations also differ in nothing but the class object, and takes the
    requirements the generation declares, by field name.
    """

    body = {field: require(contract) for field, contract in requirements.items()}
    return type(name, (Dsn,), {"__module__": __name__, **body})


class TestDeclaration:
    def test_a_declaration_names_its_identity_and_its_key(self) -> None:
        declaration = Declaration(Store)

        assert declaration.identity == STORAGE
        assert declaration.key == ComponentKey(STORAGE)

    def test_the_qualifier_tells_instances_of_one_identity_apart(self) -> None:
        assert Declaration(Store, instance="tenant-a").key == ComponentKey(STORAGE, "tenant-a")

    def test_build_passes_the_declaration_s_arguments_through(self) -> None:
        built = Declaration(Dsn, args=(), kwargs={"dsn": "postgresql://local"}).build()

        assert isinstance(built, Dsn)
        assert built.dsn == "postgresql://local"


class TestDeclaring:
    def test_a_declaration_is_answered_by_its_key(self) -> None:
        registry = ComponentRegistry()

        key = registry.declare(Store)

        assert key == ComponentKey(STORAGE)
        assert registry.declaration(key).component_class is Store

    def test_what_is_declared_comes_back_in_declaration_order(self) -> None:
        registry = ComponentRegistry()
        store = registry.declare(Store)
        engine = registry.declare(Engine)

        assert [declaration.key for declaration in registry.declarations()] == [store, engine]

    def test_contains_answers_for_the_keys_this_registry_holds(self) -> None:
        registry = ComponentRegistry()
        key = registry.declare(Store)

        assert key in registry
        assert ComponentKey(STORAGE, "tenant-a") not in registry

    def test_declaring_one_key_twice_is_refused(self) -> None:
        registry = ComponentRegistry()
        _ = registry.declare(Store)

        with pytest.raises(ValueError, match="already declared"):
            _ = registry.declare(Store)

    def test_a_key_the_registry_does_not_hold_is_a_key_error(self) -> None:
        registry = ComponentRegistry()

        with pytest.raises(KeyError):
            _ = registry.declaration(ComponentKey(STORAGE))

    def test_nothing_declared_is_a_plan_with_nothing_in_it(self) -> None:
        registry = ComponentRegistry()

        assert registry.order() == ()
        assert registry.reverse_order() == ()


class TestEdges:
    def test_a_requirement_becomes_an_edge_and_the_order_follows_it(self) -> None:
        registry = ComponentRegistry()
        _ = registry.declare_all([Declaration(Store), Declaration(Engine)])

        assert registry.order() == (STORAGE, ENGINE)
        assert registry.reverse_order() == (ENGINE, STORAGE)

    def test_a_batch_may_arrive_in_any_order(self) -> None:
        registry = ComponentRegistry()

        _ = registry.declare_all([Declaration(Facade), Declaration(Mirror), Declaration(Engine), Declaration(Store)])

        order = registry.order()
        assert order[0] == STORAGE
        assert order[-1] == FACADE
        assert order.index(ENGINE) < order.index(FACADE)
        assert order.index(MIRROR) < order.index(FACADE)

    def test_two_instances_of_one_identity_share_a_node(self) -> None:
        registry = ComponentRegistry()
        first = registry.declare(Store, instance="tenant-a")
        second = registry.declare(Store, instance="tenant-b")

        assert registry.order() == (STORAGE,)
        assert {declaration.key for declaration in registry.declarations()} == {first, second}

    def test_two_fields_naming_one_contract_share_a_node(self) -> None:
        registry = ComponentRegistry()
        _ = registry.declare_all([Declaration(Store), Declaration(Twin)])

        assert registry.order() == (STORAGE, TWIN)

    def test_a_requirement_declared_nowhere_is_refused(self) -> None:
        registry = ComponentRegistry()

        with pytest.raises(KeyError):
            _ = registry.declare(Engine)

        assert registry.declarations() == ()

    def test_a_cycle_among_a_batch_is_refused_before_anything_is_declared(self) -> None:
        registry = ComponentRegistry()
        registry.bind(LeftService, Left)
        registry.bind(RightService, Right)

        with pytest.raises(DependencyCycleError):
            _ = registry.declare_all([Declaration(Left), Declaration(Right)])

        assert registry.declarations() == ()
        assert registry.order() == ()


class TestInterfaces:
    def test_an_interface_names_the_component_that_provides_it(self) -> None:
        registry = ComponentRegistry()
        registry.bind(WeatherService, WeatherTool)

        assert registry.provider_identity(WeatherService) == WEATHER
        assert registry.provider_identity(Store) == STORAGE

    def test_an_interface_with_nothing_bound_names_no_identity(self) -> None:
        registry = ComponentRegistry()

        with pytest.raises(UnboundServiceError):
            _ = registry.provider_identity(WeatherService)

    def test_a_requirement_on_an_interface_becomes_an_edge_to_its_provider(self) -> None:
        registry = ComponentRegistry()
        registry.bind(WeatherService, WeatherTool)

        _ = registry.declare_all([Declaration(Forecaster), Declaration(WeatherTool)])

        assert registry.order() == (WEATHER, FORECASTER)

    def test_an_interface_nothing_provides_is_refused_with_the_declaration(self) -> None:
        registry = ComponentRegistry()

        with pytest.raises(UnboundServiceError):
            _ = registry.declare(Forecaster)

        assert registry.declarations() == ()

    def test_binding_an_interface_twice_is_refused(self) -> None:
        registry = ComponentRegistry()
        registry.bind(WeatherService, WeatherTool)

        with pytest.raises(ValueError, match="already bound"):
            registry.bind(WeatherService, Store)

    def test_a_component_class_already_names_itself(self) -> None:
        registry = ComponentRegistry()

        with pytest.raises(ValueError, match="names itself"):
            registry.bind(Store, Engine)


class TestRedeclaring:
    def test_a_replacement_keeps_the_key_and_the_edges_it_had(self) -> None:
        registry = ComponentRegistry()
        _ = registry.declare_all([Declaration(Store), Declaration(Engine)])

        registry.redeclare(Declaration(Engine))

        assert registry.declaration(ComponentKey(ENGINE)).component_class is Engine
        assert registry.order() == (STORAGE, ENGINE)

    def test_a_replacement_may_be_another_generation_at_the_same_identity(self) -> None:
        registry = ComponentRegistry()
        first = generation()
        second = generation()
        key = registry.declare(first, kwargs={"dsn": "one"})

        assert first is not second
        registry.redeclare(Declaration(second, kwargs={"dsn": "two"}))

        assert registry.declaration(key).component_class is second
        assert registry.declaration(key).kwargs == {"dsn": "two"}
        assert registry.order() == (ComponentIdentity.of(first),)

    def test_a_replacement_that_asks_for_something_else_is_refused(self) -> None:
        registry = ComponentRegistry()
        first = generation()
        key = registry.declare(first)

        with pytest.raises(ValueError, match="already has requirements"):
            registry.redeclare(Declaration(generation(store=Store)))

        assert registry.declaration(key).component_class is first

    def test_redeclaring_a_key_that_is_not_declared_is_a_key_error(self) -> None:
        registry = ComponentRegistry()

        with pytest.raises(KeyError):
            registry.redeclare(Declaration(Store))


class TestWithdrawing:
    def test_a_withdrawn_key_is_no_longer_declared(self) -> None:
        registry = ComponentRegistry()
        key = registry.declare(Store)

        registry.undeclare(key)

        assert key not in registry
        assert registry.declarations() == ()
        assert registry.order() == ()

    def test_a_node_still_declared_under_keeps_its_place(self) -> None:
        registry = ComponentRegistry()
        first = registry.declare(Store, instance="tenant-a")
        second = registry.declare(Store, instance="tenant-b")

        registry.undeclare(first)

        assert registry.order() == (STORAGE,)
        assert registry.requirements_of(STORAGE) == ()
        assert {declaration.key for declaration in registry.declarations()} == {second}

    def test_withdrawing_what_something_still_declared_needs_is_refused(self) -> None:
        registry = ComponentRegistry()
        store = registry.declare(Store)
        _ = registry.declare(Engine)

        with pytest.raises(DependencyInUseError) as raised:
            registry.undeclare(store)

        assert raised.value.node == STORAGE
        assert raised.value.dependents == frozenset({ENGINE})
        assert store in registry

    def test_a_chain_withdrawn_at_once_goes_dependants_first(self) -> None:
        registry = ComponentRegistry()
        _ = registry.declare_all(
            [Declaration(Facade), Declaration(Mirror), Declaration(Engine), Declaration(Store)],
        )
        facade = ComponentKey(FACADE)
        engine = ComponentKey(ENGINE)

        # Withdrawn one at a time, the second would still be needed by the first; as a batch it is
        # exactly the set that goes, so neither is needed by anything that stays.
        registry.undeclare(facade, engine)

        assert {declaration.key for declaration in registry.declarations()} == {
            ComponentKey(STORAGE),
            ComponentKey(MIRROR),
        }

    def test_a_batch_that_is_partly_refused_withdraws_nothing(self) -> None:
        registry = ComponentRegistry()
        store = registry.declare(Store)
        _ = registry.declare(Mirror)
        _ = registry.declare(Engine)
        facade = registry.declare(Facade)

        with pytest.raises(DependencyInUseError):
            registry.undeclare(facade, store)

        assert facade in registry
        assert store in registry
        assert {declaration.key for declaration in registry.declarations()} == {
            ComponentKey(STORAGE),
            ComponentKey(ENGINE),
            ComponentKey(MIRROR),
            facade,
        }

    def test_the_check_withdraws_nothing_by_itself(self) -> None:
        registry = ComponentRegistry()
        store = registry.declare(Store)

        registry.require_undeclarable(store)

        assert store in registry
        assert registry.declarations() == (Declaration(Store),)

    def test_withdrawing_a_key_that_was_never_declared_is_a_key_error(self) -> None:
        registry = ComponentRegistry()

        with pytest.raises(KeyError):
            registry.undeclare(ComponentKey(STORAGE))

    def test_a_withdrawn_identity_can_be_declared_again(self) -> None:
        registry = ComponentRegistry()
        registry.undeclare(registry.declare(Store))

        again = registry.declare(Store)

        assert registry.order() == (STORAGE,)
        assert again in registry
