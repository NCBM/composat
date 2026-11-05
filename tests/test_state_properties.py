"""The two tables as a property: a pair of levels is joined by the fewest verbs, and the walk is one.

``test_state.py`` walks four origins against four targets by hand, which is a sample of the graph and
not a statement about it. What is worth having instead is the reading the machine is designed around:
``LEAVING`` and ``ARRIVING`` decide the graph, the graph decides the distance between two levels, and
:meth:`Migration.steps` is a search over that graph and nothing else. The distance below is worked out
here, from the tables, so widening a table without widening the search fails the case that compares
them rather than a case that was written to match the search.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from composat import ComponentState, Operation
from composat.state import ARRIVING, LEAVING, Migration

states = st.sampled_from(tuple(ComponentState))


def distance(origin: ComponentState, target: ComponentState) -> int | None:
    """The fewest verbs the tables say join the two levels; ``None`` when they do not.

    One layer of the graph per step outward, which is a different shape from the search under test:
    the answer here is "how deep is the target", not "what path got there", so a search that reports
    a walk longer than the depth has nothing to agree with.
    """
    if origin is target:
        return 0
    frontier = {origin}
    for steps in range(1, len(ComponentState)):
        frontier = {
            arrival
            for state in frontier
            for operation in Operation
            if (arrival := ARRIVING[operation]) is not None and state in LEAVING[operation]
        }
        if target in frontier:
            return steps
    return None


@given(origin=states, target=states)
def test_the_tables_join_every_pair_of_levels(origin: ComponentState, target: ComponentState) -> None:
    # Strong connectivity as a fact about the tables: a refusal at the end of the search would mean
    # they stopped being that, and this is the case that would hear it first.
    assert distance(origin, target) is not None, f"{origin.name} cannot reach {target.name}"


@given(origin=states, target=states)
def test_a_walk_is_the_fewest_verbs_the_tables_allow(origin: ComponentState, target: ComponentState) -> None:
    steps = Migration(origin, target).steps()

    assert len(steps) == distance(origin, target)
    if origin is target:
        assert steps == ()


@given(origin=states, target=states)
@settings(max_examples=50)
def test_every_step_spans_a_move_the_tables_allow_and_lands_where_it_says(
    origin: ComponentState, target: ComponentState
) -> None:
    # The walk read as a chain: each verb is driven from a level it leaves, arrives where the tables
    # say it arrives, and the next verb is driven from there — so a step the search invented would
    # show up as a level no verb may leave.
    state = origin
    for step in Migration(origin, target).steps():
        arrival = ARRIVING[step]
        assert arrival is not None, f"{step} is not a move a migration can take"
        assert state in LEAVING[step], f"{step} leaves {LEAVING[step]}, not {state.name}"
        state = arrival
    assert state is target


@given(origin=states, target=states)
def test_a_walk_never_takes_a_step_that_keeps_the_level(origin: ComponentState, target: ComponentState) -> None:
    # A verb that arrives where it started is a step towards nothing, and the search would loop on it
    # rather than report a distance — so a walk is strictly the states it passes through.
    seen = [origin]
    for step in Migration(origin, target).steps():
        seen.append(ARRIVING[step] or seen[-1])
    assert len(set(seen)) == len(seen)
    assert seen[-1] is target


@given(origin=states, target=states)
def test_the_answer_does_not_depend_on_how_often_it_is_asked(origin: ComponentState, target: ComponentState) -> None:
    assert Migration(origin, target).steps() == Migration(origin, target).steps()


def test_the_verb_that_arrives_nowhere_is_never_a_step() -> None:
    # ``reload`` is the one operation with no arrival, and the asymmetry is the tables' rather than a
    # special case: what the search must never do is hand a caller a verb it cannot arrive from.
    idle = {operation for operation, arrival in ARRIVING.items() if arrival is None}
    walked = {
        step for origin in ComponentState for target in ComponentState for step in Migration(origin, target).steps()
    }

    assert idle
    assert walked.isdisjoint(idle)
