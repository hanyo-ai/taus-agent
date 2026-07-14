"""HTTP Gateway — REST API for external scripts to communicate with agents.

Design:
- FastAPI router for external scripts to communicate with agents
- POST /message — external scripts send messages to agents
- GET /agents — list all registered agents
- GET /log — view recent message history
- WebSocket /ws — real-time bidirectional communication

Architecture:
    External Script ──→ HTTP POST /message ──→ router ──→ bus.inject() ──→ Agent
    Agent ──→ bus.send() ──→ router ──→ WebSocket ──→ External Client

Mounted as a router by app/__init__.py; not runnable standalone.
"""

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .message import Message


class SendMessageRequest(BaseModel):
    """Request body for sending a message."""
    recipient: str
    content: str
    kind: str = "text"
    sender: str = "external"


class HttpGateway:
    """HTTP gateway for external communication with agents, exposed as an APIRouter."""

    def __init__(
        self,
        bus: Any,  # MessageBus
        endpoint_name: str = "http_gateway",
    ):
        """Initialize HTTP Gateway.

        Args:
            bus: MessageBus instance
            endpoint_name: Name to register this gateway under
        """
        self.bus = bus
        self.endpoint_name = endpoint_name
        self._running = False
        self._websockets: list[WebSocket] = []

        self.router = APIRouter(tags=["mbus"])
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Setup API routes."""

        @self.router.post("/message")
        async def send_message(req: SendMessageRequest):
            """Send a message to an agent.

            Example:
                curl -X POST http://localhost:8000/message \
                     -H "Content-Type: application/json" \
                     -d '{"recipient": "main", "content": "Hello agent!", "sender": "script1"}'
            """
            # Create message
            msg = Message(
                sender=req.sender,
                recipient=req.recipient,
                content=req.content,
                kind=req.kind,
            )

            # Inject into bus
            self.bus.inject(msg)

            return {
                "status": "sent",
                "message_id": msg.id,
                "timestamp": msg.ts,
            }

        @self.router.get("/agents")
        async def list_agents():
            """List all registered agents/endpoints.

            Example:
                curl http://localhost:8000/agents
            """
            endpoints = self.bus.endpoints()
            return {
                "agents": endpoints,
                "count": len(endpoints),
            }

        @self.router.get("/log")
        async def get_log(limit: int = 50):
            """Get recent message log.

            Example:
                curl http://localhost:8000/log?limit=10
            """
            messages = self.bus.get_log(limit=limit)
            return {
                "messages": [
                    {
                        "id": m.id,
                        "sender": m.sender,
                        "recipient": m.recipient,
                        "content": m.content,
                        "kind": m.kind,
                        "timestamp": m.ts,
                        "correlation_id": m.correlation_id,
                    }
                    for m in messages
                ],
                "count": len(messages),
            }

        @self.router.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time bidirectional communication.

            Client sends JSON: {"recipient": "main", "content": "...", "kind": "text"}
            Server sends JSON: {"sender": "...", "content": "...", "kind": "..."}
            """
            await websocket.accept()
            self._websockets.append(websocket)

            try:
                while True:
                    # Receive message from client
                    data = await websocket.receive_json()

                    # Create and inject message
                    msg = Message(
                        sender=data.get("sender", "ws_client"),
                        recipient=data["recipient"],
                        content=data["content"],
                        kind=data.get("kind", "text"),
                    )
                    self.bus.inject(msg)

                    # Send acknowledgment
                    await websocket.send_json({
                        "status": "sent",
                        "message_id": msg.id,
                        "timestamp": msg.ts,
                    })

            except WebSocketDisconnect:
                pass
            except Exception as e:
                print(f"[http-gateway] WebSocket error: {e}")
            finally:
                if websocket in self._websockets:
                    self._websockets.remove(websocket)

    def start(self) -> None:
        """Register the gateway's endpoint on the bus."""
        if self._running:
            return
        self.bus.register(self.endpoint_name)
        self._running = True

    def stop(self) -> None:
        """Unregister the gateway's endpoint from the bus."""
        if not self._running:
            return
        self._running = False
        self.bus.unregister(self.endpoint_name)

    @property
    def is_running(self) -> bool:
        """Check if gateway is running."""
        return self._running

    async def close_websockets(self) -> None:
        """Close all connected WebSocket clients (call on app shutdown)."""
        for ws in self._websockets:
            try:
                await ws.close()
            except Exception:
                pass
        self._websockets.clear()

    async def broadcast(self, msg: Message) -> None:
        """Broadcast a message to all connected WebSocket clients.

        Args:
            msg: Message to broadcast
        """
        if not self._websockets:
            return

        data = {
            "id": msg.id,
            "sender": msg.sender,
            "recipient": msg.recipient,
            "content": msg.content,
            "kind": msg.kind,
            "timestamp": msg.ts,
            "correlation_id": msg.correlation_id,
        }

        # Send to all connected clients
        disconnected = []
        for ws in self._websockets:
            try:
                await ws.send_json(data)
            except Exception:
                disconnected.append(ws)

        # Clean up disconnected clients
        for ws in disconnected:
            if ws in self._websockets:
                self._websockets.remove(ws)
