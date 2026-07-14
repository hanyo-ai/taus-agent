"""Message bus implementation - central dispatcher for agent communication."""

import asyncio
from dataclasses import dataclass
from typing import Optional
from collections import deque
from .message import Message


@dataclass
class Endpoint:
    """An endpoint registered on the bus with its inbox queue."""

    name: str
    inbox: asyncio.Queue = None

    def __post_init__(self):
        if self.inbox is None:
            self.inbox = asyncio.Queue()


class MessageBus:
    """Central message dispatcher for inter-agent communication.

    Supports:
    - Point-to-point messaging (agent → agent)
    - Broadcast messaging (one → many)
    - External injection (cross-thread safe)
    """

    def __init__(self, max_log_size: int = 1000):
        """Initialize message bus.

        Args:
            max_log_size: Maximum number of messages to keep in log
        """
        self._endpoints: dict[str, Endpoint] = {}
        self._log: deque[Message] = deque(maxlen=max_log_size)
        self._lock = asyncio.Lock()

    def register(self, name: str) -> Endpoint:
        """Register an endpoint on the bus.

        Args:
            name: Unique endpoint name

        Returns:
            Endpoint object with inbox queue

        Raises:
            ValueError: If endpoint already exists
        """
        if name in self._endpoints:
            raise ValueError(f"Endpoint '{name}' already registered")

        endpoint = Endpoint(name=name)
        self._endpoints[name] = endpoint
        return endpoint

    def unregister(self, name: str) -> bool:
        """Unregister an endpoint from the bus.

        Args:
            name: Endpoint name to remove

        Returns:
            True if removed, False if not found
        """
        return self._endpoints.pop(name, None) is not None

    def get_endpoint(self, name: str) -> Optional[Endpoint]:
        """Get an endpoint by name.

        Args:
            name: Endpoint name

        Returns:
            Endpoint if found, None otherwise
        """
        return self._endpoints.get(name)

    def endpoints(self) -> list[str]:
        """List all registered endpoint names.

        Returns:
            Sorted list of endpoint names
        """
        return sorted(self._endpoints.keys())

    async def send(self, msg: Message) -> bool:
        """Send a message to a specific endpoint.

        Args:
            msg: Message to send

        Returns:
            True if delivered, False if recipient not found
        """
        async with self._lock:
            self._log.append(msg)

        endpoint = self._endpoints.get(msg.recipient)
        if endpoint is None:
            # Recipient not found
            return False

        await endpoint.inbox.put(msg)
        return True

    async def broadcast(self, msg: Message, exclude: set[str] | None = None) -> int:
        """Broadcast a message to all endpoints except excluded ones.

        Args:
            msg: Message to broadcast (recipient should be "*")
            exclude: Set of endpoint names to exclude (typically includes sender)

        Returns:
            Number of endpoints that received the message
        """
        async with self._lock:
            self._log.append(msg)

        exclude = exclude or set()
        delivered = 0

        for name, endpoint in self._endpoints.items():
            if name not in exclude:
                await endpoint.inbox.put(msg)
                delivered += 1

        return delivered

    def inject(self, msg: Message, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Inject a message from external/cross-thread sources (thread-safe).

        Args:
            msg: Message to inject
            loop: Event loop to inject into (uses running loop if None)
        """
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                raise RuntimeError("No running event loop found")

        # Thread-safe call
        loop.call_soon_threadsafe(asyncio.create_task, self.send(msg))

    def get_log(self, limit: int | None = None) -> list[Message]:
        """Get recent message log.

        Args:
            limit: Maximum number of messages to return (None = all)

        Returns:
            List of recent messages (most recent last)
        """
        if limit is None:
            return list(self._log)
        return list(self._log)[-limit:]

    def clear_log(self) -> None:
        """Clear message log."""
        self._log.clear()
