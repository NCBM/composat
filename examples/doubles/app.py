"""A component tested without an assembly: a rule and a table are the whole of what it reads.

The two readings a case needs are public — ``requirements_of`` says what the type asks for, and
``attach`` points an instance at a rule — so a test double is an ordinary callable rather than a
subclass of anything.
"""

from __future__ import annotations

from composat import Component, require


class Repo(Component):
    name: str = "real"


class Service(Component):
    repo: Repo = require()

    async def run(self) -> str:
        return self.repo.name


class FakeRepo(Repo):
    """A double of the *contract*, not of the framework: a subclass of the component it stands for."""

    name: str = "fake"
