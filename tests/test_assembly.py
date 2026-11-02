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
    Component,
    ComponentKey,
    ComponentRegistry,
    EffectScope,
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
