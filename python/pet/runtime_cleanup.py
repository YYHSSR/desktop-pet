"""Compatibility alias for pet.infrastructure.runtime_cleanup; add new code there."""
import sys as _sys
from pet.infrastructure import runtime_cleanup as _implementation
_sys.modules[__name__] = _implementation
