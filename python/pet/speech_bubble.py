"""Compatibility alias for pet.ui.speech_bubble; add new code there."""
import sys as _sys
from pet.ui import speech_bubble as _implementation
_sys.modules[__name__] = _implementation
