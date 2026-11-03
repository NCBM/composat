"""What a component type declares about one requirement, as plain data.

The record is what a class hands out once it has read the fields its body declared: the registry
draws its edges from these, the assembly orders by them, and a field read resolves through the
descriptor installed from the same reading. A frozen record keeps :func:`composat.component.
requirements_of` a pure query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class RequirementSpec:
    """One declared requirement: the field it is read as, the contract it asks for, its extras.

    ``service`` is a component type or a ``Protocol``, the two spellings of a contract. ``extras`` is
    everything else the annotation said — an ``Annotated`` layer's metadata, which a resolver is free
    to read and this record only carries — so a requirement annotated with nothing beyond its service
    is answered the same way as one declared bare.
    """

    name: str
    service: type[object]
    extras: tuple[object, ...] = ()
