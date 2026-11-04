# 3. A key is an address, and a requirement is answered at the asker's qualifier

**Status**: accepted, 2026-11-04.

## Context

A deployment runs one instance of a component, or one per tenant, or one per row id. Whatever the
qualifier means to the application, the framework has to answer one question the same way every time:
which instance does a read get, in a process where keys come up and go down while it runs, and where
the answer has to be current at the moment of the read, not at the moment the reader was built.

## Decision

An identity is the class's own path, `module.qualname` (`ComponentIdentity`). A key is an identity plus
an opaque qualifier (`ComponentKey`); the qualifier is the declaring side's discriminator, and empty
means the single instance of that identity. The registry draws its edges between identities, so the
graph is the size of the type population, not of the deployment.

A requirement is answered under the asker's own qualifier: `composat.assembly.default_rule` is
`answering_from` over one candidate, `ComponentKey(identity, key.instance)`. Anything beyond that
candidate list is a deployment policy, expressed as a rule built where the assembly is built —
`composat.std.root.answering_from_the_root` is the shipped example, and it requires the deployment to
name which identities may be read at a qualifier other than the asker's (`only={Config, Pool}`).

Which keys come up is the manifest's: a key nothing needs is not a root, and `Assembly.start` brings up
what the named keys declare, transitively, at the qualifier of the key that named it.

## Consequences

- A miss under the caller's own key is refused instead of being answered from a sibling or the root;
  the refusal names the key and, when the table holds an instance, the level it rests at.
- The rule is a function of the table (`answering_from` takes `instances` as an argument), so it can be
  built before anything is up and goes on answering from the table as it fills. The assembly mutates
  the table it was handed and never rebinds it.
- `ComponentRegistry.provider_identity` is the single place where an interface (`Protocol`) becomes an
  identity, so the graph and the read agree about what an annotation names.
- The closure is the manifest's, and it consults no rule: `Assembly.start` derives each requirement at
  the qualifier of the key that needs it, so a place another rule would answer from — an account above
  an endpoint, an unqualified shared instance — has to be named in the batch. Left out, it fails there
  and names the field (`KeyError`), rather than being filled by a read that would have found it.

## Alternatives considered

- **A `shared` fallback inside the default rule** (own qualifier, then empty). Rejected: a key is an
  address, and a fallback makes "nothing is declared here" and "nothing is up here" the same answer.
  Kept as a deployment policy instead, which is why `answering_from_the_root` takes `only=` and
  answers from the root side only.
- **`singleton`.** Neither reading needs the framework: "one per process" is the empty qualifier, and
  "one, enforced" also forbids declaring two on purpose to exercise the failure path.
- **A framework-level `TenantKey`, narrowing the qualifier to a tenant type.** Tenant knowledge lives in
  configuration, and the qualifier also carries row ids and pinned generations. What actually needed
  fixing was the refusal message, which names the caller's key.
- **Deriving the roots from the registry** (an identity nothing requires is a root). A manifest grows in
  stages, so "nothing requires it" does not mean "it should be up"; which keys are roots is a deployment
  decision.
- **`Assembly.resolve` as the default rule, plus a `resolver` setter.** The rule would become a method of
  the thing that fills the table: it could only be attached after construction, with a placeholder value
  first, and it would stop being a pure function of table and registry.
- **Looking an identity up by name** (`identities_of(name)`). An identity has no separate name: the
  version, and any label over it, belongs to the application.
- **The registry handing out a type closure, the assembly mapping it to keys.** The closure walks keys,
  not types — the qualifier comes from the key that named it, an instance already up is skipped, and an
  identity the caller listed is not derived — so the assembly would walk it anyway. The registry answers
  requirements per identity; the address mapping stays in the assembly.
- **Closing over direct requirements only.** Half a closure leaves `order()` insufficient: the dependency
  that was left out surfaces as a field read deep inside `load`, which is the furthest possible place
  from the declaration that asked for it.
- **Declaring orchestration explicitly** (`orchestrates`, a component writing out the keys it brings up).
  It says the requirement closure a second time, adds an edge kind and a validation layer, and differs
  from a requirement in one respect only — nobody reads the instance — which is the caller's choice, not
  a shape of the declaration.
