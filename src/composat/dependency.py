"""The dependency graph and its ordering

The graph is **mutable**: nodes and edges come and go at runtime, so ordering is recomputed on
every query. `graphlib.TopologicalSorter` schedules a *fixed* graph — after `prepare()` it refuses
`add()` for good, and dropping a node or an edge is beyond it. It is therefore used here only as a
**one-shot ordering primitive**: build it from the current adjacency, walk it, drop it.

Three invariants hold whatever order the caller works in:

- **acyclic** — a `link()` that would close a cycle is refused on the spot, with the cycle path
  reported, so the graph can always be ordered;
- **dependencies are nodes** — an edge joins declared nodes, so the graph stays in step with the
  registry that declares them;
- **in-use nodes survive** — `remove()` refuses a node that something still depends on.

Unknown keys raise `KeyError` (the membership semantics of a dict); a duplicate `add()` or `link()`
raises `ValueError` (declaring the same thing twice is a caller bug, and quietly overwriting would
hide a name collision).
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from graphlib import TopologicalSorter
from typing import Generic

from typing_extensions import TypeVar

Node = TypeVar("Node", bound=Hashable, default=Hashable)


class DependencyError(RuntimeError):
    """The graph refused a change, because the invariants above are what it exists to carry."""


class DependencyCycleError(DependencyError, Generic[Node]):
    """The `link()` would have closed a cycle; ``cycle`` is that path (first and last node equal)."""

    cycle: tuple[Node, ...]

    def __init__(self, cycle: tuple[Node, ...]) -> None:
        super().__init__(f"dependency cycle: {' -> '.join(str(node) for node in cycle)}")
        self.cycle = cycle


class DependencyInUseError(DependencyError, Generic[Node]):
    """A node was left in place: things depend on it. ``node`` and ``dependents`` say what and who."""

    node: Node
    dependents: frozenset[Node]

    def __init__(self, node: Node, dependents: frozenset[Node]) -> None:
        names = ", ".join(sorted(repr(item) for item in dependents))
        super().__init__(f"{node!r} is still depended on by: {names}")
        self.node = node
        self.dependents = dependents


class DependencyGraph[Node: Hashable]:
    """An acyclic graph whose edge ``node → dependency`` reads "node needs dependency".

    ``order()`` is the loading order (dependencies first) and ``reverse_order()`` the unloading
    order (dependents first). Keys are any hashable: which key identifies a component is the
    registry's business.
    """

    _dependencies: dict[Node, set[Node]]
    _dependents: dict[Node, set[Node]]

    def __init__(self) -> None:
        self._dependencies = {}
        self._dependents = {}

    def nodes(self) -> tuple[Node, ...]:
        """The declared nodes, in declaration order."""
        return tuple(self._dependencies)

    def add(self, node: Node, *, depends_on: Iterable[Node] = ()) -> None:
        """Declare ``node``, with the nodes it needs. Rejected as a whole if anything is off.

        Dependencies have to be declared already; when the declarations arrive in some other
        order, ``add`` every node first and wire the edges with :meth:`link`.
        """
        if node in self._dependencies:
            raise ValueError(f"node is already declared: {node!r}")
        dependencies = tuple(depends_on)
        if node in dependencies:
            raise DependencyCycleError((node, node))
        for dependency in dependencies:
            self._require_declared(dependency)
            self._require_no_cycle(node, dependency)
        self._dependencies[node] = set()
        self._dependents[node] = set()
        for dependency in dependencies:
            self._connect(node, dependency)

    def remove(self, node: Node) -> None:
        """Drop ``node`` and every edge touching it; refused while something depends on it."""
        self._require_declared(node)
        dependents = self._dependents[node]
        if dependents:
            raise DependencyInUseError(node, frozenset(dependents))
        for dependency in self._dependencies[node]:
            self._dependents[dependency].discard(node)
        del self._dependencies[node]
        del self._dependents[node]

    def link(self, node: Node, dependency: Node) -> None:
        """Make ``node`` need ``dependency``: one edge, both already declared."""
        self._require_declared(node)
        self._require_declared(dependency)
        self._require_no_cycle(node, dependency)
        if dependency in self._dependencies[node]:
            raise ValueError(f"{node!r} already depends on {dependency!r}")
        self._connect(node, dependency)

    def dependencies(self, node: Node) -> frozenset[Node]:
        """What ``node`` needs — direct edges only."""
        self._require_declared(node)
        return frozenset(self._dependencies[node])

    def dependents(self, node: Node) -> frozenset[Node]:
        """What needs ``node`` — the same edges read from their other end.

        ``remove`` answers this question internally and refuses on the answer, so a caller that has
        to know *before* it commits — a batch of removals that is only valid as a whole — reads it
        here rather than learning it one node at a time.
        """
        self._require_declared(node)
        return frozenset(self._dependents[node])

    def order(self) -> tuple[Node, ...]:
        """Loading order: dependencies first."""
        adjacency = {node: set(dependencies) for node, dependencies in self._dependencies.items()}
        return tuple(TopologicalSorter(adjacency).static_order())

    def reverse_order(self) -> tuple[Node, ...]:
        """Unloading order: dependents first. The reverse of a valid order is itself valid."""
        return tuple(reversed(self.order()))

    def _require_declared(self, node: Node) -> None:
        if node not in self._dependencies:
            raise KeyError(node)

    def _require_no_cycle(self, node: Node, dependency: Node) -> None:
        """``node → dependency`` closes a cycle when ``node`` is reachable *from* ``dependency``."""
        if node == dependency:
            raise DependencyCycleError((node, dependency))
        path = self._path(dependency, node)
        if path is not None:
            raise DependencyCycleError((node, *path))

    def _path(self, source: Node, target: Node) -> tuple[Node, ...] | None:
        """The dependency chain from ``source`` to ``target``, or ``None``."""
        pending: list[tuple[Node, tuple[Node, ...]]] = [(source, (source,))]
        seen: set[Node] = set()
        while pending:
            current, path = pending.pop()
            if current == target:
                return path
            if current not in seen:
                seen.add(current)
                pending.extend((item, (*path, item)) for item in self._dependencies[current])
        return None

    def _connect(self, node: Node, dependency: Node) -> None:
        self._dependencies[node].add(dependency)
        self._dependents[dependency].add(node)
