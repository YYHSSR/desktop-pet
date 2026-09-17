"""Compatibility alias for pet.infrastructure.config; add new code there."""
import sys as _sys
from pet.infrastructure import config as _implementation
_sys.modules[__name__] = _implementation
