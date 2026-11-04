"""The README's Python blocks are compiled, so a snippet cannot go stale unnoticed.

A page that shows code to a reader is a claim about the code, and nothing else in the gate reads it.
Compiling each block catches the failure that matters most — a name that moved, a signature that
changed — without pretending the snippets are runnable on their own: several of them name variables
the reader is expected to have.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"

_PYTHON = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)


def python_blocks(text: str) -> list[str]:
    """Every fenced ``python`` block, in the order the page shows them."""
    return _PYTHON.findall(text)


def test_the_readme_still_shows_python() -> None:
    assert len(python_blocks(README.read_text(encoding="utf8"))) >= 4


def test_every_python_block_in_the_readme_compiles() -> None:
    for index, block in enumerate(python_blocks(README.read_text(encoding="utf8"))):
        # The page shows top-level ``await``, which parses only inside a coroutine: the block is
        # wrapped in one, and what is checked is what the page says rather than where it says it.
        wrapped = f"async def readme() -> None:\n{textwrap.indent(block, '    ')}"
        _ = ast.parse(wrapped, filename=f"README.md:python[{index}]")
