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


@pytest.fixture
def packaged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A drop-in that is a package: its entry is one file, and the class it holds is two levels down."""
    root = tmp_path / "packaged"
    _ = shutil.copytree(HERE / "nested", root)
    on_the_path(monkeypatch, root)
    return root


def rewrite(path: Path, source: str) -> None:
    _ = path.write_text(source, encoding="utf-8")


_GENERATION = """
from __future__ import annotations

from typing import override

from composat import Component


class {name}(Component):
    version = {version}

    seen: list[str]

    def __init__(self, seen: list[str]) -> None:
        super().__init__()
        self.seen = seen

    @override
    async def load(self) -> None:
        self.seen.append(f"load {{self.version}}")

    @override
    async def unload(self) -> None:
        self.seen.append(f"unload {{self.version}}")
"""


def generation(name: str, version: int) -> str:
    """One generation of a drop-in, as the text an edit writes: what it is, and what it says up and down.

    Written out because an edit *is* text — the loader reads the file — and generated because a burst
    of edits differs only in the version. Its hooks report into a list the caller owns, which is how a
    case counts generations without reading anything the framework keeps.
    """
    return _GENERATION.format(name=name, version=version)


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


def test_a_packaged_dropin_is_one_component_however_many_names_its_entry_gives_it(packaged: Path) -> None:
    async def main() -> None:
        seen: list[str] = []
        loader = UnitLoader(packaged)
        await loader.load()
        unit = loader.unit("plug")
        assert unit is not None
        registry = ComponentRegistry()
        assembly = Assembly(registry, up={})

        declarations = [Declaration(cls, args=(seen,)) for cls in unit.components]
        _ = registry.declare_all(declarations)
        _ = await assembly.start(*[declaration.key for declaration in declarations])

        # One class however many names the entry gives it: one declaration, one key, one instance.
        held = assembly.keys()
        assert [str(key.identity) for key in held] == ["plug.component.Gear"]
        assert seen == ["load 1"]

        # An edit below the entry is an edit to the unit, and the entry's own re-import is what carries
        # it: what answers afterwards is the class the submodule holds now, and the old one let go.
        rewrite(packaged / "plug/component.py", generation("Gear", 2))
        assert loader.stale() == ("plug",)
        result = await rebuild(loader, assembly, "plug")

        assert [key.identity.qualname for key in result.swap.swapped] == ["Gear"]
        assert seen == ["load 1", "load 2", "unload 1"]
        held = assembly.keys()
        assert [str(key.identity) for key in held] == ["plug.component.Gear"]

    run(main())


def test_a_burst_of_edits_leaves_one_instance_and_no_generation_behind(plugins: Path) -> None:
    async def main() -> None:
        seen: list[str] = []
        rewrite(plugins / "widget.py", generation("Widget", 1))
        loader = UnitLoader(plugins)
        await loader.load()
        registry = ComponentRegistry()
        assembly = Assembly(registry, up={})

        declarations = [Declaration(cls, args=(seen,)) for cls in declared(loader, "widget").values()]
        _ = registry.declare_all(declarations)
        _ = await assembly.start(*[declaration.key for declaration in declarations])

        for version in range(2, 21):
            rewrite(plugins / "widget.py", generation("Widget", version))
            assert loader.stale() == ("widget",)
            result = await rebuild(loader, assembly, "widget")
            assert [key.identity.qualname for key in result.swap.swapped] == ["Widget"]
            assert len(assembly.keys()) == 1

        assert loader.stale() == ()
        expected = ["load 1"]
        for version in range(2, 21):
            expected += [f"load {version}", f"unload {version - 1}"]
        assert seen == expected

    run(main())
