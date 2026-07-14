
from pathlib import Path

WRITE_SCHEMA = {
    "name": "write",
    "description": (
        "Create a new file or completely overwrite an existing file with the given content. "
        "Parent directories are created automatically. Use write only for new files or full "
        "rewrites — for targeted changes to existing files, use the edit tool instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path of the file to create or overwrite."},
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["path", "content"],
    },
}


async def _write_handler(inp: dict) -> str:
    path = Path(inp["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(inp["content"], encoding="utf-8")
    lines = inp["content"].count("\n") + 1
    return f"Wrote {path} ({lines} lines)"
