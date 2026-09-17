"""Compatibility alias for pet.media.library; add new code there."""
import sys as _sys
from pet.media import library as _implementation
_sys.modules[__name__] = _implementation
