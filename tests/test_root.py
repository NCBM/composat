"""Answering from the root: a qualified caller may reach the unqualified instance, and only that far.

Two policies are told apart here. The core's ``default_rule`` looks at one place, so a tenant's
component cannot read a process-wide one; this rule looks at two, and only for what the deployment
named — which is why the negative cases matter as much as the hit.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Protocol

import pytest

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentManager,
    ComponentRegistry,
    ComponentState,
    Declaration,
    EffectScope,
    RequirementSpec,
    ServiceNotUpError,
    UnboundServiceError,
    open_call,
    require,
    resolve,
)
from composat.std.root import answering_from_the_root


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


def at(component: type[Component], instance: str = "") -> ComponentKey:
    """The key ``component`` answers at under ``instance``."""
    return ComponentKey(ComponentIdentity.of(component), instance)


def asking(service: type[object], name: str = "service") -> RequirementSpec:
    """A requirement a caller made on ``service``."""
    return RequirementSpec(name, service)


def brought_up(component: Component, level: ComponentState = ComponentState.LOADED) -> Component:
    """``component`` driven to ``level`` by the manager, so a hand-built table can hold a level."""
    run(ComponentManager().migrate(component, level))
    return component


class Store(Component):
    """The leaf a query is about, and the thing a deployment may run once for the whole process."""


class Engine(Component):
    """A caller: what a tenant's own component looks like when it needs something process-wide."""

    store: Store = require()


class Contract(Protocol):
    """An interface some deployments bind to ``Store``: a name a requirement can be written by."""

    def read(self) -> str: ...


class TestAnsweringFromTheRoot:
    def test_a_qualified_caller_reaches_the_unqualified_instance(self) -> None:
        shared = Store()
        instances = {at(Store): shared}
        rule = answering_from_the_root(ComponentRegistry(), instances, only={Store})

        assert rule(at(Engine, "acme"), asking(Store), EffectScope()) is shared

    def test_the_caller_s_own_qualifier_comes_first(self) -> None:
        shared, own = Store(), Store()
        instances = {at(Store): shared, at(Store, "acme"): own}
        rule = answering_from_the_root(ComponentRegistry(), instances, only={Store})

        assert rule(at(Engine, "acme"), asking(Store), EffectScope()) is own
        assert rule(at(Engine, "globex"), asking(Store), EffectScope()) is shared

    def test_an_identity_the_deployment_did_not_name_is_not_shared(self) -> None:
        instances = {at(Store): Store()}
        rule = answering_from_the_root(ComponentRegistry(), instances, only=set())

        with pytest.raises(ServiceNotUpError) as raised:
            _ = rule(at(Engine, "acme"), asking(Store), EffectScope())

        # The refusal names where the caller's own qualifier put it, not the place the rule looked next.
        assert raised.value.key == at(Store, "acme")
        assert raised.value.asked_from == at(Engine, "acme")

    def test_the_root_reaches_nothing_beyond_itself(self) -> None:
        instances = {at(Store, "acme"): Store()}
        rule = answering_from_the_root(ComponentRegistry(), instances, only={Store})

        with pytest.raises(ServiceNotUpError) as raised:
            _ = rule(at(Engine), asking(Store), EffectScope())

        assert raised.value.key == at(Store)

    def test_the_root_instance_is_policed_from_the_caller_s_level(self) -> None:
        shared = brought_up(Store(), ComponentState.DRAINING)
        engine = brought_up(Engine(), ComponentState.ENABLED)
        instances = {at(Store): shared, at(Engine, "acme"): engine}
        rule = answering_from_the_root(ComponentRegistry(), instances, only={Store})

        with pytest.raises(ServiceNotUpError) as raised:
            _ = rule(at(Engine, "acme"), asking(Store), EffectScope())

        # The instance that was too low is named by its own key; who asked is still the caller.
        assert raised.value.key == at(Store)
        assert raised.value.asked_from == at(Engine, "acme")
        assert raised.value.level is ComponentState.DRAINING

    def test_nothing_up_anywhere_names_the_one_place_it_looked(self) -> None:
        rule = answering_from_the_root(ComponentRegistry(), {}, only={Store})

        with pytest.raises(ServiceNotUpError) as raised:
            _ = rule(at(Engine, "acme"), asking(Store), EffectScope())

        assert raised.value.key == at(Store, "acme")
        assert raised.value.level is None

    def test_an_interface_may_be_the_thing_that_crosses(self) -> None:
        registry = ComponentRegistry()
        registry.bind(Contract, Store)
        shared = Store()
        instances = {at(Store): shared}
        rule = answering_from_the_root(registry, instances, only={Contract})

        assert rule(at(Engine, "acme"), asking(Contract), EffectScope()) is shared

    def test_an_interface_nothing_answers_for_is_refused_where_the_rule_is_built(self) -> None:
        with pytest.raises(UnboundServiceError):
            _ = answering_from_the_root(ComponentRegistry(), {}, only={Contract})


