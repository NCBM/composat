"""Errors the framework layer raises on its own behalf.

The errors whose semantics are settled live here as they land; the orphan error arrives with the
assembly layer's ``replace``. The state machine's own refusal — a verb driven from a state it does not
leave — lives with its tables, in :class:`composat.state.ComponentStateError`.
"""

from .identity import ComponentKey
from .state import ComponentState


class UnboundServiceError(RuntimeError):
    """A service type names no component, so the annotation can answer for nothing.

    A component may ask for a service by the interface it is written against instead of by the
    component class that provides it. Such an annotation names something once the registry has been
    told who provides it, and the declaration that asks for it is where an empty mapping is refused:
    answering with a default or dropping the annotation leaves a requirement missing with nobody the
    wiser.
    """

    service: type[object]

    def __init__(self, service: type[object]) -> None:
        super().__init__(f"no component is bound to {service!r}")
        self.service = service


class ServiceNotUpError(RuntimeError):
    """The instance a caller asked for is not one it may read: either absent or resting too low.

    A call is made for one key, and its services are looked up under that key: instances of one
    identity are told apart by the qualifier, so a caller served by the instance at ``tenant-a`` is
    answered from the provider at ``tenant-a`` as well. A miss there is the answer — another instance
    of the provider belongs to another key, and answering with it would be answering a question the
    caller did not ask.

    Three facts, because a deployment reads them together: the key that was missed, who asked, and —
    when the table does hold an instance there — the level it was resting at. "Nothing is up" and "it
    is up and not serving" are different incidents with different answers, one being a declaration to
    add and the other work still to finish; and the caller's own key is what says which tenant, or
    which generation, was being served when the miss happened.
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
    one, so reading the field is as plain as reading any attribute. The assembly makes that true,
    through ``composat.component.attach``, before ``load`` — an instance reading one of its own
    requirements in a hook reads something already bound.

    This error means the instance ran ahead of the binding: constructed and used by hand, or read from
    a hook of something that went through no assembly. It is raised only for a name the type
    declared, so probing an instance for anything else keeps Python's plain ``AttributeError``.
    """

    component: object
    field: str

    def __init__(self, component: object, field: str) -> None:
        super().__init__(f"{type(component).__name__}.{field} is a declared requirement, and nothing is attached to it")
        self.component = component
        self.field = field


class LifecycleReentryError(RuntimeError):
    """A component asked to be driven while it was already being driven.

    The verbs take the instance's lock for the whole move and are not reentrant, so a hook that drove
    its own component would wait on a lock it is holding. The guard refuses the ask where it is made
    instead of leaving a hung task behind, and what a component does instead of driving itself is
    *declare* what it needs: the assembly brings requirements up in order, and a component never has
    to.

    The guard follows the task that is running the hook, which catches the second half of the same
    mistake — a task a hook starts inherits it, so coming back later to drive the manager is refused
    as well — while an operation running in another task stays independent, as concurrent work on
    other components must.
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

    A batch speaks for the keys it names from the moment it accepts them until the last of them is
    done, and for the keys it had to derive along the way, because those are the same promise. A
    second call that names one of them is refused here rather than queued: two callers that disagree
    about whether a tenant should be serving would otherwise be settled by whichever happened to be
    scheduled second, and the one that is still holding the intent is the one that can say which it
    meant.

    ``moving`` is what the call that holds the key is doing, in the words its verb uses — "being
    brought up", "being taken down", "being moved to ENABLED", "being replaced" — because that is
    what says whether waiting on the other call is even the shape of the answer.
    """

    key: ComponentKey
    moving: str

    def __init__(self, key: ComponentKey, moving: str) -> None:
        super().__init__(f"{key} is {moving} by another call")
        self.key = key
        self.moving = moving
