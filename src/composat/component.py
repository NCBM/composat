"""The component base: an identity, the fields it declares, and the hooks it answers.

A component is business methods. The framework reads three things off the class:

* **the identity** — the class's own path, from :class:`~composat.identity.ComponentIdentity`;
* **requirements** — annotations naming a contract, installed as descriptors so that reading the
  field is what resolves the dependency;
* **capabilities** — the hooks the type writes, which is what the state machine drives.

Everything else an instance carries is the application's: an annotated plain resource, an attribute
with no annotation, a private cache. :meth:`Component._classify` is the one place the line is drawn.
"""

from __future__ import annotations

import contextlib
from typing import Annotated, ClassVar, Protocol, cast, get_args, get_origin, get_type_hints

from .errors import UnboundRequirementError
from .identity import ComponentIdentity, ComponentKey
from .scope import EffectScope, Reclaimable
from .spec import RequirementSpec


class RequirementResolver(Protocol):
    """How one requirement becomes an instance: the caller's rule, read off a key and a requirement.

    The first argument is the *asker*'s own key, not a call's: a field is read from inside a hook,
    outside the call level. The second is the whole requirement rather than just its service, so an
    implementation that understands the requirement's ``extras`` acts on them — that is where a scope
    strategy attaches — and one that ignores them resolves by key alone.

    The third is the epoch the resolution is made on behalf of, which lets a resolver hand back
    something with a lifetime shorter than the assembly's: an instance built for this reader alone is
    registered in that epoch and released when it closes. A resolver answering from the assembly's
    instances registers nothing and passes it through, so one seam covers both kinds of answer.
    """

    def __call__(self, key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable, /) -> object:
        """The instance answering ``requirement`` for ``key``, registered on ``effects`` if new."""
        ...


LIFECYCLE_HOOKS = ("load", "unload", "reload", "enable", "disable", "drain")
"""The hooks the state machine drives, in the order they are reported.

The base declares all six, which gives ``@override`` something to check against and makes driving an
unwritten hook an ordinary no-op. Declaring one is the base's job; :meth:`Component.capabilities`
decides what a type claims.
"""


class _Bound:
    """The attribute a requirement is read as: a live resolution, never a stored instance.

    Reading asks the instance it was read from, at that instance's key and on that instance's epoch,
    so a replaced provider is visible to the reader with nothing refreshed. Off the class it answers
    with the requirement itself, which is how a tool asks what a field declares.

    Assigning raises: the resolver owns the answer, and a stored copy would be a second answer that
    silently disagrees with the first.
    """

    spec: RequirementSpec

    def __init__(self, spec: RequirementSpec) -> None:
        self.spec = spec

    def __get__(self, instance: Component | None, owner: type[object] | None = None) -> object:
        if instance is None:
            return self.spec
        return instance.resolve(self.spec)

    def __set__(self, instance: Component, value: object) -> None:
        raise AttributeError(f"{self.spec.name} resolves to the instance that answers it, and is not assignable")


def _peeled(annotation: object) -> object:
    """``annotation`` with its ``Annotated`` wrappers removed: the contract it names, if any."""
    current = annotation
    while get_origin(current) is Annotated:
        current = cast("object", get_args(current)[0])
    return current


def _extras(annotation: object) -> tuple[object, ...]:
    """The metadata every ``Annotated`` layer of ``annotation`` carries, outermost first."""
    current = annotation
    found: list[object] = []
    while get_origin(current) is Annotated:
        args = get_args(current)
        found.extend(args[1:])
        current = cast("object", args[0])
    return tuple(found)


def _is_contract(annotation: object) -> type[object] | None:
    """The contract ``annotation`` names, or ``None`` when it names an ordinary value.

    The whole of the framework's classification, in one rule:

    * a :class:`Component` subclass or a ``Protocol`` → a **contract**: the field is a requirement,
      a descriptor is installed, and the registry draws an edge;
    * anything else — ``list[str]``, a plain resource type, an annotation-free attribute — **an
      ordinary attribute**: the constructor's assignment survives and the framework reads that name
      once, for the freeze.

    A ``Protocol`` with no provider bound to it is a contract that has no answer yet, and the
    registry raises :class:`~composat.errors.UnboundServiceError` where it draws the edge.
    """
    contract = _peeled(annotation)
    if not isinstance(contract, type):
        return None
    klass = cast("type[object]", contract)
    if not (issubclass(klass, Component) or bool(getattr(klass, "_is_protocol", False))):
        return None
    return klass


