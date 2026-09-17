"""Compatibility alias for pet.services.agent_link; add new code there."""
import sys as _sys
from pet.services import agent_link as _implementation
_sys.modules[__name__] = _implementation
