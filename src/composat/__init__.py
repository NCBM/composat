"""composat: a component lifespan interface.

The rewrite is rebuilding the package layer by layer. What is here so far is the foundation the
rest stands on — identities and keys, effect scopes, the component base and what it declares, the
state machine and the manager that drives it, the dependency graph, the registry of declarations,
and the assembly: the table of what is up, the rule a requirement is answered by, the batch verbs
that fill the table and empty it, the report they hand back, and the stream of moves they report
while they run. On top of those sits the loader, which turns a path into the module there and the
components it exports, and answers what that module's source looks like now. What turns a loaded
module into a running generation is ``composat.std.pluggable``, the component that owns the decision;
see ``archive/pre-rewrite`` for the design all of this is rebuilt from.
"""

from . import context as context
from .assembly import Assembly as Assembly
from .assembly import AssemblyResult as AssemblyResult
from .assembly import Event as Event
from .assembly import Replacement as Replacement
from .assembly import default_rule as default_rule
from .component import LIFECYCLE_HOOKS as LIFECYCLE_HOOKS
from .component import Component as Component
from .component import RequirementResolver as RequirementResolver
from .component import reopen_epoch as reopen_epoch
from .component import require as require
from .context import CallContext as CallContext
from .dependency import DependencyCycleError as DependencyCycleError
from .dependency import DependencyError as DependencyError
from .dependency import DependencyGraph as DependencyGraph
from .dependency import DependencyInUseError as DependencyInUseError
from .errors import KeyInTransitError as KeyInTransitError
from .errors import LifecycleReentryError as LifecycleReentryError
from .errors import NoCallContextError as NoCallContextError
from .errors import ServiceNotUpError as ServiceNotUpError
from .errors import UnboundRequirementError as UnboundRequirementError
from .errors import UnboundServiceError as UnboundServiceError
from .identity import ComponentIdentity as ComponentIdentity
from .identity import ComponentKey as ComponentKey
from .loader import LoadedUnit as LoadedUnit
from .loader import Unit as Unit
from .loader import components_of as components_of
from .loader import fingerprint_of as fingerprint_of
from .loader import load_unit as load_unit
from .loader import load_units as load_units
from .loader import loaded_unit as loaded_unit
from .manager import ComponentManager as ComponentManager
from .registry import ComponentRegistry as ComponentRegistry
from .registry import Declaration as Declaration
from .scope import EffectScope as EffectScope
from .scope import Reclaimable as Reclaimable
from .scope import name_of as name_of
from .spec import RequirementSpec as RequirementSpec
from .state import Committed as Committed
from .state import ComponentState as ComponentState
from .state import ComponentStateControl as ComponentStateControl
from .state import ComponentStateError as ComponentStateError
from .state import Migration as Migration
from .state import Operation as Operation
