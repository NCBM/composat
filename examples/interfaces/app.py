"""A requirement written as an interface: what a component asks for, and which class answers it.

The annotation names the protocol, so the component depends on the shape it needs rather than on the
class that happens to provide it; the registry is where the two are tied together, and the tying
happens before anything that asks for it is declared.
"""

from __future__ import annotations

from typing import Protocol

from composat import Component, ComponentRegistry, require


class Weather(Protocol):
    """The contract a reader asks for: one method, and no opinion about who implements it."""

    def forecast(self, city: str) -> str: ...


class WeatherTool(Component):
    """One provider of it. Another implementation would be another class, and a different binding."""

    def forecast(self, city: str) -> str:
        return f"{city}: clear"


class Report(Component):
    weather: Weather = require()

    async def line(self, city: str) -> str:
        return self.weather.forecast(city)


def build(*, bound: bool = True) -> ComponentRegistry:
    """A registry holding the provider, bound to the interface when ``bound`` says so."""
    registry = ComponentRegistry()
    if bound:
        registry.bind(Weather, WeatherTool)
    _ = registry.declare(WeatherTool)
    return registry
