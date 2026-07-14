"""Redesigned agent persistence with sessions and auto-resource management."""
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


class AgentPersistence:
    """Handle agent state persistence with session management."""

    AGENTS_DIR = Path(".agents")

    @classmethod
    def ensure_agents_dir(cls) -> None:
        """Ensure .agents directory exists."""
        cls.AGENTS_DIR.mkdir(exist_ok=True)

    @classmethod
    def get_agent_dir(cls, name: str) -> Path:
        """Get path to agent directory by name."""
        return cls.AGENTS_DIR / name

    @classmethod
    def get_agent_file(cls, name: str) -> Path:
        """Get path to agent.json file."""
        return cls.get_agent_dir(name) / "agent.json"

    @classmethod
    def get_memory_file(cls, name: str) -> Path:
        """Get path to agent's MEMORY.md file."""
        return cls.get_agent_dir(name) / "MEMORY.md"

    @classmethod
    def get_skills_dir(cls, name: str) -> Path:
        """Get path to agent's skills directory."""
        return cls.get_agent_dir(name) / "skills"

    @classmethod
    def get_sessions_dir(cls, name: str) -> Path:
        """Get path to agent's sessions directory."""
        return cls.get_agent_dir(name) / "sessions"

    @classmethod
    def generate_session_id(cls) -> str:
        """Generate a session ID based on timestamp."""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @classmethod
    def save_agent(
        cls,
        name: str,
        description: str = "",
        config: dict | None = None,
        metadata: dict | None = None,
        source_skills_dir: str | None = None,
        skill_names: list[str] | None = None,
    ) -> Path:
        """Save or update agent configuration (without context).

        Args:
            name: Agent name (used as directory name)
            description: Agent description
            config: LLM configuration
            metadata: Additional metadata
            source_skills_dir: Source skills directory to copy from
            skill_names: Specific skill names to copy (copies all if None)

        Returns:
            Path to agent directory
        """
        cls.ensure_agents_dir()
        agent_dir = cls.get_agent_dir(name)
        agent_dir.mkdir(parents=True, exist_ok=True)

        agent_file = cls.get_agent_file(name)

        # Load existing data to preserve created_at
        created_at = datetime.now().isoformat()
        if agent_file.exists():
            try:
                existing = json.loads(agent_file.read_text(encoding="utf-8"))
                created_at = existing.get("created_at", created_at)
            except Exception:
                pass

        # Build agent data (NO context)
        agent_data = {
            "name": name,
            "description": description,
            "created_at": created_at,
            "updated_at": datetime.now().isoformat(),
            "config": config or {},
            "metadata": metadata or {},
        }

        # Write agent.json
        agent_file.write_text(
            json.dumps(agent_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        # DO NOT auto-create MEMORY.md here
        # Let the model create it using write tool based on conversation context

        # Create sessions directory
        sessions_dir = cls.get_sessions_dir(name)
        sessions_dir.mkdir(exist_ok=True)

        # Copy skills if source provided
        copied_skills = 0
        if source_skills_dir:
            copied_skills = cls._copy_skills(
                source_skills_dir,
                cls.get_skills_dir(name),
                skill_names=skill_names,
            )
            if copied_skills > 0:
                print(f"  [skills] 已复制 {copied_skills} 个技能文件")

        return agent_dir

    @classmethod
    def save_session(
        cls,
        agent_name: str,
        messages: list[dict],
        session_id: str | None = None,
    ) -> str:
        """Save a conversation session for an agent.

        Args:
            agent_name: Agent name
            messages: Conversation messages
            session_id: Optional session ID (auto-generated if None)

        Returns:
            session_id
        """
        if session_id is None:
            session_id = cls.generate_session_id()

        sessions_dir = cls.get_sessions_dir(agent_name)
        sessions_dir.mkdir(parents=True, exist_ok=True)

        session_file = sessions_dir / f"{session_id}.json"

        session_data = {
            "session_id": session_id,
            "agent_name": agent_name,
            "created_at": datetime.now().isoformat(),
            "messages": messages,
            "message_count": len(messages),
        }

        session_file.write_text(
            json.dumps(session_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        return session_id

    @classmethod
    def load_agent(cls, name: str) -> dict:
        """Load agent configuration (without context).

        Args:
            name: Agent name

        Returns:
            Agent data dictionary with keys:
                - name, description, config, metadata
                - agent_dir: Path to agent directory
                - memory_path: Path to MEMORY.md
                - skills_dir: Path to skills directory
                - sessions: List of available sessions

        Raises:
            FileNotFoundError: If agent doesn't exist
            ValueError: If agent data is invalid
        """
        agent_dir = cls.get_agent_dir(name)
        agent_file = cls.get_agent_file(name)

        if not agent_dir.exists():
            raise FileNotFoundError(f"Agent '{name}' not found")

        if not agent_file.exists():
            raise FileNotFoundError(f"Agent '{name}' configuration not found")

        try:
            data = json.loads(agent_file.read_text(encoding="utf-8"))

            # Add paths
            data['agent_dir'] = str(agent_dir)
            data['memory_path'] = str(cls.get_memory_file(name))
            data['skills_dir'] = str(cls.get_skills_dir(name))

            # List available sessions
            sessions_dir = cls.get_sessions_dir(name)
            if sessions_dir.exists():
                sessions = sorted(
                    [f.stem for f in sessions_dir.glob("*.json")],
                    reverse=True
                )
                data['sessions'] = sessions
            else:
                data['sessions'] = []

            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid agent data in {agent_file}: {e}")

    @classmethod
    def load_session(cls, agent_name: str, session_id: str) -> list[dict]:
        """Load a specific session's messages.

        Args:
            agent_name: Agent name
            session_id: Session ID

        Returns:
            List of messages
        """
        session_file = cls.get_sessions_dir(agent_name) / f"{session_id}.json"

        if not session_file.exists():
            raise FileNotFoundError(f"Session '{session_id}' not found")

        data = json.loads(session_file.read_text(encoding="utf-8"))
        return data.get("messages", [])

    @classmethod
    def list_agents(cls) -> list[dict]:
        """List all saved agents.

        Returns:
            List of agent metadata
        """
        cls.ensure_agents_dir()

        agents = []
        for agent_dir in cls.AGENTS_DIR.iterdir():
            if not agent_dir.is_dir():
                continue

            agent_file = agent_dir / "agent.json"
            if not agent_file.exists():
                continue

            try:
                data = json.loads(agent_file.read_text(encoding="utf-8"))

                # Count sessions
                sessions_dir = agent_dir / "sessions"
                session_count = len(list(sessions_dir.glob("*.json"))) if sessions_dir.exists() else 0

                # Check resources
                has_memory = (agent_dir / "MEMORY.md").exists()
                has_skills = (agent_dir / "skills").exists()

                agents.append({
                    "name": data.get("name"),
                    "description": data.get("description", ""),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "session_count": session_count,
                    "has_memory": has_memory,
                    "has_skills": has_skills,
                    "agent_dir": str(agent_dir),
                })
            except Exception:
                continue

        # Sort by updated_at descending
        agents.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return agents

    @classmethod
    def delete_agent(cls, name: str) -> bool:
        """Delete an agent and all its sessions.

        Args:
            name: Agent name

        Returns:
            True if deleted, False if not found
        """
        agent_dir = cls.get_agent_dir(name)

        if agent_dir.exists():
            shutil.rmtree(agent_dir)
            return True
        return False

    @classmethod
    def _create_default_memory(cls, memory_file: Path, agent_name: str) -> None:
        """Create a default MEMORY.md template with smart content."""
        # Try to infer context from agent name
        name_parts = agent_name.replace("-", " ").replace("_", " ")

        template = f"""# {agent_name} 的记忆

## Agent 信息
- 名称: {agent_name}
- 创建时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- 用途: {name_parts}

## 用户信息
- 称呼:
- 角色:
- 偏好:

## 专业领域
（请根据对话内容填写）

## 项目上下文
（请记录项目相关信息）

## 使用的工具和技能
（自动更新使用的skills）

## 重要决策和约定
（记录重要的技术决策）

## 待办事项
- [ ]

## 备注

"""
        memory_file.write_text(template, encoding="utf-8")

    @classmethod
    def _copy_skills(
        cls,
        source_dir: str | Path,
        target_dir: Path,
        skill_names: list[str] | None = None,
    ) -> int:
        """Copy skills from source to target directory.

        Copies entire skill subdirectories (each containing SKILL.md).
        If skill_names is provided, only those specific skills are copied.

        Args:
            source_dir: Source skills directory
            target_dir: Target skills directory
            skill_names: Optional list of skill names to copy (copies all if None)

        Returns:
            Number of skills copied
        """
        source = Path(source_dir)
        if not source.exists() or not source.is_dir():
            return 0

        target_dir.mkdir(parents=True, exist_ok=True)

        copied_count = 0
        for skill_file in source.rglob("SKILL.md"):
            skill_dir = skill_file.parent
            skill_name = skill_dir.name

            # Filter by skill_names if provided
            if skill_names is not None and skill_name not in skill_names:
                continue

            # Copy the entire skill subdirectory
            dest_dir = target_dir / skill_name
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(skill_dir, dest_dir)
            copied_count += 1
            print(f"  [skill] 已复制: {skill_name}")

        return copied_count

    @classmethod
    def _serialize_messages(cls, messages: list[dict]) -> list[dict]:
        """Serialize messages for storage."""
        serialized = []
        for msg in messages:
            msg_copy = msg.copy()

            # Handle content that might be a list of objects
            if "content" in msg_copy:
                content = msg_copy["content"]
                if isinstance(content, list):
                    serialized_content = []
                    for item in content:
                        if hasattr(item, "__dict__"):
                            # Convert object to dict
                            serialized_content.append(cls._obj_to_dict(item))
                        else:
                            serialized_content.append(item)
                    msg_copy["content"] = serialized_content

            serialized.append(msg_copy)

        return serialized

    @classmethod
    def _obj_to_dict(cls, obj) -> dict:
        """Convert an object to a dictionary."""
        if hasattr(obj, "model_dump"):
            # Pydantic v2
            return obj.model_dump()
        elif hasattr(obj, "dict"):
            # Pydantic v1
            return obj.dict()
        elif hasattr(obj, "__dict__"):
            result = {"_type": obj.__class__.__name__}
            result.update(obj.__dict__)
            return result
        else:
            return {"value": str(obj)}
