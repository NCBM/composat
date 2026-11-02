"""The instances this process has up, and the rule a requirement is answered by.

Driving a component through its states is the manager's job, and deciding what is declared is the
registry's; what is here is the table the two meet in — one instance per key — and the answer a
declared requirement reads out of it.

The rule is a function of the table rather than a method of the thing that fills it
(:func:`default_rule`), and that is a reading rather than a style. A requirement is answered from
whatever is up *now*, so the rule has to see the table as it fills; taking the table as its argument
is what lets a rule be built before there is anything to answer with, and what lets an application
build a chain whose tail is the framework's own rule without either of them being installed later::

    up: dict[ComponentKey, Component] = {}
    chain = Chain(PerRequest(...), tail=default_rule(registry, up))
    assembly = Assembly(registry, up=up, rule=chain)

So an assembly does not own a rule slot to assign into — it is handed one — and the table it was
handed is the table it fills: ``up`` is mutated, never rebound, or the rule would go on reading a
table nothing writes to.
"""

from __future__ import annotations

from collections.abc import Mapping

from .component import Component, RequirementResolver
from .errors import ServiceNotUpError
from .identity import ComponentKey
from .registry import ComponentRegistry
from .scope import Reclaimable
from .spec import RequirementSpec


def default_rule(
    registry: ComponentRegistry,
    instances: Mapping[ComponentKey, Component],
) -> RequirementResolver:
    """The framework's own answer: what is up under the asker's qualifier, or a refusal.

    Built from two things it does not own — the registry says which identity a contract names, the
    table says which instances are up — and neither is read at build time, so the closure goes on
    answering from the table as it fills: a rule made before anything is up is still the rule in
    force once something is.

    The qualifier is the whole lookup. Instances of one identity are told apart by it, so a caller
    served by the provider at ``tenant-a`` reads its requirements from ``tenant-a`` as well; a slot
    that is declared and is not up is a miss (``ServiceNotUpError``), never a reason to answer from
    another instance, which would be answering a question the caller did not ask.

    A ``RequirementSpec`` is accepted as the whole requirement rather than as its service, so an
    annotation that carries extras arrives intact; this rule has no vocabulary to read them with, and
    resolving by key alone is what "the requirement says nothing beyond its service" means.

    ``effects`` is where a resolver hands back something shorter-lived than the assembly, and this
    one registers nothing into it: an instance the assembly brought up is not the caller's resource,
    and releasing one from a call's scope would take it down under everyone else.
    """

    def resolve(key: ComponentKey, requirement: RequirementSpec, effects: Reclaimable, /) -> object:
        _ = effects
        provider = ComponentKey(registry.provider_identity(requirement.service), key.instance)
        instance = instances.get(provider)
        if instance is None:
            raise ServiceNotUpError(provider)
        return instance

    return resolve


class Assembly:
    """The instances this process has up, and the rule they reach each other by.

    One instance per key: an identity may have several (a tenant each), and each answers at its own
    qualifier. What is here so far is the table and the rule over it; the batch operations that fill
    the table arrive with the manager, which is what drives one component through its states.
    """

    _registry: ComponentRegistry
    _up: dict[ComponentKey, Component]
    _rule: RequirementResolver

    def __init__(
        self,
        registry: ComponentRegistry,
        up: dict[ComponentKey, Component] | None = None,
        rule: RequirementResolver | None = None,
    ) -> None:
        """Hold ``registry``'s instances in ``up``, answered by ``rule`` or by the default rule.

        ``up`` is the table this assembly fills, and a caller that passes one gets to build a rule
        over it before the assembly exists (:func:`default_rule`). It is held as given and never
        rebound, so a rule built from it keeps reading what the assembly writes.
        """
        self._registry = registry
        self._up = {} if up is None else up
        self._rule = default_rule(registry, self._up) if rule is None else rule

    @property
    def rule(self) -> RequirementResolver:
        """The rule in force, which is also what an instance is attached with.

        Reading it back is how an application asks whether its chain was the one installed, and how a
        test asks what a field read is going to go through; there is no setter, because a rule is
        given once and a second one would leave the instances attached earlier pointing at the first.
        """
        return self._rule
