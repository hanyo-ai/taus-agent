"""Create agent tool - allows agent to create/save itself."""

CREATE_AGENT_SCHEMA = {
    "name": "create_agent",
    "description": (
        "Create a new agent with configuration, session, and skills. "
        "IMPORTANT: After calling this tool, you MUST use the 'write' tool to create the agent's MEMORY.md file "
        "at path '.agents/{name}/MEMORY.md' with relevant information extracted from our conversation, including:\n"
        "- User information and preferences\n"
        "- Project context and technical details\n"
        "- Important decisions and conventions\n"
        "- Skills and tools used\n"
        "- Any other valuable context\n\n"
        "Use this when user asks to create or save an agent."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Agent name in kebab-case (e.g., 'telegram-bot', 'test-engineer')"
            },
            "description": {
                "type": "string",
                "description": "Brief description of agent's purpose"
            }
        },
        "required": ["name"]
    }
}


async def _create_agent_handler(inp: dict, agent) -> str:
    """Handler for create_agent tool.

    Args:
        inp: Tool input with name and description
        agent: Reference to the Agent instance

    Returns:
        Success message with created agent information
    """
    from pathlib import Path
    from ..persistence import AgentPersistence

    name = inp["name"]
    description = inp.get("description", "")

    # Validate name format
    if not name:
        return "Error: Agent name cannot be empty"

    # Update agent metadata
    original_name = agent.agent_name
    agent.agent_name = name
    agent.memory_path = str(AgentPersistence.get_memory_file(name))

    try:
        # Save agent with current session
        saved_name = agent.save(description=description, save_current_session=True)

        agent_dir = AgentPersistence.get_agent_dir(saved_name)

        # Count resources
        sessions_dir = AgentPersistence.get_sessions_dir(saved_name)
        session_count = len(list(sessions_dir.glob("*.json"))) if sessions_dir.exists() else 0

        skills_dir = AgentPersistence.get_skills_dir(saved_name)
        skill_count = len(list(skills_dir.rglob("SKILL.md"))) if skills_dir.exists() else 0

        result = f"""Agent created successfully!

Name: {saved_name}
Description: {description or '(none)'}
Directory: {agent_dir}
Session: {agent.session_id} ({len(agent.context.messages)} messages saved)
Skills: {skill_count} files copied

NEXT STEP: Create MEMORY.md file at: .agents/{saved_name}/MEMORY.md
Use the 'write' tool to create this file with relevant information from our conversation.

After creating MEMORY.md, the agent will be ready to use:
  /load {saved_name}
  /load {saved_name} {agent.session_id}
"""
        return result

    except Exception as e:
        # Restore original name on error
        agent.agent_name = original_name
        return f"Error creating agent: {e}"
