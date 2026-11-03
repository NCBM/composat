"""Reloading a loaded unit: running its source again, moving the name, and re-pointing what bound it.

The cases here write modules to disk and then rewrite them, because that is the only way to ask the
question the verb exists for — the code on disk is a different generation from the code that is
running, and the answer is what the loader does about it.

What a loaded class was is read through ``requirements()`` where a case needs to tell one generation's
text from another's: it is the one readable account of a class that survives being written in a string.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from collections.abc import Coroutine, Iterator
from pathlib import Path
from typing import cast

import pytest

from composat import (
    Assembly,
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Declaration,
    Unit,
    requirements_of,
)
from composat.std.pluggable import UnitLoader, reload_module


def run[T](coro: Coroutine[object, object, T], /) -> T:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _forget_new_modules() -> Iterator[None]:
    """Take every module a case imported back out of ``sys.modules``, so one case's names stay its own."""
    known = set(sys.modules)
    yield
    for name in [name for name in sys.modules if name not in known]:
        del sys.modules[name]


@pytest.fixture
def dropins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty directory on ``sys.path``: where the modules a case writes get their names from."""
    on_the_path(monkeypatch, tmp_path)
    return tmp_path


def on_the_path(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Put ``root`` in front of ``sys.path``, where the loader will find it, for this case only."""
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])


def module_at(directory: Path, relative: str, source: str) -> Path:
    """Write ``source`` to ``relative`` under ``directory``, rewriting it if it is already there."""
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def loaded(root: Path) -> UnitLoader:
    """A loader component that has run its ``load`` over ``root``."""
    loader = UnitLoader(root)
    run(loader.load())
    return loader


_ALPHA = """
from composat import Component

value = 1

class Alpha(Component):
    pass
"""

_BETA = """
from composat import Component

from alpha import value

class Beta(Component):
    pass
"""

_ALPHA_EDITED = """
from composat import Component

value = 2

class Alpha(Component):
    pass
"""

_ALPHA_CONSTRUCTED = """
from composat import Component

class Alpha(Component):
    def __init__(self, dsn: str) -> None:
        super().__init__()
        self.dsn = dsn
"""

_ALPHA_EDITED_CONSTRUCTED = """
from composat import Component

value = 2

class Alpha(Component):
    def __init__(self, dsn: str) -> None:
        super().__init__()
        self.dsn = dsn
"""

_TWO_ALPHAS = """
from composat import Component

class Alpha(Component):
    pass

class Gamma(Component):
    pass
"""


def value_in(module_name: str) -> object:
    """What ``module_name`` binds to ``value``: a case cannot name a module it wrote as a string.

    Cast because a module's namespace is ``dict[str, Any]`` — the name exists only in the source a case
    just wrote, and ``object`` is enough to compare against the number that source said.
    """
    return cast("object", sys.modules[module_name].__dict__.get("value"))


def requirements_in(components: tuple[type[Component], ...]) -> int:
    """How many requirements the components of one unit declare, together.

    ``requirements()`` is the typed account of what a class asks for, so it is how a case tells one
    generation's source from another's without naming the class it wrote as a string.
    """
    return sum(len(requirements_of(component)) for component in components)


def only_component(loader: UnitLoader, name: str) -> type[Component]:
    """The one component the unit ``name`` holds, which is the class a swap moves a key onto."""
    unit = loader.unit(name)
    assert isinstance(unit, Unit)
    assert len(unit.components) == 1
    return unit.components[0]


def at(component: type[Component], instance: str = "") -> ComponentKey:
    """The key ``component`` answers at under ``instance``."""
    return ComponentKey(ComponentIdentity.of(component), instance)


def wired(*declarations: Declaration) -> Assembly:
    """An assembly holding ``declarations``, which is what a swap needs to find keys in."""
    registry = ComponentRegistry()
    _ = registry.declare_all(declarations)
    return Assembly(registry)


def kept(instance: Component) -> object:
    """What ``instance`` was constructed with: a case cannot name an attribute a string declared."""
    return cast("object", instance.__dict__.get("dsn"))


