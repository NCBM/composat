"""A plugin host: one unit's code and instances move forward together, and classes come and go.

Three steps per module, each of them the caller's: notice (``stale``), move the code (``reload_module``),
move the instances (``swap_instances``). What an edit also changed — a class that appeared, a class
that is gone — is nobody's but the host's, because a class without a key has no qualifier and no
arguments until the deployment says what they are.
"""

from __future__ import annotations

from dataclasses import dataclass

from composat import Assembly, AssemblyResult, Component, ComponentIdentity, Declaration
from composat.std.pluggable import ReloadResult, SwapResult, UnitLoader


def declared(loader: UnitLoader, name: str) -> dict[ComponentIdentity, type[Component]]:
    """What the generation now loaded holds, by identity: the class each identity names *now*."""
    unit = loader.unit(name)
    if unit is None:
        raise KeyError(name)
    return {ComponentIdentity.of(cls): cls for cls in unit.components}


@dataclass(frozen=True, slots=True)
class Rebuild:
    """What one edit did: the code that moved, the instances that followed, the classes that came or went."""

    reload: ReloadResult
    swap: SwapResult
    appeared: AssemblyResult
    withdrawn: AssemblyResult


async def rebuild(loader: UnitLoader, assembly: Assembly, name: str) -> Rebuild:
    """Bring one unit forward, then settle the classes its edit added or removed.

    The old set is read *before* the reload: afterwards ``loader.unit`` already answers the new
    generation and the one it replaced is gone. A class that appeared is declared as itself and
    started — that part is the default rather than the only choice, since a deployment that wants a
    qualifier or constructor arguments writes its own declaration instead. A class the edit removed
    is withdrawn together with the keys the swap could not place.
    """
    before = set(declared(loader, name))
    reloaded = loader.reload_module(name)
    swap = await loader.swap_instances(assembly, name)
    after = declared(loader, name)

    added = [Declaration(after[identity]) for identity in after.keys() - before]
    appeared = await assembly.admit(*added) if added else AssemblyResult((), {}, ())
    withdrawn = await assembly.evict(*swap.unmatched) if swap.unmatched else AssemblyResult((), {}, ())
    return Rebuild(reloaded, swap, appeared, withdrawn)
