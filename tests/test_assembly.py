"""The seam: what a declared requirement reads, and what the assembly hands that read.

The rule is exercised as a function over a table rather than through a running assembly, which is
the whole point of it being one: what a field read gets is decided by the qualifier and by what is
up, and neither needs a manager to test. What the assembly adds is the table the rule reads, so that
is what its own cases are about.
"""

from __future__ import annotations

from typing import Protocol

import pytest

from composat import (
    Assembly,
    AssemblyResult,
    Component,
    ComponentKey,
    ComponentRegistry,
    EffectScope,
    Replacement,
    RequirementResolver,
    ServiceNotUpError,
    UnboundServiceError,
)
from composat.assembly import default_rule
from composat.scope import Disposable, Revoke
from composat.spec import RequirementSpec


class Store(Component):
    """A leaf: other components ask for it."""


class Engine(Component):
    store: Store


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
