# What a hook must not wait for

This page is for the `await` inside a lifecycle hook or an observer: the ways a batch hangs without
anything raising, and what to write instead. `reading-through-a-swap.md` is the same question asked
from a request handler; this is the view from inside a move.

## The one shape every hang has

A move produces the events that announce it, and those events are delivered **between** moves, never
inside one (`Assembly.observe`). So a hook that waits for something its own call will produce is
waiting for itself: the call cannot continue until the hook returns, and the event cannot be delivered
until the call continues. Nothing is refused, no error is raised, and the batch sits there until
somebody cancels or times it out.

What the framework refuses is the other half of the same problem — driving the assembly from inside it
— and it refuses it at the point of the ask: `LifecycleReentryError` for a hook (the guard follows the
task, so a task a hook starts is refused too) and a note in `Assembly.observe` for an observer, which
runs inside the batch it is reporting. What is left, and what this page is about, is the waiting: no
guard can tell your `await` from a slow one.

## The hazards

| the hook | what happens | what to write instead |
|---|---|---|
| awaits `composat.std.wait.serving(key)` for a key the running call is moving | the publish it waits for is staged until the call ends, so it waits for its own return | read the field — it already answers the new generation — and hand the follow-up to the caller, or to a task started after the batch |
| awaits a key the same batch has not reached yet | the batch waits for the hook, the hook waits for the batch | declare it (`require()`) so it is brought up first, or bring it up in an earlier call |
| awaits long work in `drain` with no bound | the swap window stays open for as long as the work runs, and nothing inside the instance cuts it short | `composat.std.drain.InFlight` for what the instance started, plus a bound of your own for request-shaped work — and write the hook so a second entry is harmless, because a teardown cut by a cancellation is driven again from the level it reached |
| runs a blocking call (`time.sleep`, a sync client, `future.result()`) on the loop | not a hang but a stall: the loop stops and every waiter in the process stops with it | the async form of the call, or `asyncio.to_thread` where there is none |
| awaits `serving` with no deadline | the wait outlives the deployment that was going to satisfy it | `asyncio.timeout` around every wait a caller can put a bound on |
| drives the assembly, or starts a task that does | `LifecycleReentryError` from the hook; from an observer, a move racing the batch it is reporting | say what is needed (`require()`, `provider_changed`) and let the call do it |

## Recognising one

A hung batch holds the keys it claimed and nothing is moving them: `Assembly.in_transit(key)` answers
the claim, so the key looks busy while no task is making progress. The task stack then says which wait
is unsatisfiable, and the question to ask of that frame is the one above — *who produces what I am
waiting for, and can they run while I hold this?*

## What is fine to wait for

Anything another task can advance: a queue, a connection pool, a **different** key moved by a
concurrent call, a `Future` your own code resolves. The rule is narrower than "hooks must not await":
a hook must not wait for the batch it is part of.
