"""A running plugin: an edit becomes a new generation of both the code and the instance behind its key."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from composat import Assembly, ComponentRegistry, Declaration
from composat.std.pluggable import UnitLoader
from examples.reload.app import declared, rebuild
from examples.support import on_the_path, run

HERE = Path(__file__).parent


@pytest.fixture
def plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the example's plugin on the import path: a case may edit it without touching the tree."""
    root = tmp_path / "plugins"
    _ = shutil.copytree(HERE / "plugin", root)
    on_the_path(monkeypatch, root)
    return root


def rewrite(path: Path, source: str) -> None:
    _ = path.write_text(source, encoding="utf-8")


WIDGET_V2 = """
from composat import Component


class Widget(Component):
    version = 2


class Gadget(Component):
    version = 1
"""

WIDGET_V3 = """
from composat import Component


class Widget(Component):
    version = 3
"""


def test_an_edit_reaches_the_code_and_the_instance(plugins: Path) -> None:
    async def main() -> None:
        loader = UnitLoader(plugins)
        await loader.load()
        registry = ComponentRegistry()
        assembly = Assembly(registry, up={})
        _ = registry.declare_all(Declaration(cls) for cls in declared(loader, "widget").values())
        _ = await assembly.start(*[Declaration(cls).key for cls in declared(loader, "widget").values()])

        key = next(iter(assembly.keys()))
        before = assembly.instance(key)
        assert before is not None
        old_class = type(before)
        assert loader.stale() == ()

        rewrite(plugins / "widget.py", WIDGET_V2)
        assert loader.stale() == ("widget",)

        result = await rebuild(loader, assembly, "widget")

        after = assembly.instance(key)
        assert after is not None
        assert type(after) is not old_class
        assert type(after).__module__ == old_class.__module__
        assert loader.stale() == ()
        assert [key.identity.qualname for key in result.swap.swapped] == [old_class.__qualname__]

    run(main())


def test_an_edit_that_does_not_compile_leaves_the_running_generation(plugins: Path) -> None:
    async def main() -> None:
        loader = UnitLoader(plugins)
        await loader.load()
        registry = ComponentRegistry()
        assembly = Assembly(registry, up={})
        _ = registry.declare_all(Declaration(cls) for cls in declared(loader, "widget").values())
        _ = await assembly.start(*[Declaration(cls).key for cls in declared(loader, "widget").values()])
        key = next(iter(assembly.keys()))
        before = assembly.instance(key)
        assert before is not None
        old_class = type(before)

        rewrite(plugins / "widget.py", "class Widget(:\n")
        with pytest.raises(SyntaxError):
            _ = loader.reload_module("widget")

        # The generation that runs is the one that compiled: a module that would not import is a
        # change that has not happened yet.
        still = assembly.instance(key)
        assert still is before
        assert type(still) is old_class

        rewrite(plugins / "widget.py", WIDGET_V2)
        result = await rebuild(loader, assembly, "widget")

        assert type(assembly.instance(key)) is not old_class
        assert [key.identity.qualname for key in result.swap.swapped] == [old_class.__qualname__]

    run(main())

def test_a_class_the_edit_added_is_declared_and_a_removed_one_withdrawn(plugins: Path) -> None:
    async def main() -> None:
        loader = UnitLoader(plugins)
        await loader.load()
        registry = ComponentRegistry()
        assembly = Assembly(registry, up={})
        _ = registry.declare_all(Declaration(cls) for cls in declared(loader, "widget").values())
        _ = await assembly.start(*[Declaration(cls).key for cls in declared(loader, "widget").values()])

        rewrite(plugins / "widget.py", WIDGET_V2)
        added = await rebuild(loader, assembly, "widget")
        assert [key.identity.qualname for key in added.appeared.completed] == ["Gadget"]

        rewrite(plugins / "widget.py", WIDGET_V3)
        removed = await rebuild(loader, assembly, "widget")
        assert [key.identity.qualname for key in removed.withdrawn.completed] == ["Gadget"]
        held = assembly.keys()
        assert {key.identity.qualname for key in held} == {"Widget"}

    run(main())
