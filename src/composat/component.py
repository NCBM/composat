"""The component base: an identity, the fields it declares, and the hooks it answers.

A component is business methods. The framework reads three things off the class:

* **the identity** — the class's own path, from :class:`~composat.identity.ComponentIdentity`;
* **requirements** — the fields the class body hands to :func:`require`, whose descriptor is what
  answers a read with the instance the field asks for;
* **capabilities** — the hooks the type writes, which is what the state machine drives.

Everything else an instance carries is the application's: an annotated plain resource, an attribute
with no annotation, a private cache. A field is a requirement because the class body said so, not
because of what its annotation looks like; the annotation says what the field reads as, which is how
a checker keeps up with both halves.

What the framework *does* to a component is here too, as functions rather than methods —
:func:`requirements_of` and :func:`capabilities_of` on a type, :func:`attach`, :func:`read` and
:func:`reopen_epoch` on an instance: an operation an application must not be able to replace is not
offered to it as a member. ``@final`` on a method of the base answers the same question after the
fact, refusing an extension the declaration had already invited.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Annotated, ClassVar, Protocol, cast, get_args, get_origin, get_type_hints

from .context import current_or_none
from .errors import UnboundRequirementError
from .identity import ComponentKey
from .scope import EffectScope, Reclaimable
from .spec import RequirementSpec
from .state import ComponentStateControl


class RequirementResolver(Protocol):
    """How one requirement becomes an instance: the caller's rule, read off a key and a requirement.

    The first argument is the *asker*'s own key, not a call's: a field is read from inside a hook,
    outside the call level. The second is the whole requirement rather than just its service, so an
    implementation that understands the requirement's ``extras`` acts on them — that is where a scope
    strategy attaches — and one that ignores them resolves by key alone.

    The third is the scope the resolution is made on behalf of, which lets a resolver hand back
    something with a lifetime shorter than the assembly's: what it builds for this reader alone is
    registered there and released when that scope closes. It is the innermost lifetime the read
    happens inside (:func:`read` passes the call's scope when one is in force, the instance's epoch
    otherwise), so "this answer lives as long as the request" is written in the resolver rather than
    in a second read path. A resolver answering from the assembly's instances registers nothing and
    passes it through, so one seam covers both kinds of answer.
    """

    def __call__(self, key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable, /) -> object:
        """The instance answering ``requirement`` for ``key``, registered on ``effects`` if new."""
        ...


@dataclass(frozen=True, slots=True)
class Binding:
    """Where one instance's requirements are read from: the key it answers at, and the rule it asks.

    Written by :func:`attach` before ``load`` and read by :func:`read`, which is the whole of what the
    attribute is for. It is a value rather than two attributes on the instance so that the pair cannot
    be half-written, and public rather than private so that a hook can say which tenant it is serving
    (``self.binding.key``) without the framework keeping a second copy somewhere.
    """

    key: ComponentKey
    rule: RequirementResolver


LIFECYCLE_HOOKS = ("load", "unload", "reload", "enable", "disable", "drain")
"""The hooks the state machine drives, in the order they are reported.

