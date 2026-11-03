"""One component type, one instance per tenant: the qualifier is the address, and a call is made for one.

Admission is the other half of it: a connection arrives, so its declaration arrives with it, and it
leaves the same way. What a tenant reads is answered at that tenant's own qualifier, never at another.
"""

from __future__ import annotations

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Declaration,
    require,
)


class Database(Component):
    """Takes its own construction argument, which is what ``Declaration(args=...)`` is for."""

    tenant: str
    rows: dict[str, str]

    def __init__(self, tenant: str) -> None:
        super().__init__()
        self.tenant = tenant
        self.rows = {}


class Service(Component):
    db: Database = require()

    async def put(self, key: str, value: str) -> None:
        self.db.rows[key] = value


def key_of(cls: type[Component], tenant: str) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls), instance=tenant)


def declaration_of(tenant: str) -> tuple[Declaration, ...]:
    """The two declarations a tenant needs, in the order they are admitted."""
    return (
        Declaration(Database, instance=tenant, args=(tenant,)),
        Declaration(Service, instance=tenant),
    )


def build() -> Assembly:
    return Assembly(ComponentRegistry(), up={})
