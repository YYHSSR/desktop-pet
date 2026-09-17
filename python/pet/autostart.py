"""Compatibility alias for pet.infrastructure.autostart; add new code there."""
import sys as _sys
from pet.infrastructure import autostart as _implementation
_sys.modules[__name__] = _implementation
