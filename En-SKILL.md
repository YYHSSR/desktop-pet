---
name: En-SKILL.md
description: "通用工程开发交付、环境工具链规范、文档预处理与 AI 产出治理规范"
---

# En-SKILL 通用工程开发与产出治理规范

本规范适用于所有 AI 助手（Antigravity、Cursor、Claude、Copilot、ChatGPT 等），作为在当前工作区进行**代码开发**、**编译调试**、**文档预处理**与**产出治理**的通用执行标准。

---

## 1. 本地工具链与环境规范

执行任何构建、编译、脚本运行或依赖检测时，**必须优先调用以下本地工具链**：

| 工具链 | 核心路径 | 说明与推荐调用方式 |
| :--- | :--- | :--- |
| **Python 12** | `D:\python\miniconda\envs\py12\python.exe` | 包含项目 Python 依赖及 `markitdown`，Scripts 目录为 `D:\python\miniconda\envs\py12\Scripts` |
| **C++ (MSYS2)** | `E:\msys\msys` | 主要使用 MinGW64 工具链（`E:\msys\msys\mingw64\bin`，含 `g++`, `gcc`, `cmake`, `ninja`, `windeployqt`） |
| **Node.js / pnpm** | `E:\nodejs` | Node.js 运行时（`E:\nodejs\node.exe`），pnpm 位于 `%APPDATA%\npm\pnpm.cmd` |

> **终端调用标准（PowerShell 示例）**：
> ```powershell
> # 1. Python 12 运行
> & 'D:\python\miniconda\envs\py12\python.exe' -m <module_or_script>
> 
> # 2. C++ 编译与打包环境变量
> $env:PATH = "E:\msys\msys\mingw64\bin;D:\python\miniconda\envs\py12\Scripts;$env:PATH"
> 
> # 3. Node.js / pnpm 环境调用
> $env:PATH = "E:\nodejs;C:\Users\zf\AppData\Roaming\npm;$env:PATH"
> ```

---

## 2. AI 产出统一治理目录 (`AI/`)

**所有由 AI 生成的任何文档、报告、转换产物或过渡数据，必须统一收纳于工作区根目录的 `AI/` 文件夹中**（不存在时自动创建），严禁直接散落在工作区根目录：

```text
工作区根目录/
└── AI/                     # AI 专属产出根目录（按需自动创建）
    ├── reports/            # 任务总结报告 (Walkthrough)、技术方案与设计文档 (.md)
    ├── markdown/           # MarkItDown 转换文档及 Markdown 衍生分析产物
    ├── extractions/        # 中间数据、JSON 结构化提取产物等
    └── temp/               # 调试过渡文件、临时日志（任务结束必须清理）
```

### 产出治理红线：
1. **子目录分类管理**：根据产物类型自动建立/归入对应子目录（如 `markdown/`、`reports/` 等），严禁随意混合。
2. **文件名语义清晰**：命名必须清晰易懂、表意明确（例：`AI/reports/2026-09-18_walkthrough_桌宠右键交互重构.md`，禁止 `temp.md`、`1.txt` 等无意义命名）。
3. **临时文件清理**：任务完成或交付前，必须主动清空 `AI/temp/` 下的过程缓存与无效日志。
4. **用户源码保护**：严禁未经用户授权擅自改动或删除非 AI 产生的用户源码与核心业务配置。

---

## 3. 非 Markdown 文档转换流程（MarkItDown）

当接收到非 Markdown 格式的文档（PDF、HTML、富文本等）分析任务时：

1. **前置确认**：向用户主动确认：
   > *"检测到非 Markdown 文档任务，是否先使用 MarkItDown 将其转换为 Markdown（保存至 `AI/markdown/` 目录）以进行精准分析？"*
2. **标准转换流程**（使用 Python 12 环境）：
   ```powershell
   if (!(Test-Path "AI/markdown")) { New-Item -ItemType Directory -Path "AI/markdown" | Out-Null }
   & 'D:\python\miniconda\envs\py12\python.exe' -m markitdown "<input_file>" > "AI/markdown/<filename>.md"
   ```

---

## 4. 任务总结报告机制 (Walkthrough / 视情况而定 / 主动询问)

**核心准则**：**报告生成视情况而定，严禁每次未经确认强制自动生成**。
- **主动询问机制**：在完成代码编辑、功能实现、重构优化或重要工程任务后，AI 应在交付时主动询问用户：
  > *"本次任务已完成，是否需要在 `AI/reports/` 下生成一份详细的 Walkthrough 任务总结报告？"*
- **按需执行**：仅当用户明确要求或确认同意后，AI 才在 `AI/reports/` 目录下创建报告；日常微调、简单答疑无需生成。

### Walkthrough 总结报告规范（确认需要时执行）：
- **路径与命名**：`AI/reports/<YYYY-MM-DD_walkthrough_任务主题简述>.md`
- **报告必备核心内容**：
  ```markdown
  # Walkthrough 任务总结：[任务主题简述]

  ## 1. 任务背景与目标
  - 简要陈述本次任务要解决的核心问题或用户诉求。

  ## 2. 具体工作与改动清单 (Changes Made)
  - 修改/新增的核心文件列表。
  - 关键代码实现细节、重构方案与解决的核心技术难点。

  ## 3. 实现效果与交付成果 (Results & Deliverables)
  - 功能实现情况与改进成果。
  - 交付文件路径（如可执行文件、打包产物、配置说明等）。

  ## 4. 验证方式与测试结果 (Verification & Tests)
  - 采用的测试验证命令、编译状态与运行结果。

  ## 5. 注意事项与后续建议 (Next Steps)
  - 遗留假设、使用提醒或建议下一步的优化方向。
  ```
