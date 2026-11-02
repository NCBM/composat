"""The call level: whose key the work is done for, what it resolves through, and where its effects go.

A call is the seam between a component's lifetime and a request's, so the cases here are about what
belongs to the call rather than to the instance: the key a resolve is made for, the scope an effect
lands in, and what is left behind when the block ends.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

import pytest

from composat import Component, ComponentKey, NoCallContextError, Reclaimable, RequirementSpec
from composat.context import current, current_or_none, open_call, put, resolve


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


class Store(Component):
    """A leaf, so a call has a contract to name."""


class Cache(Component):
    """A second leaf, so a nested call has somewhere else to point."""


def at(component: type[Component], instance: str = "") -> ComponentKey:
    """The key ``component`` answers at under ``instance``."""
    return ComponentKey(component.identity(), instance)


class Recorder:
    """A rule that answers with what it was given, and remembers how it was asked."""

    def __init__(self, answer: object = None) -> None:
        self.answer = answer
        self.asked: list[tuple[ComponentKey, RequirementSpec, object]] = []

    def __call__(self, key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable, /) -> object:
        self.asked.append((key, requirement, effects))
        return self.answer


class TestWhatIsBound:
    def test_a_call_is_readable_from_underneath(self) -> None:
        rule = Recorder()

        async def drive() -> None:
            async with open_call(at(Store, "tenant-a"), rule) as call:
                assert current() is call
                assert current_or_none() is call
                assert call.key == at(Store, "tenant-a")

        run(drive())

    def test_outside_a_call_asking_is_refused_and_probing_answers_none(self) -> None:
        assert current_or_none() is None
        with pytest.raises(NoCallContextError, match="no call is in progress"):
            _ = current()
        with pytest.raises(NoCallContextError):
            _ = resolve(Store)
        with pytest.raises(NoCallContextError):
            _ = put(lambda: None)

    def test_the_binding_comes_off_when_the_block_ends(self) -> None:
        async def drive() -> None:
            async with open_call(at(Store), Recorder()):
                assert current_or_none() is not None
            assert current_or_none() is None

        run(drive())

    def test_two_calls_side_by_side_stay_independent(self) -> None:
        rule = Recorder()

        async def served(instance: str) -> ComponentKey:
            async with open_call(at(Store, instance), rule):
                await asyncio.sleep(0)
                return current().key

        async def drive() -> list[ComponentKey]:
            return list(await asyncio.gather(served("a"), served("b")))

        assert run(drive()) == [at(Store, "a"), at(Store, "b")]


class TestResolving:
    def test_a_resolve_names_the_call_s_key_and_its_scope(self) -> None:
        rule = Recorder(answer="the instance")

        async def drive() -> None:
            async with open_call(at(Store, "tenant-a"), rule) as call:
                assert resolve(Store) == "the instance"
                key, _requirement, effects = rule.asked[0]
                assert key == at(Store, "tenant-a")
                assert effects is call.effects

        run(drive())

    def test_a_bare_type_asks_for_the_ordinary_one(self) -> None:
        # No extras, and a name that says where the requirement came from: a call never had a
        # declaration to carry routing, and pretending otherwise would invent one.
        rule = Recorder()

        async def drive() -> None:
            async with open_call(at(Store), rule):
                _ = resolve(Store)

        run(drive())

        assert rule.asked[0][1] == RequirementSpec("<resolve>", Store, ())

    def test_a_nested_call_shadows_the_outer_one_and_restores_it(self) -> None:
        rule = Recorder()

        async def drive() -> None:
            async with open_call(at(Store, "a"), rule) as outer:
                async with open_call(at(Cache, "b"), rule) as inner:
                    assert inner is not outer
                    assert current() is inner
                    assert current().key == at(Cache, "b")
                assert current() is outer
                assert current().key == at(Store, "a")

        run(drive())


class TestWhatBelongsToTheCall:
    def test_an_effect_registered_on_the_call_is_released_when_it_ends(self) -> None:
        released: list[str] = []

        async def drive() -> None:
            async with open_call(at(Store), Recorder()) as call:
                _ = put(lambda: released.append("first"))
                _ = call.put(lambda: released.append("second"))
                assert len(call.effects) == 2
            assert len(call.effects) == 0

        run(drive())

        assert released == ["second", "first"]

    def test_an_effect_is_released_while_the_call_it_belongs_to_is_still_bound(self) -> None:
        seen: list[bool] = []

        async def drive() -> None:
            async with open_call(at(Store), Recorder()):
                _ = put(lambda: seen.append(current_or_none() is not None))
            assert current_or_none() is None

        run(drive())

        assert seen == [True]

    def test_a_failing_body_still_releases_what_it_registered(self) -> None:
        released: list[str] = []

        async def body() -> None:
            async with open_call(at(Store), Recorder()):
                _ = put(lambda: released.append("released"))
                raise ValueError("body")

        with pytest.raises(ValueError, match="body"):
            run(body())

        assert released == ["released"]

    def test_a_release_that_fails_beside_the_body_is_reported_with_it(self) -> None:
        def refuse() -> None:
            raise RuntimeError("would not release")

        async def body() -> None:
            async with open_call(at(Store), Recorder()):
                _ = put(refuse)
                raise ValueError("body")

        with pytest.raises(ExceptionGroup) as caught:
            run(body())

        assert [type(error).__name__ for error in caught.value.exceptions] == ["ValueError", "ExceptionGroup"]

    def test_a_release_that_fails_on_its_own_is_raised(self) -> None:
        def refuse() -> None:
            raise RuntimeError("would not release")

        async def body() -> None:
            async with open_call(at(Store), Recorder()):
                _ = put(refuse)

        with pytest.raises(ExceptionGroup, match="recycling effects"):
            run(body())

    def test_a_call_has_a_scope_of_its_own_rather_than_sharing_one(self) -> None:
        async def drive() -> None:
            async with open_call(at(Store), Recorder()) as first, open_call(at(Store), Recorder()) as second:
                assert first.effects is not second.effects

        run(drive())
