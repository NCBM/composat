"""Driving one component: which hooks run, in what order, and what a failing one leaves behind.

The component here is a recorder, so a case can ask what the manager did rather than what the state
happens to be now. What the cases are about is the two readings a move makes: it commits each step
before running the hook that produces it, and a hook that says no takes its own step back — leaving
the instance where it truly is rather than where the move started.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

import pytest

from composat import (
    Component,
    ComponentManager,
    ComponentState,
    ComponentStateError,
    LifecycleReentryError,
)


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


class Recorder(Component):
    """A component that writes down each hook it was driven through, and where it stood in it."""

    def __init__(self, *failing: str) -> None:
        super().__init__()
        self.seen: list[str] = []
        self.states: list[ComponentState] = []
        self.failing = frozenset(failing)

    async def _note(self, hook: str) -> None:
        self.seen.append(hook)
        self.states.append(self.state.state)
        if hook in self.failing:
            raise RuntimeError(f"{hook} refused")

    async def load(self) -> None:
        await self._note("load")

    async def unload(self) -> None:
        await self._note("unload")

    async def reload(self) -> None:
        await self._note("reload")

    async def enable(self) -> None:
        await self._note("enable")

    async def disable(self) -> None:
        await self._note("disable")

    async def drain(self) -> None:
        await self._note("drain")


class Reentering(Component):
    """A component that reaches for the manager from inside its own hook."""

    def __init__(self, manager: ComponentManager) -> None:
        super().__init__()
        self.manager = manager

    async def load(self) -> None:
        await self.manager.migrate(self, ComponentState.ENABLED)


def loaded(component: Component) -> None:
    run(ComponentManager().migrate(component, ComponentState.LOADED))


def served(component: Component) -> None:
    run(ComponentManager().migrate(component, ComponentState.ENABLED))


class TestTheMove:
    def test_a_load_runs_the_one_hook_and_lands_loaded(self) -> None:
        component = Recorder()

        loaded(component)

        assert component.seen == ["load"]
        assert component.state.state is ComponentState.LOADED

    def test_a_target_is_reached_by_the_verbs_that_get_there(self) -> None:
        component = Recorder()

        served(component)

        assert component.seen == ["load", "enable"]

    def test_a_hook_runs_in_the_state_it_is_producing(self) -> None:
        # The commit is optimistic, which is what makes a hook able to ask "am I up" and be answered
        # about the world it is making rather than the one it is leaving.
        component = Recorder()

        served(component)

        assert component.states == [ComponentState.LOADED, ComponentState.ENABLED]

    def test_a_shutdown_walks_down_in_order(self) -> None:
        component = Recorder()
        served(component)

        run(ComponentManager().migrate(component, ComponentState.UNLOADED))

        assert component.seen == ["load", "enable", "drain", "disable", "unload"]
        assert component.state.state is ComponentState.UNLOADED

    def test_coming_out_of_service_keeps_what_was_loaded(self) -> None:
        component = Recorder()
        served(component)
        component.seen.clear()

        run(ComponentManager().migrate(component, ComponentState.LOADED))

        assert component.seen == ["drain", "disable"]
        assert component.state.state is ComponentState.LOADED

    def test_a_target_it_is_already_at_drives_nothing(self) -> None:
        component = Recorder()

        loaded(component)
        component.seen.clear()
        loaded(component)

        assert component.seen == []

    def test_what_is_running_is_readable_from_the_hook(self) -> None:
        pending: list[str | None] = []

        class Watching(Recorder):
            async def load(self) -> None:
                pending.append(self.state.pending)
                await super().load()

        component = Watching()
        run(ComponentManager().migrate(component, ComponentState.LOADED))

        assert pending == ["migrate to LOADED"]
        assert component.state.pending is None


class TestAFailingHook:
    def test_it_takes_back_its_own_step_and_no_more(self) -> None:
        component = Recorder("enable")

        with pytest.raises(RuntimeError, match="enable refused"):
            served(component)

        assert component.seen == ["load", "enable"]
        assert component.state.state is ComponentState.LOADED

    def test_a_failed_drain_leaves_it_serving(self) -> None:
        component = Recorder("drain")
        served(component)

        with pytest.raises(RuntimeError, match="drain refused"):
            run(ComponentManager().migrate(component, ComponentState.UNLOADED))

        assert component.state.state is ComponentState.ENABLED

    def test_a_failed_unload_still_counts_as_unloaded(self) -> None:
        # What it could not release is in an unknown state, and the epoch closes with it: a component
        # that says LOADED while its resources are half gone is the one reading nobody can act on.
        component = Recorder("unload")
        loaded(component)

        with pytest.raises(RuntimeError, match="unload refused"):
            run(ComponentManager().migrate(component, ComponentState.UNLOADED))

        assert component.state.state is ComponentState.UNLOADED

    def test_a_verb_from_the_wrong_state_is_refused_before_any_hook_runs(self) -> None:
        component = Recorder()

        with pytest.raises(ComponentStateError, match=r"cannot reload from UNLOADED"):
            run(ComponentManager().reload(component))

        assert component.seen == []


class TestTheEpoch:
    def test_unloading_releases_what_the_epoch_held_and_opens_the_next(self) -> None:
        released: list[str] = []
        component = Recorder()
        _ = component.effect_scope.put(lambda: released.append("store"))
        loaded(component)

        run(ComponentManager().migrate(component, ComponentState.UNLOADED))

        assert released == ["store"]
        assert len(component.effect_scope) == 0
        assert not component.effect_scope.dead

    def test_a_failed_unload_closes_the_epoch_anyway(self) -> None:
        released: list[str] = []
        component = Recorder("unload")
        loaded(component)
        _ = component.effect_scope.put(lambda: released.append("store"))

        with pytest.raises(RuntimeError, match="unload refused"):
            run(ComponentManager().migrate(component, ComponentState.UNLOADED))

        assert released == ["store"]
        assert not component.effect_scope.dead

    def test_the_next_epoch_takes_effects_again(self) -> None:
        component = Recorder()
        loaded(component)
        run(ComponentManager().migrate(component, ComponentState.UNLOADED))

        loaded(component)

        # A closed scope left behind by the unload would raise here rather than take the effect.
        _ = component.effect_scope.put(lambda: None)
        assert len(component.effect_scope) == 1


class TestReload:
    def test_it_rebuilds_what_is_loaded_and_leaves_the_state_alone(self) -> None:
        component = Recorder()
        served(component)

        run(ComponentManager().reload(component))

        assert component.seen[-1] == "reload"
        assert component.state.state is ComponentState.ENABLED


class TestTheGuard:
    def test_a_component_cannot_drive_its_own_lifecycle(self) -> None:
        manager = ComponentManager()
        component = Reentering(manager)

        with pytest.raises(LifecycleReentryError, match=r"migrate to ENABLED while migrate to LOADED"):
            run(manager.migrate(component, ComponentState.LOADED))
        assert component.state.state is ComponentState.UNLOADED
