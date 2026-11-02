"""What a component type declares: its requirements, its capabilities, and its epoch.

A requirement is an annotation naming a contract; everything else an annotation may name is an
ordinary attribute. These cases pin that line from both sides, because it is the one place the
framework decides what to look at.
"""

from __future__ import annotations

from typing import Annotated, Protocol

import pytest

from composat import ComponentKey, EffectScope, Reclaimable, UnboundRequirementError
from composat.component import Component, attach
from composat.spec import RequirementSpec


class Store(Component):
    """A leaf: no requirements of its own."""


class CacheService(Protocol):
    def get(self, key: str) -> str: ...


class Redis:
    """A plain resource: not a component, and not a protocol. The framework ignores it."""

    def get(self, key: str) -> str:
        return key


class Reader(Component):
    store: Store
    cache: CacheService
    resource: Redis
    notes: list[str]


MARKER = object()


class Tagged(Component):
    store: Annotated[Store, MARKER]


class TestClassification:
    def test_a_component_and_a_protocol_are_requirements(self) -> None:
        assert Reader.requirements() == (
            RequirementSpec("store", Store),
            RequirementSpec("cache", CacheService),
        )

    def test_any_other_annotation_is_an_ordinary_attribute(self) -> None:
        names = {requirement.name for requirement in Reader.requirements()}
        assert "resource" not in names
        assert "notes" not in names

    def test_an_ordinary_attribute_keeps_whatever_the_constructor_put_there(self) -> None:
        reader = Reader()
        reader.resource = Redis()
        assert isinstance(reader.resource, Redis)

    def test_the_extras_of_an_annotated_requirement_are_carried_untouched(self) -> None:
        (requirement,) = Tagged.requirements()
        assert requirement.extras == (MARKER,)

    def test_a_subclass_inherits_what_its_base_asks_for(self) -> None:
        class Child(Reader):
            pass

        assert Child.requirements() == Reader.requirements()

    def test_the_descriptor_is_on_the_class_from_the_moment_it_exists(self) -> None:
        assert isinstance(vars(Reader)["store"], object)
        assert "store" in dir(Reader)


class TestReadingARequirement:
    def test_the_class_answers_with_the_declaration(self) -> None:
        assert isinstance(Reader.__dict__["store"], object)
        assert Reader.requirements()[0].name == "store"

    def test_an_instance_with_nothing_attached_says_so(self) -> None:
        with pytest.raises(UnboundRequirementError, match=r"Reader\.store"):
            _ = Reader().store

    def test_a_bound_instance_asks_its_resolver_at_its_own_key(self) -> None:
        asked: list[tuple[ComponentKey, RequirementSpec]] = []
        store = Store()
        reader = Reader()

        # The resolver is written against the seam's own type, which is what makes the signature the
        # framework promises: ``put()`` is the whole of what a rule may ask the epoch for, and a rule
        # narrowed to ``EffectScope`` would be asking for more than it is handed.
        def resolver(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable) -> object:
            asked.append((key, requirement))
            assert effects is reader.effect_scope
            return store

        key = ComponentKey(Reader.identity())
        attach(reader, key, resolver)

        assert reader.store is store
        assert asked == [(key, RequirementSpec("store", Store))]

    def test_a_requirement_is_not_assignable(self) -> None:
        def resolver(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable) -> object:
            _ = key, requirement, effects
            return Store()

        reader = Reader()
        attach(reader, ComponentKey(Reader.identity()), resolver)
        with pytest.raises(AttributeError, match="not assignable"):
            reader.store = Store()  # type: ignore[assignment]

    def test_the_framework_s_operations_are_not_members_of_the_type(self) -> None:
        # What the framework does to an instance is a function in the module, so there is nothing for
        # an application to override: a member by that name would be its own method, not the read.
        assert not hasattr(Component, "attach")
        assert not hasattr(Component, "resolve")

    def test_a_member_named_like_the_read_path_does_not_take_it_over(self) -> None:
        class Loud(Reader):
            def resolve(self, requirement: RequirementSpec) -> object:
                raise AssertionError("the read path is not dispatch on the component")

        store = Store()

        def resolver(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable) -> object:
            _ = key, requirement, effects
            return store

        loud = Loud()
        attach(loud, ComponentKey(Loud.identity()), resolver)

        assert loud.store is store


class TestCapabilities:
    def test_a_component_that_writes_nothing_can_do_nothing(self) -> None:
        assert Store.capabilities() == ()

    def test_a_component_that_writes_hooks_reports_them_in_a_fixed_order(self) -> None:
        class Full(Component):
            async def unload(self) -> None: ...
            async def load(self) -> None: ...

        assert Full.capabilities() == ("load", "unload")

    def test_a_base_that_writes_a_hook_is_inherited(self) -> None:
        class Base(Component):
            async def unload(self) -> None: ...

        class Child(Base):
            pass

        assert Child.capabilities() == ("unload",)

    def test_the_base_itself_claims_nothing(self) -> None:
        assert Component.capabilities() == ()

    def test_an_override_counts_once(self) -> None:
        class Base(Component):
            async def unload(self) -> None: ...

        class Overriding(Base):
            async def unload(self) -> None: ...

        assert Overriding.capabilities() == ("unload",)

    def test_a_misspelled_hook_is_not_a_capability(self) -> None:
        class Typo(Component):
            async def laod(self) -> None: ...

        assert Typo.capabilities() == ()


class TestTheEpoch:
    def test_an_instance_is_born_with_an_empty_scope(self) -> None:
        assert len(Store().effect_scope) == 0

    def test_two_instances_never_share_one(self) -> None:
        assert Store().effect_scope is not Store().effect_scope

    def test_reopening_closes_the_old_scope_and_answers_a_fresh_one(self) -> None:
        import asyncio

        scope = EffectScope()
        features = []

        def release() -> None:
            features.append("released")

        _ = scope.put(release)
        fresh = asyncio.run(scope.reopen())

        assert features == ["released"]
        assert scope.dead
        assert len(fresh) == 0
        with pytest.raises(RuntimeError, match="closed"):
            _ = scope.put(release)


class TestLateDeclarations:
    def test_a_requirement_added_after_the_freeze_is_refused(self) -> None:
        class Late(Component):
            pass

        Late.__annotations__ = {"store": Store}
        with pytest.raises(ValueError, match="after its fields were frozen"):
            _ = Late.requirements()

    def test_an_unrelated_attribute_may_still_be_set_on_the_class(self) -> None:
        class Plain(Component):
            pass

        Plain.helper = staticmethod(lambda: 1)  # type: ignore[attr-defined]
        assert Plain.capabilities() == ()
