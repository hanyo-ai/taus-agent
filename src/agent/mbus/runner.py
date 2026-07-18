"""Agent runner - wraps an Agent as a persistent asyncio task."""

import asyncio
from typing import TYPE_CHECKING

from .bus import MessageBus, Endpoint
from .message import Message, format_bus_prompt

if TYPE_CHECKING:
    from ..core import Agent


class AgentRunner:
    """Wraps an Agent as a persistent asyncio task that processes messages from a bus.

    The runner:
    - Registers its endpoint on the bus
    - Loops: waits for incoming messages → runs agent → sends results back
    - Supports lifecycle (start/stop)
    """

    def __init__(self, agent: "Agent", bus: MessageBus, endpoint_name: str):
        """Initialize agent runner.

        Args:
            agent: Agent instance to run
            bus: Message bus for communication
            endpoint_name: Name to register this agent under
        """
        self.agent = agent
        self.bus = bus
        self.endpoint_name = endpoint_name
        self.endpoint: Endpoint | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._processing_lock = asyncio.Lock()  # Ensure atomic message processing

    def start(self) -> None:
        """Start the agent runner as a background task."""
        if self._running:
            return

        self.endpoint = self.bus.register(self.endpoint_name)
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the agent runner gracefully."""
        if not self._running:
            return

        self._running = False

        # Send stop signal
        if self.endpoint:
            await self.endpoint.inbox.put(
                Message(sender="system", recipient=self.endpoint_name, content="", kind="stop")
            )

        # Wait for task to finish
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

        # Unregister from bus
        self.bus.unregister(self.endpoint_name)
        self.endpoint = None

    async def _run_loop(self) -> None:
        """Main message processing loop.

        Receives messages and runs the agent.  The agent is responsible for
        sending any results back via the send_message tool — AgentRunner no
        longer auto-replies, which eliminates duplicate messages when the
        agent already called send_message explicitly.
        """
        while self._running:
            try:
                msg: Message = await self.endpoint.inbox.get()

                if msg.kind == "stop":
                    break

                async with self._processing_lock:
                    prompt = self._format_prompt(msg)
                    try:
                        await self.agent.run(prompt)
                    except Exception as e:
                        print(
                            f"[{self.endpoint_name}] error handling "
                            f"{msg.kind} message from {msg.sender}: {e}"
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[{self.endpoint_name}] Unexpected error in run loop: {e}")

    def _format_prompt(self, msg: Message) -> str:
        """Format an incoming message into a tagged prompt for the agent."""
        return format_bus_prompt(msg)

    @property
    def is_running(self) -> bool:
        """Check if runner is active."""
        return self._running
