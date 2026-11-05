# 5. One writer per key, nothing queued, and a report that cannot refuse

**Status**: accepted, 2026-11-04.

## Context

Two calls can arrive for the same key at once — a tenant torn down while its replacement is being built,
one connection admitted while the reaper withdraws another. Without a claim, both read "not up" and both
build: a key taken down and built again while its first generation is still unloading. Two tenants that
share nothing must still be able to run side by side.

Separately, a batch has to hand back what happened. A hook that fails, a dependant that objects to being
told, an application handler that raises on the way out of a move: each of these is a different fact, and
a caller needs to read them apart.

## Decision

A verb claims the keys it names, and the keys it derives, from the moment it accepts them until the
last of them is done (`Assembly._claim`). The claim is a region with no `await` in it: the event loop
already refuses to interleave two such regions, so nothing here is locked and unrelated keys keep
running. A second call that names a claimed key is refused with `KeyInTransitError`, whose message
carries the holder's own phrase ("being brought up", "being moved to ENABLED").

The claim is also readable without taking one (`Assembly.in_transit`), and it carries the level the
move ends at. That second half is what a waiter needs: a key being published and a key being taken
down are both in transit, and waiting for the second is waiting for a level the caller cannot use.

Nothing is queued, and no verb invents an order between two callers.

A batch that comes up short is reported (`AssemblyResult`, `Replacement`); what is wrong with the
call itself — a key nothing declares, a key listed twice, a key already up — is raised before the first
instance exists. `stop` takes down exactly the keys it was given and passes over a key that is not up,
which is what makes `finally: await stop(*assembly.keys())` safe to run unconditionally. `admit` refuses
a key that is already declared, and leaves the declaration standing when the key fails to come up; what
clears it is `evict`, what retries it is `start`.

Observers receive `Event`s synchronously, staged under the instance's lock and delivered outside it. A
handler cannot refuse a move, cannot stop the others, and its failure is reported per event in
`observer_failures` rather than raised into the batch.

## Consequences

- A caller that wants to wait for a key in transit holds its own lock; the refusal tells it what the
  other call is doing. Where the wait is worth making, `std.wait.serving` reads the claim: it waits out
  a swap, and refuses a key at rest below serving instead of spending the caller's deadline on it.
- An observer that has to await queues the event (`put_nowait`) and owns the task that drains it.
- `ok` stays a reading about the batch: a batch whose keys all came up came up, whatever the sinks said.

## Alternatives considered

- **One coarse lock around `start` / `stop` / `replace` / `admit` / `evict`.** It serializes tenants that
  share nothing, and `asyncio.Lock` is not reentrant — hooks and observers run inside the verb's own task —
  so it would need a reentry guard on top before it stopped hanging.
- **Queueing a second verb on a key.** The framework would be inventing an order between two callers; a
  hook that stalls becomes an invisible wait, and two callers can wait on each other.
- **`admit` as "ensure it is running"** (already declared counts as success). That swallows the mistake of
  never calling `evict` on a dead connection. `admit` keeps `start`'s reading: a declaration is not
  "running", and a key that is already declared is a caller error.
- **`stop()` with no keys tearing everything down**, or a separate `stop_all()`. The same thing said
  twice; the single call site is `stop(*assembly.keys())`, and a no-op empty batch is what makes the
  `finally` idempotent.
- **Observers typed `async` (or `None | Awaitable[None]`).** Delivering with an `await` cannot happen in
  the `finally` of a cancelled path (`ComponentManager.migrate` already ran into that), and it would push
  `_retract` and `replace`'s error paths around. An observer that truly needs asynchrony queues; `put_nowait`
  is synchronous.
- **A sync→async bridge** (an `awaitlet`-style adapter). Crossing inside staging means awaiting under the
  lock; crossing inside delivery turns a metrics sink into a thread boundary, and neither exceptions nor
  cancellation belong to the batch any more.
- **Letting a subscriber declare it is concurrency-safe**, so one event's handlers can be gathered. That is
  a promise the application makes and the framework cannot check, and `_deliver` would split into two paths
  with two cancellation semantics. A sink that needs it queues and gathers on its own.
- **An observer that can refuse or stop propagation.** This stream is a broadcast of committed facts, and a
  subscriber that could retract a fact contradicts reporting `undone` truthfully. The need is real and
  already has places to live: `_require_startable`, `_require_settled`, a hook raising, and the whole-batch
  check `admit` and `evict` do before touching anything.
- **Letting an observer's exception through.** A handler that raises is a metrics bug, and if it
  propagated, that bug would become a deployment failure. A failed handler and a failed batch are
  different facts, and both are reported.
