"""File-based signal system for agent emergency brake + correction injection.

Uses the filesystem as a cross-async-boundary signalling mechanism —
REPL writes signal files, Agent's main loop polls them at detection points.
"""

from pathlib import Path
import asyncio

SIGNALS_DIR = Path(".signals")


class AgentAborted(Exception):
    """Raised when the agent is aborted by the user."""
    pass


class AgentPaused(Exception):
    """Raised when the agent is paused — unwinds stack back to REPL."""
    pass


class AgentBrake:
    """Emergency brake + correction injection via filesystem signals."""

    def __init__(self, agent_id: str = "default"):
        SIGNALS_DIR.mkdir(exist_ok=True)
        self.pause_file = SIGNALS_DIR / f"{agent_id}.pause"
        self.abort_file = SIGNALS_DIR / f"{agent_id}.abort"
        self.correction_file = SIGNALS_DIR / f"{agent_id}.correction"

    # ── Write side (REPL / user interaction layer) ──

    def pause(self, correction: str | None = None):
        """Hit the brake. Optionally attach a correction message."""
        self.pause_file.touch()
        if correction:
            self.correction_file.write_text(correction, encoding="utf-8")

    def abort(self):
        """Emergency abort — pause + abort signal."""
        self.abort_file.touch()
        self.pause_file.touch()

    def resume(self):
        """Release the brake and continue."""
        self.pause_file.unlink(missing_ok=True)

    # ── Read side (Agent main loop detection points) ──

    def is_paused(self) -> bool:
        return self.pause_file.exists()

    def is_aborted(self) -> bool:
        return self.abort_file.exists()

    def consume_correction(self) -> str | None:
        """Read and consume the correction message (one-shot)."""
        if self.correction_file.exists():
            text = self.correction_file.read_text(encoding="utf-8").strip()
            self.correction_file.unlink()
            return text or None
        return None

    def reset(self):
        """Clear pause/abort signals — called at the start of every agent run.
        Does NOT clear correction — that is consumed by wait_if_paused."""
        self.pause_file.unlink(missing_ok=True)
        self.abort_file.unlink(missing_ok=True)

    # ── Async helper for detection points ──

    async def wait_if_paused(self, context):
        """Check once whether the brake is engaged.

        Call this at every detection point in the agent main loop.

        Returns immediately if not paused.
        Raises AgentPaused if paused (unwinds back to REPL for user interaction).
        Raises AgentAborted if the user aborted.

        Before raising, consumes and injects any pending correction.
        """
        if not self.is_paused():
            return

        if self.is_aborted():
            raise AgentAborted()

        # Consume correction before unwinding
        correction = self.consume_correction()
        if correction:
            context.add_user(f"[人类纠偏] {correction}")

        raise AgentPaused()
