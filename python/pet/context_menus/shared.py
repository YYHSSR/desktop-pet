"""Compatibility alias for pet.ui.context_menus.shared; add new code there."""
import sys as _sys
from pet.ui.context_menus import shared as _implementation
_sys.modules[__name__] = _implementation
