"""What a protocol-typed requirement is answered with, and what happens when nothing answers it."""

from __future__ import annotations

import pytest

from composat import (
    Assembly,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    UnboundServiceError,
)
from examples.interfaces.app import Report, Weather, WeatherTool, build
from examples.support import run


def key_of(cls: type) -> ComponentKey:
    return ComponentKey(ComponentIdentity.of(cls))


def test_a_bound_interface_is_answered_by_the_class_it_names() -> None:
    async def main() -> None:
        registry = build()
        _ = registry.declare(Report)
        assembly = Assembly(registry)

        _ = await assembly.start(key_of(Report))
        report = assembly.instance(key_of(Report))
        assert isinstance(report, Report)

        assert await report.line("Oslo") == "Oslo: clear"
        assert report.weather is assembly.instance(key_of(WeatherTool))

    run(main())


def test_an_interface_nothing_answers_is_refused_where_the_component_is_declared() -> None:
    registry = build(bound=False)

    with pytest.raises(UnboundServiceError) as raised:
        _ = registry.declare(Report)

    assert raised.value.service is Weather


def test_binding_twice_is_refused() -> None:
    registry = build()

    with pytest.raises(ValueError, match="already bound"):
        registry.bind(Weather, WeatherTool)


def test_a_component_class_names_itself_and_needs_no_binding() -> None:
    registry = ComponentRegistry()

    with pytest.raises(ValueError, match="names itself"):
        registry.bind(WeatherTool, Report)
