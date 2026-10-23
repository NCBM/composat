"""Who a component is: the identity a *type* declares, and the key that names one *instance*.

The two are kept apart on purpose. An identity (``name@version``) is what a component *is* — the
family it belongs to; a key (identity + qualifier) is what tells one instance of that family from
another. The registry, the graph, and every lookup are indexed by the key; the version is what lets
two implementations of one name coexist, ordered rather than merely compared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, override

from .versioning import Version, VersionLike


@dataclass(frozen=True, slots=True)
class ComponentIdentity:
    """Who a component *type* is: ``name@version``.

    The name is the type's own word for itself (``class WeatherTool(..., name="weather")``, or the
    class path by default); the version is what lets two implementations coexist and
    it is ordered, not just compared for equality — see :class:`~composat.versioning.Version`.
    ``'@'`` and ``':'`` are reserved for rendering the key, so both fields stay clear of them.
    """

    name: str
    version: VersionLike

    def __post_init__(self) -> None:
        for label, value in (("name", self.name), ("version", str(self.version))):
            if not value:
                raise ValueError(f"a component identity needs a {label}")
            if "@" in value or ":" in value:
                raise ValueError(f"a component {label} must not contain '@' or ':': {value!r}")
        if not isinstance(self.version, Version):
            object.__setattr__(self, "version", Version.of(self.version))

    @override
    def __str__(self) -> str:
        return f"{self.name}@{self.version}"

    @classmethod
    def parse(cls, text: str) -> Self:
        """The identity ``text`` names; ``ValueError`` when it is not one."""
        name, separator, version = text.partition("@")
        if not separator:
            raise ValueError(f"not a component identity: {text!r}")
        return cls(name, version)


@dataclass(frozen=True, slots=True)
class ComponentKey:
    """One instance: an identity plus the qualifier that tells instances of it apart.

    ``instance`` is the declaring side's own discriminator — a tenant, a row id, whatever makes this
    instance *this* instance — and it is opaque here. Empty means "the single instance of that
    identity", which is the common case for platform-wide components; it is also the slot a
    declaration marked ``shared`` sits in, and what makes that one instance the answer for every
    qualifier holding no declaration of its own.
    """

    identity: ComponentIdentity
    instance: str = ""

    @override
    def __str__(self) -> str:
        return f"{self.identity}:{self.instance}" if self.instance else str(self.identity)

    @classmethod
    def parse(cls, text: str) -> Self:
        """The key ``text`` names; the qualifier may itself contain ``':'``."""
        head, separator, instance = text.partition(":")
        return cls(ComponentIdentity.parse(head), instance if separator else "")
