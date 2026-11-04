# 2. A requirement is written in the class body, and the framework answers the field

**Status**: accepted, 2026-11-04.

## Context

A component declares what it needs from other components. Whatever the spelling, three things have to
stay true: a static checker must see the field and its type; reading the field must be a live read, so
a replaced provider is visible without refreshing anything; and the reading must work inside `load`,
before the instance has been handed anything.

A component also has to say which lifecycle hooks it answers. Hooks are coroutines the state machine
drives (`load`, `unload`, `reload`, `enable`, `disable`, `drain`), and a misspelled one has to fail at
the checker rather than become a hook that never runs.

## Decision

A requirement is an assignment in the class body: `store: Store = require()`. Being an assignment, the
field counts as initialized to a checker; the annotation says what the field reads as; the descriptor
`require()` installed is what performs the read, through `composat.component.read`, at the reader's own
key and on the innermost lifetime the read happens inside.

Capabilities are the hooks the type writes, read by `composat.component.capabilities_of` off the MRO
with the base skipped. The base declares all six hooks so `@override` has something to check against;
declaring one is not claiming it.

What the framework does to an instance is a function of the module, not a method of the class —
`attach`, `read`, `reopen_epoch`, `requirements_of`, `capabilities_of` — so an operation an application
must not be able to replace is not offered to it as a member.

## Consequences

- A requirement that is not written in the body is refused when the class is asked, not silently
  ignored: `_Bound.__set__` raises, and `Component._require_settled` names the late field.
- A class whose annotation names a class defined further down the module settles at its first ask
  instead of at class creation.
- An error from a requirement read is `UnboundRequirementError` rather than a bare `AttributeError`,
  and only for names the type declared.

## Alternatives considered

- **`@dataclass_transform()`.** Synthesizes an `__init__`, so a requirement would be a constructor
  argument (`Cache(store=...)`). That contradicts the live read, reading a field inside `load`, and
  `Assembly.replace` moving the key before the old instance is gone.
- **`caps=` alongside the hooks.** Two statements of one fact: `async def unload` already says both
  "this is a hook" and "this type writes it". Two statements drift, and the drift is silent.
- **Hooks as free functions taking the instance.** Nothing to put `@override` on, so a typo is a
  function nobody calls, discovered at runtime and far from the mistake.
- **`abc.ABC` / `ABCMeta`.** Capability detection never asks `isinstance`, so the check the machinery
  would speed up is not on any hot path, and it would buy abstraction nobody asked for.
- **Constructor injection.** Dependencies bound at construction are a snapshot, for the same reasons
  `@dataclass_transform` is.
- **An awaitable requirement** (`store: Awaitable[Store]`, or a rule written as a coroutine). A
  requirement is read from code that is not a coroutine — a plain function under a component asks the
  call for what it needs (`examples/calls`) — and a descriptor's `__get__` cannot await, so making the
  read asynchronous would make every reading site, and everything above it, asynchronous. The await
  belongs to acquiring the instance (`load`, `enable`) and to waiting for a replacement
  (`composat.std.wait.serving`).
- **The annotation alone declares the requirement** (`store: Store` with no marker). A checker cannot
  see a descriptor installed by `__init_subclass__`, so every such field earns
  `reportUninitializedInstanceVariable`; and "this field is a requirement" can be said outright rather
  than inferred from the shape of a type.
- **Contract given in the body only** (`store: Store = declares(Store)`). The class body would have to
  resolve the contract at definition time, which turns forward references, `TYPE_CHECKING` imports and
  module cycles into hard errors — and states the contract twice.
- **A marker without a return type** (`store: Store = declared()`). Measured: `reportAny` and
  `reportExplicitAny` warn, which is noisier than the problem it solves.
- **`@final` on base methods.** A method that needs it is a method that does not belong to the type's
  protocol; the fix is to move it off the type, which is what these functions are.
