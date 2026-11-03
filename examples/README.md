# Examples

One shape an application takes per directory, written the way an application writes it: `app.py` is
the application, and the `test_*.py` beside it drives the whole thing. These are the end-to-end
cases — `pdm run pytest` runs them alongside the unit cases in `tests/`.

| directory | shape |
|---|---|
| `host` | declare, start the roots, stop everything on the way out |
| `calls` | one call per request: what it owns, and what plain code underneath reads |
| `tenants` | one instance per tenant, admitted and withdrawn while the process runs |
| `replace` | one key swapped for a new generation, with readers waiting it out |
| `reload` | an edited module: new code, new instances, classes that came and went |
| `doubles` | a component driven by a rule of its own, with no assembly at all |

`support.py` holds the two helpers they share, and `conftest.py` takes back the modules a case loaded.