class TestNestedCalls:
    """A call opened inside another asks from its own key, so a session inside a tenant is reachable.

    This is the only shape the one-hop rule leaves for a context nested under another: no requirement
    of a tenant's component is answered at a client's qualifier, and a call is what moves the reading.
    """

    def test_a_call_opened_inside_one_moves_what_is_read_from(self) -> None:
        shared, acme, client = Store(), Store(), Store()
        instances = {at(Store): shared, at(Store, "acme"): acme, at(Store, "acme/client"): client}
        rule = answering_from_the_root(ComponentRegistry(), instances, only={Store})

        async def main() -> list[Store]:
            seen: list[Store] = []
            async with open_call(at(Engine, "acme"), rule):
                seen.append(resolve(Store))
                async with open_call(at(Engine, "acme/client"), rule):
                    seen.append(resolve(Store))
                seen.append(resolve(Store))
            return seen

        # The tenant answers the request, the client answers the session, and the tenant answers
        # again once the inner call is over.
        assert run(main()) == [acme, client, acme]

    def test_a_reader_below_the_tenant_does_not_climb_to_it(self) -> None:
        instances = {at(Store, "acme"): Store()}
        rule = answering_from_the_root(ComponentRegistry(), instances, only={Store})

        async def main() -> None:
            async with open_call(at(Engine, "acme/client"), rule):
                _ = resolve(Store)

        with pytest.raises(ServiceNotUpError) as raised:
            run(main())

        # Two places are looked at, and the tenant above the caller is neither of them: the caller's
        # own qualifier, then the root.
        assert raised.value.key == at(Store, "acme/client")
        assert raised.value.asked_from == at(Engine, "acme/client")


class TestTheBatchFollowsThePolicy:
    """The places a read falls back to are the places a batch brings up, so the root needs no naming."""

    def test_a_dependant_that_comes_up_alone_brings_the_root_up_with_it(self) -> None:
        registry = ComponentRegistry()
        up: dict[ComponentKey, Component] = {}
        _ = registry.declare_all((Declaration(Store), Declaration(Engine, "acme")))
        assembly = Assembly(registry, up=up, rule=answering_from_the_root(registry, up, only={Store}))

        result = run(assembly.start(at(Engine, "acme")))

        assert result.completed == (at(Store), at(Engine, "acme"))

    def test_a_dependant_admitted_later_reads_the_root_that_is_already_up(self) -> None:
        registry = ComponentRegistry()
        up: dict[ComponentKey, Component] = {}
        _ = registry.declare_all((Declaration(Store), Declaration(Engine, "acme")))
        assembly = Assembly(registry, up=up, rule=answering_from_the_root(registry, up, only={Store}))
        _ = run(assembly.start(at(Engine, "acme")))

        result = run(assembly.admit(Declaration(Engine, "globex")))

        assert result.completed == (at(Engine, "globex"),)
        assert assembly.instance(at(Store, "globex")) is None
        shared = assembly.instance(at(Store))
        admitted = assembly.instance(at(Engine, "globex"))
        assert isinstance(shared, Store)
        assert isinstance(admitted, Engine)
        assert admitted.store is shared
