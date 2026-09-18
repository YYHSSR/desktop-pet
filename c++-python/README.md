# C++/Python 混合架构开发与联调说明

一个产品、一套正式界面（C++/QML 宿主）、一套业务规则（Python Worker）。
宿主负责实时渲染与交互；Worker 是按需启动的后台子进程，只处理低频 Agent 业务。
协议契约：[`protocol/envelope.schema.json`](protocol/envelope.schema.json) +
[`protocol/fixtures/`](protocol/fixtures/)（两端共用，改动必须先改契约）。

## 目录结构

```
c++-python/
├─ host/                # C++/QML 宿主工程（渲染、交互、仲裁、进程管理）
│  ├─ src/ qml/ assets/
│  └─ tests/            # 宿主测试（契约分帧 / 动作仲裁 / 菜单结构等）
├─ protocol/            # 信封 schema + framing_cases.json + 共用夹具（唯一判据）
├─ worker/              # Python Worker（pet_worker 包，仅标准库，禁止 GUI 依赖）
│  ├─ src/pet_worker/   # 生产代码
│  └─ tests/            # Worker 单测（不 import PySide6，作为硬约束验证）
└─ tests/               # 跨进程集成测试：以 stdio 启动真实 Worker 子进程
```

## 环境准备

Worker 运行期只依赖标准库；dev 依赖仅 pytest。建议使用独立虚拟环境：

```powershell
cd c++-python/worker
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "pytest>=7.4"
```

## 运行测试

```powershell
# Worker 单测（含「整包导入不得引入 Qt」的硬约束测试）
cd c++-python
worker\.venv\Scripts\python.exe -m pytest worker/tests -q

# 跨进程集成测试（每个用例启动真实 Worker 子进程）
worker\.venv\Scripts\python.exe -m pytest tests -q

# C++ 侧（契约分帧 + 动作仲裁 + 既有测试）
ctest --test-dir host/build --output-on-failure
```

`tests/test_bridge_protocol.cpp`（C++）与 `worker/tests/test_framing_contract.py`（Python）
消费**同一份** `protocol/fixtures/framing_cases.json`，两端必须得到完全一致的结果。

## 宿主侧配置（ConfigManager 单写者）

| 键 | 默认 | 说明 |
|---|---|---|
| `python_worker.enabled` | `true` | Worker 总开关（关闭即 Agent 联动停用，桌宠本体不受影响） |
| `python_worker.python_path` | `python` | 解释器；联调时建议填绝对路径 |
| `python_worker.worker_entry` | 空 | `pet_worker/__main__.py` 路径；缺省按安装布局探测 |

说明：

- 宿主以脚本路径直启 Worker 入口时会自动把包的父目录加入 `PYTHONPATH`；
  若 Worker 已按包安装则无需配置。
- agent 启停语义：`agent_link.<key>` 布尔键 +
  `agent_link.custom_agents` 条目（`key` / `name` / `path`，`path` 即统一协议 JSONL 事件文件）。

## 环境变量

| 变量 | 说明 |
|---|---|
| `DESKTOP_PET_DATA` | 覆盖数据目录（`config.json` 所在处）。联调/迁移实验**必须**用它隔离，避免与旧版 Python 进程同写一份数据 |
| `DESKTOP_PET_ASSETS` | 覆盖素材目录（既有） |

## 真实联调

1. 准备隔离数据目录，写入 `config.json`（`python_worker` +
   `agent_link.custom_agents`，并把 `agent_link.<key>` 置为 `true`）。
2. `$env:DESKTOP_PET_DATA="<隔离目录>"` 后启动 `host/build/bin/desktop-pet.exe`。
3. 向事件文件追加统一协议 JSONL（如 `{"event":"PreToolUse","tool":"bash"}`、
   `{"event":"Stop"}`），桌宠应播出对应动作与气泡。
4. 强杀 Worker 进程：桌宠拖拽与动画不受影响，宿主按 1/2/4s 退避自动重启
   （60s 内最多 3 次），重启后自动重新同步快照。
5. 诊断：Worker 的 stderr 全部落盘到 `<数据目录>/worker-stderr.log`
   （超 1MiB 轮转到 `.old`），宿主桥接层异常也写入该文件。协议通道（stdout）
   与诊断流严格分离。

## 硬约束（违反即架构回退）

- Worker 不 import PySide6 / 任何 GUI 库（单测强制验证）；
- stdout 只允许输出协议行，日志一律走 stderr；
- `config.json` 只由 C++ 宿主写，Worker 只读快照、只能提交带 revision 的白名单 patch；
- GUI 线程绝不阻塞：只用 `readyRead` / `finished` / `errorOccurred`，禁止 `waitFor*`。
