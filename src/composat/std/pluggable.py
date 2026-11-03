"""The reloadable half of a deployment: the modules one root loaded, and what swapping one costs.

Two things live here, separated by who needs which. :func:`reload_module` is the primitive: it runs a
module again from source, moves ``sys.modules`` onto the new object, and re-points the modules this
deployment owns that bound a piece of the old one. :class:`UnitLoader` is the component that gives it
an owner — the set of modules a reload may touch is a *decision*, and a decision needs someone to have
made it, so it is an instance field of this component rather than a process-level registry.

Three things the component deliberately does not do:

* **it does not touch ``sys.path``.** A module is named by where it is relative to a root the
  *application* put on the path, so making those modules importable is the deployment's job; a root no
  ``sys.path`` entry holds is answered by name when the load fails, not repaired here.
* **it declares nothing.** A loaded module exports component classes; whether to declare them, at
  which qualifier, and whether to bring them up is the application's assembly. The two verbs here keep
  the halves apart — :meth:`UnitLoader.reload_module` moves *code*, and
  :meth:`UnitLoader.swap_instances` moves *instances* onto it, reading the keys it moves off the
  assembly rather than keeping a record of what the deployment declared.
* **it does not watch anything.** Which modules moved is :meth:`UnitLoader.stale`; when it is worth
  acting on that is the application's loop, because it is a decision about time.

It hands nothing across a generation, either. A reload replaces the *classes*; the module's globals are
built again, so a cache, a pool or a ``dict`` that lived there is new and empty. What has to survive
belongs outside the module, restored where a component prepares itself: ``load`` is the one place an
instance is prepared, and a second half-state carried over from the generation before it would be a
second beginning.

It also installs no import hook, and that is a boundary worth stating where its cost is. The entry
file is read by :mod:`composat.loader`, which compiles the text it just read, so a ``.pyc`` cannot
serve an older entry; a package's submodules go through the ordinary machinery, so for *them* a cache
written in the same second as an equal-length edit can be served. The price is paid deliberately: a
finder in ``sys.meta_path`` is a process-wide change to how imports resolve, and the core's promise is
that looking at a module does not change that. What this component does own is knowing *which* files
matter — a component class carries ``__module__``, and the module behind a name says which file it was
read from — so :meth:`UnitLoader.stale` sees an edit to any file that holds a component, entry or
submodule, instead of stopping at the entry.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import cast, override

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    Declaration,
    Unit,
    components_of,
    fingerprint_of,
    load_unit,
    load_units,
    loaded_unit,
    requirements_of,
)


@dataclass(frozen=True, slots=True)
class ReloadResult:
    """What one reload did: the generation it left in place, and what else moved because of it.

    ``rewritten`` names the modules whose import bindings were re-pointed — those that bound a piece of
    the reloaded module, and then everything that had bound a piece of *them*, to a fixpoint. It is the
    whole list rather than the first layer: a module that did ``from it import thing`` holds the
    previous value, and it is in turn a moved generation for whoever imports from it.

    ``requirements_changed`` names the component types that now ask for a different set of services.
    Reported rather than raised: a reload that refused to land because a dependency was added would be
    an action that cannot be completed. What the *registry* does about a type that asks for something
    else is the assembly's call, and nothing here writes to it.
    """

    unit: Unit
    rewritten: tuple[str, ...] = ()
    requirements_changed: tuple[ComponentIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class SwapResult:
    """What swapping the instances of one unit did, key by key.

    ``swapped`` names the keys that now answer this loader's current generation, in the order they were
    taken — the assembly's own order, which is the order they came up, so a provider is replaced before
    the dependants that will be told about it. ``unmatched`` names the keys whose type the new
    generation no longer holds, a class deleted or renamed by the edit: they keep running the generation
    they were built with until the application declares the new class and takes the old key down, which
    is the manifest's business rather than this one's. ``failed`` is what ``replace`` raised for a key —
    a generation the registry will not take, a new instance that would not come up.

    A key left alone is in none of them: one that belongs to another unit, and one already running the
    class the current generation holds. Reported rather than raised, key by key, because a unit is not
    one decision — one component whose new text is wrong is that component's problem, and the rest of
    the unit can still move.
    """

    swapped: tuple[ComponentKey, ...] = ()
    unmatched: tuple[ComponentKey, ...] = ()
    failed: Mapping[ComponentKey, BaseException] = field(default_factory=dict[ComponentKey, BaseException])


def reload_module(name: str, *, scope: Collection[str]) -> ReloadResult:
    """Run the module ``name`` again from source and re-point the importers inside ``scope``.

    A reload is a *same-name swap*: the source is read and executed into a new module object, and
    ``sys.modules[name]`` is moved onto it. The old object stays alive — the instances built from it
    hold their class, and their class holds the module's globals — so a running instance keeps a whole
    generation rather than a mixture, until the layer above retires it.

    ``scope`` is the set of module names this reload is allowed to touch: the modules some loader
    component has taken responsibility for. It is the caller's, and it is required, because there is no
    process-level registration of "managed" modules and there should not be — the boundary of a reload
    is a decision, and the decision belongs to whoever owns the deployment. A module outside it that
    imported ``name`` is deliberately left alone: the host namespace is not something this function
    edits on its own initiative.

    Two problems that have to be answered together, and are: the new module has to be *reachable* under
    its old name, which moving ``sys.modules`` does — and the parent package's attribute has to move
    too, or ``import parent.child`` would hand back the old object — and everything that bound a piece
    of the old module has to be re-pointed, which is what the scan is for. Bindings are read off the
    source (``import`` / ``from … import``), so there is no ledger to keep and none to disagree with:
    the code says what it bound.

    A package is reloaded from its entry, so the submodules under it are forgotten first and re-imported
    from source by the new entry — a package that came up half new would be worse than one that did not
    come up.

    Raises rather than returning a failure value, like :func:`composat.loader.load_unit`: a name no
    module holds, a module this component did not load, and anything the new source raises while
    executing. A reload that fails leaves the old generation answering: the new module never reaches
    ``sys.modules``.
    """
    old = sys.modules.get(name)
    if old is None:
        raise ValueError(f"no module named {name!r} is loaded, so there is nothing to reload")
    record = loaded_unit(old)
    if record is None:
        raise ValueError(f"{name!r} was not loaded by composat, so it has no generation to continue")

    previous = _requirements_by_identity(old)
    if record.path.name == "__init__.py":
        _forget_below(name)
    del sys.modules[name]
    try:
        unit = load_unit(record.path)
    except BaseException:
        # Nothing was re-pointed yet, so putting the old object back is the whole of the undo.
        sys.modules[name] = old
        raise

    _repoint(name, unit.module, old)
    return ReloadResult(
        unit=unit,
        rewritten=_rewrite_importers(name, scope=scope),
        requirements_changed=_moved_requirements(previous, _requirements_by_identity(unit.module)),
    )


def _forget_below(name: str) -> None:
    """Forget the modules under ``name``, so re-running a package re-imports them from source.

    ``importlib`` answers ``from . import sub`` out of ``sys.modules`` before it asks any loader, so a
    submodule left in place would be the previous generation's.
    """
    for module_name in [entry for entry in sys.modules if entry.startswith(f"{name}.")]:
        del sys.modules[module_name]


def _repoint(name: str, module: ModuleType, previous: ModuleType) -> None:
    """Make ``parent.child`` answer ``module`` rather than ``previous``, for a dotted name.

    ``sys.modules`` is what ``import`` consults first, but an importer that already holds the *parent*
    package reads the child off the parent's attributes — so moving only ``sys.modules`` would leave
    ``import parent.child`` handing back the old object.
    """
    parent_name, _, child = name.rpartition(".")
    if not parent_name:
        return
    parent = sys.modules.get(parent_name)
    if parent is not None and getattr(parent, child, None) is previous:
        setattr(parent, child, module)


def _requirements_by_identity(module: ModuleType) -> dict[ComponentIdentity, tuple[tuple[str, type[object]], ...]]:
    """What each component in ``module`` asks for, as a comparable shape: field name and service."""
    return {
        ComponentIdentity.of(cls): tuple((spec.name, spec.service) for spec in requirements_of(cls))
        for cls in components_of(module)
    }


def _moved_requirements(
    previous: dict[ComponentIdentity, tuple[tuple[str, type[object]], ...]],
    current: dict[ComponentIdentity, tuple[tuple[str, type[object]], ...]],
) -> tuple[ComponentIdentity, ...]:
    """The types the new generation asks something else of, in a stable order.

    A type only present in one of the two generations is not reported: a class that arrived or went is
    a different fact, and one the caller reads off the units themselves.
    """
    moved = [identity for identity, asked in current.items() if identity in previous and previous[identity] != asked]
    return tuple(sorted(moved, key=str))


def _rewrite_importers(name: str, *, scope: Collection[str]) -> tuple[str, ...]:
    """Re-point every module in ``scope`` that bound a piece of ``name``, until nothing changes again.

    A fixpoint rather than one pass, and the reason is the same one that makes a reload a *generation*
    rather than an object: a module whose bindings moved is itself a moved generation. Its module-level
    code did not run again, but the names in it did change, so a module that did ``from it import
    thing`` holds the previous value — and it in turn is a moved generation for whoever imports from it.

    Change is decided by identity, not by re-running the rewrite: a binding already pointing at the
    value the source would give it is left alone, which is what makes the loop terminate. Modules are
    visited in sorted order, so two runs over one scope rewrite in the same order.
    """
    rewritten: list[str] = []
    while True:
        changed = [module_name for module_name in sorted(scope) if _rewrite_one(module_name, name)]
        if not changed:
            return tuple(rewritten)
        rewritten.extend(module_name for module_name in changed if module_name not in rewritten)


def _rewrite_one(module_name: str, reloaded: str) -> bool:
    """Re-point one module's import bindings to what ``sys.modules`` now holds; did anything move?

    The module being reloaded is skipped: its globals *are* the new generation. A module in the scope
    is not necessarily one this loader *loaded* — the scope is what the caller says it owns, and an
    application's own modules are its own — so its source is found by the ordinary search the
    interpreter would use, and a module with no source file at all is left alone.

    The scan looks at the module's own top level only. An import inside a function runs when that
    function runs, and it will find the reloaded module by name then; re-pointing it here would mean
    reaching into a closure's cells, which the ordinary name lookup already answers.
    """
    if module_name == reloaded:
        return False
    module = sys.modules.get(module_name)
    if module is None:
        return False
    moved = False
    for edge in _import_edges(module, module_name):
        moved |= _repoint_edge(module, edge)
    return moved


def _repoint_edge(module: ModuleType, edge: _ImportEdge) -> bool:
    """Apply one import edge to ``module``'s namespace; did any binding change?"""
    if edge.whole:
        # ``import a.b`` binds ``a``; ``import a.b as c`` binds ``c``. Either way the value is whatever
        # ``sys.modules`` now holds for the full name, or for its head when the head is what was bound.
        target = sys.modules.get(edge.module)
        if target is None:
            target = sys.modules.get(edge.module.split(".")[0])
        if target is None:
            return False
        current: object = getattr(module, edge.bound, _ABSENT)
        if current is target:
            return False
        setattr(module, edge.bound, target)
        return True
    source = sys.modules.get(edge.module)
    if source is None:
        return False
    if edge.star:
        return _repoint_star(module, source)
    moved = False
    for attribute, bound in edge.pairs:
        wanted: object = getattr(source, attribute, _ABSENT)
        if wanted is _ABSENT:
            continue
        if getattr(module, bound, _ABSENT) is wanted:
            continue
        setattr(module, bound, wanted)
        moved = True
    return moved


