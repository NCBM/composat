# Reading through a swap

This page is for the request path: the code that reads a requirement while the deployment underneath
it is being moved. What a read can answer, what each refusal means, and which of them is worth
waiting for.

## What a read can do

A field marked `require()` is a live read: it asks the assembly's rule at the reader's own key, and the
answer is whatever is up there *now*. `ServiceNotUpError` carries three fields:

| field | says |
|---|---|
| `key` | the place the lookup ended at — the provider's key, not the reader's |
| `asked_from` | the reader, which is what a log line needs |
| `level` | `None` when nothing is up there at all, otherwise the level the instance rests at |

A level below serving is not a failure of the deployment: it says that a key is built and not taking
work. What to do about it is the next question.

## Wait, or fail?

Nothing in the error says whether the level is about to change; `Assembly.in_transit(key)` does. Read
the two together:

| what the table says | what is moving | what to do |
|---|---|---|
| `ENABLED` | anything | use it |
| below serving | nothing (`in_transit` is `None`) | fail now — nothing is going to change it |
| below serving | a move ending at `ENABLED` | wait it out, with a deadline |
| below serving | a move ending anywhere else | fail now — it is on its way out of service |

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
