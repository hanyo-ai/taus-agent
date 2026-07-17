<div align="center">
  <img src="assets/taus.svg" alt="TAUS Agent Logo" width="160" />
  <h1>TAUS Agent</h1>
  <p>
    <a href="README.md">English</a> · <strong>中文</strong>
  </p>
  <p>
    <em>轻量、可扩展的多智能体 AI 框架</em>
  </p>
  <p>
    内置工具集 · 持久化记忆 · 智能体间通信
  </p>
  <p>
    <img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" />
    <img src="https://img.shields.io/badge/python-≥3.14-3776AB.svg" alt="Python Version" />
    <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Status" />
  </p>
</div>

---

## ✨ 特性一览

| 特性 | 描述 |
|---|---|
| 🧰 **内置工具集** | `read`、`bash`、`edit`、`write`、`create_agent` 开箱即用 |
| 🧠 **持久化记忆** | 自动生成 `MEMORY.md`，跨会话摘要注入 |
| 🛠️ **技能系统** | 从 `skills/<name>/SKILL.md` 热加载技能 |
| 📬 **消息总线** | 点对点 & 广播通信，附带 HTTP 网关 |
| 📦 **上下文压缩** | 对话过长时自动压缩摘要 |
| 🤖 **子 Agent** | 运行时通过 `create_agent` 创建隔离子代理 |
| 🖥️ **REPL & HTTP API** | 终端交互式对话 & REST/WebSocket 接口 |
| 🌐 **浏览器自动化** | 基于 CDP 的浏览器控制（导航、截图、JS 执行） |
| 🧩 **多模型支持** | Anthropic、OpenAI 及兼容后端 |

---

## 📦 安装

```bash
# 克隆仓库
git clone https://github.com/your-org/taus-agent.git
cd taus-agent

# 要求 Python ≥ 3.14
python --version

# 使用 uv 安装（推荐）
pip install uv
uv sync

# 或使用 pip
pip install -e .
```

## ⚙️ 配置

在项目根目录创建 `.env` 文件：

```bash
# 必需：模型端点
MODEL="claude-sonnet-4-6"
BASE_URL="http://127.0.0.1:3000/anthropic"
API_KEY="your-api-key"

# 可选：启用推理模式
THINKING=1

# 可选：提供商 (anthropic, openai)
# PROVIDER=openai
```

---

## 🚀 快速开始

### REPL 模式

```bash
python main.py
```

直接在终端与 Agent 对话，所有内置工具和技能随取随用。

> **💡 提示：** 如果你开启了系统代理（如 `HTTP_PROXY`、`HTTPS_PROXY`），Agent 可能无法连接本地 API 端点。可使用以下命令绕过所有代理：
>
> ```bash
> no_proxy=* NO_PROXY=* python main.py
> ```

### Python API

```python
import asyncio
from src.agent.core import Agent
from src.agent.llm import ClientConfig

config = ClientConfig(
    model="claude-sonnet-4-6",
    api_key="your-key",
    base_url="http://127.0.0.1:3000/anthropic",
)

agent = Agent(config, agent_name="my-agent", skills_dir="skills")
response = await agent.run("你好，你能做什么？")
print(response)
```

### HTTP / WebSocket 服务

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

HTTP 网关允许外部服务向总线上的 Agent 发送消息。

---

## 🧠 架构

```
┌─────────────────────────────────────────────────┐
│                    main.py                       │
│  ┌──────────────┐       ┌──────────────────┐    │
│  │  Bus Agent    │       │   REPL Agent     │    │
│  │  (endpoint)   │ ◄────►│   (终端)         │    │
│  └──────┬───────┘       └──────────────────┘    │
│         │                                        │
│  ┌──────┴───────────────────────────────────┐   │
│  │          消息总线 (Message Bus)            │   │
│  │  点对点 · 广播 · 日志                      │   │
│  └──────┬───────────────────────────────────┘   │
│         │                                        │
│  ┌──────┴───────┐   ┌──────────────────┐        │
│  │  HTTP Gateway│   │   Agent Runner    │        │
│  │  (FastAPI)   │   │  (进程内)         │        │
│  └──────────────┘   └──────────────────┘        │
└─────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 路径 | 职责 |
|---|---|---|
| **Agent Core** | `src/agent/core.py` | Agent 生命周期、工具调度、上下文管理 |
| **LLM Client** | `src/agent/llm.py` | 多提供商 AI 客户端 |
| **Message Bus** | `src/agent/mbus/` | 智能体间通信、HTTP 网关、Agent 运行器 |
| **Tool System** | `src/agent/tool.py` | 工具注册、Schema 生成、核心工具定义 |
| **Persistence** | `src/agent/persistence.py` | 会话管理、状态序列化 |
| **Context** | `src/agent/context.py` | 对话历史与自动压缩 |
| **Skill Loader** | `src/agent/skill_loader.py` | 热加载技能 Markdown |
| **Browser** | `src/browser/` | CDP 浏览器自动化 |
| **REPL** | `src/utils/repl.py` | 交互式终端提示 |

---

## 🛠️ 工具系统

每个 Agent 内置以下核心工具：

| 工具 | 描述 |
|---|---|
| `read` | 读取文件内容（支持行号 & 分页） |
| `bash` | 执行 Shell 命令（支持超时控制） |
| `edit` | 通过文本替换精确编辑文件 |
| `write` | 创建或覆写文件 |
| `create_agent` | 创建拥有独立上下文和技能的 Agent |

工具定义在 `src/agent/tools/` 目录，通过 `ToolRegistry` 注册。每个工具是一个 Python 函数，类型注解自动转换为 LLM 可识别的工具 Schema。

---

## 🧩 技能系统

技能是放置在 `skills/<name>/SKILL.md` 的 Markdown 指令文件，可在运行时加载：

```
skills/
├── news-aggregator/SKILL.md       # 多源新闻聚合
├── ble-scanner/SKILL.md           # BLE 设备扫描与控制
├── xiaohongshu-scraper/SKILL.md   # 小红书内容抓取
├── x-poster/SKILL.md              # X/Twitter 自动发帖
└── browser-automation/SKILL.md    # CDP 浏览器自动化
```

在对话中使用 `load_skill` 工具即可动态加载技能，其内容会注入到系统提示词中。

---

## 💾 持久化记忆

每个 Agent 在 `.agents/<name>/` 下维护一个 `MEMORY.md` 文件：

```
.agents/
├── main/
│   ├── agent.json          # 会话状态
│   └── MEMORY.md           # 跨会话用户上下文
└── repl/
    ├── agent.json
    └── MEMORY.md
