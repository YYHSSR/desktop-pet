"""Compatibility alias for pet.ui.context_menus.menu_styles.legacy; add new code there."""
import sys as _sys
from pet.ui.context_menus.menu_styles import legacy as _implementation
_sys.modules[__name__] = _implementation
