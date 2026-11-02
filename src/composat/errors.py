"""Errors the framework layer raises on its own behalf.

The errors whose semantics are settled live here as they land; the lifecycle and orphan errors
arrive with the manager and assembly layers.
"""

from .identity import ComponentKey


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
    """The component that provides a service has no instance up for the caller.

    A call is made for one key, and its services are looked up under that key: instances of one
    identity are told apart by the qualifier, so a caller served by the instance at ``tenant-a`` is
    answered from the provider at ``tenant-a`` as well. A miss there is the answer — another
    instance of the provider belongs to another key, and answering with it would be answering a
    question the caller did not ask.
    """

    key: ComponentKey

    def __init__(self, key: ComponentKey) -> None:
        super().__init__(f"nothing is up under {key}")
        self.key = key


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

    A field annotation is a *requirement*: the type says the instance will be handed one, so reading
    the field is as plain as reading any attribute. The assembly makes that true, through
    ``composat.component.attach``, before ``load`` — an instance reading one of its own requirements
    in a hook reads something already bound.

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