class TestWhatTheLoaderHolds:
    def test_the_root_is_loaded_one_unit_per_module(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        _ = module_at(
            dropins, "beta/__init__.py", "from composat import Component\n\nclass Beta(Component):\n    pass\n"
        )

        loader = loaded(dropins)

        assert [unit.module.__name__ for unit in loader.units()] == ["alpha", "beta"]
        assert loader.scope() == {"alpha", "beta"}
        assert loader.unit("beta") is not None
        assert loader.unit("gamma") is None

    def test_unload_forgets_them(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)

        run(loader.unload())

        assert loader.units() == ()
        assert loader.scope() == frozenset()


class TestReloading:
    def test_the_new_source_runs_and_the_name_answers_it(self, dropins: Path) -> None:
        path = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)
        before = loader.unit("alpha")
        assert before is not None

        _ = path.write_text(
            textwrap.dedent(
                """
                from composat import Component, require

                value = 2

                class Store(Component):
                    pass

                class Alpha(Component):
                    store: Store = require()
                """
            ).lstrip(),
            encoding="utf-8",
        )
        result = loader.reload_module("alpha")

        assert result.unit.module is sys.modules["alpha"]
        assert result.unit.module is not before.module
        assert loader.unit("alpha") is result.unit
        assert result.requirements_changed == (ComponentIdentity("alpha", "Alpha"),)
        assert requirements_in(before.components) == 0
        assert requirements_in(result.unit.components) == 1

    def test_the_old_generation_keeps_what_it_was_built_with(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)
        before = loader.unit("alpha")
        assert before is not None

        _ = module_at(dropins, "alpha.py", "from composat import Component\n\nclass Alpha(Component):\n    pass\n")
        after = loader.reload_module("alpha").unit

        assert after.components[0] is not before.components[0]
        assert ComponentIdentity.of(after.components[0]) == ComponentIdentity.of(before.components[0])

    def test_an_importer_in_the_scope_is_repointed(self, dropins: Path) -> None:
        alpha = module_at(dropins, "alpha.py", _ALPHA)
        _ = module_at(dropins, "beta.py", _BETA)
        loader = loaded(dropins)
        assert value_in("beta") == 1

        _ = alpha.write_text(
            "from composat import Component\n\nvalue = 2\n\nclass Alpha(Component):\n    pass\n",
            encoding="utf-8",
        )
        result = loader.reload_module("alpha")

        assert result.rewritten == ("beta",)
        assert value_in("beta") == 2

    def test_an_importer_outside_the_scope_is_left_alone(self, dropins: Path, tmp_path: Path) -> None:
        alpha = module_at(dropins, "alpha.py", _ALPHA)
        elsewhere = tmp_path / "elsewhere"
        _ = module_at(elsewhere, "outsider.py", "from alpha import value\n")
        loader = loaded(dropins)
        outsider = UnitLoader(elsewhere)
        run(outsider.load())

        _ = alpha.write_text(
            "from composat import Component\n\nvalue = 2\n\nclass Alpha(Component):\n    pass\n",
            encoding="utf-8",
        )
        _ = loader.reload_module("alpha")

        assert value_in("elsewhere.outsider") == 1

    def test_a_reload_that_raises_leaves_the_old_generation_answering(self, dropins: Path) -> None:
        path = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)
        before = loader.unit("alpha")
        assert before is not None

        _ = path.write_text('raise RuntimeError("bang")\n', encoding="utf-8")

        with pytest.raises(RuntimeError, match="bang"):
            _ = loader.reload_module("alpha")
        assert sys.modules["alpha"] is before.module
        assert loader.unit("alpha") is before


