# -*- coding: utf-8 -*-
"""pytest 引导：把 ``src`` 放进 sys.path。

显式注入而不是只依赖 ``pyproject.toml`` 的 ``pythonpath``：从仓库根目录与从
``worker/`` 目录分别执行 pytest 时 rootdir 不同，只靠 ini 会时灵时不灵。
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