def _repoint_star(module: ModuleType, source: ModuleType) -> bool:
    """Re-point everything ``from source import *`` bound.

    What a star import binds is decided by the source at the time, not by the statement — so the answer
    is "the names that are there now", filtered the way ``import *`` filters them: the source's
    ``__all__`` when it states one, and otherwise the names not starting with an underscore. A name the
    importer bound to something else since then is re-pointed too, which is what ``import *`` means:
    the namespace is the source's, and it just moved.
    """
    declared: object = getattr(source, "__all__", None)
    if isinstance(declared, (list, tuple)):
        names = [name for name in cast("list[object]", declared) if isinstance(name, str)]
    else:
        names = [name for name in vars(source) if not name.startswith("_")]
    moved = False
    for name in names:
        wanted: object = getattr(source, name, _ABSENT)
        if wanted is _ABSENT or getattr(module, name, _ABSENT) is wanted:
            continue
        setattr(module, name, wanted)
        moved = True
    return moved


_ABSENT = object()
"""A sentinel for "this namespace has no such name", which ``None`` cannot say: a module may bind ``None``."""


@dataclass(frozen=True, slots=True)
class _ImportEdge:
    """One ``import`` statement, as the two things rewriting needs: where it points, and what it bound.

    ``whole`` is the ``import a.b`` form — the binding is the module object itself, under the name the
    author asked for. Otherwise it is a ``from a import x`` form, and ``pairs`` are the ``(attribute in
    the source, name bound here)`` pairs the statement drew — kept as pairs because ``from a import x as
    y`` binds a name the source never heard of. An import of ``*`` keeps its own flag: what it bound is
    decided by the module it read, not by the statement.
    """

    module: str
    bound: str = ""
    pairs: tuple[tuple[str, str], ...] = ()
    whole: bool = False
    star: bool = False


