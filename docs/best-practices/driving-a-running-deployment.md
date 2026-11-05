# Driving a running deployment

This page is for the code that owns the process rather than the code that serves a request: an admin
endpoint, a reaper, a host's lifespan. It says which verb is which decision, in what order to make
them, and what the numbers in between mean. `docs/architecture.md` says what holds; this says what to
do with it.

## Five decisions, five calls

| the deployment decides | the call |
|---|---|
| a row is new | `await assembly.admit(Declaration(...), enable=...)` |
| a row changed and the class did not | `registry.redeclare(declaration)` then `await assembly.enable(key)` |
| the implementation behind a key changes | `await assembly.replace(key, Declaration(...))` |
| a tenant stops, and may come back | `await assembly.quiesce(*keys)` |
| a tenant goes away | `await assembly.evict(*keys)` |

A few calls in this shape are easy to get wrong, and each is wrong in a way that shows up later rather
than here:

- **`admit` is not "make sure it is running".** It refuses a key that is already declared, because a
  caller that never withdraws a dead connection is a caller whose next admission should fail loudly. An
  update is therefore `redeclare` + `enable`, or `replace`.
- **A failed `admit` leaves the declaration standing.** The batch reports the key in `failed` and the
  declaration stays, which is what lets a network blip be retried with `start`; what clears it is
  `evict`. Nothing is hidden: ADR 0005 names a key that is declared and not up as a state of its own.
- **`evict` refuses a key something still needs** (`DependencyInUseError`, naming who needs it). Order
  the withdrawals the way `stop` orders a teardown — dependants first — or the call tells you which one
  you forgot.
- **`start` brings up the keys you name, and what they require.** A declaration nothing requires stays
  down — declared, never built, its `enable` never reached — however it got into the table, so a host
  that means "everything I declared is running" names those keys itself.

## Every verb is one writer, so a host must retry

A verb speaks for the keys it names until it is done, and a second call naming one of them is refused
with `KeyInTransitError` rather than queued (ADR 0005). That includes `stop`: a host's shutdown is
`stop(*assembly.keys())`, and a tenant being swapped by an admin request at that moment makes the whole
shutdown raise instead of taking the other keys down.

So the host wraps every write in one bounded retry, the shutdown included:

```python
async def driving(call: Callable[[], Awaitable[AssemblyResult]], *, attempts: int = 20) -> AssemblyResult:
    for _ in range(attempts):
        try:
            return await call()
        except KeyInTransitError:
            await asyncio.sleep(0.05)
    raise
```

A swap is milliseconds, so a window in seconds is generous. What the retry must not do is loop without
a bound: the framework refuses rather than queues because two callers that disagree about whether a
tenant should be serving must not be settled by whichever was scheduled second, and a retry forever
puts that decision back but with nobody awake to make it.

The retry is the caller's answer to a refusal; a hook has the opposite problem, since it runs *inside*
the call and cannot retry its way out of waiting for it ([what a hook must not wait for](what-a-hook-must-not-wait-for.md)).

## The three numbers

There are exactly three durations in this shape, and they are chosen in this order:

1. **The retired generation's `drain`** bounds the swap. `replace` builds the new generation, moves the
   key to it, and then tears the old one down; that teardown walks through `DRAINING`, where `drain`
   runs. So `drain` is both "how long the old generation finishes what it accepted" and "how long the
   key answers a generation that is built and not serving". Give it a bound, and put in it what must
   not be cut off: requests already streaming, not just background tasks (`composat.std.drain.InFlight`
   tracks tasks an instance started, so request work needs its own count).
2. **The reader's deadline** (`asyncio.timeout` around `serving`) has to be longer than a drain, or a
   swap that is going perfectly well still turns into a timeout. It should be shorter than the
   ingress's own patience, so the failure is a `503` this process wrote rather than a connection the
   client gave up on.
3. **The shutdown's drain** is the same hook, on the way out, and it is where a host spends its
   `lifespan` budget. Stopping in parallel with a swap does not work: the shutdown waits for the swap.

