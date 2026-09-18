# -*- coding: utf-8 -*-
"""跨进程集成测试共用夹具：以 stdio 方式启动**真实 Worker**。

这里的用例不以 mock 代替桥接——分帧、握手、背压、故障恢复全部发生在
真实的子进程边界上，与 C++ 宿主看到的行为同构。
"""

from __future__ import annotations

import pathlib
import sys

WORKER_SRC = pathlib.Path(__file__).resolve().parents[1] / "worker" / "src"
if str(WORKER_SRC) not in sys.path:
    sys.path.insert(0, str(WORKER_SRC))

import pytest  # noqa: E402

from host_harness import WorkerHost  # noqa: E402


@pytest.fixture()
def host():
    worker = WorkerHost()
    try:
        yield worker
    finally:
        worker.close()
