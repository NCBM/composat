# 6. An effect belongs to the innermost lifetime it is registered in

**Status**: accepted, 2026-11-04.

## Context

Three lifetimes nest inside one another: the process (the assembly), an instance between `load` and
`unload` (an epoch), and the work one caller is doing (a call). Something has to be released —
a transaction, a temporary file, a lease, a background task — and releasing it at the wrong one of the
three either leaks it or takes it away from a reader still using it.

## Decision

Every release is a registration on an `EffectScope`, and the scope a resolver receives is the
innermost lifetime the read happens inside: the call's scope when a call is in force, otherwise the
instance's epoch (`composat.component.read`). A call's scope is created by `open_call` and released on
the way out, before the binding comes off; an epoch is closed and replaced by `reopen_epoch`, whose new
scope is the one the next epoch starts with.

The seam a resolver may use is `Reclaimable.put`, so what holds effects is swappable and a resolver
cannot reach past it.

A call remembers what it resolved (`CallContext.resolve`, one answer per contract), because a rule that
builds a per-request object has nowhere else to keep it. That memory dies with the call: a snapshot is
wrong only when it outlives the work it was taken for. A field read does not go through it — a
component reading what it declares is reading its own address, which stays live.

Background work goes on the scope too: `put(task.cancel)` is the floor (whoever starts it registers it,
and a released epoch cancels it), and `composat.std.drain.InFlight` is the component built on it —
`add(work)` tracks the task, `drain(after)` waits, then cuts, then waits for the cut to land.

## Consequences

- A per-call object can be shared across methods without the framework keeping a table: the caller that
  opened the call owns it and passes it (`examples/calls`), or asks for it twice and gets one answer
  (`examples/units`).
- A release registered during a release still lands in the scope the release belongs to, because the
  binding comes off last and a closed scope refuses new effects.
- A task that outlives its call is the caller's leak; the scope it inherited is closed.

## Alternatives considered

- **`EffectScope` as a component.** The per-call key space is unbounded, the lifetime belongs to
  `open_call` rather than to the assembly, a per-request up/down pair would pollute the event stream,
  and as a requirement it recurses — the scope is an argument of resolution.
- **A resolution cache keyed by contract across calls.** That is a snapshot by construction and it
  contradicts `replace` and `provider_changed` (it would answer with the previous generation after the
  swap). The per-call memory is the same mechanism with a boundary that expires on its own.
- **A structured task group** for background work. `TaskGroup` waits for its children and cancels the
  group when one fails, so "finish what is nearly done, then cut the rest" cannot be expressed; after
  wrapping it for failure isolation, what is left is the supervision loop the application would have
  written itself (`examples/watch`).
- **A scheduler** (APScheduler and the like) for timed work. A job closes over the class of the
  generation that registered it, so a swap either keeps the old code alive or breaks the job; and it is a
  thread pool with a `ContextVar` boundary, which the call level cannot follow. A timed effect is a
  registration on the scope (`EffectScope.after`) and the application schedules the next one.
- **Refusing an unnamed effect at `put`.** `put` sits on the request hot path, and a naming discipline
  is not worth turning into a runtime failure; `EffectScope.effects` names what it can, and a lambda
  reports as `<lambda>`.
- **Releasing background work by tracking it in the assembly.** The assembly answers which keys are up,
  not what is running inside them; a per-instance list is the shape the lifetime already has.
