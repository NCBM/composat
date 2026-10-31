"""Who a component is: the identity a *type* declares, and the key that names one *instance*.

An identity (``module.qualname``) is the unit a class was written in; a key adds the qualifier that
tells one instance of that unit from another. The registry, the graph, and every lookup are indexed
by the key.

The identity is the class's own path, so a reload keeps the text and replaces the class object: the
unit is stable, this generation of it is replaceable. A class defined inside a function carries
``<locals>`` in its path — stable across generations, and reachable by neither the loader nor a
reload, which is worth documenting rather than refusing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, override


@dataclass(frozen=True, slots=True)
class ComponentIdentity:
    """Who a component *type* is: the module it is defined in, and its qualified name there.

    Both fields come from the class itself, which is the pair that survives a reload. Two providers
    of one path are compared for equality only; a deployment that needs to choose among them brings
    its own ordering.
    """

    module: str
    qualname: str

    def __post_init__(self) -> None:
        for label, value in (("module", self.module), ("qualname", self.qualname)):
            if not value:
                raise ValueError(f"a component identity needs a {label}")
            if ":" in value:
                raise ValueError(f"a component {label} must not contain ':': {value!r}")

    @classmethod
    def of(cls, component: type[object]) -> Self:
        """The identity ``component`` declares: where it was defined, and what it is called there."""
        return cls(component.__module__, component.__qualname__)

    @override
    def __str__(self) -> str:
        return f"{self.module}.{self.qualname}"


@dataclass(frozen=True, slots=True)
class ComponentKey:
    """One instance: an identity plus the qualifier that tells instances of it apart.

    ``instance`` is the declaring side's own discriminator — a tenant, a row id, whatever makes this
    instance *this* instance — and it is opaque here. Empty names the single instance of that
    identity, which is the common case for platform-wide components.

    The qualifier may itself contain ``':'``, which is what makes the string form parse unambiguously.
    """

    identity: ComponentIdentity
    instance: str = ""

    @override
    def __str__(self) -> str:
        return f"{self.identity}:{self.instance}" if self.instance else str(self.identity)

    @classmethod
    def parse(cls, text: str) -> Self:
        """The key ``text`` names; the qualifier is whatever follows the first ``':'``."""
        head, separator, instance = text.partition(":")
        module, _, qualname = head.rpartition(".")
        if not module or not qualname:
            raise ValueError(f"not a component key: {text!r}")
        return cls(ComponentIdentity(module, qualname), instance if separator else "")
