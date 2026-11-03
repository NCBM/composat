"""A unit of work: what one call resolves once, however deep the code asking sits.

The ledger is nobody's component. It is opened by the rule the deployment installs, on the first ask
inside a call, and released when that call ends — and what makes it *one* ledger is the call's own
memory: ask twice, or ask from plain code three frames down, and the same object answers.

Nothing here takes the ledger as an argument, which is the whole difference this example is about.
"""

from __future__ import annotations

from collections.abc import Mapping

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Declaration,
    Reclaimable,
    RequirementResolver,
    RequirementSpec,
    default_rule,
    require,
    resolve,
)


class Store(Component):
    """What outlives a call: the rows written, and the units of work let go."""

    written: int = 0
    released: int = 0

    def __init__(self) -> None:
        super().__init__()
        self.written = 0
        self.released = 0


class Ledger:
    """One call's unit of work: the rows it collected, and a close the store counts."""

    store: Store
    rows: list[str]

    def __init__(self, store: Store) -> None:
        self.store = store
        self.rows = []

    def close(self) -> None:
        """Give the unit of work up: the call's scope runs this once, however often it was asked."""
        self.store.released += 1


def units(registry: ComponentRegistry, instances: Mapping[ComponentKey, Component]) -> RequirementResolver:
    """Answer components the way the framework does, and open one ledger per call for ``Ledger``.

    ``Ledger`` is declared nowhere, and it is not a requirement of anything: the assembly will never
    bring one up. It is what a *call* opens, which is why the rule is where it is named — and the
    call's memory is what turns two asks into one ledger.
    """
    shared = default_rule(registry, instances)

    def answer(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable, /) -> object:
        if requirement.service is not Ledger:
            return shared(key, requirement, effects)
        store = instances[ComponentKey(ComponentIdentity.of(Store))]
        assert isinstance(store, Store)  # the page requires it, so the batch brought it up
        ledger = Ledger(store)
        _ = effects.put(ledger.close)
        return ledger

    return answer


class Page(Component):
    """The component: it does not take the ledger, it asks the call for it."""

    store: Store = require()

    async def render(self, text: str) -> str:
        ledger = resolve(Ledger)
        ledger.rows.append(text)
        self.store.written += 1
        return f"{len(ledger.rows)}:{text}"


def audit() -> int:
    """Plain code underneath, two frames down: nobody handed it anything, the call answers."""
    return len(resolve(Ledger).rows)


def build() -> tuple[Assembly, ComponentKey]:
    registry = ComponentRegistry()
    up: dict[ComponentKey, Component] = {}
    _ = registry.declare_all((Declaration(Store), Declaration(Page)))
    page_key = ComponentKey(ComponentIdentity.of(Page))
    return Assembly(registry, up=up, rule=units(registry, up)), page_key
