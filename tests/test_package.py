"""The package imports; the rewrite has a floor to stand on.

One test, and it says one thing: ``composat`` is importable from ``src``. That is enough to keep
``pytest`` from reporting success over a tree it never read, and it is the only claim this branch
makes about the package while the package is empty.
"""

from __future__ import annotations

import composat


def test_the_package_imports() -> None:
    assert composat.__name__ == "composat"
