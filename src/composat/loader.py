"""Loading components from a path: the module there, what it exports, and a fingerprint of its source.

A component type names itself by where it was written — ``module.qualname`` — so "which components
are at this path" is a question about a module, and this module answers it. The boundary is narrow on
purpose: it turns a path into the module there and the classes inside it, and stops short of *when*
to load it. Watching a file, deciding that a running instance is due for a new generation, moving
``sys.modules`` onto one — those are decisions about time, and they belong to the application
(``composat.std.pluggable`` is the component that owns them). There is likewise no registry here: the
caller takes the returned :class:`Unit` and declares it, and this module never learns what happened
next.

Three things it deliberately does not do:

* **it does not touch ``sys.path``.** A module is named by where it is relative to a root the
  *application* put on the path, so making those modules importable is the deployment's job. A root no
  ``sys.path`` entry holds is a deployment problem, and the loader says so by name when it tries to
  load one (``ValueError``: not inside any sys.path entry).
* **it does not install an import hook.** The entry file is read through the loader this module hands
  importlib, so its own bytecode cache cannot serve an older reading; a package's submodules go
  through the ordinary machinery and get no record of their own. A record per submodule — and with it
  a ``stale()`` that sees an edit below a package's entry — is ``std/pluggable.py``'s to give, because
  it is the component that claims a tree rather than one file.
* **it does not declare what it loads.** A module exports component classes; whether to declare them,
  at which qualifier, and whether to bring them up is the application's assembly. This module moves
  classes, not instances, which is what keeps a manifest from having to know anything about keys.

The fingerprint is the second property of the *source* alone (:func:`fingerprint_of`), and the record
:func:`load_unit` stamps on the module (:class:`LoadedUnit`) is what makes it comparable across time:
comparing the two says whether the code that is *running* is still the code on *disk*. No watcher, no
hook and no table of previous hashes is needed — the previous fingerprint is on the module, where the
module's own record of itself belongs.

The name a module loads under comes from its path — the ordinary dotted name ``sys.path`` derives, so
``alpha.py`` on a root is ``alpha`` and ``alpha/`` beside it is ``alpha`` too. A module is the unit a
reload works at, so a name that said which generation were current would make every edit a new module
and nothing could ever be swapped. Two modules meant to *coexist* are correspondingly two files
(``alpha_v1.py`` and ``alpha_v2.py``) — two names because they are two files.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import CodeType, ModuleType
from typing import final, override

from .component import Component

STAMP = "__composat_unit__"
"""Where a loaded module records what it is, on the module itself.

