"""A start brings up what its roots require; a failed component is reported, not fatal."""

from __future__ import annotations

from composat import ComponentIdentity, ComponentKey
from examples.host.app import App, Broken, Store, build
from examples.support import run


def key_of(cls: type) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls))


def test_a_root_brings_up_what_it_requires() -> None:
    async def main() -> None:
        assembly = build()
        result = await assembly.start(key_of(App))

        assert result.ok
        assert [key.identity.qualname for key in result.completed] == ["Store", "Engine", "App"]

        app = assembly.instance(key_of(App))
        assert isinstance(app, App)
        # A field read is live: it answers the instance the assembly holds, not a copy taken earlier.
        assert app.engine.store is assembly.instance(key_of(Store))
        assert app.engine.store.opened

    run(main())


def test_one_failure_leaves_the_rest_serving() -> None:
    async def main() -> None:
        assembly = build()
        result = await assembly.start(key_of(Broken), key_of(App))

        assert not result.ok
        assert list(result.failed) == [key_of(Broken)]
        assert key_of(App) in result.completed

    run(main())


def test_stopping_everything_is_one_line() -> None:
    async def main() -> None:
        assembly = build()
        _ = await assembly.start(key_of(App))
        store = assembly.instance(key_of(Store))
        assert isinstance(store, Store)

        result = await assembly.stop(*assembly.keys())

        assert result.ok
        assert assembly.keys() == ()
        assert not store.opened

    run(main())