def _resolved_annotations(cls: type[object]) -> dict[str, object]:
    """Every annotation ``cls`` and its bases declare, by name, bases first, evaluated.

    ``include_extras`` is on purpose: the ``Annotated`` metadata is where a requirement's extras
    live. An unresolvable name is an error rather than a field quietly read as ordinary, and the two
    cases behind it read differently. A class not defined yet is a forward reference, retried at the
    first ask. A component declared inside a function whose annotation names a sibling declared there
    is the other: the strings evaluate against the module, where that local name is absent, so the
    message names the fix — declare the component at module level.
    """
    try:
        return get_type_hints(cls, include_extras=True)
    except NameError as error:
        if "<locals>" not in cls.__qualname__:
            raise
        where = f"{cls.__qualname__} names {error.name!r} in an annotation it cannot resolve"
        raise NameError(f"{where}: declare the component at module level, so the name is reachable") from error


def _field_names(cls: type[object]) -> frozenset[str]:
    """The annotation names ``cls`` and its bases carry, taken raw and unresolved.

    Names need no evaluation, which is why the freeze can happen at class creation while the
    classification cannot: a forward reference is a perfectly good name and not yet a type.
    """
    names: set[str] = set()
    for klass in reversed(cls.__mro__):
        names.update(vars(klass).get("__annotations__") or {})
    return frozenset(names)


