"""A swap moves the key to the new generation, and tells whoever was reading the old one."""

from __future__ import annotations

from composat import Declaration
from composat.std.wait import serving
from examples.replace.app import Client, Store, build, key_of
from examples.support import run


def test_a_swap_changes_the_instance_and_tells_the_dependants() -> None:
    async def main() -> None:
        assembly = build()
        _ = await assembly.start(key_of(Client))
        before = assembly.instance(key_of(Store))
        assert isinstance(before, Store)

        _ = await assembly.replace(key_of(Store), Declaration(Store, args=("host-b",)))

        after = assembly.instance(key_of(Store))
        assert isinstance(after, Store)
        assert after is not before
        assert after.dsn == "host-b"
        assert before.dsn == "host-a"

        client = assembly.instance(key_of(Client))
        assert isinstance(client, Client)
        assert client.noticed == ["store"]

    run(main())


def test_a_reader_waits_the_swap_out_rather_than_polling() -> None:
    async def main() -> None:
        assembly = build()
        _ = await assembly.start(key_of(Client))

        serving_key = key_of(Store)
        _ = await assembly.replace(serving_key, Declaration(Store, args=("host-c",)))

        store = await serving(assembly, serving_key)
        assert isinstance(store, Store)
        assert store.dsn == "host-c"

    run(main())
