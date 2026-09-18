# Desktop Pet 混合版（C++ 界面 + Python 业务）

一个 exe：C++/QML 负责桌宠渲染与交互，Python Worker 作为按需启动的后台进程负责 Agent 业务（事件监听、状态归一化、行为建议）。**本包自包含**：角色素材与 ffmpeg 已内置；`agent_backend` 默认 `native`，不配置 Python 也能完整使用桌宠。

## 启动

双击 `desktop-pet.exe` 即可（native 模式，与单独 C++ 版行为一致）。

## 启用 Python 后端（Agent 联动）

1. 本机安装 Python 3.10+（Worker 仅用标准库，无需 pip 安装任何包）。
2. 编辑数据目录下的 `config.json`（`%APPDATA%\desktop-pet\config.json`）：

```json
{
  "agent_backend": "python",
  "python_worker": {
    "enabled": true,
    "python_path": "python",
    "worker_entry": ""
  },
  "agent_link": {
    "custom_agents": [
      { "key": "myagent", "name": "我的Agent", "path": "C:/path/to/事件文件.jsonl" }
    ],
    "myagent": true
  }
}
```

3. 重启桌宠。Worker 会按需拉起；向事件文件追加统一协议 JSONL（如
   `{"event":"PreToolUse","tool":"bash"}`、`{"event":"Stop"}`）即可看到桌宠反馈。

## 说明

- Worker 入口留空时自动探测本包内 `c++-python/worker/src/pet_worker/__main__.py`。
- `agent_backend` 改回 `native` 立即回退纯 C++ 行为，Worker 自动停止。
- Worker 崩溃/卡死不影响桌宠：宿主按 1/2/4s 退避自动重启（60s 内最多 3 次）。
- 诊断日志：`%APPDATA%\desktop-pet\worker-stderr.log`。
- 鼠标交互：点在角色本体（非透明像素）触发随机点击回应；透明画布区域不响应；
  右键弹菜单；拖拽移动。
