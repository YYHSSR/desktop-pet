# Desktop Pet 桌宠

一只带 **AI Agent 状态联动** 的桌面宠物：桌宠会感知 Antigravity IDE、ChatGPT（Codex）、
自定义 Agent 的工作状态——干活时换动作、思考时冒气泡、任务完成时提醒你。

| 渲染与交互 | Agent 联动 |
|---|---|
| C++/QML 宿主（本机窗口、透明置顶、WebM 动画） | Python Worker（按需启动的后台子进程，仅标准库） |

架构：宿主负责桌宠的**渲染与交互**（首帧不依赖任何子进程），Worker 负责**全部
Agent 业务规则**（事件监听、状态归一化、行为建议）。两端通过 stdin/stdout 上的
UTF-8 JSON Lines 协议通信，契约以 [`c++-python/protocol/`](c++-python/protocol/)
为唯一判据——C++ 与 Python 的测试消费同一份夹具。

## 功能特性

- **桌宠本体**：WebM 透明动画、拖拽物理（Q 弹形变）、漫步/调头、自言自语气泡
  （5 套样式）、点击台词绑定、多角色、多屏记忆、缩放、全屏自动隐藏、开机自启、
  右键菜单（播放动画/启动应用/快捷网址/设置）
- **Agent 联动**（Worker 实现，只**建议**不执行，由宿主仲裁）：
  - **custom agent**：统一协议 JSONL 事件文件的只读增量监听
  - **antigravity**：hooks 自动安装 + 活动会话库 + transcript 多文件 tail 三通道
  - **chatgpt**：本机 Codex 会话状态轮询（SQLite 只读）
  - 状态归一化：未知事件一律忽略；行为策略：节流去抖、动作轮换、气泡文案
- **故障隔离**：Worker 崩溃/卡死只影响联动，桌宠本体不受影响；
  宿主按 1/2/4s 退避自动重启（60s 窗口最多 3 次），重启后快照自动重新同步

## 目录结构

```
desktop-pet/
└─ c++-python/                # 整个产品
   ├─ host/                   # C++/QML 宿主（渲染、交互、动作仲裁、Worker 进程管理）
   │  ├─ src/ qml/ assets/    #   源码、界面、角色素材
   │  └─ tests/               #   宿主测试（协议契约 / 动作仲裁 / 菜单结构等）
   ├─ protocol/               # 协议契约：信封 schema + 两端共用夹具（唯一判据）
   ├─ worker/                 # Python Worker（pet_worker 包，仅标准库，禁止 GUI 依赖）
   │  ├─ src/pet_worker/      #   生产代码
   │  └─ tests/               #   Worker 单测（含「零 GUI 依赖」硬约束验证）
   ├─ tests/                  # 跨进程集成测试（启动真实 Worker 子进程）
   ├─ packaging/              # 发布包说明
   └─ build_and_package.ps1   # 一键打包（自包含：Qt 运行时 + 素材 + ffmpeg + Worker）
```

## 快速开始

**使用**：下载 `desktop-pet-hybrid-portable.zip` 解压后双击 `desktop-pet.exe`
（包内自包含 Qt 运行时、角色素材、ffmpeg）。启用 Agent 联动需本机 Python 3.10+
（Worker 仅用标准库），详见包内 README。

**从源码构建**（依赖：MSYS2 MinGW64 (GCC 13+)、CMake 3.20+、Ninja、
Qt 6.8+：Core/Gui/Quick/Qml/Widgets/Network/Sql/Concurrent）：

```powershell
# 构建（CMAKE_PREFIX_PATH 指向你的 Qt6 安装）
cmake -G "Ninja" -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=<Qt安装路径> -B c++-python\host\build c++-python\host
cmake --build c++-python\host\build

# C++ 测试（协议契约 / 动作仲裁 / 配置兼容 / 菜单结构 / 渲染冒烟）
ctest --test-dir c++-python\host\build --output-on-failure

# Worker 单测（独立虚拟环境，验证「零 GUI 依赖」硬约束）
cd c++-python\worker && python -m venv .venv && .venv\Scripts\python -m pip install pytest
.venv\Scripts\python -m pytest worker\tests -q

# 跨进程集成测试（每个用例启动真实 Worker 子进程）
cd c++-python && worker\.venv\Scripts\python -m pytest tests -q
```

**打包**：

```powershell
powershell -File c++-python\build_and_package.ps1
# 产物：c++-python\dist\desktop-pet-hybrid\  与  desktop-pet-hybrid-portable.zip
```

## 配置

配置文件：`%APPDATA%\desktop-pet\config.json`（**宿主是唯一写者**，Worker 只读快照、
只能提交带 revision 的白名单 patch）。

| 键 | 默认 | 说明 |
|---|---|---|
| `python_worker.{enabled,python_path,worker_entry}` | true / `python` / 自动探测 | Worker 进程参数。Worker 是唯一的 Agent 业务实现（custom + antigravity + chatgpt）；Python 不可用时仅 Agent 联动停用，桌宠本体不受影响 |
| `agent_link.*` | — | antigravity / chatgpt / custom_agents 启停、notify_* 提醒开关、thinking_texts 气泡文案 |

其余键（自言自语、5 套气泡样式、点击台词绑定、多角色、多屏记忆、缩放、全屏自动
隐藏等）与原 Python 版同名同义。

## 架构速览

- **C++ 宿主**：渲染/物理/拖拽/气泡窗口、Worker 进程生命周期（按需启动、握手超时、
  退避重启、Windows Job Object 兜底回收）、协议分帧与背压、**动作仲裁**
  （拖拽锁 / 代次 / TTL / 动作白名单 / 气泡让路）、配置唯一写者。
- **Python Worker**：custom agent 增量监听、antigravity 三通道适配、chatgpt 状态
  轮询、状态归一化、行为策略——只**建议**不执行，由宿主仲裁后执行。
- **通信契约**：信封（协议版本 / 会话 / 序号 / kind / 类型 / 载荷）+ 分帧规则 +
  背压上限，全部以 `protocol/` 内 schema 与夹具为准；两端各自实现、共用测试。

## License

见 [LICENSE](LICENSE)。
