"""Loading components from a path: the module there, what it exports, and a fingerprint of its source.

Every case writes its modules to disk, because the unit of the loader is a file: the name comes from
where the file sits relative to a ``sys.path`` root, and the fingerprint from what the file says. The
modules a case writes are keyed by name in one process-wide ``sys.modules``, so they are taken back
out again afterwards — otherwise two cases writing ``alpha.py`` would see each other's module, which
is the loader doing its job and a test reading the wrong one.
"""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from composat import (
    ComponentIdentity,
    LoadedUnit,
    fingerprint_of,
    load_unit,
    load_units,
    loaded_unit,
)


@pytest.fixture(autouse=True)
def _forget_new_modules() -> Iterator[None]:
    """Take every module a case imported back out of ``sys.modules``, so one case's names stay its own."""
    known = set(sys.modules)
    yield
    for name in [name for name in sys.modules if name not in known]:
        del sys.modules[name]


def on_the_path(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """Put ``root`` in front of ``sys.path``, where the loader will find it, for this case only."""
    monkeypatch.setattr(sys, "path", [str(root), *sys.path])


@pytest.fixture
def dropins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty directory on ``sys.path``: where the modules a case writes get their names from."""
    on_the_path(monkeypatch, tmp_path)
    return tmp_path


def module_at(directory: Path, relative: str, source: str) -> Path:
    """Write ``source`` to ``relative`` under ``directory``: a module file, or a package's entry."""
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


class TestOneModule:
    def test_the_name_is_derived_from_the_path_under_a_syspath_root(self, dropins: Path) -> None:
        _ = module_at(
            dropins,
            "alpha.py",
            """
            from composat import Component

            class Alpha(Component):
                pass
            """,
        )

        unit = load_unit(dropins / "alpha.py")

        assert unit.module.__name__ == "alpha"
        assert unit.path == (dropins / "alpha.py").resolve()
        assert [ComponentIdentity.of(cls) for cls in unit.components] == [ComponentIdentity("alpha", "Alpha")]

    def test_the_module_is_registered_while_it_runs(self, dropins: Path) -> None:
        _ = module_at(
            dropins,
            "beta.py",
            """
            import sys

            from composat import Component

            class Beta(Component):
                seen = sys.modules["beta"].__name__
            """,
        )

        unit = load_unit(dropins / "beta.py")

        assert unit.module.Beta.seen == "beta"  # pyright: ignore[reportAny]

    def test_a_package_is_entered_through_its_init(self, dropins: Path) -> None:
        _ = module_at(dropins, "gamma/inner.py", "value = 1\n")
        _ = module_at(
            dropins,
            "gamma/__init__.py",
            """
            from composat import Component

            class Gamma(Component):
                pass
            """,
        )

        unit = load_unit(dropins / "gamma")

        assert unit.module.__name__ == "gamma"
        assert unit.path == (dropins / "gamma/__init__.py").resolve()
        assert [str(ComponentIdentity.of(cls)) for cls in unit.components] == ["gamma.Gamma"]

    def test_a_package_may_reexport_a_component_from_a_submodule(self, dropins: Path) -> None:
        _ = module_at(dropins, "delta/__init__.py", "from .inner import Nested\n")
        _ = module_at(
            dropins,
            "delta/inner.py",
            """
            from composat import Component

            class Nested(Component):
                pass
            """,
        )

        unit = load_unit(dropins / "delta")

        assert [str(ComponentIdentity.of(cls)) for cls in unit.components] == ["delta.inner.Nested"]

    def test_every_component_the_module_holds_is_collected_in_order(self, dropins: Path) -> None:
        _ = module_at(
            dropins,
            "pair.py",
            """
            from composat import Component

            class First(Component):
                pass

            class Second(Component):
                pass
            """,
        )

        unit = load_unit(dropins / "pair.py")

        assert [cls.__name__ for cls in unit.components] == ["First", "Second"]

    def test_a_component_imported_from_elsewhere_is_not_this_units(self, dropins: Path) -> None:
        _ = module_at(
            dropins,
            "donor.py",
            """
            from composat import Component

            class Donor(Component):
                pass
            """,
        )
        _ = module_at(
            dropins,
            "taker.py",
            """
            from composat import Component

            from donor import Donor

            class Own(Component):
                pass
            """,
        )

        unit = load_unit(dropins / "taker.py")

        assert [cls.__name__ for cls in unit.components] == ["Own"]

    def test_a_module_of_helpers_holds_no_component(self, dropins: Path) -> None:
        _ = module_at(dropins, "helpers.py", "def shout(text: str) -> str:\n    return text.upper()\n")

        assert load_unit(dropins / "helpers.py").components == ()


class TestLoadingTheSameModuleTwice:
    def test_the_module_is_executed_once_and_handed_back(self, dropins: Path) -> None:
        _ = module_at(
            dropins,
            "talkative.py",
            """
            from pathlib import Path

            from composat import Component

            _trace = Path(__file__).with_name("trace.txt")
            _trace.write_text(_trace.read_text() + "ran\\n" if _trace.exists() else "ran\\n")


            class Talkative(Component):
                pass
            """,
        )

        first = load_unit(dropins / "talkative.py")
        second = load_unit(dropins / "talkative.py")

        assert second.module is first.module
        assert sys.modules["talkative"] is first.module
        assert (dropins / "trace.txt").read_text(encoding="utf-8") == "ran\n"

    def test_a_name_another_module_holds_is_refused_rather_than_shadowed(self, dropins: Path) -> None:
        _ = module_at(dropins, "json.py", "value = 1\n")

        with pytest.raises(ValueError, match="which this loader did not load"):
            _ = load_unit(dropins / "json.py")
        assert sys.modules["json"] is json

    def test_two_files_deriving_one_name_are_refused(self, dropins: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        first = module_at(dropins, "same.py", "value = 1\n")
        nested = module_at(dropins, "nest/same.py", "value = 1\n")
        _ = load_unit(first)
        on_the_path(monkeypatch, dropins / "nest")

        with pytest.raises(ValueError, match="derives the same name"):
            _ = load_unit(nested)


class TestLoadingADirectory:
    def test_every_module_at_the_level_is_loaded_in_path_order(self, dropins: Path) -> None:
        _ = module_at(dropins, "b/__init__.py", "value = 1\n")
        _ = module_at(dropins, "a.py", "value = 1\n")
        _ = module_at(dropins, "__init__.py", "value = 1\n")
        _ = module_at(dropins, "notes.txt", "not a module\n")
        _ = module_at(dropins, "deep/keep.py", "value = 1\n")

        units = load_units(dropins)

        assert [unit.module.__name__ for unit in units] == ["a", "b"]

    def test_a_missing_directory_is_refused(self, dropins: Path) -> None:
        with pytest.raises(FileNotFoundError, match="no directory of modules"):
            _ = load_units(dropins / "absent")


class TestWhatTheSourceSaysNow:
    def test_the_fingerprint_is_the_syntax_alone(self, dropins: Path) -> None:
        path = module_at(dropins, "notes.py", 'value = "one"\n')
        first = fingerprint_of(path)

        _ = path.write_text("# a comment\n\nvalue = 'one'\n", encoding="utf-8")
        assert fingerprint_of(path) == first

        _ = path.write_text('value = "two"\n', encoding="utf-8")
        assert fingerprint_of(path) != first

    def test_a_directory_folds_into_its_entry(self, dropins: Path) -> None:
        _ = module_at(dropins, "pkg/__init__.py", "value = 1\n")

        assert fingerprint_of(dropins / "pkg") == fingerprint_of(dropins / "pkg/__init__.py")

    def test_a_loaded_module_carries_what_it_was(self, dropins: Path) -> None:
        _ = module_at(dropins, "alpha.py", "value = 1\n")

        unit = load_unit(dropins / "alpha.py")
        record = loaded_unit(unit.module)

        assert isinstance(record, LoadedUnit)
        assert record == LoadedUnit(unit.path, unit.fingerprint)
        assert not record.is_stale()

    def test_an_edit_is_reported_and_a_second_load_still_hands_back_what_runs(self, dropins: Path) -> None:
        path = module_at(dropins, "alpha.py", "value = 1\n")
        first = load_unit(path)

        _ = path.write_text("value = 2\n", encoding="utf-8")
        record = loaded_unit(first.module)

        assert isinstance(record, LoadedUnit)
        assert record.is_stale()
        again = load_unit(path)
        assert again.module is first.module
        assert again.fingerprint == first.fingerprint

    def test_a_deleted_module_makes_staleness_raise(self, dropins: Path) -> None:
        path = module_at(dropins, "alpha.py", "value = 1\n")
        record = loaded_unit(load_unit(path).module)
        assert isinstance(record, LoadedUnit)

        path.unlink()

        with pytest.raises(FileNotFoundError):
            _ = record.is_stale()

    def test_a_module_this_loader_did_not_load_has_no_record(self) -> None:
        assert loaded_unit(json) is None


class TestRefusals:
    def test_a_path_outside_every_syspath_root_is_refused(self, tmp_path: Path) -> None:
        path = module_at(tmp_path, "stray.py", "value = 1\n")

        with pytest.raises(ValueError, match=r"not inside any sys\.path entry"):
            _ = load_unit(path)

    def test_a_name_no_import_could_write_is_refused(self, dropins: Path) -> None:
        path = module_at(dropins, "alpha-v2.py", "value = 1\n")

        with pytest.raises(ValueError, match="not a name a module can be imported as"):
            _ = load_unit(path)

    def test_a_missing_file_is_refused(self, dropins: Path) -> None:
        with pytest.raises(FileNotFoundError, match="no module at"):
            _ = load_unit(dropins / "absent.py")

    def test_a_directory_with_no_entry_is_not_a_module(self, dropins: Path) -> None:
        (dropins / "empty").mkdir()

        with pytest.raises(FileNotFoundError, match=r"no __init__\.py"):
            _ = load_unit(dropins / "empty")

    def test_a_module_that_raises_is_passed_through_and_taken_back_out(self, dropins: Path) -> None:
        path = module_at(dropins, "boom.py", 'raise RuntimeError("bang")\n')

        with pytest.raises(RuntimeError, match="bang"):
            _ = load_unit(path)
        assert "boom" not in sys.modules