``sys.modules`` is an ordinary part of the standard library and this module already depends on it — a
module has to be in there before it runs, and the name derived from its path is the index that makes
loading one module twice return the first. The record goes *in* the module rather than in a table
beside it, which keeps the index single-sourced: a second table could disagree with it, and would have
to be cleaned up when a module goes away. It is also what marks a module as *ours*, which is how a
name something else put in ``sys.modules`` is told apart from one this loader served.
"""


@final
@dataclass(frozen=True, slots=True)
class LoadedUnit:
    """What a module was, at the moment it was loaded: recorded on the module, read back from it.

    This is the *past* tense of a :class:`Unit`, and the distinction is the point. A ``Unit`` answers
    "what was loaded, and what it exports now"; the fingerprint it carries is the one taken at load
    time, and a caller that asks the file again (:func:`fingerprint_of`) is asking a different
    question. This record is what keeps the first answer around with no memory on this module's side.
    """

    path: Path
    """The entry file the module was executed from.

    A module that moved is the same code at a different address, which is why the fingerprint does not
    cover it.
    """

    fingerprint: str
    """The module's fingerprint as its source read when it was loaded.

    Read before the module executes, so it describes the source as of that moment; a file edited in the
    instant between that read and the interpreter's is the one case it misses. Worth knowing rather
    than hiding: this is a claim about a reading, and a reading has a time.
    """

    def is_stale(self) -> bool:
        """Whether the source at :attr:`path` still says what was loaded.

        ``False`` means the code running is the code on disk. ``True`` means the file has moved on since
        it was read — an edit nothing else in the deployment was told about, and the one fact a
        written-down version cannot answer: it is the same before and after a change that kept it.

        It reads the filesystem, so it raises ``FileNotFoundError`` when the module is gone: a deleted
        drop-in is a different event from an edited one, and one answer for both would conflate them.
        """
        return fingerprint_of(self.path) != self.fingerprint


def loaded_unit(module: ModuleType) -> LoadedUnit | None:
    """What ``module`` was loaded as, or ``None`` when this loader did not load it.

    Read straight off the module, so it works for a module obtained by plain ``import`` as well as one
    in hand from :func:`load_unit`. ``None`` is a real answer and not an error: it says the module
    carries no record, which is exactly how a name something *else* put in ``sys.modules`` is told
    apart from a module this loader served.
    """
    found: object = getattr(module, STAMP, None)
    return found if isinstance(found, LoadedUnit) else None


@final
@dataclass(frozen=True, slots=True)
class Unit:
    """One loaded module: where it came from, the module itself, what it exports, and its source hash.

    ``components`` is every component class the module's entry namespace holds, in the order it put
    them there. ``__module__`` is what decides ownership: a class written in the module, or in one of
    its subpackages and re-exported here, belongs to the unit; one merely imported from elsewhere does
    not — ``from composat.std import Something`` puts a component in the namespace, but it is not this
    module's own.

    An empty tuple is a whole answer rather than an error: a module of helpers is a module, and a
    manifest that names it gets one unit with nothing to declare. What is *not* guessed at is a class
    a submodule knows about and the entry does not export — searching a package for something that
    looks like a component is how a unit ends up loading the wrong class.

    ``fingerprint`` is the source as it read when this unit was loaded, which is the same hash
    :class:`LoadedUnit` keeps on the module. What the file says *now* is :func:`fingerprint_of`, called
    again: the difference between the two is the whole of "this module has moved on".
    """

    path: Path
    module: ModuleType
    components: tuple[type[Component], ...]
    fingerprint: str


def load_unit(path: Path) -> Unit:
    """Load the module at ``path`` and report the components it exports.

    ``path`` is a module file (``alpha.py``) or a package (``alpha/``, entered through its
    ``__init__.py``). The module goes into ``sys.modules`` under the name its *path* derives, which is
    what makes it reachable by the name an application's own modules would import it as.

    The same name loads once: a module already in the process is handed back rather than executed
    again, because executing it again would run its import-time side effects a second time. What is
    handed back describes the code that is *running* — the fingerprint it was executed with — rather
    than the file that is on disk now, so an edit made since is :meth:`LoadedUnit.is_stale`'s to
    report, and re-reading the source is not something this call does behind the caller's back.

    Raises rather than returning a failure value, and adds no error type of its own: a path that is
    absent is ``FileNotFoundError``, a module no ``sys.path`` entry holds is ``ValueError`` (the
    application guarantees ``sys.path`` — this module never writes to it), and anything the module
    raises while executing is passed through untouched, because its traceback is the most useful thing
    anyone gets. A batch that has to tolerate one bad module is a ``try`` in the caller's loop —
    whether one broken drop-in should stop the others is a policy about loading, and loading is the
    caller's decision.
    """
    resolved = _entry_of(Path(path).resolve())
    name = _module_name_of(resolved)

    already = sys.modules.get(name)
    if already is not None:
        return _reuse(already, resolved)

    fingerprint = fingerprint_of(resolved)
    module = _execute(resolved, name, package=resolved.name == "__init__.py")
    setattr(module, STAMP, LoadedUnit(resolved, fingerprint))
    return Unit(resolved, module, components_of(module), fingerprint)


def load_units(root: Path) -> tuple[Unit, ...]:
    """Load every module directly inside the directory ``root``, in path order.

    A directory is not a module: it is where a *set* of them lives. What counts is the modules at that
    level — every ``*.py`` beside the entry, and every subdirectory holding an ``__init__.py`` (loaded
    as a package) — and not the modules *under* them, which belong to the package above. An
    ``__init__.py`` in ``root`` is skipped: it is the entry of the directory as a whole, and a
    directory of modules is not itself a module.

    The units come back in the order their paths sort, so two runs over one directory agree. Failing on
    the first bad module is deliberate: this returns what it loaded or raises, and a caller that wants
    the rest is a ``try`` around :func:`load_unit` — the policy about one broken drop-in belongs where
    the drop-ins are known.
    """
    directory = Path(root).resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"no directory of modules at {root}")
    candidates = sorted(
        entry
        for entry in directory.iterdir()
        if (entry.suffix == ".py" and entry.name != "__init__.py")
        or (entry.is_dir() and (entry / "__init__.py").is_file())
    )
    return tuple(load_unit(entry) for entry in candidates)


def fingerprint_of(path: Path) -> str:
    """The fingerprint of the module at ``path``: a hash of its syntax alone.

    Syntax rather than bytecode, because that is the layer the question is asked at. Comments, blank
    lines and quote style leave no trace in it; neither do ``PYTHONHASHSEED``, ``-O`` / ``-OO``, or the
    interpreter's own version — so two deployments that say the same thing agree, which is what makes
    the comparison worth making. Behaviour is beyond it: a value computed at import time
    (``os.getpid()``) is absent from the hash, and two modules that differ only there fingerprint the
    same.

    One module, one file. A directory is folded into its entry, the same way :func:`load_unit` takes
    one; a package's submodules are modules of their own, and a fingerprint that covered the whole tree
    would answer a question about none of them.

    The algorithm is fixed rather than a parameter. What a fingerprint *means* has to be the same on
    both sides of a comparison, and a pluggable one would make it depend on who called — so an
    application that wants a different hash writes its own function and reads the same file.

    Pure and stateless: it reads the file and returns, so asking twice about an edited module answers
    differently. No history of answers is kept here — noticing that it changed is the caller's job, and
    so is deciding what to do about it.
    """
    entry = _entry_of(Path(path).resolve())
    text = entry.read_text(encoding="utf-8")
    return hashlib.sha256(ast.dump(ast.parse(text, filename=entry.name)).encode()).hexdigest()


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """A source loader that compiles the text itself, so a stale ``.pyc`` cannot be served.

    ``SourceFileLoader`` decides whether the cache beside the source is still good by comparing the
    source's mtime and size against what it recorded — and the mtime it records is the whole second.
    Two edits within one second that keep the file's length therefore read as "unchanged", and the
    loader silently serves the *old* code: measured, not theorised. ``sys.dont_write_bytecode`` offers
    no help, because it governs writing the cache while the problem is on the reading side.

    That is the worst possible failure here. A drop-in is loaded precisely because its file just
    changed, and being handed the previous version of it — with no error, and with the fingerprint
    correctly reporting the new source — is a discrepancy nothing downstream could explain. Compiling
    the text we just read removes the question rather than answering it.
    """

    @override
    def get_code(self, fullname: str) -> CodeType:
        return compile(self.get_data(self.path), self.path, "exec", dont_inherit=True)


def _execute(entry: Path, name: str, *, package: bool) -> ModuleType:
    """Run the module under ``name``, from source, registered in ``sys.modules`` as it runs.

    The registration happens before the module executes because a package's own relative imports need
    it. A module that raises halfway is taken back out: left registered, the next load of the same name
    would find it and hand back something that stopped coming up.
    """
    loader = _SourceOnlyLoader(name, str(entry))
    spec = importlib.util.spec_from_loader(name, loader, is_package=package)
    if spec is None:  # pragma: no cover - a SourceFileLoader always yields a spec
        raise TypeError(f"{entry} could not be described as a module")
    if package:
        spec.submodule_search_locations = [str(entry.parent)]
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        loader.exec_module(module)
    except BaseException:
        _ = sys.modules.pop(name, None)
        raise
    return module


def _reuse(module: ModuleType, resolved: Path) -> Unit:
    """The module already under this name, once it is confirmed to be the one at ``resolved``.

    What decides is the record on the module, not the module's ``__file__``: the record is something
    this loader wrote, while ``__file__`` is set by the machinery and can belong to anything that put a
    module under this name. No record means the name is held by something else, which is refused
    rather than shadowed.

    A record pointing at a different file means two ``sys.path`` entries offer one name — a shadowed
    module, not a second copy: paths are resolved before they are compared, so a symlink or a repeat of
    the same root is the same module, while two different files under one name is the case where
    "which one did I load" has two answers.

    The fingerprint comes off the record rather than off the file: the unit is the one *running*, and
    what it was is on the module. Its components are collected again — the namespace may have grown a
    class since, and reading it is free — which keeps one answer for "what does this unit hold".
    """
    record = loaded_unit(module)
    if record is None:
        raise ValueError(f"{resolved} wants the name {module.__name__}, which this loader did not load")
    if record.path != resolved:
        raise ValueError(f"{module.__name__} is loaded from {record.path}, and {resolved} derives the same name")
    return Unit(record.path, module, components_of(module), record.fingerprint)


def components_of(module: ModuleType) -> tuple[type[Component], ...]:
    """Every component class ``module`` holds under its entry namespace, in namespace order.

    "Exports" is meant literally: the classes are read out of the module namespace, so they are there
    because the module put them there — written in the file, or imported into it from a subpackage of
    the same unit. ``__module__`` is what separates the two cases, and what keeps a component imported
    from elsewhere out of this unit.

    All of them are collected rather than one being chosen. A module may hold two components meant to
    be declared at two keys, and refusing that would be refusing a deployment shape the assembly
    already supports; a caller that wants exactly one says so where the manifest is written.

    The module is the argument rather than the unit, because a module is what a caller has: a
    generation that has been superseded is still a module object with classes in it, and asking it
    what it holds is how a reload compares the two.
    """
    name = module.__name__
    members: dict[str, object] = vars(module)
    found: list[type[Component]] = []
    for value in members.values():
        if not isinstance(value, type) or not issubclass(value, Component):
            continue
        if value.__module__ == name or value.__module__.startswith(f"{name}."):
            found.append(value)
    return tuple(found)


def _entry_of(path: Path) -> Path:
    """The file a module is entered through: the module itself, or a package's ``__init__.py``."""
    if path.is_dir():
        entry = path / "__init__.py"
        if not entry.is_file():
            raise FileNotFoundError(f"{path} is a directory with no __init__.py, so it is not a module")
        return entry
    if not path.is_file():
        raise FileNotFoundError(f"no module at {path}")
    return path


