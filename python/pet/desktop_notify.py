"""Compatibility alias for pet.ui.desktop_notify; add new code there."""
import sys as _sys
from pet.ui import desktop_notify as _implementation
_sys.modules[__name__] = _implementation
