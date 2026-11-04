# 1. Decisions are recorded in this repository

**Status**: accepted, 2026-11-04.

## Context

The design was settled away from the code, in notes that were written to be thrown away. What
survived is the code: each module's docstring says what that module guarantees now. What did not
survive is the other half of a design — which shapes were considered and refused, and why. A reader
who wondered "why not `@dataclass_transform`" found no answer anywhere in the tree, and whoever tried
the same shape next had to have the same argument again.

## Decision

`docs/adr/` holds one record per decision, numbered from `0001`, append-only. A record states the
context, the decision, its consequences, and the alternatives that were refused. A decision that is
later reversed gets a new record, and the old one gains a line `Superseded by NNNN`; no record is
edited to look as though it had always been right.

The records describe in prose why the code is shaped as it is, and name the symbols they are about.
They do not restate what those symbols guarantee: a change shows up in the docstring, so the record
cites the symbol instead.

## Consequences

- The tree a clone gets carries the design, not just the outcome.
- Every record is coupled to the code by symbol names, so a rename has to reach into `docs/adr/`.
- A refused alternative now costs a paragraph instead of a conversation.

## Alternatives considered

- **Keep the decisions in the design document of the project that commissioned the work.** It is not
  shipped with the code, cannot be opened from a clone, and states the design as of one moment.
- **One appendix listing every refused alternative.** An alternative means something only next to the
  decision that refused it: "queue in-transit keys" is a different objection under a section about
  locks than under a section about reporting.
- **Record nothing and rely on the docstrings.** A docstring explains the shape that exists; it has
  no place to say what the same problem looked like with another shape, and adding that to every
  module would put the argument back where it cannot be found.
