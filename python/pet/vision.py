"""Compatibility alias for pet.infrastructure.vision; add new code there."""
import sys as _sys
from pet.infrastructure import vision as _implementation
_sys.modules[__name__] = _implementation
