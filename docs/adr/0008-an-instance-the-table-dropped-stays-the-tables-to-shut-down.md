# 8. An instance the table dropped stays the table's to shut down

**Status**: accepted, 2026-11-05.

## Context

Two paths take an instance out of the table before driving it down, and both have an `await` between
the removal and the last hook:

- `replace` commits the key to the new generation, then tells the dependants, then takes the old one
  down, then publishes the new one. The notify walk is the await in the middle.
- `_retract` pops the key, then drives the instance down — `drain`, `disable`, `unload` — which is
  three hooks, two of them awaited, on the ordinary `stop`/`evict` path and on a cancelled batch's
  way back.

A `CancelledError` in either window unwinds the call with the instance answering nothing and owned by
nobody: not in the table, not in a result, not in a claim. `instances()`, `stop(*keys())` and every
other verb pass over it, and the epoch it holds is released only by `reopen_epoch`, which nothing
reaches. An `EffectScope` has no finaliser that could do it — disposal is an authored `await` — so the
effects of an unreachable instance are held until the process exits.

Measured before the fix, with one gated hook and one effect registered per hook entry:

| cancelled in | effects still held after the host's shutdown |
|---|---|
| `stop`, `drain` | 3 |
| `stop`, `disable` | 4 |

The swap window is the same hole with the old generation as what is lost: the call that would have
unloaded it was the one that got cancelled.

Both contradict what the batch path promises (`_retract_all`: "the table the cancelled batch left is
one a host can still shut down with one call") and what `ComponentManager.migrate` leans on when it
says a cancelled move's epoch is "released by whatever holds the instance next": after either window,
nobody holds it.

A consumer cannot express the state from outside. There is no accessor for a dropped instance,
`Assembly.instance` answers nothing or its successor, and `ComponentManager.migrate` needs the
reference that was lost. `asyncio.shield` around the verb avoids it and was the workaround adopted
downstream, which is what makes this a defect rather than a blocker.

## Decision

The assembly records the debt. One list per key holds the instances the table no longer answers and
still owes a teardown: `_owe` writes into it, `_discharge` takes an instance out once its teardown
returns, and `_retract` drives everything owed under the key down in the order the instances stopped
answering.

What the record buys is a debt, not a move the assembly takes over: `replace` still gives the claim
back when it is abandoned, and a cancelled `stop` still leaves its keys unclaimed. The teardown is
owed; the rest of the move is not.

### The readings a caller can rely on

1. **Nothing it built is unreachable.** From the moment an instance stops being what a key answers
   with, a reference to it is in `_owed[key]` until its teardown returns: `_owe` runs in the same step
   as the removal and `_discharge` in the same step as the return, so no cancellation can fall into
   the gap.
2. **`keys()` is total, `keys(owing=False)` is what answers.** A key that answers *and* owes — the
   state an abandoned swap leaves — appears once in each, so neither list has to be deduplicated by
   its caller.
3. **One call settles all of it.** `stop(*keys())` drives every debt down and discharges it even when a
   hook refuses, so after it returns `keys() == ()` whatever the report says. That is what makes
   `finally: await assembly.stop(*assembly.keys())` the one teardown a host writes.
4. **An owed key answers nothing.** `instance` is `None` from the moment of the pop and `instances()`
   is the up table, so a dependant reading during the teardown sees a miss rather than an instance on
   its way out. The debt is bookkeeping about a shut-down instance, not a second answer for the key.

### What a reader sees, and why the debt is not waited for

`in_transit` is the reading that says whether a level is about to change and where it is going, and it
is a claim: it is taken by a verb and given back when that call ends, cancelled or not. So an
abandoned move leaves `in_transit` `None` and the table saying "not answering", which is the truth —
nothing is driving that key, and a reader that waited would be waiting for a call that no longer
exists.

`composat.std.wait.serving` reads those two together and gets the half it needs from the claim, and the
half it was missing from the debt: the refusal that names an absence now says which one
(`ServiceNotUpError.owing`), because a key listed while answering nothing went down and a key not
listed was never built. Neither is coming back on its own — the first wants the host's teardown, the
second a declaration and a `start` — so a wait that promised otherwise would burn the caller's
deadline on it.

An abandoned swap's own key is the remaining reading, and the one this decision leaves alone: the
commit happened and the publish step did not, so the key answers the new generation at `LOADED`,
`in_transit` is `None`, and a reader that needs it serving is refused with that level until someone
levels it — `migrate(key, to=ENABLED)`.

## Consequences

- The debt is the host's to pay: a host that never runs its teardown still leaks the epoch, the same
  contract every other failure path has.
- Dependants the notify walk had not reached are not told, and nothing re-runs the walk. Telling is a
  notification, and re-running it is safe only for handlers the application knows to be idempotent.
- A hook that refuses to let go does not hold up the rest of the debt: every remaining instance is
  still driven down, and its refusal is kept on the first one's chain (`_chain`) rather than dropped.
  Being second says nothing about whether a hook wants fixing.
- The list holds one entry per abandoned move, so several cancellations on one key keep several
  instances owed; each goes on the first retraction of that key.
- A key that is only a debt may be brought up again — `_require_startable` asks about the up table, not
  the debt — and the new instance answers while the old one is paid on the next retraction of the key.
  The debt belongs to the key, not to the instance that comes after it.
- A batch's own rollback (`_retract_all`) speaks for the keys it brought up. A debt an earlier call
  left elsewhere is the host's `stop(*keys())` to pay, and is not swept up by an unrelated failure.

## Alternatives considered

- **Own the tail.** From the commit on, run the notify walk, the teardown and the publish step as a
  task the assembly owns, so a cancellation abandons the wait rather than the move, and the claim is
  released when the tail ends. A reader would then still find a move in flight after the cancel, which
  is its own way of telling a key that is coming back from one that is gone. Refused for now: a claim
  that outlives its call means the caller no longer decides when a key is free, and the tail needs a
  place to live and a way to be drained on shutdown — a change to what a claim means, worth its own
  decision.
- **Commit after the teardown.** The gap `replace` exists to avoid: the key would answer nothing while
  its provider unloads.
- **Teardown from the handler.** Catch `CancelledError`, await the teardown in a fresh call, re-raise.
  A caller cancelled twice lands in the same hole one level down, and `ComponentManager` already says
  why: awaiting while being cancelled is not guaranteed to finish.
- **Leave the instance in the table until its teardown ends.** Then a dependant reading during the
  teardown would be handed an instance on its way out, which is the reading the table exists to deny,
  and a cancelled teardown would leave a key that answers a dying instance. It would also make the
  debt invisible to `keys(owing=False)` and to every verb that iterates the up table as a teardown
  list.
- **Expose the debt as a table of its own** (`owes(key) -> tuple[Component, ...]`, or the instances
  themselves). Nothing in the framework or in `composat.std` needs more than "is this key owed" — the
  wait needs exactly that — and an accessor for a dropped instance invites a caller to drive one
  through the manager behind the assembly's back, which is the ownership this decision avoids
  claiming.
- **`asyncio.shield` at the call site.** A discipline the framework would have to demand and cannot
  check; it also leaves the abandoned state unrepresentable when a consumer forgets it.