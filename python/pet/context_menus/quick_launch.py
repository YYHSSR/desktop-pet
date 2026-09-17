"""Compatibility alias for pet.ui.context_menus.quick_launch; add new code there."""
import sys as _sys
from pet.ui.context_menus import quick_launch as _implementation
_sys.modules[__name__] = _implementation
