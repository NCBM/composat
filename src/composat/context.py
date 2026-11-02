"""The call level: whose work it is, what it resolves through, and where its effects go.

A component outlives the work running through it. A request that enters a component has its own
reasons to hold things — a transaction, a temporary file, a lease — and those are released when the
*request* is over rather than when the component's life is. A call is the key that work is being done
for, bound where the work runs, so that anything underneath resolves from where it stands.

**A call follows the task, not the thread.** The binding is a ``ContextVar``, so concurrent calls stay
independent of each other and a task started inside one inherits it. That inheritance cuts both ways:
ordinary application code underneath a component takes part in the same call, and a task that outlives
the call is left holding a scope that has already closed. *The caller guarantees its sub-tasks are
finished.* There is no task registry to move that obligation into yet, and a scope is closed the
moment its release is asked for, so nothing that strays afterwards can collect into it.

**The binding comes off last.** Releasing a call's effects is an await, and an effect that resolves
something while it is being released is answered from the call it belonged to. The binding is removed
after the release, not before.

**Asking outside a call is a fact about the program.** It is a :class:`NoCallContextError` rather than
the ``LookupError`` a bare ``ContextVar.get()`` would raise: the two are different mistakes, and a
reader should be able to tell them apart at a glance.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast, final

from .errors import NoCallContextError
from .identity import ComponentKey
from .scope import EffectScope, Reclaimable
from .spec import RequirementSpec

if TYPE_CHECKING:
    from .scope import Disposable, Revoke

type Rule = Callable[[ComponentKey, RequirementSpec, Reclaimable], object]
"""How a requirement becomes an instance: the shape of :class:`composat.RequirementResolver`.

Spelled out here rather than imported, because of the direction this module sits in: a field read asks
the call whether it is inside one, so the dependency runs ``component → context`` and the rule's own
type cannot run the other way without a cycle.
"""

_AT_THE_CALL = "<resolve>"
"""The name a call-level resolve gives the requirement it makes up.

``resolve(Store)`` names a bare type, and a bare type carries no extras — that is the reading, not a
gap: it says "the ordinary one, no special handling". A caller that wants routing asks by the
requirement a field would be declared with, which is what a field read already is.
"""


@final
@dataclass(eq=False, slots=True)
class CallContext:
    """One call: the key it is made for, the rule it resolves by, and the effects it registers.

    Handed out by :func:`open_call`, and reachable from underneath through :func:`current`, which is
    what lets ordinary application code take part in the call its component is serving.
    """

    key: ComponentKey
    rule: Rule
    effects: EffectScope = field(default_factory=EffectScope)

    def resolve[T](self, contract: type[T], /) -> T:
        """The instance answering ``contract`` for this call, looked up afresh on every read.

        The rule decides what that means; this adds nothing to it. In particular a resolve is not
        remembered: two resolves in one call build two, unless the rule keeps what it made — and a
        rule that wants one per call has the scope to keep it in, registering the release and the
        forgetting together. Caching here would be a snapshot, and a snapshot of what a key answers is
        exactly what a replacement makes wrong.
        """
        return cast("T", self.rule(self.key, RequirementSpec(_AT_THE_CALL, contract), self.effects))

    def put(self, effect: Disposable, /) -> Revoke:
        """Register ``effect`` in this call's scope; it is recycled when the call ends, last first."""
        return self.effects.put(effect)


_CALL: ContextVar[CallContext | None] = ContextVar("composat_call", default=None)


def current_or_none() -> CallContext | None:
    """The call in progress, or ``None`` when there is none: the probe, for code that may run in both."""
    return _CALL.get()


def current() -> CallContext:
    """The call in progress; :class:`NoCallContextError` when the code is running outside one."""
    call = _CALL.get()
    if call is None:
        raise NoCallContextError
    return call


def resolve[T](contract: type[T], /) -> T:
    """Answer ``contract`` for the call in progress."""
    return current().resolve(contract)


def put(effect: Disposable, /) -> Revoke:
    """Register ``effect`` in the call in progress, to be recycled when that call ends."""
    return current().put(effect)


@asynccontextmanager
async def open_call(key: ComponentKey, rule: Rule, /) -> AsyncGenerator[CallContext]:
    """Make ``key`` the caller for the duration, and give the call a scope of its own.

    ``rule`` is the answer a requirement is resolved by, which is the same one a field read goes
    through (:attr:`composat.Assembly.rule`), so a call and a field never disagree about what a
    contract means — only about which key is asking.

    The effects are recycled on the way out *before* the binding comes off, so that an effect can
    still resolve what it needs while it is being released. A nested call shadows the one around it
    and restores it on the way out: a component serving one key may serve another for a while, and
    "one call, one key" is a reading of one block rather than a rule about a task.

    A body that fails still has its effects released, and a release that fails too is reported beside
    the failure rather than instead of it — the call failed *and* left something behind, and a reader
    told only one of the two would go looking for the wrong thing. The release is shielded, so being
    cancelled does not cut it short.
    """
    call = CallContext(key, rule)
    token = _CALL.set(call)
    failed: BaseException | None = None
    try:
        yield call
    except BaseException as error:
        failed = error
        raise
    finally:
        try:
            await asyncio.shield(call.effects.dispose())
        except BaseException as releasing:
            if failed is None:
                raise
            raise BaseExceptionGroup("the call failed and its effects would not release", [failed, releasing]) from None
        finally:
            _CALL.reset(token)
