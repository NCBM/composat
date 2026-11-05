"""What a component type declares: its requirements, its capabilities, and its epoch.

A requirement is an annotation naming a contract; everything else an annotation may name is an
ordinary attribute. These cases pin that line from both sides, because it is the one place the
framework decides what to look at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Protocol, override

import pytest

from composat import ComponentIdentity, ComponentKey, Reclaimable, UnboundRequirementError, require
from composat.component import Component, attach, capabilities_of, requirements_of
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
    store: Store = require()
    cache: CacheService = require()
    # Annotated and never assigned: the point of them is that an annotation on its own is an ordinary
    # attribute, so what a case reads here is the classification rather than a value.
    resource: Redis  # pyright: ignore[reportUninitializedInstanceVariable]
    notes: list[str]  # pyright: ignore[reportUninitializedInstanceVariable]


MARKER = object()


class Tagged(Component):
    store: Annotated[Store, MARKER] = require()


class Named(Component):
    """A requirement whose contract is written at the declaration site, with no annotation."""

    store = require(Store)  # pyright: ignore[reportUnannotatedClassAttribute]


class Early(Component):
    """A contract written below its own class: the forward reference Python allows."""

    later: Later = require()


class Later(Component):
    """The contract ``Early`` names before it exists."""


class Waiting(Component):
    """A second forward reference, left unasked so a marker can be installed on it first."""

    later: Later = require()


class Unasked(Component):
    """A third, settled by a read off the class rather than by an ask of the map."""

    later: Later = require()


if TYPE_CHECKING:

    class Nowhere:
        """Read by the checker alone: at runtime the name is absent, which is the case below."""


class Orphan(Component):
    """A name that never resolves: the first ask is where that is reported."""

    ghost: Nowhere = require()


class TestClassification:
    def test_a_component_and_a_protocol_are_requirements(self) -> None:
        assert requirements_of(Reader) == (
            RequirementSpec("store", Store),
            RequirementSpec("cache", CacheService),
        )

    def test_a_contract_the_class_body_does_not_mark_is_an_ordinary_attribute(self) -> None:
        # The annotation names a protocol, which used to be the whole of the declaration. It is not
        # one now: what the class body marks is a requirement, and this one marked nothing.
        class Unmarked(Component):
            cache: CacheService  # pyright: ignore[reportUninitializedInstanceVariable]

        assert requirements_of(Unmarked) == ()

    def test_any_other_annotation_is_an_ordinary_attribute(self) -> None:
        names = {requirement.name for requirement in requirements_of(Reader)}
        assert "resource" not in names
        assert "notes" not in names

    def test_an_ordinary_attribute_keeps_whatever_the_constructor_put_there(self) -> None:
        reader = Reader()
        reader.resource = Redis()
        assert isinstance(reader.resource, Redis)

    def test_the_extras_of_an_annotated_requirement_are_carried_untouched(self) -> None:
        (requirement,) = requirements_of(Tagged)
        assert requirement.extras == (MARKER,)

    def test_a_subclass_inherits_what_its_base_asks_for(self) -> None:
        class Child(Reader):
            pass

        assert requirements_of(Child) == requirements_of(Reader)

    def test_a_subclass_that_marks_one_of_its_own_answers_for_both(self) -> None:
        class Child(Reader):
            other: Store = require()

        assert requirements_of(Child) == (*requirements_of(Reader), RequirementSpec("other", Store))

    def test_the_contract_may_be_written_at_the_declaration_site(self) -> None:
        assert requirements_of(Named) == (RequirementSpec("store", Store),)


class TestAMuddledMarker:
    def test_a_contract_the_annotation_disagrees_with_is_refused(self) -> None:
        def declares() -> None:
            class Contradicting(Reader):  # pyright: ignore[reportUnusedClass]
                store: CacheService = require(Store)  # pyright: ignore[reportIncompatibleVariableOverride, reportAssignmentType]

        with pytest.raises(ValueError, match=r"declared for Store and annotated CacheService"):
            declares()

    def test_a_marker_over_something_that_is_not_a_contract_is_refused(self) -> None:
        def declares() -> None:
            class MarkedList(Component):  # pyright: ignore[reportUnusedClass]
                notes: list[str] = require()

        with pytest.raises(ValueError, match="notes is a requirement whose annotation names something"):
            declares()


class TestReadingARequirement:
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

        key = ComponentKey(ComponentIdentity.of(Reader))
        attach(reader, key, resolver)

        assert reader.store is store
        assert asked == [(key, RequirementSpec("store", Store))]

    def test_a_requirement_is_not_assignable(self) -> None:
        def resolver(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable) -> object:
            _ = key, requirement, effects
            return Store()

        reader = Reader()
        attach(reader, ComponentKey(ComponentIdentity.of(Reader)), resolver)
        with pytest.raises(AttributeError, match="not assignable"):
            reader.store = Store()  # type: ignore[assignment]


class TestCapabilities:
    def test_a_component_that_writes_nothing_can_do_nothing(self) -> None:
        assert capabilities_of(Store) == ()

    def test_a_component_that_writes_hooks_reports_them_in_a_fixed_order(self) -> None:
        class Full(Component):
            @override
            async def unload(self) -> None: ...
            @override
            async def load(self) -> None: ...

        assert capabilities_of(Full) == ("load", "unload")

    def test_a_base_that_writes_a_hook_is_inherited(self) -> None:
        class Base(Component):
            @override
            async def unload(self) -> None: ...

        class Child(Base):
            pass

        assert capabilities_of(Child) == ("unload",)

    def test_the_base_itself_claims_nothing(self) -> None:
        assert capabilities_of(Component) == ()

    def test_an_override_counts_once(self) -> None:
        class Base(Component):
            @override
            async def unload(self) -> None: ...

        class Overriding(Base):
            @override
            async def unload(self) -> None: ...

        assert capabilities_of(Overriding) == ("unload",)

    def test_a_misspelled_hook_is_not_a_capability(self) -> None:
        class Typo(Component):
            async def laod(self) -> None: ...

        assert capabilities_of(Typo) == ()


class TestTheEpoch:
    def test_two_instances_never_share_one(self) -> None:
        assert Store().effect_scope is not Store().effect_scope


class TestLateDeclarations:
    def test_a_requirement_installed_after_the_class_was_made_is_refused(self) -> None:
        class Late(Component):
            pass

        Late.store = require()  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(ValueError, match=r"Late was handed store after it was made"):
            _ = requirements_of(Late)

    def test_an_unrelated_attribute_may_still_be_set_on_the_class(self) -> None:
        class Plain(Component):
            pass

        Plain.helper = staticmethod(lambda: 1)  # pyright: ignore[reportAttributeAccessIssue]
        assert capabilities_of(Plain) == ()


class TestAForwardReference:
    def test_a_contract_written_below_its_class_is_settled_at_the_first_ask(self) -> None:
        assert requirements_of(Early) == (RequirementSpec("later", Later),)
        assert Early.later == RequirementSpec("later", Later)

    def test_a_read_off_the_class_settles_it_too(self) -> None:
        assert Unasked.later == RequirementSpec("later", Later)

    def test_a_marker_installed_on_a_class_still_waiting_is_refused(self) -> None:
        Waiting.extra = require()  # pyright: ignore[reportAttributeAccessIssue]
        with pytest.raises(ValueError, match=r"Waiting was handed extra after it was made"):
            _ = requirements_of(Waiting)

    def test_a_name_that_never_resolves_fails_at_the_first_ask(self) -> None:
        with pytest.raises(NameError, match="Nowhere"):
            _ = requirements_of(Orphan)


class TestWhatTheFrameworkReads:
    def test_a_class_cannot_answer_the_readings_the_framework_drives(self) -> None:
        class Quiet(Component):
            store: Store = require()

            @classmethod
            def requirements(cls) -> tuple[RequirementSpec, ...]:
                return ()

            @classmethod
            def capabilities(cls) -> tuple[str, ...]:
                return ("load", "unload")

            @classmethod
            def identity(cls) -> ComponentIdentity:
                return ComponentIdentity("somewhere", "Quiet")

        assert [spec.name for spec in requirements_of(Quiet)] == ["store"]
        assert capabilities_of(Quiet) == ()
        assert ComponentIdentity.of(Quiet).module == __name__
