"""Compatibility alias for pet.infrastructure.music_detect; add new code there."""
import sys as _sys
from pet.infrastructure import music_detect as _implementation
_sys.modules[__name__] = _implementation