def _import_edges(module: ModuleType, module_name: str) -> tuple[_ImportEdge, ...]:
    """The top-level imports a module's source states, or nothing when its source cannot be found.

    Read from the source rather than from the module's namespace, because the namespace cannot say
    *where* a name came from: ``thing`` reads the same whether it was imported or defined. The source is
    the one recorded when the module was loaded, and otherwise the ordinary search — a module in the
    scope need not be one this component loaded, and one with no source file at all has no edges to
    re-point.
    """
    record = loaded_unit(module)
    path = record.path if record is not None else _source_of_module(module_name)
    if path is None:
        return ()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    except (OSError, SyntaxError):
        return ()
    package = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
    return tuple(edge for node in tree.body for edge in _edges_of(node, package))


def _source_of_module(module_name: str) -> Path | None:
    """The file ``importlib`` would read for ``module_name``, or ``None`` when there is none.

    Asked rather than guessed, and asked through the same machinery an import uses, so a namespace
    package or a built-in module is answered as "no source" without this module keeping a list of the
    ones that can have one.
    """
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return None
    location = None if spec is None else spec.origin
    if not isinstance(location, str):
        return None
    path = Path(location)
    return path if path.suffix == ".py" and path.is_file() else None


def _edges_of(node: ast.stmt, package: str) -> tuple[_ImportEdge, ...]:
    """The edges one top-level statement contributes; an alias each, and a flat name per ``import``."""
    if isinstance(node, ast.Import):
        return tuple(
            _ImportEdge(module=alias.name, bound=alias.asname or alias.name.split(".")[0], whole=True)
            for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        target = _imported_from(node, package)
        if target is None:
            return ()
        if any(alias.name == "*" for alias in node.names):
            return (_ImportEdge(module=target, star=True),)
        return (
            _ImportEdge(module=target, pairs=tuple((alias.name, alias.asname or alias.name) for alias in node.names)),
        )
    return ()


def _imported_from(node: ast.ImportFrom, package: str) -> str | None:
    """The absolute module name a ``from … import`` names, resolving the dots against ``package``.

    ``level`` is how many dots the source wrote, and each one is a step up from the package the
    importing module lives in — one dot is that package itself, two is its parent. A statement that
    climbs past the root names nothing, and ``None`` says so rather than inventing a module.
    """
    if node.level:
        steps = package.split(".") if package else []
        if node.level > len(steps):
            return None
        base = ".".join(steps[: len(steps) - (node.level - 1)]) if steps else ""
    else:
        base = ""
    if not node.module:
        return base or None
    return f"{base}.{node.module}" if base else node.module


@dataclass(frozen=True, slots=True)
class _Generation:
    """One unit as this component took it: what it loaded, and the files its components were read from.

    The extra files are what lets :meth:`UnitLoader.stale` see an edit below a package's entry without
    an import hook: a component class carries ``__module__``, and the module behind a name says which
    file it was read from, so the files that *matter* are findable after the fact. A submodule holding
    no component is not in here — it is not one this component claims to notice.
    """

    unit: Unit
    sources: dict[Path, str]


class UnitLoader(Component):
    """The modules loaded from one root, the scope a reload of any of them may touch, and their files.

    ``load`` loads every module directly inside ``root`` — a package counting as one module — and
    records them as the scope. ``unload`` forgets them: a module outlives the component that loaded it
    (its classes are held by the instances built from them, and by anything that imported it), so
    dropping the *name* would not unload the code, it would only make the next importer build a third
    class. What unload owns is this component's own bookkeeping, and that is what it clears.
    """

    root: Path

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = Path(root).resolve()
        self._generations: dict[str, _Generation] = {}

    @override
    async def load(self) -> None:
        self._generations = {unit.module.__name__: _generation_of(unit) for unit in load_units(self.root)}

    @override
    async def unload(self) -> None:
        self._generations = {}

    def scope(self) -> frozenset[str]:
        """The module names this component is responsible for: what a reload is allowed to touch.

        Frozen and copied, because it is handed to :func:`reload_module` as the boundary of an edit — a
        caller that mutated it mid-reload would be changing the decision while it is being acted on.
        """
        return frozenset(self._generations)

    def units(self) -> tuple[Unit, ...]:
        """What is loaded, in the order the loader took them (its root's module order)."""
        return tuple(generation.unit for generation in self._generations.values())

    def unit(self, name: str) -> Unit | None:
        """The unit loaded under ``name``, or ``None`` when this loader does not hold that name."""
        generation = self._generations.get(name)
        return None if generation is None else generation.unit

    def stale(self) -> tuple[str, ...]:
        """The units whose source no longer says what was loaded, in sorted order.

        Every file that holds a component counts, not just the entry: an edit to ``pkg/inner.py`` is a
        real change to the code that runs, and a fingerprint taken of the file a class was read from is
        how it is noticed without a finder in the import path.

        This is the judgement a reload needs and the only one: a fingerprint says the file moved, and it
        says it in both of the cases a version cannot — an edit that landed with nothing else to show
        for it. Whose business it is to *act* on it is the caller's, which is why this answers a list
        rather than reloading anything.

        A file that is gone counts as stale rather than raising: a scan across a directory should report
        "this one moved on" instead of stopping at the first missing file. ``LoadedUnit.is_stale``
        is where the distinction between a deletion and an edit is still available.
        """
        moved: list[str] = []
        for name, generation in self._generations.items():
            for path, fingerprint in generation.sources.items():
                try:
                    if fingerprint_of(path) != fingerprint:
                        moved.append(name)
                        break
                except FileNotFoundError:
                    moved.append(name)
                    break
        return tuple(sorted(moved))

    def reload_module(self, name: str) -> ReloadResult:
        """Swap the module ``name`` and re-point its importers **inside this loader's scope**.

        The verb is the primitive's; what this adds is the scope, and the bookkeeping afterwards: the
        record it holds is replaced with the new generation, so the next :meth:`stale` compares against
        what is running rather than against what has been replaced twice.

        Named after the primitive rather than ``reload``, which on a component already means the
        lifecycle hook — the manager drives that one, and a public method wearing its name with a
        different signature is the kind of ambiguity a reader pays for. The bare name inside the body
        below is the module-level function, because this method's own name is not what it looks up.

        Raises what the primitive raises — a name this loader does not hold is refused here, before
        anything is executed, because it is a caller mistake rather than a module that failed.
        """
        if name not in self._generations:
            raise ValueError(f"{name!r} is not one of the modules {self.root} loaded")
        result = reload_module(name, scope=self.scope())
        self._generations[name] = _generation_of(result.unit)
        return result

    async def swap_instances(self, assembly: Assembly, name: str) -> SwapResult:
        """Put this loader's current generation of ``name`` behind every key built from an older one.

        The other half of a reload, and the half a code move cannot do: :meth:`reload_module` moves the
        *module*, and a running instance keeps the class it was built with — so a deployment that
        reloads and stops there has the new code in ``sys.modules`` and the old code still serving.

        The keys are read off the assembly rather than kept here. An instance's identity is its class's
        own path, so "what is running from this unit" is a reading of the table; a second record of
        which keys a unit owns would be a second truth about the same thing, and one that goes stale
        the moment a key is admitted or evicted behind its back.

        A key is replaced with the class at the *same* identity in the current generation, which is what
        a reload keeps, and built with the key's own declaration (:meth:`Assembly.declaration`), so
        constructor arguments survive a swap the caller never wrote down: only the class moves. A class
        the edit renamed or deleted has no counterpart, and is reported in
        :attr:`SwapResult.unmatched` rather than guessed at. A key already running the current class is
        passed over, so calling this after every reload is safe, including for one already swapped.

        Everything is reported rather than raised, key by key. One generation the registry will not
        take — a type that now asks for something else — is that key's failure, and the others still
        move. A cancelled swap stops where it is: cancellation is not a per-key outcome.
        """
        generation = self._generations.get(name)
        if generation is None:
            raise ValueError(f"{name!r} is not one of the modules {self.root} loaded")
        available = {ComponentIdentity.of(cls): cls for cls in generation.unit.components}
        swapped: list[ComponentKey] = []
        unmatched: list[ComponentKey] = []
        failed: dict[ComponentKey, BaseException] = {}
        # The keys as they were when the swap started: replacing one moves the table, and this walks the
        # picture it took rather than a picture that changes under it.
        keys = assembly.keys()
        for key in keys:
            instance = assembly.instance(key)
            if instance is None:  # pragma: no cover - a key the table named is one it holds
                continue
            identity = ComponentIdentity.of(type(instance))
            if not _belongs(identity, name):
                continue
            current = available.get(identity)
            if current is None:
                unmatched.append(key)
                continue
            if type(instance) is current:
                continue
            declared = assembly.declaration(key)
            fresh = Declaration(current, instance=declared.instance, args=declared.args, kwargs=declared.kwargs)
            try:
                _ = await assembly.replace(key, fresh)
            except Exception as error:  # noqa: BLE001 - one key's failure is reported, not raised
                failed[key] = error
                continue
            swapped.append(key)
        return SwapResult(tuple(swapped), tuple(unmatched), failed)


def _belongs(identity: ComponentIdentity, name: str) -> bool:
    """Whether ``identity`` was written in ``name`` or below it: which unit a class came from.

    The same rule :func:`composat.loader.components_of` applies to a namespace, asked backwards: a
    class re-exported from a subpackage keeps the subpackage in ``__module__``, so a package's own
    components answer for the package.
    """
    return identity.module == name or identity.module.startswith(f"{name}.")


def _generation_of(unit: Unit) -> _Generation:
    """``unit`` as this component records it: the unit, and a fingerprint per file its components are in.

    The entry's fingerprint is the one the loader took when it executed the module, so the record and
    :class:`~composat.loader.LoadedUnit` never disagree; the rest are read here, because the files of a
    package's submodules are not something the loader records (it deliberately has no import hook).
    """
    sources = {unit.path: unit.fingerprint}
    for component in unit.components:
        path = _file_of(component)
        if path is not None and path not in sources:
            sources[path] = fingerprint_of(path)
    return _Generation(unit, sources)


def _file_of(component: type[Component]) -> Path | None:
    """The source file ``component`` was defined in, or ``None`` when there is no readable one.

    Read off the module the class belongs to, which is the one link the class itself carries: an
    identity says *where*, and ``sys.modules`` turns that into a path. A module that is gone from
    ``sys.modules``, and one loaded from a bytecode-only install, answer ``None`` — nothing to watch,
    rather than a guess at a file.
    """
    module = sys.modules.get(component.__module__)
    found = None if module is None else getattr(module, "__file__", None)
    if not isinstance(found, str):
        return None
    path = Path(found).resolve()
    return path if path.suffix == ".py" else None
