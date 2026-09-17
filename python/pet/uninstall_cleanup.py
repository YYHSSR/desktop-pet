"""Compatibility alias for pet.services.uninstall_cleanup; add new code there."""
import sys as _sys
from pet.services import uninstall_cleanup as _implementation
_sys.modules[__name__] = _implementation
