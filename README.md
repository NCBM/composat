# composat

A component lifespan interface. Components declare what they need, an application declares what
exists, and an assembly brings instances up and takes them down: it fills a table in dependency order,
reports what happened, and keeps a key answering through a swap.

Requires Python 3.12+.

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

Neither batch verb raises for what a component did: both report it, so one tenant's broken
configuration stays that tenant's problem. `assembly.replace(key, Declaration(NewEngine))` puts a new
generation behind a key without a gap in what the key answers;
`composat.std.wait.serving(assembly, key)` is how a reader waits out the window where the old
generation is still being torn down.

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

Which keys are roots is a deployment decision: a manifest can be grown in stages, and "nothing needs
it" then does not mean "it should be up". Tolerating one broken module is also the caller's —
`load_unit` raises, so the policy is written in a loop with a `try` in it.

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
to swap to, and is reported.

## What this is not

These are the boundaries a reader needs before choosing. The refusals that cost a design argument are
recorded in `docs/adr/`, each with the alternatives it turned down.

- **A discovery mechanism.** The framework loads the paths it is handed; it does not go looking for
  components in entry points, in a directory or in configuration.
- **A version axis.** An identity is `module.qualname`; which version a deployment runs is decided
  where the classes are declared.
- **A file watcher.** `composat.std.pluggable` answers what a module's source says now and how to move
  a generation; the loop, the debounce and the ignore rules are the application's.
- **An event dispatcher.** Priorities, rules, permissions and session continuation are a loop the
  application writes — including a plugin registering its matchers into a shared dispatcher.
- **A configuration system.** The framework does not read the application's self-description (author,
  description, version, a schema), and has no settings of its own.
- **A runtime.** It does not run a loop, a scheduler or a server; it is driven from the one the
  application already has, and timed work beyond a one-shot `EffectScope.after` is the application's.
- **Reactive or conditional requirements.** A requirement is declared and unconditional; there is no
  activation on demand, no requirement that applies to some callers only, and no lexical isolation
  between components.
- **Surgery on a host application.** It does not reach into another framework's cache or registry to
  take a component over: a component is declared to this assembly, not captured from somewhere else.
- **An introspection tool.** A batch answers with a report, a move with an event, and the table with
  what is up now — "why is this key not here" is a record the application keeps itself.
- **Effect bookkeeping beyond a scope.** A scope holds what was registered on it and answers the names
  of those effects; there are no effect labels, no hierarchy and no global ledger.
- **A swap across identities.** `replace` moves one key to a new class at the same identity; replacing
  a tree of keys in one step is not offered.

## The gate

The checks a change has to pass are the ones in
[`.github/workflows/checks.yml`](.github/workflows/checks.yml); `pdm install -G dev` at the root
installs what they need.

## Documentation

| file | what it holds |
|---|---|
| `docs/architecture.md` | how the layers fit, the direction of imports, and what holds across modules |
| `docs/adr/` | the decisions behind those shapes, numbered in the order they were made, with the alternatives each refused |
| `examples/README.md` | one row per shape an application takes, each a working directory |
| `CHANGELOG.md` | what changed in each release |

There is no separate API reference: a public symbol's docstring is the reference, and the pages above
point into the code.
