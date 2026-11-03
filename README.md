# composat

A component lifespan interface. Components declare what they need, an application declares what
exists, and an assembly brings instances up and takes them down: it fills a table in dependency order,
reports what happened, and keeps a key answering through a swap.

Requires Python 3.12+. The tree is the package (`src/composat`).

## A component

```python
from composat import Component, require


class Store(Component): ...


class Engine(Component):
    store: Store = require()
```

A field marked with `require()` is a requirement, and its annotation says what the field reads as.
Nothing is inferred from the annotation's shape, so an ordinary field stays ordinary. Reading the
field answers the instance that provides it, at the reader's own key — it is a live read, not a copy
taken at construction.

The framework drives six hooks — `load`, `unload`, `reload`, `enable`, `disable`, `drain` — and a type
that writes none of them is one nothing drives. A hook is written with `@override`, which is also what
turns a misspelled hook into a checker error instead of a hook that silently never runs.

## Declaring and running

```python
from composat import Assembly, ComponentIdentity, ComponentKey, ComponentRegistry, Declaration

registry = ComponentRegistry()
_ = registry.declare_all((Declaration(Store), Declaration(Engine)))
assembly = Assembly(registry, up={})

key = ComponentKey(ComponentIdentity.of(Engine))
result = await assembly.start(key)  # brings Store up too: what a requirement needs is the closure
assert result.ok
await assembly.stop(*result.completed)  # what this batch built, not what it named
```

Both batch verbs report rather than raise, so one tenant's broken configuration stays that tenant's
problem. `assembly.replace(key, Declaration(NewEngine))` puts a new generation behind a key without a
gap in what the key answers; `composat.std.wait.serving(assembly, key)` is how a reader waits out the
window where the old generation is still being torn down.

A key is an identity plus a qualifier: `ComponentIdentity` is the class's own path
(`module.qualname`), and the qualifier is whatever tells instances of it apart — a tenant, a row id,
or nothing at all.

## Loading a directory

`load_units(root)` answers one `Unit` per module: the module, the component classes its entry namespace
holds, and a hash of its source. Declaring what those hold is the application's, and so is deciding
which of them are roots:

```python
from composat import Declaration, load_units

for unit in load_units(root):
    _ = registry.declare_all(Declaration(cls) for cls in unit.components)

result = await assembly.start(*roots)  # the rest come up as requirements of these
```

Which keys are roots is a deployment decision rather than a derived one: a manifest can be grown in
stages, and "nothing needs it" then does not mean "it should be up". Tolerating one broken module is
also the caller's — `load_unit` raises, so a loop of `try` is where that policy is written.

## Editing a running module

`composat.std.pluggable.UnitLoader` holds the modules one root loaded and answers which of them no
longer match their source. It is a component, declared like any other:

```python
from composat import Declaration
from composat.std.pluggable import UnitLoader

_ = registry.declare_all((Declaration(UnitLoader, args=(root,)),))
```

Two verbs, because moving code and moving instances are two decisions:

```python
for name in loader.stale():
    _ = loader.reload_module(name)  # a new generation of the module, under the same name
    _ = await loader.swap_instances(assembly, name)  # every key built from the old one, on the new
```

A reload replaces the classes, not the module's globals: what has to survive a generation belongs
outside the module and is restored in `load`. A unit whose class the edit renamed has no counterpart
to swap to, and is reported rather than guessed at.

## The gate

```bash
pdm run pytest
pdm run ruff format --check .
pdm run ruff check .
pdm run basedpyright
```

`basedpyright` takes no arguments: the configuration in `pyproject.toml` covers `src` and `tests`
alike.
