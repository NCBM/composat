"""What the examples share: driving a coroutine, and a directory on the import path."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from pathlib import Path

import pytest


def run[T](coro: Coroutine[object, object, T], /) -> T:
    """Drive a coroutine to its answer, the way a script's ``asyncio.run(main())`` does."""
    return asyncio.run(coro)


def on_the_path(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Put ``root`` in front of ``sys.path`` for one case: where a loaded unit gets its name from."""
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])
