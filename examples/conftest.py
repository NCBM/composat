"""A case that loads a module from disk leaves names behind; each example keeps its own."""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _forget_new_modules() -> Iterator[None]:
    known = set(sys.modules)
    yield
    for name in [name for name in sys.modules if name not in known]:
        del sys.modules[name]
