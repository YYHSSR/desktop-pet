"""Compatibility alias for pet.ui.context_menus.modern; add new code there."""
import sys as _sys
from pet.ui.context_menus import modern as _implementation
_sys.modules[__name__] = _implementation
