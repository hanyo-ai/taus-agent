"""Agent core with redesigned persistence - name-based, session-aware."""
import asyncio
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Any
from .llm import LLMClient, Usage, ClientConfig
from .context import Context
from .tool import ToolRegistry, build_core_registry, ToolDef
from .skill_loader import SkillLoader
from .prompts import SYSTEM_PROMPT, MEMORY_TEMPLATE
from .persistence import AgentPersistence


def _load_memory(memory_path: str | Path | None = None) -> tuple[str, str]:
    """Return (essential_summary, full_content) from MEMORY.md.

    Essential = only the section ABOVE the first `---` separator line
    (after stripping YAML frontmatter). Contains user info only.
    Credentials, conventions, decisions stay in the full file for on-demand read.
    If MEMORY.md does not exist, a template is created automatically.

    Args:
        memory_path: Path to MEMORY.md file. If None, uses "MEMORY.md" in current dir.
    """
    if memory_path is None:
        path = Path("MEMORY.md")
    else:
        path = Path(memory_path)

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MEMORY_TEMPLATE, encoding="utf-8")
        print(f"[memory] 已创建 {path} 模板，请编辑填写凭据和偏好。")
    try:
        content = path.read_text(encoding="utf-8").strip()
    except Exception:
        return "", ""

    lines = content.split("\n")

    # Find the first `---` that acts as a section separator (skip frontmatter opening)
    sep_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "---" and i > 0:
            sep_idx = i
            break

    if sep_idx is not None:
        essential = "\n".join(lines[:sep_idx]).strip()
        # Strip YAML frontmatter delimiters
        if essential.startswith("---"):
            essential = essential[3:].strip()
    else:
        essential = content

    return essential, content


def _build_system_prompt(
    template_path: str = "prompts/system.md",
    skill_loader: SkillLoader | None = None,
    memory_path: str | Path | None = None,
    is_child: bool = False,
    ) -> str:
    # Sub-agents use a different template without create_agent
    if is_child:
        template_path = "prompts/system_sub.md"

    try:
        template = Path(template_path).read_text(encoding="utf-8")

    except Exception:
        template = "You are a helpful assistant"

    # Skills
    if skill_loader:
        section = skill_loader.system_prompt_section()
        template = template.replace("{{SKILLS}}", section if section else "（无）")
    else:
        template = template.replace("{{SKILLS}}", "（无）")


    essential, _ = _load_memory(memory_path)
    if essential:
        template += (
            f"{essential}\n"
        )

    return template