class TestSwappingInstances:
    def test_what_it_is_built_with_survives_the_swap(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA_CONSTRUCTED)
        loader = loaded(dropins)
        alpha = only_component(loader, "alpha")
        key = at(alpha)
        assembly = wired(Declaration(alpha, kwargs={"dsn": "memory"}))
        _ = run(assembly.start(key))

        _ = module_at(dropins, "alpha.py", _ALPHA_EDITED_CONSTRUCTED)
        _ = loader.reload_module("alpha")
        _ = run(loader.swap_instances(assembly, "alpha"))

        replaced = assembly.instance(key)
        assert replaced is not None
        assert kept(replaced) == "memory"

    def test_a_class_the_new_generation_dropped_is_reported(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _TWO_ALPHAS)
        loader = loaded(dropins)
        unit = loader.unit("alpha")
        assert unit is not None
        alpha, gamma = unit.components
        assembly = wired(Declaration(alpha), Declaration(gamma))
        _ = run(assembly.start(at(alpha), at(gamma)))

        _ = module_at(dropins, "alpha.py", _ALPHA_EDITED)
        _ = loader.reload_module("alpha")
        result = run(loader.swap_instances(assembly, "alpha"))

        assert result.unmatched == (at(gamma),)
        assert result.swapped == (at(alpha),)
        assert type(assembly.instance(at(gamma))) is gamma

    def test_a_key_of_another_unit_is_left_alone(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        _ = module_at(dropins, "zeta.py", "from composat import Component\n\nclass Zeta(Component):\n    pass\n")
        loader = loaded(dropins)
        alpha = only_component(loader, "alpha")
        zeta = only_component(loader, "zeta")
        assembly = wired(Declaration(alpha), Declaration(zeta))
        _ = run(assembly.start(at(alpha), at(zeta)))
        held = assembly.instance(at(zeta))

        _ = module_at(dropins, "alpha.py", _ALPHA_EDITED)
        _ = loader.reload_module("alpha")
        result = run(loader.swap_instances(assembly, "alpha"))

        assert result.swapped == (at(alpha),)
        assert assembly.instance(at(zeta)) is held

    def test_a_key_already_on_the_current_generation_is_passed_over(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)
        alpha = only_component(loader, "alpha")
        key = at(alpha)
        assembly = wired(Declaration(alpha))
        _ = run(assembly.start(key))

        _ = module_at(dropins, "alpha.py", _ALPHA_EDITED)
        _ = loader.reload_module("alpha")
        _ = run(loader.swap_instances(assembly, "alpha"))
        held = assembly.instance(key)
        again = run(loader.swap_instances(assembly, "alpha"))

        assert again.swapped == ()
        assert assembly.instance(key) is held

    def test_a_generation_the_registry_will_not_take_is_that_keys_failure(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        _ = module_at(dropins, "store.py", "from composat import Component\n\nclass Store(Component):\n    pass\n")
        loader = loaded(dropins)
        alpha = only_component(loader, "alpha")
        store = only_component(loader, "store")
        key = at(alpha)
        assembly = wired(Declaration(alpha), Declaration(store))
        _ = run(assembly.start(key, at(store)))

        _ = module_at(
            dropins,
            "alpha.py",
            textwrap.dedent(
                """
                from composat import Component, require

                from store import Store

                class Alpha(Component):
                    store: Store = require()
                """
            ).lstrip(),
        )
        _ = loader.reload_module("alpha")
        result = run(loader.swap_instances(assembly, "alpha"))

        assert list(result.failed) == [key]
        assert type(assembly.instance(key)) is alpha

    def test_a_name_this_loader_does_not_hold_is_refused(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)

        with pytest.raises(ValueError, match="is not one of the modules"):
            _ = run(loader.swap_instances(wired(), "gamma"))


class TestAsADeclaredComponent:
    def test_it_is_declared_like_any_other_and_loads_its_root(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        registry = ComponentRegistry()
        _ = registry.declare_all((Declaration(UnitLoader, args=(dropins,)),))
        assembly = Assembly(registry)
        key = ComponentKey(ComponentIdentity.of(UnitLoader))

        result = run(assembly.start(key))
        loader = assembly.instance(key)

        assert result.ok
        assert isinstance(loader, UnitLoader)
        assert loader.scope() == {"alpha"}
        assert run(assembly.stop(*result.completed)).ok


class TestStaleness:
    def test_a_submodule_with_no_component_is_out_of_its_reach(self, dropins: Path) -> None:
        _ = module_at(dropins, "pkg/__init__.py", "from .inner import Inner\n")
        _ = module_at(dropins, "pkg/inner.py", "from composat import Component\n\nclass Inner(Component):\n    pass\n")
        helpers = module_at(dropins, "pkg/helpers.py", "def shout(text: str) -> str:\n    return text.upper()\n")
        loader = loaded(dropins)

        _ = helpers.write_text("def shout(text: str) -> str:\n    return text.upper() + '!'\n", encoding="utf-8")

        assert loader.stale() == ()

    def test_a_file_that_is_gone_counts_as_stale(self, dropins: Path) -> None:
        path = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)

        path.unlink()

        assert loader.stale() == ("alpha",)


class TestRefusals:
    def test_a_name_this_loader_does_not_hold_is_refused(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", _ALPHA)
        loader = loaded(dropins)

        with pytest.raises(ValueError, match="is not one of the modules"):
            _ = loader.reload_module("gamma")

    def test_a_module_no_name_holds_is_refused(self) -> None:
        with pytest.raises(ValueError, match="is loaded, so there is nothing to reload"):
            _ = reload_module("no_such_module_anywhere", scope=())

    def test_a_module_this_component_did_not_load_has_no_generation(self) -> None:
        with pytest.raises(ValueError, match="was not loaded by composat"):
            _ = reload_module("json", scope=())
