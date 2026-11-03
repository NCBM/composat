"""The dependency graph: what it refuses, and the two orders it answers.

The graph carries three invariants — acyclic, edges join declared nodes, in-use nodes survive — and
these cases pin each from the side that a caller can observe. Ordering is delegated to
``graphlib``, so the cases that matter are the shapes a component graph actually reaches: a diamond,
independent islands, and a chain deep enough to sort across many levels.
"""

from __future__ import annotations

import pytest

from composat import DependencyCycleError, DependencyGraph, DependencyInUseError


def diamond() -> DependencyGraph[str]:
    graph: DependencyGraph[str] = DependencyGraph()
    graph.add("store")
    graph.add("api", depends_on=("store",))
    graph.add("worker", depends_on=("store",))
    graph.add("app", depends_on=("api", "worker"))
    return graph


class TestDeclaring:
    def test_nodes_come_back_in_declaration_order(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        graph.add("b")
        graph.add("a")

        assert graph.nodes() == ("b", "a")

    def test_a_dependency_named_at_declaration_time_must_already_be_declared(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()

        with pytest.raises(KeyError):
            graph.add("app", depends_on=("store",))

    def test_a_node_may_be_declared_before_what_it_needs(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        graph.add("app")
        graph.add("store")
        graph.link("app", "store")

        assert graph.order() == ("store", "app")

    def test_declaring_one_node_twice_is_refused(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        graph.add("store")

        with pytest.raises(ValueError, match="already declared"):
            graph.add("store")

    def test_the_same_edge_twice_is_refused(self) -> None:
        graph = diamond()

        with pytest.raises(ValueError, match="already depends on"):
            graph.link("api", "store")

    def test_a_node_may_not_need_itself(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()

        with pytest.raises(DependencyCycleError):
            graph.add("store", depends_on=("store",))


class TestCycles:
    def test_closing_a_cycle_reports_the_path(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        graph.add("a")
        graph.add("b", depends_on=("a",))
        graph.add("c", depends_on=("b",))

        with pytest.raises(DependencyCycleError) as raised:
            graph.link("a", "c")

        assert raised.value.cycle == ("a", "c", "b", "a")

    def test_a_refused_edge_leaves_the_graph_orderable(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        graph.add("a")
        graph.add("b", depends_on=("a",))
        with pytest.raises(DependencyCycleError):
            graph.link("a", "b")

        assert graph.order() == ("a", "b")

    def test_a_batch_that_closes_a_cycle_is_refused_as_a_whole(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        graph.add("a")
        graph.add("b", depends_on=("a",))

        with pytest.raises(DependencyCycleError):
            graph.add("c", depends_on=("a", "b", "c"))

        assert graph.nodes() == ("a", "b")


class TestRemoving:
    def test_a_node_nothing_needs_goes_away(self) -> None:
        graph = diamond()
        graph.remove("app")

        assert set(graph.nodes()) == {"store", "api", "worker"}

    def test_removing_a_node_drops_the_edges_that_touched_it(self) -> None:
        graph = diamond()
        graph.remove("app")
        graph.remove("api")

        assert graph.dependencies("worker") == frozenset({"store"})

    def test_a_node_something_needs_is_refused(self) -> None:
        graph = diamond()

        with pytest.raises(DependencyInUseError) as raised:
            graph.remove("store")

        assert raised.value.node == "store"
        assert raised.value.dependents == frozenset({"api", "worker"})

    def test_an_unknown_node_is_a_key_error(self) -> None:
        graph = diamond()

        with pytest.raises(KeyError):
            graph.remove("ghost")


class TestReadingEitherEndOfAnEdge:
    def test_dependents_is_what_the_removal_refuses_on(self) -> None:
        graph = diamond()

        assert graph.dependents("store") == frozenset({"api", "worker"})
        assert graph.dependents("app") == frozenset()

    def test_dependents_of_an_unknown_node_is_a_key_error(self) -> None:
        with pytest.raises(KeyError):
            _ = diamond().dependents("ghost")


class TestOrdering:
    def test_a_diamond_puts_the_shared_node_first_and_the_root_last(self) -> None:
        order = diamond().order()

        assert order[0] == "store"
        assert order[-1] == "app"
        assert set(order[1:3]) == {"api", "worker"}

    def test_independent_islands_are_ordered_in_one_pass(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        graph.add("b")
        graph.add("d")
        graph.add("a", depends_on=("b",))
        graph.add("c", depends_on=("d",))

        order = graph.order()

        assert order.index("b") < order.index("a")
        assert order.index("d") < order.index("c")

    def test_every_edge_points_forwards_in_the_loading_order(self) -> None:
        graph: DependencyGraph[str] = DependencyGraph()
        for index in range(12):
            graph.add(f"n{index}", depends_on=[f"n{other}" for other in range(max(0, index - 3), index)])

        order = graph.order()

        for node in graph.nodes():
            for dependency in graph.dependencies(node):
                assert order.index(dependency) < order.index(node)

    def test_the_reverse_order_is_the_loading_order_backwards(self) -> None:
        graph = diamond()

        assert graph.reverse_order() == tuple(reversed(graph.order()))
