"""composat: a component lifespan interface.

The rewrite is rebuilding the package layer by layer. What is here so far is the foundation the
rest stands on — versions, identities and keys, effect scopes, the dependency graph, and the
errors those layers raise. The component, registry, assembly, and loader layers land on top of
these; see ``archive/pre-rewrite`` for the design they are being rebuilt from.
"""

from .dependency import DependencyCycleError as DependencyCycleError
from .dependency import DependencyError as DependencyError
from .dependency import DependencyGraph as DependencyGraph
from .dependency import DependencyInUseError as DependencyInUseError
from .errors import NoCallContextError as NoCallContextError
from .errors import ServiceNotUpError as ServiceNotUpError
from .errors import UnboundServiceError as UnboundServiceError
from .identity import ComponentIdentity as ComponentIdentity
from .identity import ComponentKey as ComponentKey
from .scope import EffectScope as EffectScope
from .scope import name_of as name_of
from .versioning import Version as Version
from .versioning import VersionLike as VersionLike
from .versioning import VersionParts as VersionParts
