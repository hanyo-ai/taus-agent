import asyncio
import os

from dotenv import load_dotenv

from src.agent.core import Agent
from src.agent.llm import ClientConfig
from src.agent.prompts import SYSTEM_PROMPT
from src.agent.mbus import MessageBus, AgentRunner
from src.utils.repl import run_repl


async def main_async() -> None:
    load_dotenv()

    # Create message bus for inter-agent communication
    bus = MessageBus()

    # Shared config for both agents
    config = ClientConfig(
        model=os.environ.get("MODEL", "claude-sonnet-4-6"),
        api_key=os.environ.get("API_KEY", ""),
        base_url=os.environ.get("BASE_URL", ""),
        thinking=False if os.environ.get("THINKING", "0") == "0" else True,
        system_prompt=SYSTEM_PROMPT,
        stream=True,
        provider=os.environ.get("PROVIDER", "anthropic"),
    )

    # Agent 1: For message bus (in-process sub-agent communication via
    # send_message/spawn_agent tools). Note: this bus is local to this
    # process — it is separate from the bus created by `uvicorn app:app`.
    bus_agent = Agent(
        config,
        agent_name="main",  # Named agent for persistence
        skills_dir="skills",
        on_tool_call=lambda name, inp: print(f"\n[bus-agent tool: {name}] ", end="", flush=True),
        on_thinking=lambda t: print(f"\033[2m{t}\033[0m", end="", flush=True),
        bus=bus,
        endpoint_name="main",
    )

    # Agent 2: For REPL (local terminal interaction)
    # This agent is independent and doesn't interfere with bus_agent
    repl_agent = Agent(
        config,
        agent_name="repl",  # Different name to avoid context conflicts
        skills_dir="skills",
        on_tool_call=lambda name, _: print(f"\n[repl tool: {name}] ", end="", flush=True),
        on_thinking=lambda t: print(f"\033[2m{t}\033[0m", end="", flush=True),
        auto_save_session=True, 
    )

    # Create AgentRunner for bus_agent (handles messages from the HTTP API's
    # message bus routes when `uvicorn app:app` is run alongside this REPL)
    runner = AgentRunner(bus_agent, bus, endpoint_name="main")
    runner.start()

    print(f"[main] Bus agent 'main' ready for inter-agent messages")
    print(f"[main] For HTTP/WebSocket access, run: uvicorn app:app --host 0.0.0.0 --port 8000")
    print(f"[main] REPL agent ready for local interaction")
    print(f"[main] Type your commands below, or press Ctrl+D to exit\n")

    try:
        # Run REPL with repl_agent (independent context)
        await run_repl(repl_agent)
    finally:
        # Cleanup
        await runner.stop()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