The base declares all six, which gives ``@override`` something to check against and makes driving an
unwritten hook an ordinary no-op. Declaring one is the base's job; :func:`capabilities_of` decides
what a type claims.
"""


class _Bound:
    """The attribute a requirement is read as: a live resolution, never a stored instance.

    ``require()`` returns one, so the class body is what installs it: the field exists as an ordinary
    assignment, which is what a checker reads, and *is* the descriptor, which is what a read goes
    through. What the class body cannot say is the name, or the annotation the marker sits under, so
    :meth:`settle` fills in the record once the class exists — and a marker nobody settled, set on a
    finished class rather than written in its body, is refused rather than read as a second answer.

    Reading asks the instance it was read from, at that instance's key and on that instance's epoch,
    so a replaced provider is visible to the reader with nothing refreshed. Off the class it answers
    with the requirement itself, which is how a tool asks what a field declares.

    Assigning raises: the resolver owns the answer, and a stored copy would be a second answer that
    silently disagrees with the first.
    """

    #: The contract given at the declaration site, when one was: ``require(Store)`` instead of
    #: ``require()``. It is what the annotation has to agree with, and not a second source of truth.
    contract: type[object] | None

    #: The requirement as the registry and the assembly read it; ``None`` until the class is settled.
    spec: RequirementSpec | None = None

    def __init__(self, contract: type[object] | None = None) -> None:
        self.contract = contract

    def settle(self, name: str, annotation: object, owner: type[object]) -> RequirementSpec:
        """Record what the field this marker was written as asks for.

        The name is the field's own, beside the marker; the annotation is read for what the field
        reads as. Two markers are refused rather than recorded: one whose contract at the declaration
        site the annotation disagrees with, since one of the two is then wrong, and one whose
        annotation names nothing a requirement could ever be answered by.
        """
        named = _is_contract(annotation)
        if self.contract is not None and named is not None and named is not self.contract:
            asked, said = self.contract.__qualname__, named.__qualname__
            raise ValueError(f"{owner.__qualname__}.{name} is declared for {asked} and annotated {said}")
        contract = self.contract if self.contract is not None else named
        if contract is None:
            where = "names no contract" if annotation is None else "names something that is not a contract"
            raise ValueError(f"{owner.__qualname__}.{name} is a requirement whose annotation {where}")
        self.spec = RequirementSpec(name, contract, _extras(annotation))
        return self.spec

    def __get__(self, instance: Component | None, owner: type[object] | None = None) -> object:
        if self.spec is None:
            where = "a class" if owner is None else owner.__qualname__
            raise ValueError(f"{where} was handed a requirement after it was made: declare it in the class body")
        if instance is None:
            return self.spec
        return read(instance, self.spec)

    def __set__(self, instance: Component, value: object) -> None:
        field = "this field" if self.spec is None else self.spec.name
        raise AttributeError(f"{field} resolves to the instance that answers it, and is not assignable")


def require[T](contract: type[T] | None = None) -> T:
    """Declare a field a requirement: the framework answers it, and reading the field is how.

    ``store: Store = require()`` is the whole spelling. The annotation says what the field reads as,
    which is what a checker needs; the marker says the framework is what answers it, which used to be
    guessed from the annotation. Being an assignment in the class body, the field counts as
    initialized to a checker, so a requirement no longer looks like an instance variable nothing
    ever sets.

    ``contract`` is the optional half, for a checker that will not infer a return type from the
    annotation the call is written under: ``store: Store = require(Store)`` reads the same and is
    refused when the two disagree. Written without the annotation it is still a complete declaration,
    and the field still reads as ``Store`` — what is lost is a checker's willingness to accept a
    class attribute nobody annotated.

    The contract need not be in scope while the class is being made, because no annotation is read
    then either: a class defined further down the module, or imported only for typing, declares its
    requirements the same way.
    """
    return cast("T", _Bound(contract))


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

    This is the check a *marked* field goes through, not what finds the marked fields: the class body
    said which fields are requirements, and this decides whether what each one is annotated with can
    answer for it.

    * a :class:`Component` subclass or a ``Protocol`` → a **contract**, and the registry draws an
      edge for it;
    * anything else — ``list[str]``, a plain resource type — **an ordinary value**, which a
      requirement cannot ask for.

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


def _own_fields(cls: type[object]) -> dict[str, object]:
    """What ``cls``'s own body holds, whatever it holds: its class dict, in written order.

    ``vars`` is typed as holding ``Any``, and a class body may hold anything: a method, an
    annotation's default, a marker. Naming the copy is what lets a marker be found by ``isinstance``
    rather than by trusting the dict it came out of.
    """
    return cast("dict[str, object]", dict(vars(cls)))


def _unsettled(value: object) -> bool:
    """Whether ``value`` is a requirement marker no class has read yet."""
    return isinstance(value, _Bound) and value.spec is None


class Component:
    """A component: business methods, plus the declarations the framework reads.

    A component is a plain class. Its identity is the path it was written at — the module and the
    qualified name there — which is what a reload keeps while replacing the class object. It declares
    the six lifecycle hooks, and answers nothing for any hook until a type writes its own:
    :func:`capabilities_of` reports the ones a type writes, and a component that writes none is one
    nothing drives.

    What the framework reads and does on a component are functions in this module, not members of it:
    :func:`requirements_of` and :func:`capabilities_of` on a type, and :func:`attach`, :func:`read` and
    :func:`reopen_epoch` on an instance. An operation an application must not be able to replace is not
    offered to it as a method — its identity is read by :meth:`ComponentIdentity.of`, which is where a
    class's own path is turned into one.
    """

    #: The requirements this type declares, once settled; ``None`` until then. The map is complete
    #: for the class — what it inherited, then what its own body declared — so a subclass answers for
    #: both without walking anything.
    _declared: ClassVar[dict[str, RequirementSpec] | None] = None

    effect_scope: EffectScope

    #: Where this instance is in its lifecycle, and the lock its moves run under. One per instance,
    #: and read-only from outside: the manager is what drives it, through the writer the control
    #: hands out for as long as it holds the lock.
    state: ComponentStateControl

    #: Where this instance's requirements are read from, written by :func:`attach` before ``load``.
    #: ``None`` until then, which is what :func:`read` reports as an unbound requirement.
    binding: Binding | None = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Settle the requirements now, when every name they are written under already resolves.

        Settling here keeps a read from changing the shape of the class: a checker, ``hasattr`` and
        ``dir()`` all see the same class they saw when it was made.

        An annotation naming a class defined further down the module does not resolve while its own
        class is still being built, and Python allows that forward reference. Such a class stays
        unsettled and is settled at its first ask, when everything the module defines exists.
        """
        super().__init_subclass__(**kwargs)
        with contextlib.suppress(NameError):
            # A forward reference. It resolves at the first ask, or fails loudly there.
            cls._classify()

    def __init__(self) -> None:
        # An instance is born inside an epoch rather than acquiring one on ``load``, which makes the
        # two ends symmetric and a component that never loaded recycle an empty scope.
        self.effect_scope = EffectScope()
        self.state = ComponentStateControl()

    @classmethod
    def _requirements_of(cls) -> dict[str, RequirementSpec]:
        """The requirements of ``cls``, settled once: what it inherited, then what it declares.

        A marker that never got a record is refused on every ask, which puts the refusal where a
        reader would otherwise be misled: a requirement installed on a class that already answered is
        not in the map, so it would be declared, never ordered, never resolved.
        """
        cls._require_settled()
        if cls is Component:
            return {}
        if cls.__dict__.get("_declared") is None:
            cls._classify()
        return cast("dict[str, RequirementSpec]", cls._declared)

    @classmethod
    def _classify(cls) -> None:
        """Settle ``cls``'s requirements once: one record per field its body marked.

        The class body installed the descriptors — ``require()`` returned them — so what is left is
        to read each one's annotation and keep the record, which is what the registry draws edges
        from. The map comes out whole, the base's requirements first, so a subclass that declares one
        of its own still answers for everything it inherited.

        Only the fields this class's own body marks are settled here: a base settled its own when it
        was made, and re-settling it would rewrite a shared descriptor. A class that marks nothing
        reads no annotation at all, so an ordinary field naming something odd is none of this ask's
        business.
        """
        own = {name: value for name, value in _own_fields(cls).items() if isinstance(value, _Bound)}
        if not own:
            cls._declared = dict(cls._inherited())
            return
        annotations = _resolved_annotations(cls)
        declared = dict(cls._inherited())
        for name, marker in own.items():
            declared[name] = marker.settle(name, annotations.get(name), cls)
        cls._declared = declared

    @classmethod
    def _inherited(cls) -> dict[str, RequirementSpec]:
        """The map the nearest base settled, settling it first if its own ask was deferred."""
        for klass in cls.__mro__[1:]:
            if klass is Component:
                return {}
            base = cast("type[Component]", klass)
            if base.__dict__.get("_declared") is None:
                base._classify()
            found = base.__dict__.get("_declared")
            if isinstance(found, dict):
                return cast("dict[str, RequirementSpec]", found)
        return {}

    @classmethod
    def _require_settled(cls) -> None:
        """Refuse a marker set on a class that had already been made: nothing would ever order it."""
        late = sorted(name for name, value in _own_fields(cls).items() if _unsettled(value))
        if late:
            fix = "a requirement is written in the class body"
            raise ValueError(f"{cls.__qualname__} was handed {', '.join(late)} after it was made: {fix}")

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

        Being told and being able to read are two facts, though. The swap moves the key to the new
        generation before that generation starts serving, so a receiver driven in the middle of it
        reads a provider that is built and not yet serving — a miss, and a receiver that has to act on
        the new one now is what ``composat.std.wait.serving`` exists for.

        It is a notification rather than a capability: it is driven for every dependant whether the
        type writes it or not, and the base's answer is to do nothing.
        """
        _ = requirement


def requirements_of(component: type[Component]) -> tuple[RequirementSpec, ...]:
    """The requirements ``component`` declares, in declaration order, as plain records.

    A reading of a type rather than a member of it, for the same reason :func:`attach` and
    :func:`read` are: the framework's own readings are not an extension point. A class that could
    answer differently would disagree with the descriptor the same declaration installed — the
    registry draws its edges from this answer, and the field resolves through that descriptor — and
    the two would part silently.

    The map it reads is the class's own bookkeeping, reached from here on purpose: what an application
    must not be able to answer for itself is a *method* on the class, and keeping the map private while
    the reading lives here is exactly that split.
    """
    settled = component._requirements_of()  # pyright: ignore[reportPrivateUsage]
    return tuple(settled.values())


def capabilities_of(component: type[Component]) -> tuple[str, ...]:
    """The hooks ``component`` writes: what the state machine is allowed to drive.

    Read off the MRO with the base **skipped**, so :class:`Component` declaring a hook makes it
    available to ``@override`` without claiming the capability for every component in existence. An
    inherited hook counts (a subclass of a disposable component is disposable) and an override counts
    once.

    A misspelled hook counts as nothing, which is what the base's declarations buy: a checker answers
    "there is no ``laod`` to override" at the typo, where a bare framework would report one capability
    fewer and leave the cause far from the effect.
    """
    written: set[str] = set()
    for klass in component.__mro__:
        if klass is Component or klass is object:
            continue
        written.update(name for name in LIFECYCLE_HOOKS if name in vars(klass))
    return tuple(name for name in LIFECYCLE_HOOKS if name in written)


def attach(component: Component, key: ComponentKey, resolver: RequirementResolver) -> None:
    """Point ``component``'s declared requirements at ``key``, resolved by ``resolver``.

    The assembly that brings the instance up calls this before ``load`` and after the instance is
    reachable, so a hook reading a requirement reads something already there. The address is kept as
    ``component.binding``, so a hook that has to say which tenant it is serving can read it back.
    """

    component.binding = Binding(key, resolver)


def read(component: Component, requirement: RequirementSpec) -> object:
    """What ``requirement`` reads as on ``component``: the instance answering it at its own key.

    The one place a requirement turns into an instance, reached from the descriptor's ``__get__``.
    Whatever a resolver registers is answered to the *innermost lifetime the read happens inside*: a
    read made during a call registers into that call's scope and is released when the call ends, and
    a read made anywhere else registers into the instance's epoch. That is what makes an annotation
    that asks for a short-lived answer mean the same thing whether it is read as a field or resolved
    from the call — and what makes the epoch the fallback rather than the rule.

    The key is the asker's either way. A call says whose work this is; it does not make the component
    reading one of its fields ask on the call's behalf.

    ``UnboundRequirementError`` when nothing is attached: an instance built and used by hand, or read
    from a hook of something that went through no assembly.
    """

    binding = component.binding
    if binding is None:
        raise UnboundRequirementError(component, requirement.name)
    call = current_or_none()
    effects: Reclaimable = component.effect_scope if call is None else call.effects
    return binding.rule(binding.key, requirement, effects)


async def reopen_epoch(component: Component) -> None:
    """Release this epoch's effects, and give the instance the scope the next one starts with.

    An epoch is one instance's lifetime between ``load`` and ``unload``: what a resolver registered on
    its behalf is released when it ends, and the scope it ends into is *closed*, so a straggler holding
    the old one cannot collect into a lifetime that has moved on. The instance is left with a fresh
    scope rather than none, which is what makes the two ends symmetric — a component that never loaded
    recycles an empty one instead of being a special case.

    The write back happens here rather than at each call site, because an instance whose scope was
    closed under it is broken in a way only the next ``put`` reports.
    """

    component.effect_scope = await component.effect_scope.reopen()
