"""Every committed step is reported as it lands, and the way down is reported the same way."""

from __future__ import annotations

from composat import ComponentIdentity, ComponentKey, ComponentState, Event, Operation
from examples.events.app import Engine, Rude, Store
from examples.levels.app import build
from examples.support import run


def key_of(cls: type) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls))


def steps(seen: list[Event]) -> list[tuple[str, Operation, ComponentState, ComponentState]]:
    return [(event.key.identity.qualname, event.operation, event.origin, event.arrival) for event in seen]


def test_a_key_coming_up_is_reported_step_by_step() -> None:
    async def main() -> None:
        assembly = build(Store, Engine)
        seen: list[Event] = []
        unsubscribe = assembly.observe(seen.append)

        _ = await assembly.start(key_of(Engine))
        unsubscribe()

        assert steps(seen) == [
            ("Store", Operation.load, ComponentState.UNLOADED, ComponentState.LOADED),
            ("Store", Operation.enable, ComponentState.LOADED, ComponentState.ENABLED),
            ("Engine", Operation.load, ComponentState.UNLOADED, ComponentState.LOADED),
            ("Engine", Operation.enable, ComponentState.LOADED, ComponentState.ENABLED),
        ]

    run(main())


def test_the_way_down_is_reported_dependants_first() -> None:
    async def main() -> None:
        assembly = build(Store, Engine)
        _ = await assembly.start(key_of(Engine))
        seen: list[Event] = []
        _ = assembly.observe(seen.append)

        _ = await assembly.stop(*assembly.keys())

        assert [(event.key.identity.qualname, event.operation) for event in seen] == [
            ("Engine", Operation.drain),
            ("Engine", Operation.disable),
            ("Engine", Operation.unload),
            ("Store", Operation.drain),
            ("Store", Operation.disable),
            ("Store", Operation.unload),
        ]

    run(main())


def test_unsubscribing_stops_that_one_sink() -> None:
    async def main() -> None:
        assembly = build(Store)
        seen: list[Event] = []
        unsubscribe = assembly.observe(seen.append)
        unsubscribe()

        _ = await assembly.start(key_of(Store))

        assert seen == []

    run(main())


def test_a_sink_that_raises_is_reported_apart_from_the_batch() -> None:
    async def main() -> None:
        assembly = build(Store)
        _ = assembly.observe(Rude())

        result = await assembly.start(key_of(Store))

        assert result.ok  # the key came up: what failed is the reporting
        assert [event.key for event, _error in result.observer_failures] == [key_of(Store)] * 2

    run(main())
