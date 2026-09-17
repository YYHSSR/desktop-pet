"""Compatibility alias for pet.ui.settings_dialog; add new code there."""
import sys as _sys
from pet.ui import settings_dialog as _implementation
_sys.modules[__name__] = _implementation
