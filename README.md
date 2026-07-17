<div align="center">
  <img src="assets/taus.svg" alt="TAUS Agent Logo" width="160" />
  <h1>TAUS Agent</h1>
  <p>
    <strong>English</strong> · <a href="README.zh.md">中文</a>
  </p>
  <p>
    <em>A lightweight, extensible multi-agent AI framework with built-in tools, persistent memory, and inter-agent messaging.</em>
  </p>
  <p>
    <img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" />
    <img src="https://img.shields.io/badge/python-≥3.14-3776AB.svg" alt="Python Version" />
    <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Status" />
  </p>
</div>

---

## ✨ Features

| Feature | Description |
|---|---|
| 🧰 **Built-in Toolset** | `read`, `bash`, `edit`, `write`, `create_agent` — fully integrated |
| 🧠 **Persistent Memory** | Auto-generated `MEMORY.md` per agent with cross-session summarization |
| 🛠️ **Skill System** | Hot-loadable skills from `skills/<name>/SKILL.md` |
| 📬 **Message Bus** | Inter-agent point-to-point & broadcast messaging with HTTP gateway |
| 📦 **Context Compression** | Automatic summarization when conversation grows too long |
| 🤖 **Sub-Agent Spawning** | Spawn isolated sub-agents at runtime via `create_agent` |
| 🖥️ **REPL & HTTP API** | Interactive terminal & REST/WebSocket endpoints |
| 🌐 **Browser Automation** | CDP-based browser control (navigate, screenshot, JS eval, DOM ops) |
| 🧩 **Multi-Provider LLM** | Anthropic, OpenAI, and any OpenAI-compatible backend |

---

## 📦 Installation

```bash
# Clone the repository
git clone https://github.com/your-org/taus-agent.git
cd taus-agent

# Requires Python 3.14+
python --version  # should be >= 3.14

# Install with uv (recommended)
pip install uv
uv sync

# Or with pip
pip install -e .
```

## ⚙️ Configuration

Create a `.env` file in the project root:

```bash
# Required: Model endpoint
MODEL="claude-sonnet-4-6"
BASE_URL="http://127.0.0.1:3000/anthropic"
API_KEY="your-api-key"

# Optional: enable reasoning mode
THINKING=1

# Optional: provider (anthropic, openai)
# PROVIDER=openai
```

---

## 🚀 Quick Start

### REPL Mode

```bash
python main.py
```

Start chatting with the agent directly in your terminal — it has access to all built-in tools and loaded skills.

> **💡 Tip:** If you have system proxy enabled (e.g. `HTTP_PROXY`, `HTTPS_PROXY`), the agent may fail to connect to local API endpoints. Run with `no_proxy=*` to bypass all proxies:
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
response = await agent.run("Hello, what can you do?")
print(response)
```

### HTTP / WebSocket Server

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

The HTTP gateway enables external services to send messages to agents on the bus.

---

## 🧠 Architecture

```
┌─────────────────────────────────────────────────┐
│                    main.py                       │
│  ┌──────────────┐       ┌──────────────────┐    │
│  │  Bus Agent    │       │   REPL Agent     │    │
│  │  (endpoint)   │ ◄────►│   (terminal)     │    │
│  └──────┬───────┘       └──────────────────┘    │
│         │                                        │
│  ┌──────┴───────────────────────────────────┐   │
│  │          Message Bus                      │   │
│  │  · Point-to-point · Broadcast · Logging   │   │
│  └──────┬───────────────────────────────────┘   │
│         │                                        │
│  ┌──────┴───────┐   ┌──────────────────┐        │
│  │  HTTP Gateway│   │   Agent Runner    │        │
│  │  (FastAPI)   │   │  (in-process)     │        │
│  └──────────────┘   └──────────────────┘        │
└─────────────────────────────────────────────────┘
```

### Core Modules

| Module | Path | Role |
|---|---|---|
| **Agent Core** | `src/agent/core.py` | Agent lifecycle, tool dispatch, context management |
| **LLM Client** | `src/agent/llm.py` | Multi-provider AI client (Anthropic / OpenAI) |
| **Message Bus** | `src/agent/mbus/` | Inter-agent messaging, HTTP gateway, agent runner |
| **Tool System** | `src/agent/tool.py` | Tool registry, schema generation, core tool definitions |
| **Persistence** | `src/agent/persistence.py` | Session management, state serialization |
| **Context** | `src/agent/context.py` | Conversation history with compression |
| **Skill Loader** | `src/agent/skill_loader.py` | Hot-load `<skill>/SKILL.md` as system instruction |
| **Browser** | `src/browser/` | CDP-based browser automation (page, element, mouse) |
| **REPL** | `src/utils/repl.py` | Interactive prompt with tab-completion |

---

## 🛠️ Tool System

Every agent comes with a core set of tools:

| Tool | Description |
|---|---|
| `read` | Read file contents with line numbers & pagination |
| `bash` | Execute shell commands (with timeout) |
| `edit` | Precise file editing via text replacement |
| `write` | Create or overwrite files |
| `create_agent` | Spawn a sub-agent with its own context & skills |

Tools are registered in `src/agent/tools/` and loaded through `ToolRegistry`. Each tool is a Python function with Pydantic-style type annotations that are automatically converted to LLM tool schemas.

---

## 🧩 Skill System

Skills are Markdown instructions placed in `skills/<name>/SKILL.md`. They can be loaded at runtime:

```
skills/
├── news-aggregator/SKILL.md      # Multi-source news aggregation
├── ble-scanner/SKILL.md          # BLE device scanning & control
├── xiaohongshu-scraper/SKILL.md  # RED (Xiaohongshu) scraping
├── x-poster/SKILL.md             # X/Twitter posting automation
└── browser-automation/SKILL.md   # CDP browser automation
```

Use the `load_skill` tool inside a conversation to dynamically load a skill — its contents are injected into the system prompt.

---

## 💾 Persistent Memory

Each agent maintains a `MEMORY.md` file under `.agents/<name>/`:

```
.agents/
├── main/
│   ├── agent.json          # Session state
│   └── MEMORY.md           # Cross-session user context
└── repl/
    ├── agent.json
    └── MEMORY.md
