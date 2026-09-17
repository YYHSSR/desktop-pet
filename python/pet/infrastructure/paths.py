"""Central resource locations for source and PyInstaller execution.

User data stays in Config; resource lookup never depends on the working directory.
"""
from pathlib import Path
import sys


def source_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", source_root()))


def assets_root() -> Path:
    return resource_root() / "assets"