class Component:
    """A component: business methods, plus the declarations the framework reads.

    No metaclass and no abstract method. A component with nothing to load and nothing to unload is a
    legal component: :meth:`capabilities` answers what the state machine drives from what the type
    writes, and driving nothing is a no-op.

    The class body states no identity: who a component is, is where it was written — a path a reload
    keeps and a class object it replaces.
    """

    #: The requirements this type declares, once classified; ``None`` until then. A class that has
    #: not been classified answers with its nearest base's map.
    _declared: ClassVar[dict[str, RequirementSpec] | None] = None

    #: The annotation names this type was created with, frozen. A name added afterwards is refused:
    #: a requirement declared on a class that has already answered is one nothing will ever order.
    _fields: ClassVar[frozenset[str]] = frozenset()

    effect_scope: EffectScope

    _resolver: RequirementResolver | None = None
    _key: ComponentKey | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Freeze the field names, and classify now when every name already resolves.

        Classifying here keeps a read from changing the shape of the class: ``hasattr``, ``dir()`` and
        a checker reading the annotations all see the descriptors, because they were installed when
        the class was made.

        An annotation naming a class defined further down the module does not resolve while its own
        class is still being built, and Python allows that forward reference. Such a class stays
        unclassified and is classified at its first ask, when everything the module defines exists.
        """
        super().__init_subclass__(**kwargs)
        cls._fields = _field_names(cls)
        with contextlib.suppress(NameError):
            # A forward reference. It resolves at the first ask, or fails loudly there.
            cls._classify()

    def __init__(self) -> None:
        # An instance is born inside an epoch rather than acquiring one on ``load``, which makes the
        # two ends symmetric and a component that never loaded recycle an empty scope.
        self.effect_scope = EffectScope()

    @classmethod
    def identity(cls) -> ComponentIdentity:
        """The identity this type declares: the module it was written in, and its name there."""
        return ComponentIdentity.of(cls)

    @classmethod
    def requirements(cls) -> tuple[RequirementSpec, ...]:
        """The requirements this type declares, in declaration order, as plain records."""
        return tuple(cls._requirements_of().values())

    @classmethod
    def capabilities(cls) -> tuple[str, ...]:
        """The hooks this type writes: what the state machine is allowed to drive.

        Read off the MRO with the base **skipped**, so :class:`Component` declaring a hook makes it
        available to ``@override`` without claiming the capability for every component in existence.
        An inherited hook counts (a subclass of a disposable component is disposable) and an override
        counts once.

        A misspelled hook counts as nothing, which is what the base's declarations buy: a checker
        answers "there is no ``laod`` to override" at the typo, where a bare framework would report
        one capability fewer and leave the cause far from the effect.
        """
        written: set[str] = set()
        for klass in cls.__mro__:
            if klass is Component or klass is object:
                continue
            written.update(name for name in LIFECYCLE_HOOKS if name in vars(klass))
        return tuple(name for name in LIFECYCLE_HOOKS if name in written)

    @classmethod
    def _requirements_of(cls) -> dict[str, RequirementSpec]:
        """The classified requirements of ``cls``: its own map, or the nearest base's.

        The frozen fields are checked on every ask, which puts the freeze where a reader would
        otherwise be misled: a class whose annotations grew after classification answers with the
        requirements it was classified with, and the extra one would be missing from every answer
        that matters — declared, never ordered, never resolved.
        """
        grown = _field_names(cls) - cls._fields
        if grown:
            names = ", ".join(sorted(grown))
            raise ValueError(f"{cls.__qualname__} declared {names} after its fields were frozen")
        if cls is not Component and cls.__dict__.get("_declared") is None:
            cls._classify()
        for klass in cls.__mro__:
            found = vars(klass).get("_declared")
            if isinstance(found, dict):
                return cast("dict[str, RequirementSpec]", found)
        return {}

    @classmethod
    def _classify(cls) -> None:
        """Settle ``cls``'s requirements once: classify each annotation, install what it declares.

        The field names were frozen at class creation; this decides which of them are needs and
        installs the descriptors that make them readable. The installed attribute *is* the
        requirement, so declaring and reading are one act seen from two sides, and the registry draws
        an edge exactly where the field is readable.
        """
        declared: dict[str, RequirementSpec] = {}
        for name, annotation in _resolved_annotations(cls).items():
            contract = _is_contract(annotation)
            if contract is None:
                continue
            spec = RequirementSpec(name, contract, _extras(annotation))
            setattr(cls, name, _Bound(spec))
            declared[name] = spec
        cls._declared = declared

    def attach(self, key: ComponentKey, resolver: RequirementResolver) -> None:
        """Point this instance's declared requirements at ``key``, resolved by ``resolver``.

        The assembly that brings the instance up calls this before ``load`` and after the instance is
        reachable, so a hook reading a requirement reads something already there. The instance
        resolves a question about itself against the address it was attached at.
        """
        self._key = key
        self._resolver = resolver

    def resolve(self, requirement: RequirementSpec) -> object:
        """What a declared requirement reads as: the instance answering it at this instance's key.

        The one place a requirement turns into an instance, reached from the descriptor's ``__get__``.
        Whatever a resolver registers is answered to this instance's epoch, so a field read while the
        component is loaded belongs to the epoch it was read in and is released when that epoch
        closes. Reading during ``unload`` is the last moment the scope takes effects.
        """
        resolver = self._resolver
        key = self._key
        if resolver is None or key is None:
            raise UnboundRequirementError(self, requirement.name)
        return resolver(key, requirement, self.effect_scope)

    async def load(self) -> None:
        pass

    async def unload(self) -> None:
        pass

    async def reload(self) -> None:
        pass

    async def enable(self) -> None:
        pass

    async def disable(self) -> None:
        pass

    async def drain(self) -> None:
        pass

    async def provider_changed(self, requirement: RequirementSpec) -> None:
        """A provider this instance declared has been replaced or reloaded under it.

        Driven on the *dependants* — the instances that declared the requirement — after the
        replacement landed, and on the instance that was replaced never. What to do about it is the
        receiver's: re-read the field, drop a cached handle, or ask the application to replace this
        one too.

        It carries the requirement rather than the new instance, which keeps this hook what it is
        for: a receiver that wants the value reads the field, whose answer is already current, and one
        that wants to know whether it moved compares what it kept.

        It is a notification rather than a capability: it is driven for every dependant whether the
        type writes it or not, and the base's answer is to do nothing.
        """
        _ = requirement