```

- **Summary injection**: The section above the first `---` is auto-injected into each conversation.
- **On-demand detail**: Use `read MEMORY.md` for full context when needed.
- **Auto-template**: Created automatically on first run.

---

## 📬 Message Bus

The `MessageBus` enables communication between agents:

```python
from src.agent.mbus import MessageBus, Message

bus = MessageBus()

# Register endpoints
bus.register("agent-a")
bus.register("agent-b")

# Send a message
await bus.send(Message(sender="agent-a", recipient="agent-b", content="Hello!"))

# Broadcast to all
await bus.broadcast(Message(sender="agent-a", recipient="*", content="Hi everyone!"))
```

An HTTP gateway (`HttpGateway`) exposes the bus via FastAPI for external integration.

---

## 📁 Project Structure

```
taus-agent/
├── main.py                    # Entry point (REPL + bus agent)
├── pyproject.toml             # Project metadata & dependencies
├── README.md                  # This file
├── LICENSE                    # Apache 2.0
├── .env                       # Environment configuration
├── assets/
│   └── taus.svg              # Logo
├── src/
│   ├── agent/
│   │   ├── core.py           # Agent core
│   │   ├── llm.py            # LLM client (multi-provider)
│   │   ├── context.py        # Conversation context
│   │   ├── prompts.py        # System prompts & memory template
│   │   ├── tool.py           # Tool registry
│   │   ├── persistence.py    # Session persistence
│   │   ├── skill_loader.py   # Skill loading
│   │   ├── tools/            # Built-in tool implementations
│   │   └── mbus/             # Message bus & HTTP gateway
│   ├── browser/              # CDP browser automation
│   └── utils/                # REPL, completer
├── skills/                   # Loadable skill definitions
├── prompts/                  # Additional prompt files
├── examples/                 # Usage examples
└── tests/                    # Test suite
```

---

## 📚 Examples

| Example | File | Description |
|---|---|---|
| AI News & Post | `examples/ai_news_and_post.py` | Aggregate news and auto-post to X |
| Baidu Search | `examples/baidu_search.py` | Browser-based Baidu search |
| X Auto Reply | `examples/x_auto_reply.py` | Automated replies on X/Twitter |
| X Grok Reply | `examples/x_grok_reply.py` | Grok-powered X replies |
| News Step 1 | `examples/step1_news.py` | Single-step news aggregation |

---

## 🗺️ Roadmap

- [x] **Built-in Toolset** — `read`, `bash`, `edit`, `write`, `create_agent`
- [x] **Context Compression** — Auto-summary for long conversations
- [x] **Skill Loading** — Hot-load `skills/<name>/SKILL.md`
- [x] **Persistent Memory** — `MEMORY.md` summary injection
- [x] **Sub-Agent Spawning** — `create_agent` for isolated sub-agents
- [x] **Message Bus** — Inter-agent communication & HTTP gateway
- [ ] **CLI REPL / HTTP API** (improvements)
- [ ] **Model Auto-Switching**
- [ ] **Agent Manager** — Span tracing support
- [ ] **Agent Groups** — Manual group creation
- [ ] **Progressive Memory Disclosure**
- [ ] **REST API** — Full external interaction
- [ ] **SQLite Storage**
- [ ] **Office CLI & Browser Integration**

---

## 🤝 Contributing

Contributions are welcome! Please ensure:

1. Code style is consistent with the project
2. Tests are added for new features
3. Documentation is updated accordingly

---

## 📄 License

This project is licensed under the [Apache 2.0 License](LICENSE).

---

<br />

<div align="center">
  <sub>Built with ❤️</sub>
</div>
