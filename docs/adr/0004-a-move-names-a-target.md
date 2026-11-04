# 4. A move names a target, and a failure undoes its own step

**Status**: accepted, 2026-11-04.

## Context

An instance sits at one of four levels, and the verbs that move it are coroutines that can raise or be
cancelled. A caller that says "bring this tenant up" should not have to write out the sequence, and a
hook that fails halfway must not leave a state that is not true: reporting `UNLOADED` while `load` sits
on sockets nothing will release is worse than reporting a failure.

## Decision

The machine is two tables, `LEAVING` and `ARRIVING`, and a verb is one row of each. A move is a
`Migration(origin, target)` whose steps are the fewest verbs that reach the target (breadth first, over
the same two tables), so a caller names a level and the verbs fall out.

One `asyncio.Lock` per instance is held for the whole move, and the arrival is committed before the
hook runs, so a hook reads the state it is producing. `ComponentStateControl._commit` answers an undo;
a hook that raises takes back its own step and nothing else, because what already ran did run. `unload`
is the one step not taken back — a component that could not release what it held is still down — and a
failed or undone step that leaves the instance at `UNLOADED` closes the epoch with it.

`reload` is the verb that is not a migration: it has preconditions and no arrival, so it is driven by
`ComponentManager.reload` rather than by a one-step migration.

## Consequences

- Every level is reachable from every other, so a refusal at the end of the search means the tables
  stopped being strongly connected.
- A cancelled move is the one case left open: releasing an epoch is an await, and awaiting while being
  cancelled does not finish, so the epoch of a cancelled `load` is released by whoever holds the
  instance next.
- An observer sees both the arrival and, when a hook raised, the move that did not hold — reported as
  `Committed(undone=True)`.

## Alternatives considered

- **Transitional states** (`LOADING`, `DISABLING`, `UNLOADING`). They flip the optimistic commit — the
  rule that a hook runs inside the state it is producing — and every read of `state` would grow a
  branch. A `pending` field answers "what is happening" without touching the state space.
- **`transaction(...)` handing out `(set, advance)`.** The `set(origin)` closure exists only to serve a
  hand-written rollback; once the machine is data, the context manager owns the rollback and `advance`
  is the only writer.
- **Whole-trip rollback** (`migrate(..., restore=...)`, back to the origin on failure). Completed hooks
  are not undone, so returning to the origin would be a claim the instance cannot back: the state would
  say `UNLOADED` while the resources `load` took are still held. Rolling back one step keeps every
  reported state true, and `start`'s batch undo walks down from wherever each key actually is.
- **One batch skeleton parameterised by failure policy** (`_walk(*keys, strict=, enable=)`). `replace` is
  not a batch, and the shared skeleton would carry two parameters that exist for it alone. `start` and
  `stop` keep their own loops plus the small pieces they actually share.
- **Narrowing the reentry guard to the instance being moved**, so that a component may drive another.
  Then the plan has two sources: what a hook brings up is not in `order()`, so `stop`'s reverse order and
  admission's "already up" both drift. "A root brings up what it needs" is done by the assembly's
  closure instead, and there is one entrance.
