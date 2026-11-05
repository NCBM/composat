"""Errors the framework layer raises on its own behalf.

The state machine's own refusal — a verb driven from a state it does not leave — lives with its
tables, in :class:`composat.state.ComponentStateError`.
"""

from .identity import ComponentKey
from .state import ComponentState


class UnboundServiceError(RuntimeError):
    """A service type names no component, so the annotation can answer for nothing.

    A component asks for a service by the interface it is written against, and such an annotation
    names a provider only once the registry has been told who provides it: the declaration that asks
    is where an empty mapping is refused. Answering with a default, or dropping the annotation, would
    leave a requirement missing with nobody the wiser.
    """

    service: type[object]

    def __init__(self, service: type[object]) -> None:
        super().__init__(f"no component is bound to {service!r}")
        self.service = service


class ServiceNotUpError(RuntimeError):
    """The instance a caller asked for is not one it may read: absent, or resting too low.

    Services are looked up under the caller's own key, so another instance of the same provider
    belongs to another key, and answering with it would answer a question the caller did not ask.
    The three fields tell "nothing is up" from "it is up and not serving": the second is work still to
    finish, and the first is a declaration to add.
    """

    key: ComponentKey
    asked_from: ComponentKey | None
    level: ComponentState | None

    def __init__(
        self,
        key: ComponentKey,
        *,
        asked_from: ComponentKey | None = None,
        level: ComponentState | None = None,
    ) -> None:
        said = f"nothing is up under {key}" if level is None else f"the instance under {key} is {level.name}"
        if asked_from is not None:
            said = f"{said}, asked for from {asked_from}"
        super().__init__(said)
        self.key = key
        self.asked_from = asked_from
        self.level = level


class NoCallContextError(RuntimeError):
    """The call level was asked from outside any call.

    ``ContextVar.get()`` answers a missing binding with ``LookupError``, and a call that is absent is
    a fact about where the code is running rather than a key that was missed. This error states
    which, so a reader tells the two apart at a glance.
    """

    def __init__(self) -> None:
        super().__init__("no call is in progress: there is nothing to resolve or register against")


class UnboundRequirementError(RuntimeError):
    """A declared requirement was read before anything resolved it to an instance.

    A field marked with ``require()`` is a *requirement*: the type says the instance will be handed
    one, so reading the field is as plain as reading any attribute. ``composat.component.attach``
    makes that true before ``load``. This error means the instance ran ahead of the binding —
    constructed and used by hand, or read from a hook of something that went through no assembly —
    and it is raised only for a name the type declared, so probing an instance for anything else keeps
    Python's plain ``AttributeError``.
    """

    component: object
    field: str

    def __init__(self, component: object, field: str) -> None:
        super().__init__(f"{type(component).__name__}.{field} is a declared requirement, and nothing is attached to it")
        self.component = component
        self.field = field


class LifecycleReentryError(RuntimeError):
    """A component asked to be driven while it was already being driven.

    The verbs hold the instance's lock for the whole move and are not reentrant, so a hook that drove
    its own component would wait on a lock it is holding; the guard refuses the ask where it is made
    instead of leaving a hung task behind. What a component does instead is *declare* what it needs:
    the assembly brings requirements up in order, and a component never has to.

    The guard follows the task running the hook, so a task a hook starts inherits it and coming back
    later to drive the manager is refused as well, while an operation running in another task stays
    independent, as concurrent work on other components must.
    """

    component: object
    operation: str
    current: str

    def __init__(self, component: object, operation: str, current: str) -> None:
        super().__init__(f"{type(component).__name__} asked for {operation} while {current} was running")
        self.component = component
        self.operation = operation
        self.current = current


class KeyInTransitError(RuntimeError):
    """Another call already has ``key``: it is being brought up, taken down, moved or replaced.

    A batch speaks for the keys it names, and for the ones it derived along the way, from the moment
    it accepts them until the last of them is done; a second call naming one of them is refused rather
    than queued, because two callers that disagree about whether a tenant should be serving would
    otherwise be settled by whichever happened to be scheduled second. ``moving`` is a phrase for the
    message, in the words the holder's verb uses — "being brought up", "being moved to ENABLED" — and
    it carries no promise: whether waiting on the other call is even the shape of the answer is
    :meth:`composat.Assembly.in_transit`'s reading, which says where the move is going.
    """

    key: ComponentKey
    moving: str

    def __init__(self, key: ComponentKey, moving: str) -> None:
        super().__init__(f"{key} is {moving} by another call")
        self.key = key
        self.moving = moving
