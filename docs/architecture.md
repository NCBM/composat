# Architecture

The package is a stack of layers; a module imports only from the layers below it. This page says how
the modules fit, and what holds across all of them.

```mermaid
flowchart TB
    std["std: drain, pluggable, root, wait"]
    assembly["assembly"]
    registry["registry"]
    component["component, manager, loader"]
    context["context, errors"]
    foundations["identity, scope, spec, state, dependency"]

    std --> assembly
    assembly --> registry
    registry --> component
    component --> context
    context --> foundations
```

An arrow means "imports from". A layer may also reach past its neighbour to anything below it —
`assembly` reads `scope` and `state` directly, `registry` reads `dependency` — but no layer reaches
upwards.

## The layers

| layer | modules | owns | may import |
|---|---|---|---|
| deployment | `std.*` | draining, waiting out a swap, answering from the root, moving a loaded module onto a new generation | the package surface |
| verbs | `assembly` | one instance per key, the rule a requirement is answered by, the batch verbs and their reports | everything below |
| order | `registry` | declarations, the identity graph, and the order they imply | declaration, lifetime, foundations |
| declaration | `component`, `manager`, `loader` | what a type declares, what drives an instance through its levels, what a path holds | lifetime, foundations |
| lifetime | `context`, `errors` | which call a read belongs to, and what the framework refuses | foundations |
| foundations | `identity`, `scope`, `spec`, `state`, `dependency` | who a component is, where effects go, what one requirement says, the four levels, the order | nothing inside the package |

Three imports say something the arrows cannot:

- `component` imports `context`, never the other way round: a field read asks whether it is inside a
  call. That is why `context.Rule` spells the resolver's shape out instead of importing `component`.
- `registry` imports `errors` for the refusal of an interface with nothing bound to it.
- `std.wait` imports `composat.errors` to refuse a key that is not in the table.

## Reading order

`identity` → `scope` → `spec` → `state` → `dependency` → `errors` → `context` → `component` →
`manager` → `loader` → `registry` → `assembly` → `std.*`. Each one only uses what is to its left, so
reading them in this order means never meeting a symbol that has not been introduced.

## What holds across the modules

1. **The table is the only record of what is up, and of what it still owes.** One instance per key
   ([ADR 0003](adr/0003-a-key-is-an-address.md)), plus one list per key of the instances it dropped and
   still has to shut down ([ADR 0008](adr/0008-an-instance-the-table-dropped-stays-the-tables-to-shut-down.md)).
   `Assembly.keys` reads both as one list; `keys(owing=False)` and `Assembly.instance` are how a caller
   tells what answers from what is owed.
2. **A key has one writer.** A verb claims the keys it names and the keys it derives, for as long as it
   works on them; a second call is refused, not queued ([ADR 0005](adr/0005-one-writer-per-key.md)).
3. **An effect belongs to the innermost lifetime it is registered in.** A read during a call registers
   into the call; anywhere else, into the instance's epoch
   ([ADR 0006](adr/0006-effects-belong-to-the-innermost-lifetime.md)).
4. **A move commits its arrival before the hook runs**; a raising hook takes back its own step and no
   other ([ADR 0004](adr/0004-a-move-names-a-target.md)).
5. **Events are staged under the instance's lock and delivered outside it**, so an application handler
   never runs inside a move ([ADR 0005](adr/0005-one-writer-per-key.md)).
6. **A declaration and an instance are two facts.** A key can be declared and not up (a failed
   admission), and up under a declaration that has since been withdrawn — for a moment, by design
   ([ADR 0005](adr/0005-one-writer-per-key.md)).
7. **A qualifier is an opaque string, and no layer reads one.** A deployment that addresses two levels
   — an account and one endpoint of it — keeps both in the string and derives the places a requirement
   may be answered from in its own rule ([ADR 0003](adr/0003-a-key-is-an-address.md)); the shipped
   policy takes that derivation as `below=` ([below](#a-rule-with-levels-in-it)).
8. **A key is either being moved or at rest, and the table says which.** A verb's claim is readable
   (`Assembly.in_transit`), and it carries the level the move ends at, so a reader that found a level
   it cannot use can tell a key that is about to answer from one that is going away — and only the
   first is worth waiting for ([ADR 0005](adr/0005-one-writer-per-key.md)). A claim goes back with the
   call that took it, cancelled or not, so an abandoned move leaves its key at rest and answering
   nothing ([ADR 0008](adr/0008-an-instance-the-table-dropped-stays-the-tables-to-shut-down.md)).
9. **Only a caller that is serving is policed.** A provider has to be `ENABLED` for a caller that is
   serving, and `ENABLED` or `DRAINING` for one that is draining; everything else — a hook in `load` or
   `unload`, and a key the table does not place at all — reaches whatever the assembly holds. So a
   component that has been stopped still reads its own fields: what `quiesce` takes away is the work
   that arrives *through* it, not what it can do to itself (`composat.state.READABLE`).

## A rule with levels in it

A deployment's addresses may carry more than one level, and the framework is not the one that knows
it: the places to try come out of the deployment's own function. `answering_from_the_root` takes it
as `below=` and adds the root after it, so an account-and-endpoint address and a process-wide
instance are answered by one policy rather than two that would have to agree:

```python
rule = answering_from_the_root(registry, up, only={Config, Pool}, below=ancestors)
```

`ancestors` reads `/` as the level separator and names every level above the caller, nearest first:
`acme/one/client` answers `acme/one/client`, then `acme/one`, then `acme`. Every level is kept because
a caller three levels down depends on the level *directly* above it — a session reading the endpoint
it belongs to — and a derivation that cut to the first segment would skip the places in between.

A second axis is another derivation, and `joined` puts both in the one slot a rule has:

```python
def routing(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
    """The credential this deployment picked, and no opinion about anything else."""
    if identity != registry.provider_identity(LLMProvider):
        return ()
    account, _, _endpoint = key.instance.partition("/")
    return (ComponentKey(identity, f"{account}/credential-a"),)


rule = answering_from_the_root(registry, up, only={Config}, below=joined(routing, ancestors))
```

A derivation with no opinion about an identity answers nothing, which is what keeps a routing table
out of every other identity's way. Places are read in the order the list is built — the asker's own
qualifier, then what `below` derives, then the root — and `Assembly.start` closes over that same list,
so an account a read would fall back to comes up with its dependant. Nothing else is widened: `only=`
still says which identities may cross to the root, and every place is a place of the same identity — a
sibling is never one. An asker at the empty qualifier is asked of `below` like any other: its own place is
the root, and a table keyed by identity reaches a component declared there.

What a rule derives is a list of places, and putting an instance at one is the manifest's: the
qualifier is written on the declaration, and the constructor's arguments with it.

```python
_ = registry.declare_all(
    (
        Declaration(Config),  # the one instance of that identity, at the empty qualifier
        Declaration(Credential, instance="acme/credential-a", args=("a",)),
        Declaration(Endpoint, instance="acme/one"),
    )
)
```

A swap keeps the address and changes only what is built behind it —
`replace(ComponentKey(ComponentIdentity.of(Credential), "acme/credential-a"), Declaration(...))` — so a
rule never has to be rebuilt for a generation to change.
