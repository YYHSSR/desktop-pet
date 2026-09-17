"""Compatibility alias for pet.ui.context_menu; add new code there."""
import sys as _sys
from pet.ui import context_menu as _implementation
_sys.modules[__name__] = _implementation
