"""Messaging tools for inter-agent communication."""

import json

SEND_MESSAGE_SCHEMA = {
    "name": "send_message",
    "description": (
        "Send a message to another agent or endpoint via the message bus. "
        "Use this to communicate with other agents, send tasks, or send results. "
        "The recipient will receive the message and can respond."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "Name of the recipient endpoint (e.g., 'researcher', 'telegram')"
            },
            "content": {
                "type": "string",
                "description": "Message content to send"
            },
            "kind": {
                "type": "string",
                "enum": ["text", "task", "result"],
                "description": "Message type: 'text' for chat, 'task' for work assignment, 'result' for responses"
            }
        },
        "required": ["recipient", "content"]
    }
}


SPAWN_AGENT_SCHEMA = {
    "name": "spawn_agent",
    "description": (
        "Spawn a new sub-agent as a persistent background task. "
        "The sub-agent will run concurrently and can be communicated with via send_message. "
        "Only the main agent can spawn sub-agents.\n\n"
        "IMPORTANT: After calling this tool, use the 'write' tool to populate the sub-agent's "
        "MEMORY.md file at path '.agents/{name}/MEMORY.md' with context relevant to its task, "
        "such as:\n"
        "- Task-specific background and constraints from this conversation\n"
        "- Credentials, endpoints, or config the sub-agent will need\n"
        "- Conventions or output format the sub-agent should follow\n\n"
        "Skip this step only for trivial one-off tasks that need no extra context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Unique name for the sub-agent (used as endpoint identifier)"
            },
            "instructions": {
                "type": "string",
                "description": "Initial instructions or task for the sub-agent"
            },
            "description": {
                "type": "string",
                "description": "Optional description of the sub-agent's purpose"
            }
        },
        "required": ["name", "instructions"]
    }
}


LIST_AGENTS_SCHEMA = {
    "name": "list_agents",
    "description": "List all currently online agents/endpoints on the message bus.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": []
    }
}


async def _send_message_handler(inp: dict, bus, endpoint_name: str) -> str:
    """Handler for send_message tool.

    Args:
        inp: Tool input with recipient, content, kind
        bus: MessageBus instance
        endpoint_name: Current agent's endpoint name

    Returns:
        Success or error message
    """
    from ..mbus import Message

    recipient = inp["recipient"]
    content = inp["content"]
    kind = inp.get("kind", "text")

    msg = Message(
        sender=endpoint_name,
        recipient=recipient,
        content=content,
        kind=kind
    )

    success = await bus.send(msg)

    if success:
        return f"Message sent to '{recipient}' (id: {msg.id[:8]})"
    else:
        return f"Error: Recipient '{recipient}' not found on the bus"


async def _spawn_agent_handler(inp: dict, main_agent, bus) -> str:
    """Handler for spawn_agent tool.

    Args:
        inp: Tool input with name, instructions, description
        main_agent: Main agent instance (for config reference)
        bus: MessageBus instance

    Returns:
        Success message with spawned agent info
    """
    from ..core import Agent
    from ..mbus import AgentRunner
    from ..llm import ClientConfig

    name = inp["name"]
    instructions = inp["instructions"]
    description = inp.get("description", "")

    # Check if agent with this name already exists
    if bus.get_endpoint(name) is not None:
        return f"Error: Agent '{name}' already exists on the bus"

    try:
        # Create a new child agent with similar config to main
        config = ClientConfig(
            model=main_agent.llm.cfg.model,
            api_key=main_agent.llm.cfg.api_key,
            base_url=main_agent.llm.cfg.base_url,
            system_prompt=main_agent.llm.cfg.system_prompt,
            max_token=main_agent.llm.cfg.max_token,
            stream=main_agent.llm.cfg.stream,
            thinking=main_agent.llm.cfg.thinking,
            provider=main_agent.llm.cfg.provider,
        )

        # Sub-agents run as background asyncio tasks in the same process/terminal
        # as the main agent. Without explicit no-op callbacks, Agent's default
        # callbacks print directly to stdout, interleaving sub-agent output with
        # the main agent's REPL. Silence them; results surface via send_message.
        child_agent = Agent(
            config=config,
            agent_name=name,
            skills_dir=main_agent.skills_dir,
            is_child=True,
            bus=bus,
            endpoint_name=name,
            on_text=lambda t: None,
            on_thinking=lambda t: None,
            on_tool_call=lambda tool_name, tool_input: None,
        )

        # Persist agent.json so it shows up in /agents and can be reloaded
        child_agent.save(description=description, save_current_session=False)

        # Create and start runner
        runner = AgentRunner(agent=child_agent, bus=bus, endpoint_name=name)
        runner.start()

        # Send initial instructions — use the ACTUAL endpoint name of the
        # parent (session agent), NOT hardcoded "main".  Sub-agents must
        # reply to this exact endpoint so the message routes back to the
        # correct session on the bus.
        parent_endpoint = main_agent.endpoint_name or "main"
        from ..mbus import Message

        # Augment instructions so the LLM knows the correct reply endpoint
        reply_instructions = (
            f"{instructions}\n\n"
            f"CRITICAL: When you finish the task use the send_message tool "
            f"with recipient=\"{parent_endpoint}\" (exactly this string). "
            f"Do NOT send to 'main' — send to '{parent_endpoint}'."
        )
        init_msg = Message(
            sender=parent_endpoint,
            recipient=name,
            content=reply_instructions,
            kind="task"
        )
        await bus.send(init_msg)

        result = f"""Sub-agent spawned successfully!

Name: {name}
Description: {description or '(none)'}
Endpoint: {name}
Status: Running
Reply endpoint: {parent_endpoint}

The agent is now online and processing the initial task.
Use send_message to communicate with it.
"""
        return result

    except Exception as e:
        return f"Error spawning agent: {e}"


async def _list_agents_handler(inp: dict, bus) -> str:
    """Handler for list_agents tool.

    Args:
        inp: Tool input (empty)
        bus: MessageBus instance

    Returns:
        JSON list of online agents
    """
    endpoints = bus.endpoints()

    result = {
        "count": len(endpoints),
        "agents": endpoints
    }

    return json.dumps(result, indent=2)
