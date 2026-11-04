"""Answering from the root: one instance at the empty qualifier, for every caller that asks.

A qualifier is what tells instances of one identity apart, and it is one axis: a caller served by the
provider at ``tenant-a`` reads its requirements at ``tenant-a`` too. A deployment that runs *some*
identities once for the whole process — a controller, a config, a pool — wants the other reading: the
caller's own qualifier first, and the unqualified instance after it.

That is a policy rather than a fact about the type, so it lives here and not in the core. The same
class is the process-wide one in one deployment and one-per-tenant in another, and what crosses the
axis is decided where the assembly is built, once. A batch closes over the same list, so a dependant
that comes up on its own brings the shared instance up with it::

    up: dict[ComponentKey, Component] = {}
    rule = answering_from_the_root(registry, up, only={Config, Pool})
    assembly = Assembly(registry, up=up, rule=rule)

What it deliberately does not do is fall back in the other direction or across siblings: a qualified
caller may reach the root, the root reaches nothing else, and ``tenant-a`` never reads
``tenant-b``. Those are the two ways a lookup table turns into a coincidence.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from composat import (
    Component,
    ComponentIdentity,
    ComponentKey,
    ComponentRegistry,
    Policy,
    answering_from,
)


def answering_from_the_root(
    registry: ComponentRegistry,
    instances: Mapping[ComponentKey, Component],
    /,
    *,
    only: Collection[type[object]],
) -> Policy:
    """Answer at the asker's own qualifier, and after it at the unqualified instance of ``only``.

    ``only`` is what may cross the axis, and it is named by the deployment rather than inferred: a
    component type is accepted, and so is an interface that has a binding, because the registry is
    what turns either into the identity the lookup is by. There is no default for it, because there
    is no safe one — the wider the list, the likelier a caller whose own instance failed to come up
    is served by the root one with nothing saying so.

    The caller's qualifier comes first, which is what makes the shared instance a *default*: a
    deployment that runs one provider for one tenant and shares the rest says so by admitting that
    tenant's own instance, and nothing about the rule changes.

    A type that names nothing — an interface with no binding — is refused here, where the rule is
    built, rather than at the first read that would have needed it.
    """
    crossing = frozenset(registry.provider_identity(cls) for cls in only)

    def candidates(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
        asked = ComponentKey(identity, key.instance)
        if not key.instance or identity not in crossing:
            return (asked,)
        return (asked, ComponentKey(identity, ""))

    return answering_from(registry, instances, candidates)
