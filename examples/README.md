# Examples

One shape an application takes per directory, written the way an application writes it: `app.py` is
the application, and the `test_*.py` beside it drives the whole thing. These are the end-to-end
cases — `pdm run pytest` runs them alongside the unit cases in `tests/`.

| directory | shape |
|---|---|
| `host` | declare, start the roots, stop everything on the way out |
| `calls` | one call per request: what it owns, and what plain code underneath reads |
| `units` | one unit of work per call: the rule opens it, any depth asks for it, the call releases it once |
| `http` | an ASGI deployment: a request is a call, a client is a session, a tenant is a qualifier |
| `tenants` | one instance per tenant, admitted and withdrawn while the process runs |
| `replace` | one key swapped for a new generation: readers waiting it out, and a handle that follows its reader |
| `reload` | edited modules: new code, new instances, classes that came and went, a burst of edits, a package drop-in |
| `levels` | a batch built first, then moved a level at a time: `enable`, `quiesce`, `migrate` |
| `events` | the moves an assembly reports as it makes them, and a sink that fails |
| `watch` | a burst of edits folded into one reload, with the loop and the timer owned by the scope |
| `interfaces` | a requirement written as a protocol, and the binding that says what answers it |
| `doubles` | a component driven by a rule of its own, with no assembly at all |

`support.py` holds the two helpers they share, and `conftest.py` takes back the modules a case loaded.

`tenant_swap.py` is the one shape kept in a single module rather than a directory: an account's
credential swapped under a serving endpoint, the read that lands in the swap's window, and a tenant
quiesced and evicted on the way out. Its test sits beside it at the root.
