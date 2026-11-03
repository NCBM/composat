"""Tenants side by side: each is admitted, read and withdrawn without touching the others."""

from __future__ import annotations

import pytest

from composat.context import current, open_call
from examples.support import run
from examples.tenants.app import Database, Service, build, declaration_of, key_of


def test_each_tenant_reads_its_own_instance() -> None:
    async def main() -> None:
        assembly = build()
        _ = await assembly.admit(*declaration_of("a"), *declaration_of("b"))

        service_a = assembly.instance(key_of(Service, "a"))
        service_b = assembly.instance(key_of(Service, "b"))
        assert isinstance(service_a, Service)
        assert isinstance(service_b, Service)
        assert service_a.db.tenant == "a"
        assert service_b.db.tenant == "b"
        assert service_a.db is not service_b.db

        await service_a.put("row", "from-a")
        assert service_b.db.rows == {}

    run(main())


def test_a_call_reads_at_the_qualifier_it_was_opened_for() -> None:
    async def main() -> None:
        assembly = build()
        _ = await assembly.admit(*declaration_of("a"), *declaration_of("b"))

        async with open_call(key_of(Service, "b"), assembly.rule):
            assert current().resolve(Database).tenant == "b"

        async with open_call(key_of(Service, "a"), assembly.rule):
            assert current().resolve(Database).tenant == "a"

    run(main())


def test_a_tenant_leaves_and_the_others_keep_serving() -> None:
    async def main() -> None:
        assembly = build()
        _ = await assembly.admit(*declaration_of("a"), *declaration_of("b"))

        _ = await assembly.evict(key_of(Service, "a"), key_of(Database, "a"))

        assert assembly.instance(key_of(Service, "a")) is None
        assert assembly.instance(key_of(Service, "b")) is not None
        with pytest.raises(KeyError):
            _ = await assembly.start(key_of(Service, "a"))

    run(main())


def test_a_tenant_that_arrives_late_is_admitted_at_run_time() -> None:
    async def main() -> None:
        assembly = build()
        _ = await assembly.admit(*declaration_of("a"))

        result = await assembly.admit(*declaration_of("c"))

        assert result.ok
        assert key_of(Service, "c") in result.completed
        assert assembly.instance(key_of(Service, "c")) is not None

    run(main())
