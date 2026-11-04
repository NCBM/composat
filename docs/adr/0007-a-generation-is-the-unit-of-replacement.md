# 7. A generation of instances is the unit of replacement

**Status**: accepted, 2026-11-04.

## Context

Code changes while the process runs, and the process is expected to keep answering. Two things move
independently: a module's *text* — what a fresh import would execute — and the *instances* built from the
classes that text declared. Code that is reloaded without its instances moving means the new module object
sits in `sys.modules` while the old classes go on serving.

## Decision

A reload is a same-name swap: the source is executed into a new module object and `sys.modules[name]`
moves onto it (`composat.std.pluggable.reload_module`). The old object stays alive, held by the instances
built from it, so a running instance keeps a whole generation rather than a mixture.

Names that bound a piece of the reloaded module are re-pointed, to a fixpoint, inside the scope the
loader names — the modules that component loaded, plus whatever the deployment says it owns. The scan
reads the module's own `import` statements to know *which names to re-point*; it never decides *what
depends on what*: that is `require()`, and the registry already knows it.

Instances move separately (`UnitLoader.swap_instances`): one key at a time, the class at the same identity
in the current generation, built with the key's own declaration, swapped by `Assembly.replace`. A class the
edit renamed or deleted has no counterpart and is reported (`SwapResult.unmatched`) rather than guessed at.

Nothing is carried across a generation. The module's globals are rebuilt, so a cache or a pool that lived
there is new and empty; what has to survive belongs outside the module and is restored in `load`.

## Consequences

- A deployment that reloads and stops there has the new code in `sys.modules` and the old code serving;
  the second verb is not optional, which is why there are two.
- A swap has no gap: the key moves to the new generation before the old one is torn down, and the new one
  is published (enabled) only after the old one has let go.
- A generation the registry will not take — a type that now asks for something else — fails that key and
  leaves the others to move.

## Alternatives considered

- **Staging a whole tree, then switching.** Two staging tables, a `sys.modules` snapshot and one promote
  step. A key hand-over already reduces a generation change to a pair of instances, with the old one torn
  down last; staging a tree exists to serve "the code has to be re-imported", and that state machine belongs
  to a loader, not to the table of what is up.
- **Deciding dependants by scanning imports.** Dependencies are declared, not discovered: the `require()`
  edge exists on the type, and inferring it from `from x import y` would make an import look like a
  requirement. The cost is accepted — there is no reverse "who imports me" table, and a module outside the
  loader's scope that imported the reloaded one is deliberately left alone.
- **Swapping functions in place** (pairing definitions by AST and rewriting `__code__`, keeping module-level
  registration). The unit is an instance. That contract is more fragile than a generation swap: the module's
  structure must keep its prefix, and every binding the unchanged code reads must keep its name — for the
  sake of avoiding one `load`.
- **Carrying state across generations** (`keeping()` / a restored dict). State belongs to the application,
  and a component restores what it needs in `load` from an outside store. A module is executed again, so
  anything carried would be a second beginning.
