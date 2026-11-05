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

The second axis is the deployment's, passed in rather than guessed: ``below`` is where an address
carries something above the caller's own place, and :func:`ancestors` and :func:`joined` are the
derivations this ships::

    rule = answering_from_the_root(registry, up, only={Config}, below=joined(routing, ancestors))

What it deliberately does not do is fall back in the other direction or across siblings: a qualified
caller may reach the root, nothing derived here ever takes the root anywhere else, and ``tenant-a``
never reads ``tenant-b``. Those are the two ways a lookup table turns into a coincidence. ``below`` is
the deployment's own reading, and it is asked of every caller, the root included.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping

from composat import (
    Candidates,
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
    below: Candidates | None = None,
) -> Policy:
    """Answer at the asker's own qualifier, then wherever ``below`` goes, and last at the root.

    ``only`` is what may cross the axis, and it is named by the deployment rather than inferred: a
    component type is accepted, and so is an interface that has a binding, because the registry is
    what turns either into the identity the lookup is by. There is no default for it, because there
    is no safe one — the wider the list, the likelier a caller whose own instance failed to come up
    is served by the root one with nothing saying so.

    ``below`` is what the address itself is worth: the places tried on the way *down* to the root,
    which is where a qualifier carrying more than one level puts its account. It is a
    :data:`~composat.Candidates` because the framework does not read a qualifier, so a deployment that
    splits one says how. Left out, the asker's own qualifier is the only place below the root, which is
    the whole of what a one-level address needs. It is asked of every caller, the empty qualifier
    included: an address with nothing in it has no level to derive, but a table keyed by identity reaches
    a component declared at the root.

    The caller's qualifier comes first, which is what makes the shared instance a *default*: whatever
    ``below`` returns goes after it, because a list that starts anywhere else answers a caller from
    another instance while its own is up. A deployment that runs one provider for one tenant and
    shares the rest says so by admitting that tenant's own instance, and nothing about the rule
    changes.

    A type that names nothing — an interface with no binding — is refused here, where the rule is
    built, rather than at the first read that would have needed it.
    """
    crossing = frozenset(registry.provider_identity(cls) for cls in only)

    def candidates(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
        asked = ComponentKey(identity, key.instance)
        places = [asked]
        if below is not None:
            for place in below(key, identity):
                if place != asked and place not in places:
                    places.append(place)
        if identity in crossing:
            root = ComponentKey(identity, "")
            if root not in places:
                places.append(root)
        return tuple(places)

    return answering_from(registry, instances, candidates)


def ancestors(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
    """The caller's own place, then each level above it in the address, nearest first.

    A qualifier is opaque to the framework, and this is the one reading of one that a deployment can
    hand back: ``/`` separates levels, so ``acme/one/two`` answers ``acme/one/two``, then
    ``acme/one``, then ``acme``. Every level is kept rather than only the account, because a caller
    three levels down depends on the level directly above it — an endpoint's session reading the
    endpoint — and a derivation that cut to the first segment would skip a level nothing else names.

    An address with no ``/`` in it names one place, which is where this reads the same as the default
    rule. It says nothing about the root: a place is a place, and what may cross to the root is
    ``only=``.
    """
    places = [ComponentKey(identity, key.instance)]
    rest = key.instance
    while "/" in rest:
        rest = rest.rpartition("/")[0]
        places.append(ComponentKey(identity, rest))
    return tuple(places)


def joined(*derivations: Candidates) -> Candidates:
    """The places every derivation names, asked in order, each place once.

    Two axes do not get two rules — a candidate list is a whole policy, and an assembly applies one —
    so a deployment that routes one identity by a table and the rest by their address says both here.
    A derivation with no opinion about an identity answers nothing, which is what keeps the routing
    table out of every other identity's way.

    The order is the answer: the first derivation's first place leads, and a place two derivations name
    is listed once, at its earliest position, so one read does not try the same instance twice.
    """

    def places(key: ComponentKey, identity: ComponentIdentity, /) -> tuple[ComponentKey, ...]:
        ordered: list[ComponentKey] = []
        for derivation in derivations:
            for place in derivation(key, identity):
                if place not in ordered:
                    ordered.append(place)
        return tuple(ordered)

    return places
