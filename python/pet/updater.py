"""Compatibility alias for pet.infrastructure.updater; add new code there."""
import sys as _sys
from pet.infrastructure import updater as _implementation
_sys.modules[__name__] = _implementation
