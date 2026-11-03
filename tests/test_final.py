"""Which classes an application may extend, and which it may not.

``@final`` is a promise made to a checker, not to the interpreter: a subclass is a type error and a
working class both. The rule is therefore checked here rather than left to review — a class this
package defines is closed unless it is named open, so a new one cannot be left unclassified.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from typing import cast

import pytest

import composat

#: Classes that stay open: what an application writes (``Component``), what it raises and catches
#: (the error hierarchy), what a subclass cannot extend (``Protocol``), what cannot be subclassed
#: anyway (``Enum``). A private name is open by its underscore.
OPEN = frozenset(
    {
        "Component",
        "ComponentState",
        "ComponentStateError",
        "DependencyCycleError",
        "DependencyError",
        "DependencyInUseError",
        "KeyInTransitError",
        "LifecycleReentryError",
        "NoCallContextError",
        "Operation",
        "Reclaimable",
        "RequirementResolver",
        "ServiceNotUpError",
        "UnboundRequirementError",
        "UnboundServiceError",
    }
)


def defined_here() -> list[type]:
    """Every class the package defines, as opposed to the ones it imports."""
    found: list[type] = []
    for info in pkgutil.walk_packages(composat.__path__, f"{composat.__name__}."):
        module = importlib.import_module(info.name)
        members = cast("Iterable[object]", vars(module).values())
        found += [member for member in members if isinstance(member, type) and member.__module__ == module.__name__]
    return found


def named_open(cls: type) -> bool:
    return cls.__qualname__.rsplit(".", 1)[-1].startswith("_") or cls.__name__ in OPEN


CLASSES = defined_here()


@pytest.mark.parametrize("cls", CLASSES, ids=[cls.__name__ for cls in CLASSES])
def test_a_class_is_closed_unless_it_is_named_open(cls: type) -> None:
    if named_open(cls):
        assert "__final__" not in cls.__dict__, f"{cls.__qualname__} is named open and carries @final"
    else:
        assert "__final__" in cls.__dict__, f"{cls.__qualname__} is neither @final nor named open"


def test_every_open_name_names_a_class() -> None:
    stale = OPEN - {cls.__name__ for cls in CLASSES}
    assert not stale, f"OPEN names no class: {sorted(stale)}"