class Agent:
    def __init__(
        self,
        config: ClientConfig,
        *,
        agent_name: str | None = None,
        skills_dir: str = "skills",
        on_text: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
        memory_path: str | Path | None = None,
        auto_save_session: bool = True,
        is_child: bool = False,
        bus: Any = None,
        endpoint_name: str | None = None,
        ):
        """Initialize Agent.

        Args:
            config: LLM client configuration
            agent_name: Agent name (for persistence). If None, creates temp agent.
            skills_dir: Path to skills directory
            on_text: Callback for text output
            on_thinking: Callback for thinking output
            on_tool_call: Callback for tool calls
            memory_path: Path to MEMORY.md (auto-set if agent_name provided)
            auto_save_session: Auto-save session after each run
            is_child: True for sub-agents loaded via /load (no create_agent tool)
            bus: Message bus for inter-agent communication (optional)
            endpoint_name: Endpoint name on the bus (optional)
        """
        self.agent_name = agent_name or "temp_agent"
        self.skills_dir = skills_dir
        self.auto_save_session = auto_save_session
        self.is_child = is_child
        self.session_id: str | None = None
        self.bus = bus
        self.endpoint_name = endpoint_name
        self._loaded_skills: set[str] = set()  # Track skills loaded via load_skill

        self.llm = LLMClient(config=config)
        self.context = Context()
        self.registry = build_core_registry()

        # Set memory path
        if agent_name and memory_path is None:
            # Use agent's own MEMORY.md
            self.memory_path = str(AgentPersistence.get_memory_file(agent_name))
        else:
            self.memory_path = memory_path

        # Register context-aware tools (must be after setting memory_path)
        self._register_context_aware_tools()

        self.skill_loader = SkillLoader(skills_dir)
        if self.skill_loader.available():
            self._register_load_skill()

        self.on_text = on_text or (lambda t: print(t, end="", flush=True))
        self.on_thinking = on_thinking or self._default_on_thinking
        self.on_tool_call = on_tool_call or (lambda name, inp: print(f"\n[tool: {name}] ", end="", flush=True))

        self.llm.cfg.system_prompt = _build_system_prompt(
            skill_loader=self.skill_loader,
            memory_path=self.memory_path,
            is_child=self.is_child,
        )

    def _register_load_skill(self) -> None:
        """Register the load_skill tool so the model can fetch full skill instructions."""
        schema = self.skill_loader.skill_tool_schema()

        async def handler(inp: dict) -> str:
            skill_name = inp["name"]
            self._loaded_skills.add(skill_name)  # Track loaded skill
            return self.skill_loader.load(skill_name)

        self.registry.register(ToolDef(schema=schema, handler=handler))

    def _register_context_aware_tools(self) -> None:
        """Register tools that need agent context (memory path, skills dir, etc)."""
        from .tools import READ_SCHEMA, EDIT_SCHEMA, WRITE_SCHEMA, BASH_SCHEMA, CREATE_AGENT_SCHEMA
        from .tools import _read_handler, _edit_handler, _write_handler, _bash_handler, _create_agent_handler

        # Wrap handlers to provide agent context
        async def read_with_context(inp: dict) -> str:
            # If path is MEMORY.md and agent has memory_path, redirect to it
            modified_inp = inp.copy()
            if inp.get("path") == "MEMORY.md" and self.memory_path:
                modified_inp["path"] = self.memory_path
            return await _read_handler(modified_inp)

        async def edit_with_context(inp: dict) -> str:
            # If path is MEMORY.md and agent has memory_path, redirect to it
            modified_inp = inp.copy()
            if inp.get("path") == "MEMORY.md" and self.memory_path:
                modified_inp["path"] = self.memory_path
            return await _edit_handler(modified_inp)

        async def write_with_context(inp: dict) -> str:
            # If path is MEMORY.md and agent has memory_path, redirect to it
            modified_inp = inp.copy()
            if inp.get("path") == "MEMORY.md" and self.memory_path:
                modified_inp["path"] = self.memory_path
            return await _write_handler(modified_inp)

        async def create_agent_with_context(inp: dict) -> str:
            # Pass agent instance to the handler
            return await _create_agent_handler(inp, self)

        # Register core tools (read, write, edit, bash, create_agent)
        self.registry.register(ToolDef(schema=READ_SCHEMA, handler=read_with_context))
        self.registry.register(ToolDef(schema=EDIT_SCHEMA, handler=edit_with_context))
        self.registry.register(ToolDef(schema=WRITE_SCHEMA, handler=write_with_context))
        self.registry.register(ToolDef(schema=BASH_SCHEMA, handler=_bash_handler))

        # Only main agent can create sub-agents
        if not self.is_child:
            self.registry.register(ToolDef(schema=CREATE_AGENT_SCHEMA, handler=create_agent_with_context))

        # Register messaging tools if bus is available
        if self.bus is not None:
            self._register_messaging_tools()

    def _register_messaging_tools(self) -> None:
        """Register messaging tools when bus is available."""
        from .tools.messaging import (
            SEND_MESSAGE_SCHEMA, SPAWN_AGENT_SCHEMA, LIST_AGENTS_SCHEMA,
            _send_message_handler, _spawn_agent_handler, _list_agents_handler
        )

        async def send_message_with_context(inp: dict) -> str:
            return await _send_message_handler(inp, self.bus, self.endpoint_name)

        async def list_agents_with_context(inp: dict) -> str:
            return await _list_agents_handler(inp, self.bus)

        # Register send_message and list_agents for all agents with bus
        self.registry.register(ToolDef(schema=SEND_MESSAGE_SCHEMA, handler=send_message_with_context))
        self.registry.register(ToolDef(schema=LIST_AGENTS_SCHEMA, handler=list_agents_with_context))

        # Only main agent can spawn sub-agents
        if not self.is_child:
            async def spawn_agent_with_context(inp: dict) -> str:
                return await _spawn_agent_handler(inp, self, self.bus)

            self.registry.register(ToolDef(schema=SPAWN_AGENT_SCHEMA, handler=spawn_agent_with_context))

    @staticmethod
    def _default_on_thinking(t: str) -> None:
        print(f"\033[2m{t}\033[0m", end="", flush=True)

    async def run(self, user_message: str):
        self.context.add_user(user_message)

        final_text: list[str] = []
        consecutive_errors = 0
        tools_schema = self.registry.schemas() if hasattr(self, "registry") else []

        while True:
            """ check compact """
            turn_text: list[str] = []

            def _on_text(t: str) -> None:
                turn_text.append(t)
                self.on_text(t)

            def _on_thinking(t: str) -> None:
                self.on_thinking(t)
            try:
                ressponse = await self.llm.create(
                    self.context.to_list(),
                    tools = tools_schema,
                    on_text=_on_text,
                    on_thinking=_on_thinking
                )
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                print(f"\n[LLM error #{consecutive_errors}]: {e}", flush=True)
                if consecutive_errors >= 3:
                    return f"Error: too many consecutive LLM failures — {e}"
                await asyncio.sleep(2 ** consecutive_errors)
                continue

            content = ressponse.content
            self.context.add_assistant(content)
            tool_uses:list[dict] = []
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "tool_use":
                    tool_uses.append(
                        {
                            "id": getattr(block, "id", None),
                            "name": getattr(block, "name", None),
                            "input": getattr(block, "input", {})
                        }
                    )

            if not tool_uses:
                final_text.extend(turn_text)
                break

            tool_results: list[dict] = []
            for tu in tool_uses:
                name = tu["name"]
                inp = tu["input"]
                tid = tu["id"]

                if self.on_tool_call:
                    self.on_tool_call(name, inp)
                try:
                    result = await self.registry.dispatch(name, inp)
                    content_str = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                    tool_results.append({
                        "tool_use_id": tid,
                        "type": "tool_result",
                        "content": content_str,
                    })
                except Exception as e:
                    tool_results.append({
                        "tool_use_id": tid,
                        "type": "tool_result",
                        "content": f"Tool error: {e}",
                        "is_error": True,
                    })

            self.context.add_tool_results(tool_results)

        # Auto-save session if enabled
        if self.auto_save_session and self.agent_name != "temp_agent":
            self.save_session()

        # Return the final text
        return "".join(final_text)

    def save(self, description: str = "", save_current_session: bool = True) -> str:
        """Save agent configuration and optionally current session.

        Args:
            description: Agent description
            save_current_session: Whether to save current session (default: True)

        Returns:
            agent_name
        """
        # Serialize config
        config_dict = {
            "model": self.llm.cfg.model,
            "api_key": self.llm.cfg.api_key,
            "base_url": self.llm.cfg.base_url,
            "system_prompt": self.llm.cfg.system_prompt,
            "max_token": self.llm.cfg.max_token,
            "max_retrise": self.llm.cfg.max_retrise,
            "timeout": self.llm.cfg.timeout,
            "stream": self.llm.cfg.stream,
            "thinking": self.llm.cfg.thinking,
            "tool_choice": self.llm.cfg.tool_choice,
            "default_headers": self.llm.cfg.default_headers,
            "provider": self.llm.cfg.provider,
        }

        # Determine source skills directory
        source_skills = None
        loaded_skill_names: list[str] | None = None
        agent_skills_dir = str(AgentPersistence.get_skills_dir(self.agent_name))

        # Only copy skills if they're from a different location
        if self.skills_dir and self.skills_dir != agent_skills_dir:
            source_skills = self.skills_dir
            # Only copy skills that were actually loaded during this session
            loaded_skill_names = list(self._loaded_skills) if self._loaded_skills else []

        AgentPersistence.save_agent(
            name=self.agent_name,
            description=description,
            config=config_dict,
            metadata={"auto_save_session": self.auto_save_session},
            source_skills_dir=source_skills,
            skill_names=loaded_skill_names,
        )

        # Save current session if requested and there are messages
        if save_current_session and len(self.context.messages) > 0:
            self.save_session()

        return self.agent_name

    def save_session(self, session_id: str | None = None) -> str:
        """Save current session.

        Args:
            session_id: Optional session ID (auto-generated if None)

        Returns:
            session_id
        """
        session_id = AgentPersistence.save_session(
            agent_name=self.agent_name,
            messages=AgentPersistence._serialize_messages(self.context.messages),
            session_id=session_id or self.session_id,
        )
        self.session_id = session_id
        return session_id

    @classmethod
    def load(
        cls,
        name: str,
        session_id: str | None = None,
        *,
        on_text: Callable[[str], None] | None = None,
        on_thinking: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict], None] | None = None,
    ) -> "Agent":
        """Load agent from saved configuration.

        Args:
            name: Agent name
            session_id: Optional session ID to restore context. If None, starts fresh.
            on_text: Text callback
            on_thinking: Thinking callback
            on_tool_call: Tool call callback

        Returns:
            Loaded Agent instance
        """
        data = AgentPersistence.load_agent(name)

        # Reconstruct config
        config_data = data["config"]
        config = ClientConfig(
            model=config_data.get("model", "claude-sonnet-4-6"),
            api_key=config_data.get("api_key", ""),
            base_url=config_data.get("base_url"),
            system_prompt=config_data.get("system_prompt", ""),
            max_token=config_data.get("max_token", 16384),
            max_retrise=config_data.get("max_retrise", 3),
            timeout=config_data.get("timeout", 120.0),
            stream=config_data.get("stream", True),
            thinking=config_data.get("thinking", {"type": "adaptive"}),
            tool_choice=config_data.get("tool_choice", {"type": "auto"}),
            default_headers=config_data.get("default_headers", {}),
            provider=config_data.get("provider", "anthropic"),
        )

        # Create agent (always a child since it's loaded)
        metadata = data.get("metadata", {})
        agent = cls(
            config=config,
            agent_name=name,
            skills_dir=data['skills_dir'],
            on_text=on_text,
            on_thinking=on_thinking,
            on_tool_call=on_tool_call,
            memory_path=data['memory_path'],
            auto_save_session=metadata.get("auto_save_session", True),
            is_child=True,
        )

        # Restore context from session if provided
        if session_id:
            try:
                messages = AgentPersistence.load_session(name, session_id)
                agent.context.messages = messages
                agent.session_id = session_id
            except FileNotFoundError:
                print(f"[warning] Session '{session_id}' not found, starting fresh")

        return agent

    @classmethod
    def list_saved(cls) -> list[dict]:
        """List all saved agents.

        Returns:
            List of agent metadata
        """
        return AgentPersistence.list_agents()

    @classmethod
    def delete_saved(cls, name: str) -> bool:
        """Delete a saved agent.

        Args:
            name: Agent name

        Returns:
            True if deleted, False if not found
        """
        return AgentPersistence.delete_agent(name)
