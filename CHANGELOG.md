# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 1.0.2

### Fixed

- An instance a cancelled call had already taken out of the table — a swap abandoned in its notify
  walk or in the old generation's teardown, a `stop` cancelled in `drain` or `disable` — stays the
  assembly's to shut down instead of becoming unreachable: `stop(*assembly.keys())` drives it down and
  the effects it holds are released. `Assembly.keys(owing=False)` is the same list without that debt,
  and `Assembly.instance` answers nothing for such a key (ADR 0008).
- `wait.serving` says which absence refused it: `ServiceNotUpError.owing` is set for a key the assembly
  still owes a teardown, and `level` stays `None` because there is no instance to name a level of.

## 1.0.1

### Fixed

- `UnitLoader.unload` clears its records once, and a requirement's annotation is unwrapped in one walk
  instead of two. Nothing behaves differently.

## 1.0.0

### Added

- A component declares its requirements in the class body (`require()`), answers lifecycle hooks
  (`load`, `unload`, `reload`, `enable`, `disable`, `drain`), and is identified by `module.qualname`.
- The call level (`open_call`, `resolve`, `current`) says whose work a read belongs to and where its
  effects end; effects belong to an `EffectScope`.
- The registry turns declarations into identities and an order; the assembly fills a table keyed by
  identity plus qualifier, reports what each batch did, and streams the moves it makes.
- `start`, `stop`, `migrate`, `enable`, `quiesce`, `admit`, `evict`, `replace` — with one writer per key
  and a report rather than an exception for anything a component did; a key being moved is readable
  while the move runs (`Assembly.in_transit`), together with the level the move ends at.
- The loader (`load_unit`, `load_units`, `fingerprint_of`); `composat.std`: `UnitLoader` for moving
  loaded modules onto a new generation, `wait.serving`, `root.answering_from_the_root`, and
  `drain.InFlight`.
- Type information ships with the package: `py.typed` and annotations on every public symbol.