```

- **摘要注入**：第一个 `---` 分隔线以上的内容自动注入每次对话
- **按需详查**：用 `read MEMORY.md` 查看完整内容
- **自动模板**：首次运行自动创建

---

## 📬 消息总线

`MessageBus` 实现了 Agent 之间的通信：

```python
from src.agent.mbus import MessageBus, Message

bus = MessageBus()

bus.register("agent-a")
bus.register("agent-b")

await bus.send(Message(sender="agent-a", recipient="agent-b", content="你好！"))

await bus.broadcast(Message(sender="agent-a", recipient="*", content="大家好！"))
```

通过 `HttpGateway` 可将消息总线暴露为 FastAPI 接口，供外部系统集成。

---

## 📁 项目结构

```
taus-agent/
├── main.py                    # 入口（REPL + 总线 Agent）
├── pyproject.toml             # 项目元数据 & 依赖
├── README.md                  # 英文文档
├── README.zh.md               # 中文文档
├── LICENSE                    # Apache 2.0
├── .env                       # 环境配置
├── assets/
│   └── taus.svg              # Logo
├── src/
│   ├── agent/
│   │   ├── core.py           # Agent 核心
│   │   ├── llm.py            # LLM 客户端（多提供商）
│   │   ├── context.py        # 对话上下文
│   │   ├── prompts.py        # 系统提示词 & 记忆模板
│   │   ├── tool.py           # 工具注册
│   │   ├── persistence.py    # 会话持久化
│   │   ├── skill_loader.py   # 技能加载
│   │   ├── tools/            # 内置工具实现
│   │   └── mbus/             # 消息总线 & HTTP 网关
│   ├── browser/              # CDP 浏览器自动化
│   └── utils/                # REPL、自动补全
├── skills/                   # 可加载的技能定义
├── prompts/                  # 额外的提示词文件
├── examples/                 # 使用示例
└── tests/                    # 测试套件
```

---

## 📚 示例

| 示例 | 文件 | 描述 |
|---|---|---|
| AI 新闻 & 发帖 | `examples/ai_news_and_post.py` | 聚合新闻并自动发布到 X |
| 百度搜索 | `examples/baidu_search.py` | 基于浏览器的百度搜索 |
| X 自动回复 | `examples/x_auto_reply.py` | X/Twitter 自动回复 |
| X Grok 回复 | `examples/x_grok_reply.py` | Grok 驱动的 X 回复 |
| 新闻第一步 | `examples/step1_news.py` | 单步新闻聚合 |

---

## 🗺️ 开发路线

- [x] **内置工具集** — `read`、`bash`、`edit`、`write`、`create_agent`
- [x] **上下文压缩** — 长对话自动摘要
- [x] **技能加载** — 热加载 `skills/<name>/SKILL.md`
- [x] **持久化记忆** — `MEMORY.md` 摘要注入
- [x] **子 Agent** — `create_agent` 创建隔离代理
- [x] **消息总线** — 智能体间通信 & HTTP 网关
- [ ] **CLI REPL / HTTP API**（持续改进）
- [ ] **模型自动切换**
- [ ] **Agent 管理器** — 链路追踪支持
- [ ] **Agent 群组** — 手动群组创建
- [ ] **渐进式记忆披露**
- [ ] **REST API** — 全量外部交互
- [ ] **SQLite 存储**
- [ ] **Office CLI & 浏览器集成**

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！请确保：

1. 代码风格与项目保持一致
2. 为新功能添加测试用例
3. 同步更新文档

---

## 📄 许可

本项目基于 [Apache 2.0](LICENSE) 许可证开源。

---

<br />

<div align="center">
  <sub>用心构筑 ❤️</sub>
</div>
