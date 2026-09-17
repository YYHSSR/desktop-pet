"""Compatibility alias for pet.ui.window; add new code there."""
import sys as _sys
from pet.ui import window as _implementation
_sys.modules[__name__] = _implementation