The third one follows from the first only if `drain` is bounded: a drain that waits forever is a
shutdown that never returns, and nothing in the framework has a clock. If a deployment cannot bound it,
bound the call — `asyncio.timeout` around `stop` — and accept that what is still running is cancelled
with the epoch.

## A loop or a timer is an effect

A component that starts a task or sets a timer has the instance's own `effect_scope` for it, and putting
it there is the whole of the bookkeeping:

```python
self.effect_scope.put(asyncio.create_task(self.follow(source)).cancel)  # ends with the instance
self.effect_scope.after(self.delay, self.flush)  # one call, later, cancelled if the instance goes first
```

Both are `Disposable`s: a callable, awaited if it is awaitable, released last-first like everything else
in the scope ([ADR 0006](../adr/0006-effects-belong-to-the-innermost-lifetime.md)). The scope belongs to
the instance, so a `reload` closes the old epoch's scope before the new instance opens its own.

Three habits:

- **Register what you start instead of keeping it.** A `self._task` and a `finally` make the same promise
  somewhere it can be forgotten, and `stop` has no reason to reach into an attribute.
- **A poll is a timer nobody registered.** `after` runs one effect once and drops it when the instance
  goes; a sleep loop hides how many times it ran, and keeps waking in an epoch that may already be over.
- **A failure in an `after` effect has no caller.** It goes to the loop's exception handler, named by
  `name_of` — which reads `__name__`, so a `lambda` reports `<lambda>` and the log line stops naming
  which timer fired.

## Bringing two credentials side by side

The swap above keeps the address and changes what is built behind it, which is the common case. A
deployment that keeps two credentials alive at once — because a workspace is being moved from one to
the other — is four steps, and the order is the whole of it:

```
admit the new provider (enable=True)  →  move the routing table  →  quiesce the old  →  evict the old
```

The table moves *after* the new one is serving, because a reader that arrives between the two steps is
answered by whatever the table says — and a table pointing at a key that is not up yet is a
`ServiceNotUpError` at request time rather than a refusal at configuration time. The withdrawal comes
last because in-flight requests still hold the old instance; `quiesce` takes it out of service and
leaves it built, which is the level a grace period needs.

## The rule a batch closes over

`Assembly(registry, up=..., rule=...)` takes the rule a *read* is answered by. A batch needs one more
thing: where each requirement may be found, so it can bring up what a key needs. `start` derives that
from the rule's candidate list, so the rule has to be a `Policy` — the type that carries one — rather than
a function that happens to answer.

A hand-written function is therefore a good rule and a poor batch: the reads answer, and `start` fails
with a `KeyError` naming the asker's own qualifier, because that is where the closure fell back to. Keep
the base and hand its places over:

```python
base = answering_from_the_root(registry, up, only={Dispatcher, Store})
rule = Policy(lambda key, requirement, effects: per_call_or(base(key, requirement, effects)), base.places)
```

Or build the rule out of `answering_from`, `joined` and `answering_from_the_root`, which are `Policy`
objects already. The symptom to recognise: the lookup answers and the batch refuses — the closure lost
the places, and nothing about the read is wrong.

## Stopping the process

A host teardown is four steps, and the order is not interchangeable:

1. **`stop(*assembly.keys())`**, inside the retry above: one call takes down everything the assembly
   holds, the keys a cancelled teardown still owes included.
2. **Drain what your own observers queued.** An observer is synchronous and is handed every move, so a
   sink that writes them down (`put_nowait` into a queue, per ADR 0005) still holds the last
   generation's events after `stop` returns. Those are the `unload`s, and they are the rows a reader of
   the ledger expects to find.
3. **Unsubscribe**, so a task that is still finishing cannot write into a pool nobody owns any more.
4. **Close the pools and the database**, in the reverse order of how they were opened.

Steps 2 and 3 are the ones a shutdown that "works" skips: the process exits either way, and what is
lost is the tail of the record rather than anything that would fail a request.
