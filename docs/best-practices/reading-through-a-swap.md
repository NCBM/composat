# Reading through a swap

This page is for the request path: the code that reads a requirement while the deployment underneath
it is being moved. What a read can answer, what each refusal means, and which of them is worth
waiting for.

## What a read can do

A field marked `require()` is a live read: it asks the assembly's rule at the reader's own key, and the
answer is whatever is up there *now*. `ServiceNotUpError` carries four fields:

| field | says |
|---|---|
| `key` | the place the lookup ended at — the provider's key, not the reader's |
| `asked_from` | the reader, which is what a log line needs |
| `level` | `None` when there is no instance there, otherwise the level it rests at |
| `owing` | `True` when the key went down and the assembly still owes its teardown |

A level below serving is not a failure of the deployment: it says that a key is built and not taking
work. What to do about it is the next question.

`owing` separates the two absences, and neither comes back on its own: a key nothing was ever up under
wants a declaration and a `start`, while one the assembly owes a teardown is one a host still has to
finish shutting down ([ADR 0008](../adr/0008-an-instance-the-table-dropped-stays-the-tables-to-shut-down.md)).

## Wait, or fail?

Nothing in the error says whether the level is about to change; `Assembly.in_transit(key)` does. Read
the two together:

| what the table says | what is moving | what to do |
|---|---|---|
| `ENABLED` | anything | use it |
| below serving | nothing (`in_transit` is `None`) | fail now — nothing is going to change it |
| below serving | a move ending at `ENABLED` | wait it out, with a deadline |
| below serving | a move ending anywhere else | fail now — it is on its way out of service |
| nothing | anything | fail now — nothing is up, whether it was never built or has gone down (`owing`) |

The third row is the swap: `replace` moves the key to the new generation before that generation starts
serving, so there is a window where the key answers a generation that is built and not serving. The
window's length is the retired generation's `drain`
([the first of the three numbers](driving-a-running-deployment.md#the-three-numbers)).

`composat.std.wait.serving` is that table written once, so a request does not have to:

```python
try:
    provider = endpoint.provider
except ServiceNotUpError as refused:
    async with asyncio.timeout(5):
        provider = await serving(assembly, refused.key)
```

It subscribes before it reads, so a move that lands between the two is seen either way, and it refuses
the rows that say "fail now" instead of burning the deadline on them. Read it as "the answer, once it
serves", not as a retry: there is no queue, and a second caller that launched a verb of its own would
be refused for the very key it is trying to use.

Two habits to avoid:

- **Do not compare `in_transit(...).doing`.** It is a phrase for a log line or a refusal message — the
  verb's own words — and it carries no promise. `arrival` is the part with a meaning (a state), and
  `serving` is the part with a policy.
- **Do not turn the deadline into a retry loop.** A swap is one move with one end; polling it makes the
  window longer for the reader and hides which of the four rows above actually happened.
- **Do not wait from inside a hook.** The events a move produces are delivered between moves, so a
  `serving` awaiting the call it is part of waits for itself
  ([what a hook must not wait for](what-a-hook-must-not-wait-for.md)).
## What the call's key decides

`open_call(key, rule)` makes `key` the caller for as long as the block runs, and a rule is handed that
key as the asker — so the candidate places it derives begin at `key.instance`. Everything `resolve()`
answers inside the block is answered *at that address*, including everything that falls back from it.

A field read is answered elsewhere: `require()` asks at the instance's own key, whatever call it runs
inside. The two read different things — what this component reads, and what this request reads:

| reading | the address it uses | moved by the call's key |
|---|---|---|
| a `require()` field | the instance's own key | no |
| `resolve(SomeComponent)` | the call's key | yes |

So a call opened at an address that is not a component's — a user id, a request id, a job id — makes
`resolve()` a miss: nothing is up at `SomeComponent:<that id>`, and nothing falls back unless the rule
says so. The field in the same block still answers, which is why the two can disagree:

```python
async with open_call(ComponentKey(ComponentIdentity.of(Dispatcher), request_id), assembly.rule):
    handler.provider  # answers: the handler's own key
    resolve(Registry)  # misses: nothing is up at Registry:<request id>
```

Two ways to keep them together: open the call at a real address, or make the rule map the call's
qualifier back to a place (`below=`, or `joined` with the deployment's own derivation).
`ServiceNotUpError.asked_from` names the key that asked — the call's, not the instance's.

## A stopped tenant is still built

`quiesce` moves a key to `LOADED`: out of service, still built. What the framework refuses is a read
made by a caller that is *serving* (or draining) — a component that has been stopped goes on running
its own methods, and its own `require()` fields go on answering, because only a caller doing work is
policed (invariant 9 in `docs/architecture.md`).

So a stopped tenant is not "the code is gone": the instance is there, its data is there, and a
background task of its own that is still running keeps working. What has changed is who may read it —
and that is the question to ask when a request starts failing after a deployment action.

## Hold the answer for the length of one request

Read the requirement once, at the start of the request, and keep the instance in a local. The read is
live, so a second read may get a different generation — and the generation the request started with is
the one whose `drain` is not waiting for it. A value read per call is a request that can find itself
half on one generation and half on another.

What not to do is the other extreme: a component that caches its provider in `load` and never looks
again keeps the retired generation past the swap, including after its `unload` closed the pool. The
field is the cache; reading it is what makes the rules, the routing table and the swap all agree.
