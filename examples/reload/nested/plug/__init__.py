"""A drop-in that is a package: the entry re-exports what lives below it.

The class below is named twice here — where it is written, and where this deployment says which of
them it runs by default — and it is one component however many names the entry gives it.
"""

from __future__ import annotations

from .component import Gear
from .defaults import Gear as Default

__all__ = ["Default", "Gear"]
