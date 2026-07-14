"""Message data structure for agent communication."""

import time
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class Message:
    """A message passed through the bus.

    Attributes:
        sender: Source endpoint name (e.g., "main", "researcher", "telegram")
        recipient: Target endpoint name, or "*" for broadcast
        content: Message payload (typically text or serialized data)
        kind: Message type - text | task | result | error | system | stop
        correlation_id: Links a result/response back to its originating message
        id: Unique message identifier
        ts: Unix timestamp when message was created
    """

    sender: str
    recipient: str
    content: str
    kind: str = "text"
    correlation_id: str | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    ts: float = field(default_factory=time.time)

    def __repr__(self) -> str:
        return (
            f"Message(id={self.id[:8]}, {self.sender}->{self.recipient}, "
            f"kind={self.kind}, corr={self.correlation_id[:8] if self.correlation_id else None})"
        )


_KIND_LABELS = {
    "task": "Task",
    "text": "Message",
    "result": "Result",
    "error": "Error",
    "system": "System",
}


def format_bus_prompt(msg: "Message") -> str:
    """Render an incoming bus message as a tagged prompt for an agent.

    Every message injected into an agent's context must carry a
    "[<Kind> from <sender>]" tag so the origin (main, a sub-agent,
    telegram:<user>, etc.) is never ambiguous in the transcript.
    """
    label = _KIND_LABELS.get(msg.kind, "Message")
    return f"[{label} from {msg.sender}]\n{msg.content}"
