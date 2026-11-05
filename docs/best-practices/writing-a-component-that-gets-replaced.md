# Writing a component that gets replaced

This page is for the code *inside* an instance: what to do on the way up, what to do on the way down,
and what the framework already covers. `driving-a-running-deployment.md` is the caller's view of
`replace`; this is the view from behind the key.

## The order a swap runs in

```
declaration  →  build to LOADED (load)  →  the key moves  →  old: drain → disable → unload
                                                          →  new: enable
```

Both generations exist in the middle of that, and both are built: the new one has run `load` while the
old one is still serving, and the old instance lives until its `unload`. `enable` is the first moment the
new generation is alone at the key, which is the whole reason a registration's *timing* matters and not
just its content.

## What goes in which hook

| what you take | the hook | why there |
|---|---|---|
| a socket, a pool, a thread, a cache of your own | `load` / `unload` | nobody else can see it, so the overlap does not matter |
| anything another object can see: a matcher in a shared dispatcher, a route, a row, a metric label | `enable` / `disable` | the new generation is visible while the old one still serves, so anything written earlier is live twice |

A registration written in `load` is live twice for as long as the old generation takes to come down: a
component that registers one matcher in `load` and counts the registry from the old generation's `unload`
sees two, and the same component registering in `enable` never sees more than one. So:

```python
class Route(Component):
    """A route registered where it can be seen, and taken back where it stops serving."""

    @override
    async def enable(self) -> None:
        self.router.add(self.path, self.answer)

    @override
    async def disable(self) -> None:
        self.router.remove(self.path)
```

`disable` rather than `unload` as the removal point, because `disable` is the step that says "this one
takes no new work" and it runs while the instance is still built — long before `unload`. A `drain` that
refuses takes its own step back, so the instance stays `ENABLED` and the teardown stops there; that is
the case where *not* having unregistered is the true picture, because the thing is still serving.

## A refused teardown is reported, not retried

`replace` reports the old generation's refusal against the key (`Replacement.failed`) and moves on; the
key answers the new generation either way, and a loader's `SwapResult.reported` carries the same
`Replacement`. What a raising `drain` or `unload` leaves behind is a retired generation still holding
something nothing in the table names — a leak to look at, not a move to repeat. Each step is driven once
and reported, so a hook must not retry what the one before it could not do.

## `provider_changed` runs after the move, and cannot change it

It is driven on the *dependants* of a replaced key, after the replacement landed, and never on the
instance that was replaced. Two things follow:

- The field already answers the new generation when the hook runs: a dependant that needs the value reads
  it, and one that needs to know it moved compares the instance it kept.
- The new generation is at `LOADED` when this runs (its `enable` is the last step of the swap), so a
  read taken here can be a `ServiceNotUpError` at `level=LOADED`. A dependant that has to act on the
  new one *now* waits with `composat.std.wait.serving` rather than retrying
  ([the four rows](reading-through-a-swap.md#wait-or-fail)).

## `drain` is the length of the window

`drain` is the one hook whose duration is visible from outside: it is how long the key answers a
generation that is built and not serving, and therefore how long a reader arriving in that window waits
([the first of the three numbers](driving-a-running-deployment.md#the-three-numbers)). It is also the
hook that must count what it waits for — `composat.std.drain.InFlight` counts tasks the instance
started, so request-shaped work needs a count of its own.
