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
        """Main message processing loop with atomic message processing."""
        while self._running:
            try:
                # Wait for incoming message
                msg: Message = await self.endpoint.inbox.get()

                # Handle stop signal
                if msg.kind == "stop":
                    break

                # CRITICAL: Lock to ensure atomic processing
                # Prevents new messages from being added to context mid-processing
                async with self._processing_lock:
                    # Format prompt from message
                    prompt = self._format_prompt(msg)

                    # Only "task"/"text" messages expect a reply. "result"/"error"
                    # are terminal notifications (e.g. a sub-agent reporting back
                    # after finishing work) — auto-replying to those would bounce
                    # forever, since the recipient's own reply is itself a
                    # "result" that the original sender would then reply to again.
                    expects_reply = msg.kind in ("task", "text")

                    # Run agent (this may take time - tool calls, LLM API, etc.)
                    try:
                        reply = await self.agent.run(prompt)

                        if expects_reply:
                            # Send result back to sender
                            result_msg = Message(
                                sender=self.endpoint_name,
                                recipient=msg.sender,
                                content=reply or "",
                                kind="result",
                                correlation_id=msg.id,
                            )
                            await self.bus.send(result_msg)

                    except Exception as e:
                        if expects_reply:
                            # Send error back to sender
                            error_msg = Message(
                                sender=self.endpoint_name,
                                recipient=msg.sender,
                                content=f"Error: {e}",
                                kind="error",
                                correlation_id=msg.id,
                            )
                            await self.bus.send(error_msg)
                        else:
                            print(
                                f"[{self.endpoint_name}] error handling "
                                f"{msg.kind} message from {msg.sender}: {e}"
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log but continue
                print(f"[{self.endpoint_name}] Unexpected error in run loop: {e}")

    def _format_prompt(self, msg: Message) -> str:
        """Format an incoming message into a tagged prompt for the agent."""
        return format_bus_prompt(msg)

    @property
    def is_running(self) -> bool:
        """Check if runner is active."""
        return self._running
