# Mini Claude

一个轻量级 AI 编码助手，基于 **ReAct（推理 + 行动）** 架构，通过工具调用与代码库交互。

## 架构

```
用户输入
    │
    ▼
┌────────────────────────────────────────┐
│  ReAct 循环 (agent.py)                 │
│                                        │
│  ① LLM 思考 → 决定调用哪个工具         │
│  ② 执行工具（grep/glob/read/write 等） │
│  ③ 工具结果送回 LLM                    │
│  ④ 循环直到 LLM 给出最终答案           │
│  （最多 20 次迭代）                     │
└────────────────────────────────────────┘
```

核心逻辑：模型自主决定"该做什么操作、查什么代码、读什么文件"，然后根据结果继续推理。

## 功能

### ReAct Agent
- 标准的 `while True` 循环：调用 LLM → 解析 tool_calls → 执行工具 → 追加结果 → 继续
- 使用 [DashScope API](https://help.aliyun.com/zh/model-studio/)（通义千问 Qwen 系列模型）
- 兼容 OpenAI SDK 调用格式

### 8 个内置工具
| 工具 | 作用 |
|------|------|
| `grep_search` | 在文件中搜索匹配正则的内容行 |
| `glob_search` | 使用 glob 模式匹配文件路径 |
| `read_file` | 读取文件内容（带缓存避免重复读） |
| `write_file` | 写入文件，自动创建目录 |
| `run_bash` | 执行 shell 命令 |
| `search_knowledge` | 语义搜索本地知识库（RAG） |
| `run_skill` | 加载并执行 skill 指令 |
| `check_health` | 检查各模块状态，出错时定位问题模块 |

### RAG 知识库

**索引构建**
- `multimodal-embedding-v1`（阿里百炼）生成向量（1024 维）
- FAISS IndexFlatL2 本地索引，无需外部服务
- 自动按段落/句切块（2000 字/块），支持中英文
- 动态批量 + 断点续传：中途中断不丢进度，下次重建从断点继续
- 命令：`/kb rebuild` 建索引，`/kb status` 查看状态

**检索管线**（三阶段）
```
用户提问 → ① LLM 查询改写 → ② embedding 粗筛(20条) + 关键词补漏
         → ③ 合并去重 → ④ gte-rerank-v2 精排 → 返回 Top-5
```
- **查询改写**：LLM 将自然语言转为密集检索关键词，提取人名/地名/事件
- **混合检索**：语义搜索 + 子串关键词匹配，互补补漏
- **重排序**：`gte-rerank-v2` 对候选文档精排，显著提升命中精度

### Skills 技能系统
- YAML frontmatter 格式的 SKILL.md 文件存放在 `~/.mini_claude/skills/<name>/`
- 技能目录可附带参考文件
- 模型可自主调用 `run_skill` 加载技能指令
- 单个description 不超过300字符，所有description不超过上下文窗口的1%

### MCP（Model Context Protocol）集成
- 支持 MCP 服务器通过 **stdio** 和 **streamable_http** 两种 transport 接入
- 配置存放于 `~/.mini_claude/mcp.json`，格式：
  ```json
  {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:/projects"],
        "env": {}
      }
    }
  }
  ```
- **启动时拉起所有服务器**，自动发现工具列表，合并到 agent 的工具列表
- MCP 工具通过前缀 `{server_name}__{tool_name}` 避免名称冲突
- 调用失败时返回错误信息给模型，由模型决定下一步
- 退出时自动 kill 所有 MCP 子进程
- ⚠️ **已知限制**：MCP 工具返回的 `image` 类型内容无法处理，仅有 `text` 类型会被传递给模型

### 上下文管理
- `/context` — 查看当前上下文使用情况（估算 token、消息数、角色分布）
- `/compact` — 用 LLM 压缩对话历史为摘要，释放上下文空间

### 交互体验
- **智能命令面板** — 输入 `/` 弹出下拉框，实时过滤可执行命令（按前缀匹配）
  ```
  > /c
  ─────────────────────────────────────────
    ▸ /clear       清空对话历史
      /compact     压缩对话历史
      /context     查看上下文使用情况
  ─────────────────────────────────────────
  (↑↓ 选择, Enter 自动补全, Tab 快速补全, Esc 关闭)
  ```
- **Enter 自动补全** — 回车时下拉框选中项自动填入缓冲区
- **Tab 快速补全** — 将选中项填入缓冲区继续编辑
- **↑↓ 浏览输入历史**（无下拉时），无下拉时 ↑↓ 切换历史输入
- **Ctrl+U** 一键清空当前输入行
- **Interactive Skill Picker** — 输入 `/skills` 后可用 **↑↓ 方向键**选择 skill，Enter 执行，q/Esc 取消
- **ANSI 彩色输出** — 全彩的命令、高亮、灰化辅助文字
- **"thinking... 已思考 X 秒"** 动画（后台线程，AI 思考时实时更新）
- **终端兼容** — ANSI 光标定位方案已针对 Windows Terminal 优化，避免下拉框残留和视口越界崩溃

### 检测 / 健康检查模块（health.py）

问题定位与状态检测的双通道机制，纯本地、零埋点、不侵入其他模块。

**三个入口：**
- `/health` — 全静态体检，零成本秒回。检查项：config（文件存在/可解析/key 已填）、api（base_url + key 静态校验）、mcp（配置 vs 实际连接数）、knowledge（索引存在/块数/索引过期提醒）、tools（定义与实现一一对应）、skills（可解析/数量/超限截断）
- `/ping` — 活体探测（消耗少量 token），三探针全跑、逐条独立报告延迟：
  - `net`：TCP+TLS 可达 `base_url`（0 token）
  - `llm`：最小 `chat.completions` 请求验证 key/模型/额度
  - `embedding`：单文本调 embedding API 验证额度
- `check_health` 工具（第 8 个内置工具）— 模型在对话中收到工具错误字符串时，可自调此工具做差分诊断，定位问题模块

**异常自动诊断：** main.py 在捕获异常时自动触发（run_turn 的 LLM 调用 + MCP 初始化两处）。按异常类型归类疑似模块（连接失败/超时 → 网络层、限流 → 配额、鉴权 → config…），并列出体检中不健康的模块，每条附下一步建议（如「运行 /ping 验证网络」）。全部健康时只打一行提示，不刷屏。

**设计要点：**
- 每个 check 自带 try/except，某模块依赖缺失时降级为「无法检查」而非拖垮整表
- MCP 连接状态通过 `set_mcp_manager()` 注入共享，命令路径与工具路径看到的是同一份状态
- 只读检查，不写文件、不发请求（除 `/ping`）

### 配置系统
- 配置文件：`~/.mini_claude/config.json`
- 环境变量覆盖：`DASHSCOPE_API_KEY`、`MINI_CLAUDE_MODEL`、`MINI_CLAUDE_BASE_URL`、`MINI_CLAUDE_MAX_TOKENS`
- 优先级：环境变量 > 配置文件 > 默认值
- 首次运行自动创建配置模板

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

首次运行会自动创建配置文件模板：

```bash
python -m mini_claude.main
```

编辑 `~/.mini_claude/config.json`，填入 API Key：

```json
{
    "api_key": "sk-ws-...",
    "model": "qwen3.7-plus",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "max_tokens": 4096
}
```

也可通过环境变量设置：

```bash
export DASHSCOPE_API_KEY="sk-ws-..."
```

### 3. 启动

```bash
python -m mini_claude.main
```

建议设置别名：

```bash
alias miniclaude="python -m mini_claude.main"
```

## 项目结构

```
mini_claude/
├── __init__.py
├── agent.py        # ReAct 循环核心
├── config.py       # 配置读取（文件 + 环境变量）
├── health.py       # 检测模块（/health、/ping、check_health 工具、异常诊断）
├── knowledge.py    # RAG 知识库（切块、embedding、FAISS 检索）
├── main.py         # REPL 入口（命令处理、交互界面）
├── mcp_manager.py  # MCP 服务器管理（启动、工具发现、调用、关闭）
├── skills.py       # Skills 系统（发现、解析、执行）
└── tools.py        # 8 个工具的定义和实现
requirements.txt
miniclaude          # shell 启动脚本
miniclaude.cmd      # Windows 启动脚本
README.md
```

## 命令

| 命令 | 作用 |
|------|------|
| `/exit` | 退出 |
| `/clear` | 清空对话历史 |
| `/context` | 查看上下文使用情况 |
| `/compact` | 压缩对话历史 |
| `/tools` | 列出可用工具 |
| `/skills` | 交互式选择并执行 skill |
| `/kb rebuild` | 重建知识库索引 |
| `/kb status` | 查看知识库状态 |
| `/health` | 检查各模块状态（静态体检） |
| `/ping` | 活体探测 API 可用性（net/llm/embedding） |
| `<skill_name>` | 直接运行 skill |

## 依赖

- Python ≥ 3.10
- openai ≥ 1.0.0
- faiss-cpu ≥ 1.8.0
- numpy ≥ 1.24
- requests
- mcp ≥ 1.28

## 模型适配

默认使用阿里百炼 DashScope API，如需适配其他 OpenAI 兼容 API：

```bash
export MINI_CLAUDE_BASE_URL="https://your-api-endpoint/v1"
export MINI_CLAUDE_MODEL="your-model-name"
```

## 已知限制

- 无记忆/持久化存储
- 无多 Agent 协作
- 无流式输出（Streaming）
- 知识库索引需手动重建（`/kb rebuild`）
- MCP 集成：仅支持 stdio transport，仅处理 text 类型内容（image/resource 类型无法传给模型）
