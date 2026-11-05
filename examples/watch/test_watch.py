"""A burst becomes one reload, and nothing outlives the instance that was watching."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from composat import ComponentIdentity, ComponentKey
from examples.levels.app import build
from examples.support import run
from examples.watch.app import Watcher


def key_of(cls: type) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls))


async def settle() -> None:
    """Long enough for one debounce window to pass, short enough to keep the suite quick."""
    await asyncio.sleep(0.05)


async def edits(queue: asyncio.Queue[str]) -> AsyncIterator[str]:
    """What a watcher hands the loop: one edit per push, in the order they landed."""
    while True:
        yield await queue.get()


def test_a_burst_of_edits_becomes_one_reload() -> None:
    async def main() -> None:
        assembly = build(Watcher)
        _ = await assembly.start(key_of(Watcher))
        watcher = assembly.instance(key_of(Watcher))
        assert isinstance(watcher, Watcher)

        await watcher.note("a")
        await watcher.note("b")
        await watcher.note("c")
        await settle()

        assert watcher.reloads == ["c"]

    run(main())


def test_two_bursts_are_two_reloads() -> None:
    async def main() -> None:
        assembly = build(Watcher)
        _ = await assembly.start(key_of(Watcher))
        watcher = assembly.instance(key_of(Watcher))
        assert isinstance(watcher, Watcher)

        await watcher.note("a")
        await watcher.note("b")
        await settle()
        await watcher.note("c")
        await settle()

        assert watcher.reloads == ["b", "c"]

    run(main())


def test_unloading_the_instance_takes_the_timer_with_it() -> None:
    async def main() -> None:
        assembly = build(Watcher)
        _ = await assembly.start(key_of(Watcher))
        watcher = assembly.instance(key_of(Watcher))
        assert isinstance(watcher, Watcher)

        await watcher.note("a")
        _ = await assembly.stop(key_of(Watcher))
        await settle()

        assert watcher.reloads == []

    run(main())


def test_the_watch_loop_follows_its_source_and_stops_with_the_instance() -> None:
    async def main() -> None:
        assembly = build(Watcher)
        _ = await assembly.start(key_of(Watcher))
        watcher = assembly.instance(key_of(Watcher))
        assert isinstance(watcher, Watcher)

        queue: asyncio.Queue[str] = asyncio.Queue()
        await watcher.watch(edits(queue))
        queue.put_nowait("a")
        queue.put_nowait("b")
        await settle()
        assert watcher.reloads == ["b"]

        queue.put_nowait("c")
        await settle()
        assert watcher.reloads == ["b", "c"]

        _ = await assembly.stop(key_of(Watcher))
        queue.put_nowait("d")
        await settle()
        assert watcher.reloads == ["b", "c"]

    run(main())
