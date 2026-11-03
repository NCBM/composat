"""One component, no assembly: a rule that answers with a double, and a case that reads through it."""

from __future__ import annotations

from composat import (
    ComponentIdentity,
    ComponentKey,
    Reclaimable,
    RequirementSpec,
    attach,
    capabilities_of,
    requirements_of,
)
from examples.doubles.app import FakeRepo, Repo, Service
from examples.support import run


def test_the_type_says_what_it_asks_for() -> None:
    assert [spec.name for spec in requirements_of(Service)] == ["repo"]
    assert requirements_of(Service)[0].service is Repo
    assert capabilities_of(Service) == ()


def test_a_component_is_driven_with_a_rule_of_its_own() -> None:
    async def main() -> None:
        double = FakeRepo()
        service = Service()
        key = ComponentKey(ComponentIdentity.of(Service))

        def rule(_key: ComponentKey, _requirement: RequirementSpec, _effects: Reclaimable, /) -> object:
            return double

        attach(service, key, rule)

        assert await service.run() == "fake"
        assert service.repo is double

    run(main())
