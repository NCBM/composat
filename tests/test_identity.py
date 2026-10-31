"""The identity a component type declares, and the key that names one instance.

The identity is the class's own path: a reload keeps the text and replaces the class, so the path is
what a registry can hold across generations. Nothing here is ordered — a deployment that needs to
choose among providers does it with its own vocabulary.
"""

from __future__ import annotations

import pytest

from composat import ComponentIdentity, ComponentKey


class Weather:
    """Stand-in for a component: an identity is read off any class, not only a component."""


class Engine:
    pass


def _defined_inside_a_function() -> type[object]:
    class Local:
        pass

    return Local


class TestTheIdentityOfAType:
    def test_it_is_the_module_and_the_qualified_name(self) -> None:
        assert ComponentIdentity.of(Weather) == ComponentIdentity(__name__, "Weather")

    def test_two_classes_in_one_module_do_not_share_it(self) -> None:
        assert ComponentIdentity.of(Engine) != ComponentIdentity.of(Weather)

    def test_a_class_defined_inside_a_function_keeps_a_stable_text(self) -> None:
        local = _defined_inside_a_function()
        identity = ComponentIdentity.of(local)
        assert identity.qualname.endswith("<locals>.Local")
        assert identity == ComponentIdentity.of(local)

    def test_the_text_form_is_the_dotted_path(self) -> None:
        assert str(ComponentIdentity.of(Weather)) == f"{__name__}.Weather"


class TestTheKey:
    def test_an_empty_qualifier_renders_as_the_identity_alone(self) -> None:
        assert str(ComponentKey(ComponentIdentity.of(Weather))) == str(ComponentIdentity.of(Weather))

    def test_a_qualifier_renders_after_the_identity_and_may_hold_a_colon(self) -> None:
        key = ComponentKey(ComponentIdentity.of(Weather), "tenant-a:7")
        assert str(key) == f"{ComponentIdentity.of(Weather)}:tenant-a:7"

    def test_parsing_is_the_inverse_of_rendering(self) -> None:
        identity = ComponentIdentity.of(Weather)
        for key in (ComponentKey(identity), ComponentKey(identity, "a:7")):
            assert ComponentKey.parse(str(key)) == key


class TestRefusals:
    @pytest.mark.parametrize(("module", "qualname"), [("", "Weather"), ("tests", ""), ("m", "a:b")])
    def test_an_identity_field_is_never_empty_and_never_holds_a_colon(self, module: str, qualname: str) -> None:
        with pytest.raises(ValueError, match="a component"):
            _ = ComponentIdentity(module, qualname)

    @pytest.mark.parametrize("text", ["weather", ".Weather", "__main__."])
    def test_a_text_that_names_no_path_is_not_a_key(self, text: str) -> None:
        with pytest.raises(ValueError, match="not a component key"):
            _ = ComponentKey.parse(text)
