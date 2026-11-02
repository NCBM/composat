"""The registry of declared components: key, declaration, and the plan they imply.

Requirements come from the component type (:meth:`~composat.component.Component.requirements`) and
are recorded as edges between **identities**: "the engine needs storage" is a fact about two types,
not about two particular instances of them. Instances of one identity share that node, which is what
keeps the graph the size of the type population rather than of the deployment. The edges are what the
order is worked out from; the field names are only how an instance later reads what it asked for, so
the two views come from one reading of the annotations and stay in step.

An annotation may name an **interface** (a ``Protocol``) rather than the component that provides it,
which keeps the asking side clear of the implementation. :meth:`ComponentRegistry.bind` is where such
a name acquires a meaning, and it is the one place an interface turns into an identity: a requirement
with nothing bound to it is refused along with the declaration that asks for it, at declaration time
rather than at the call that would have needed it.

Declarations are made before the assembly starts and stay put while it runs. Nothing here discovers a
component or chooses among them: the classes arrive from the application, which is the only thing
that knows where they came from, and the version a deployment runs is the deployment's own decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .component import Component
from .dependency import DependencyGraph
from .errors import UnboundServiceError
from .identity import ComponentIdentity, ComponentKey
from .spec import RequirementSpec


@dataclass(frozen=True, slots=True)
class Declaration:
    """A component this process knows how to build, and the qualifier that makes it *this* instance.

    ``args`` and ``kwargs`` are how the instance is constructed — the component's own business, which
    the framework passes to it without reading. The identity is the class's own path, so a reload
    keeps the text and replaces the class; the key adds the qualifier that tells instances apart.
    """

    component_class: type[Component]
    instance: str = ""
    args: tuple[object, ...] = ()
    kwargs: Mapping[str, object] = field(default_factory=dict[str, object])

    @property
    def identity(self) -> ComponentIdentity:
        """The identity the class declares."""
        return self.component_class.identity()

    @property
    def key(self) -> ComponentKey:
        """The instance this declaration names."""
        return ComponentKey(self.identity, self.instance)

    def build(self) -> Component:
        """One instance, built the way this declaration was given."""
        return self.component_class(*self.args, **self.kwargs)


class ComponentRegistry:
    """The declared components, keyed by instance key and ordered by the identity graph."""

    _declarations: dict[ComponentKey, Declaration]
    _graph: DependencyGraph[ComponentIdentity]
    _providers: dict[type[object], type[Component]]

    def __init__(self) -> None:
        self._declarations = {}
        self._graph = DependencyGraph()
        self._providers = {}

    def __contains__(self, key: object) -> bool:
        """Whether ``key`` is declared: the question a batch asks before anything is built."""
        return key in self._declarations

    def bind(self, service: type[object], provider: type[Component]) -> None:
        """Say which component type answers for ``service``: the interface-to-provider mapping.

        This is where an interface annotation acquires a meaning; with the mapping empty there is no
        identity for the requirement to point at. The binding is by *type* and says nothing about
        instances — which instance answers is decided per call, from the caller's own qualifier.

        A binding stays put. The requirement edges already drawn from the interface would otherwise
        start pointing at an implementation nobody chose, so a second binding is refused rather than
        quietly taking effect. A component class is refused too: it already names itself, and a
        binding nothing can read is worse than an error.
        """
        if issubclass(service, Component):
            raise ValueError(f"{service!r} is a component and names itself")
        if service in self._providers:
            raise ValueError(f"{service!r} is already bound to {self._providers[service]!r}")
        self._providers[service] = provider

    def provider_identity(self, service: type[object]) -> ComponentIdentity:
        """The identity ``service`` names: a component type names itself, an interface its provider.

        This is the one place that decides whether an annotation is a requirement at all, and what it
        requires, so the graph the registry draws and the instance a call resolves answer the same
        question the same way. ``UnboundServiceError`` when the service is an interface with nothing
        bound to it: the annotation named nothing, and saying so beats answering with something else.
        """
        if issubclass(service, Component):
            return service.identity()
        provider = self._providers.get(service)
        if provider is None:
            raise UnboundServiceError(service)
        return provider.identity()

    def declare(
        self,
        component_class: type[Component],
        *,
        instance: str = "",
        args: tuple[object, ...] = (),
        kwargs: Mapping[str, object] | None = None,
    ) -> ComponentKey:
        """Declare one instance; the shorthand for a batch of exactly one."""
        declaration = Declaration(component_class, instance=instance, args=args, kwargs=kwargs or {})
        return self.declare_all([declaration])[0]

    def declare_all(self, declarations: Iterable[Declaration]) -> tuple[ComponentKey, ...]:
        """Declare a batch, with the declarations in whatever order they arrive.

        Declaring commits as it goes: a batch that fails halfway keeps what it declared, which the
        registry shows and the caller can see. The two checks the whole batch shares — a cycle among
        the identities, and a requirement nothing is bound to — are made on a throwaway graph before
        the first commit, so a batch that could not be ordered leaves the registry as it was.
        """
        return tuple(self._declare(declaration) for declaration in self._in_dependency_order(list(declarations)))

    def redeclare(self, declaration: Declaration) -> None:
        """Point a declared key at another build of the identity it already names.

        The key keeps its place among ``declarations()`` and the identity keeps its node in the graph,
        so everything ordered by either of them stays where it is. What a new build cannot change is
        what its identity needs: those edges belong to the type, and a generation that asks for
        something else is a different component wearing one name. That is the reload case — the same
        path, this time asking for different services — and it is worth a refusal rather than a graph
        that silently disagrees with the annotation.

        Nothing comes back: the declaration carries the key the caller already had.
        """
        key = declaration.key
        if key not in self._declarations:
            raise KeyError(key)
        self._require_same(key.identity, self._graph.dependencies(key.identity), self._requirements(declaration))
        self._declarations[key] = declaration

    def declaration(self, key: ComponentKey) -> Declaration:
        """The declaration behind ``key``; ``KeyError`` when the registry holds none under it."""
        return self._declarations[key]

    def declarations(self) -> tuple[Declaration, ...]:
        """Every declaration, in declaration order."""
        return tuple(self._declarations.values())

    def requirements_of(self, identity: ComponentIdentity) -> tuple[RequirementSpec, ...]:
        """The requirements instances of ``identity`` declare, as their type declares them.

        The registered type's own reading rather than a second copy: a key is what tells instances of
        one identity apart, and what they need is a fact about the identity. ``KeyError`` when the
        registry holds no declaration of it.
        """
        for key, declaration in self._declarations.items():
            if key.identity == identity:
                return declaration.component_class.requirements()
        raise KeyError(identity)

    def order(self) -> tuple[ComponentIdentity, ...]:
        """Assembly order: requirements first."""
        return self._graph.order()

    def reverse_order(self) -> tuple[ComponentIdentity, ...]:
        """Teardown order: the things that need others go first."""
        return self._graph.reverse_order()

    def _declare(self, declaration: Declaration) -> ComponentKey:
        """Add one declaration, with the edges its type's requirements imply.

        The requirements have to be declared already: an edge joins declared identities, and a batch
        that arrives in some other order is :meth:`declare_all`.
        """
        key = declaration.key
        if key in self._declarations:
            raise ValueError(f"already declared: {key}")
        identity = declaration.identity
        requirements = self._requirements(declaration)
        try:
            known = self._graph.dependencies(identity)
        except KeyError:
            self._graph.add(identity, depends_on=requirements)
        else:
            # A second instance of a known identity: the edges are the type's, and they are already
            # drawn — agreeing with them is the whole check.
            self._require_same(identity, known, requirements)
        self._declarations[key] = declaration
        return key

    @staticmethod
    def _require_same(
        identity: ComponentIdentity,
        known: frozenset[ComponentIdentity],
        requirements: tuple[ComponentIdentity, ...],
    ) -> None:
        """Refuse requirements that contradict the edges the identity already has."""
        if known != frozenset(requirements):
            declared = sorted(map(str, known))
            required = sorted(map(str, requirements))
            raise ValueError(f"{identity} already has requirements {declared}, not {required}")

    def _requirements(self, declaration: Declaration) -> tuple[ComponentIdentity, ...]:
        """The identities one declaration asks for, interfaces resolved through the mapping.

        Reading a requirement can already fail: an interface with nothing bound to it names no
        identity, and declining it here is what puts the error where the requirement is written down.
        """
        return tuple(self.provider_identity(spec.service) for spec in declaration.component_class.requirements())

    def _in_dependency_order(self, declarations: Sequence[Declaration]) -> tuple[Declaration, ...]:
        """The batch in dependency order, worked out on a throwaway graph.

        A requirement outside the batch is left to :meth:`_declare`: it is either declared already
        there or the edge is refused. A cycle among the batch is refused here, before any of it is
        committed.
        """
        grouped: dict[ComponentIdentity, list[Declaration]] = {}
        for declaration in declarations:
            grouped.setdefault(declaration.identity, []).append(declaration)
        graph: DependencyGraph[ComponentIdentity] = DependencyGraph()
        for identity in grouped:
            graph.add(identity)
        for identity, group in grouped.items():
            for requirement in self._requirements(group[0]):
                if requirement in grouped:
                    graph.link(identity, requirement)
        return tuple(declaration for identity in graph.order() for declaration in grouped[identity])
