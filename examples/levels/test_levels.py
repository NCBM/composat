"""A batch is built first, then moved a level at a time without being rebuilt."""

from __future__ import annotations

from composat import ComponentIdentity, ComponentKey, ComponentState
from examples.levels.app import Refuser, Store, build
from examples.support import run


def key_of(cls: type) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls))


def test_a_batch_is_built_first_and_put_into_service_later() -> None:
    async def main() -> None:
        assembly = build(Store)
        _ = await assembly.start(key_of(Store), enable=False)
        store = assembly.instance(key_of(Store))
        assert isinstance(store, Store)
        assert store.stage == "loaded"
        assert store.state.state is ComponentState.LOADED

        moved = await assembly.enable(key_of(Store))

        assert moved.ok
        assert store.state.state is ComponentState.ENABLED
        assert store.stage == "enabled"

    run(main())


def test_quiescing_takes_it_out_of_service_and_keeps_the_instance() -> None:
    async def main() -> None:
        assembly = build(Store)
        _ = await assembly.start(key_of(Store))
        store = assembly.instance(key_of(Store))
        assert isinstance(store, Store)

        _ = await assembly.quiesce(key_of(Store))

        assert assembly.instance(key_of(Store)) is store
        assert store.state.state is ComponentState.LOADED
        assert store.stage == "disabled"

        _ = await assembly.migrate(key_of(Store), to=ComponentState.ENABLED)
        assert store.state.state is ComponentState.ENABLED

    run(main())


def test_a_component_whose_enable_refuses_stays_built() -> None:
    async def main() -> None:
        assembly = build(Refuser)
        _ = await assembly.start(key_of(Refuser), enable=False)

        result = await assembly.enable(key_of(Refuser))

        assert not result.ok
        assert list(result.failed) == [key_of(Refuser)]
        instance = assembly.instance(key_of(Refuser))
        assert instance is not None
        assert instance.state.state is ComponentState.LOADED

    run(main())
