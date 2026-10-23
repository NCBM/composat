from __future__ import annotations

from collections.abc import Iterable
from functools import total_ordering
from typing import final, override

DOTTED = 3
"""How many numbers are dotted."""
MAX_NUMBERS = 4
"""The numbers a version may carry at most."""

type VersionParts = (
    tuple[int] | tuple[int, int] | tuple[int, int, int] | tuple[int, int, int, int] | tuple[int, int, int, int, str]
)
"""The tuple form of a version: one to four numbers, and a label only after the fourth."""

type VersionLike = Version | VersionParts | str
"""What a place that takes a version accepts: the value, its tuple, or its text."""


@total_ordering
@final
class Version:
    """A version, ordered the way releases are: numbers first, then the label."""

    __slots__ = ("_label", "_numbers", "_order")

    _numbers: tuple[int, ...]
    _label: str | None
    _order: tuple[tuple[int, ...], int, str]

    def __init__(self, *parts: int | str | VersionParts) -> None:
        written: Iterable[object] = parts[0] if len(parts) == 1 and isinstance(parts[0], tuple) else parts
        self._numbers, self._label = _split(written)
        padded = self._numbers + (0,) * (MAX_NUMBERS - len(self._numbers))
        self._order = (padded, 0 if self._label else 1, self._label or "")

    @classmethod
    def of(cls, value: VersionLike) -> Version:
        """Coerce whatever a call site was given: a ``Version``, its tuple form, or its text."""
        if isinstance(value, Version):
            return value
        if isinstance(value, str):
            return cls.parse(value)
        return cls(*value)

    @classmethod
    def parse(cls, text: str) -> Version:
        """The version ``text`` spells; only the canonical text parses, so a round trip is lossless."""
        head, *tail = text.split("-")
        parts: list[int | str] = [int(part) if part.isdecimal() else part for part in head.split(".")]
        parts.extend(int(part) if part.isdecimal() else part for part in tail)
        version = cls(*parts)
        if str(version) != text:
            raise ValueError(f"not a canonical version: {text!r} (did you mean {str(version)!r}?)")
        return version

    @property
    def numbers(self) -> tuple[int, ...]:
        return self._numbers

    @property
    def label(self) -> str | None:
        return self._label

    @override
    def __str__(self) -> str:
        core = ".".join(str(number) for number in self._numbers[:DOTTED])
        tail = [str(number) for number in self._numbers[DOTTED:]]
        if self._label is not None:
            tail.append(self._label)
        return "-".join([core, *tail])

    @override
    def __repr__(self) -> str:
        parts = [str(part) for part in (*self._numbers, *((self._label,) if self._label else ()))]
        return f"Version({', '.join(parts)})"

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._order == other._order

    @override
    def __hash__(self) -> int:
        return hash(self._order)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._order < other._order


def _split(parts: Iterable[object]) -> tuple[tuple[int, ...], str | None]:
    """``(numbers, label)`` from the parts as written, refusing anything off-shape."""
    numbers: list[int] = []
    label: str | None = None
    for part in parts:
        if isinstance(part, bool):
            raise TypeError(f"a version is numbers and at most one label, not {part!r}")
        if isinstance(part, str):
            if label is not None:
                raise ValueError("a version carries at most one label")
            if not part:
                raise ValueError("a version label must not be empty")
            if "-" in part:
                raise ValueError(f"a version label must not contain '-': {part!r}")
            label = part
        elif isinstance(part, int):
            if label is not None:
                raise ValueError("a version label comes last")
            if part < 0:
                raise ValueError(f"a version number must not be negative: {part!r}")
            if len(numbers) == MAX_NUMBERS:
                raise ValueError(f"a version has at most {MAX_NUMBERS} numbers: {part!r}")
            numbers.append(part)
        else:
            raise TypeError(f"a version is numbers and at most one label, not {part!r}")
    if not numbers:
        raise ValueError("a version needs at least one number")
    if label is not None and len(numbers) != MAX_NUMBERS:
        raise ValueError(f"a version label comes after {MAX_NUMBERS} numbers, not {len(numbers)}")
    return tuple(numbers), label
