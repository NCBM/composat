# Reloading a directory of code

This page is for the host that owns a `composat.std.pluggable.UnitLoader`: which step is whose, and the
four shapes that make a reload look right and behave wrong.

## Three steps, three reports

| step | answers | what is left to you |
|---|---|---|
| `loader.stale()` | the units whose source no longer says what is running | when to look, how to debounce |
| `loader.reload_module(name)` | the new `Unit`, the modules whose bindings moved, the types whose requirements changed | nothing — but nothing is serving the new code yet |
| `loader.swap_instances(assembly, name)` | `swapped`, `unmatched`, `failed` | `unmatched`: the keys the edit no longer has a class for |

The middle step moves the *module*; the third moves the *instances*. An instance keeps the class it was
built with, so a host that reloads and stops there has new code in `sys.modules` and the old code still
serving.

## Discovery is yours, and loading a directory is not tolerant

`load_units(root)` is `tuple(load_unit(entry) for entry in candidates)`, and `UnitLoader.load` calls it.
One entry that raises takes the whole directory with it: the loader never comes up, `stale()` answers
nothing, and `reload_module` has nothing to move — for every module, not just the broken one. With one
broken entry beside a good one, `start` reports:

```
start failed:  composat.std.pluggable.UnitLoader → SyntaxError
loader up:     False
stale():       ()
```

`()` is the dangerous part: "nothing moved" and "I cannot tell" are the same answer. So a host that
loops over `load_unit` itself — the tolerant policy `loader.py` tells callers to write — must not treat
the loader's silence as an answer:

```python
loader = assembly.instance(loader_key)
if not isinstance(loader, UnitLoader):
    raise RuntimeError("no reload driver: an entry under the root did not load")
```

## A name only this loader may hold

`load_unit` reuses a module under a name only when that module carries the stamp the loader wrote. A
module that `sys.modules` already holds from an ordinary import is refused rather than shadowed
(`… wants the name zulu, which this loader did not load`). So a plugin that imports a sibling at import
time decides which of the two survives, by scan order:

| the directory, in path order | what loads |
|---|---|
| `alpha.py` (imports `zulu`) then `zulu.py` | `alpha` only: `zulu` is a rejected entry |
| `zulu.py` then `alpha.py` (imports `zulu`) | both |

Two ways out: keep the sibling import inside a hook rather than at module scope, or scan a provider before
whatever imports it — the scan order is the manifest's, so a host owns that one. `require()` does not
remove the constraint: an annotation naming a sibling still has to resolve at the requirement's first ask.

## A file that does not parse is not "stale"

`stale()` compares a fingerprint of the syntax tree, so a deleted file counts as moved on, and a file
that exists and does not compile raises `SyntaxError` out of the scan instead of being reported. That is
the common case for an editor mid-save. A watcher catches it and waits for the next quiet period:

```python
def moved(loader: UnitLoader) -> tuple[str, ...]:
    try:
        return loader.stale()
    except SyntaxError:
        return ()
```

`()` here is honest — the source does not say anything yet — which is why it is only safe in a loop
that asks again. Do not let the same `()` end a one-shot "did anything change" check.

## A rename has one half

`SwapResult.unmatched` names the *old* keys. The new side is a declaration the host writes, because a
class without a key has no qualifier and no constructor arguments until the deployment says what they
are. Read the identities before and after:

```python
before = {ComponentIdentity.of(cls) for cls in loader.unit(name).components}
reloaded = loader.reload_module(name)
swap = await loader.swap_instances(assembly, name)
after = {ComponentIdentity.of(cls): cls for cls in loader.unit(name).components}
added = [after[identity] for identity in after.keys() - before]
```

Two simultaneous renames are indistinguishable from one deletion and one addition — that is a set
difference, not a pairing. `examples/reload/app.py` is this written once.

## Why the new class is admitted before the old key goes

`evict` refuses while a declaration staying put still needs the identity
(`DependencyInUseError`, naming both sides), so the order matters:

```
admit the added classes at LOADED  →  evict(swap.unmatched)  →  enable(what was admitted)
```

Admitting at `LOADED` first means a registration written in `enable` is never live twice
([why that is where it goes](writing-a-component-that-gets-replaced.md)), and the new class can still
resolve the old one while it loads. What no order settles is a rename something staying put still
depends on: `evict` is refused because that declaration asks for the identity, and admitting the
replacement first does not change what it asks for. That is a real limit — report it, and let the host
decide whether both generations are worth running.

## A reload is not a teardown

`swap_instances` passes over a key already running the current class, so calling it after every reload
is safe; `failed` is per key, so one generation the registry will not take does not stop the others. What
it will not do is take a key `stop` is already moving: the key is spoken for, and the swap reports a
`KeyInTransitError` for it rather than racing the teardown ([the retry a host needs](driving-a-running-deployment.md#every-verb-is-one-writer-so-a-host-must-retry)).