def _module_name_of(entry: Path) -> str:
    """The ordinary dotted name the path derives, or refuse because no ``sys.path`` entry holds it.

    This is the whole naming rule: a module is known by where it is, relative to a root the
    application put on ``sys.path``. Which root is not this module's guess to make — ``sys.path`` is
    the answer, and it is read, never written.

    Longest root first, so a root nested inside another wins for the module under it. A module
    *outside* every root is refused rather than given a name of its own: a name is a claim that
    something can import it, and a module no root holds cannot be. A derived name whose parts are not
    identifiers is refused too — ``alpha-v2.py`` spells a name no ``import`` statement can write, so it
    is answered here rather than left to fail somewhere less clear.
    """
    stem = entry.parent if entry.name == "__init__.py" else entry
    roots = (Path(part or ".").resolve() for part in sys.path)
    for root in sorted(roots, key=lambda part: len(part.parts), reverse=True):
        try:
            relative = stem.relative_to(root)
        except ValueError:
            continue
        parts = relative.parts
        if entry.name != "__init__.py":
            parts = (*parts[:-1], entry.stem)
        name = ".".join(parts)
        if not name or not all(part.isidentifier() for part in parts):
            raise ValueError(f"{entry} derives {name!r}, which is not a name a module can be imported as")
        return name
    raise ValueError(f"{entry} is not inside any sys.path entry, so it has no importable name")
