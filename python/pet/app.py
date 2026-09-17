"""Compatibility alias for pet.application.app; add new code there."""
import sys as _sys
from pet.application import app as _implementation

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

_sys.modules[__name__] = _implementation
