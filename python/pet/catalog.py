"""Compatibility alias for pet.infrastructure.catalog; add new code there."""
import sys as _sys
from pet.infrastructure import catalog as _implementation
_sys.modules[__name__] = _implementation
