"""composat: a component lifespan interface.

The rewrite is rebuilding the package layer by layer. What is here so far is the foundation the
rest stands on — identities and keys, effect scopes, the component base and what it declares, the
dependency graph, the registry of declarations, and the assembly's seam: the table of what is up,
and the rule a requirement is answered by. The batch operations over that table, and the loader,
land on top of these; see ``archive/pre-rewrite`` for the design they are being rebuilt from.
"""

from .assembly import Assembly as Assembly
from .assembly import default_rule as default_rule
from .component import LIFECYCLE_HOOKS as LIFECYCLE_HOOKS
from .component import Component as Component
from .component import RequirementResolver as RequirementResolver
from .dependency import DependencyCycleError as DependencyCycleError
from .dependency import DependencyError as DependencyError
from .dependency import DependencyGraph as DependencyGraph
from .dependency import DependencyInUseError as DependencyInUseError
from .errors import NoCallContextError as NoCallContextError
from .errors import ServiceNotUpError as ServiceNotUpError
from .errors import UnboundRequirementError as UnboundRequirementError
from .errors import UnboundServiceError as UnboundServiceError
from .identity import ComponentIdentity as ComponentIdentity
from .identity import ComponentKey as ComponentKey
from .registry import ComponentRegistry as ComponentRegistry
from .registry import Declaration as Declaration
from .scope import EffectScope as EffectScope
from .scope import Reclaimable as Reclaimable
from .scope import name_of as name_of
from .spec import RequirementSpec as RequirementSpec
