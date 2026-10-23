"""Errors the framework layer raises on its own behalf.

Only the three whose semantics are settled live here so far; the lifecycle and orphan errors arrive
with the component and assembly layers they belong to.
"""

from .identity import ComponentKey


class UnboundServiceError(RuntimeError):
    """A service type names no component, so the annotation can answer for nothing.

    A component may ask for a service by the interface it is written against instead of by the
    component class that provides it. Such an annotation names something only once the registry has
    been told who provides it. With the mapping empty the annotation names nothing at all, and that
    is refused where it is written down — the declaration that asks for it — because answering with
    a default or dropping the annotation quietly leaves a requirement missing with nobody the wiser.
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

    One instance answers from further than its own qualifier, and this error is not raised for it:
    the deployment may declare the identity's single instance ``shared``, and then it is what every
    caller without an instance of its own is answered with. That is a fact about the declaration
    rather than about the lookup — settled before the caller asks — which is why it is the only
    direction this refusal opens in, and why the key below is always the one the caller asked about.
    """

    key: ComponentKey

    def __init__(self, key: ComponentKey) -> None:
        super().__init__(f"nothing is up under {key}")
        self.key = key


class NoCallContextError(RuntimeError):
    """The call level was asked from outside any call.

    ``ContextVar.get()`` answers a missing binding with ``LookupError``, and a call that is absent
    is a fact about where the code is running rather than a key that was missed. Turning it into
    this gives a caller an error it can read at a glance: ``LookupError`` would leave it guessing
    between "the code is outside the call level" and "the dictionary lacked the key".
    """

    def __init__(self) -> None:
        super().__init__("no call is in progress: there is nothing to resolve or register against")
