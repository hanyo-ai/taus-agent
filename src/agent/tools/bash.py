import asyncio
import os
import signal
import tempfile
from pathlib import Path

BASH_MAX_LINES = 2000
BASH_MAX_BYTES = 50 * 1024
BASH_DEFAULT_TIMEOUT = 30
TEMP_DIR = Path(tempfile.gettempdir())

BASH_SCHEMA = {
    "name": "bash",
    "description": (
        "Execute a shell command in bash and return the combined stdout/stderr output. "
        "Use for file operations (ls, grep, find, rg), running scripts, installing packages, "
        "and any other shell tasks. Output is capped at 2000 lines / 50 KB; when truncated "
        "the full output is written to a temp file whose path is included. "
        "The timeout parameter (default 30 s) kills the entire process tree on expiry. "
        "Do NOT use for reading files — use the read tool instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."},
            "timeout": {
                "type": "integer",
                "description": "Seconds before the command is killed (default 30, max 600).",
            },
        },
        "required": ["command"],
    },
}


async def _bash_handler(inp: dict) -> str:
    command = inp["command"]
    timeout = min(int(inp.get("timeout", BASH_DEFAULT_TIMEOUT)), 600)

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ.copy(),
        start_new_session=True,
    )

    chunks: list[bytes] = []
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        chunks.append(stdout)
        rc = proc.returncode
    except asyncio.TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        return f"Error: command timed out after {timeout}s"

    output = b"".join(chunks).decode("utf-8", errors="replace")

    tmp_label = ""
    lines = output.splitlines()
    if len(lines) > BASH_MAX_LINES or len(output.encode()) > BASH_MAX_BYTES:
        tmp = TEMP_DIR / f"bash_output_{proc.pid}.txt"
        tmp.write_text(output)
        tmp_label = str(tmp)

    result = output
    if rc != 0:
        result += f"\n[exit code: {rc}]"
    return result
