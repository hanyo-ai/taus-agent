
import base64
import mimetypes
from pathlib import Path


# Constants
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
IMAGE_MAX_PX = 1568
READ_MAX_LINES = 2000
READ_MAX_BYTES = 50 * 1024  # 50 KB


READ_SCHEMA = {
    "name": "read",
    "description": (
        "Read the contents of a file at a given path. For text files, returns lines with "
        "1-based line numbers. Supports pagination via offset/limit to read large files in "
        "chunks — use offset=N to continue reading after a truncation hint. For images "
        "(jpg, jpeg, png, gif, webp) returns the image as a base64-encoded attachment. "
        "Output is capped at 2000 lines / 50 KB; a continuation hint is appended when "
        "truncation occurs. Prefer read over cat or sed for examining files."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the file."},
            "offset": {
                "type": "integer",
                "description": "1-based line number to start reading from (default: 1).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return (default: 2000).",
            },
        },
        "required": ["path"],
    },
}


async def _read_handler(inp: dict) -> str | list:
    path = Path(inp["path"])
    if not path.exists():
        return f"Error: file not found: {path}"

    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return _read_image(path)

    offset = max(1, int(inp.get("offset", 1)))
    limit = min(int(inp.get("limit", READ_MAX_LINES)), READ_MAX_LINES)

    try:
        raw = path.read_bytes()
    except OSError as e:
        return f"Error: {e}"

    text = raw.decode("utf-8", errors="replace")
    all_lines = text.splitlines(keepends=True)
    total = len(all_lines)
    start = offset - 1           # convert to 0-based
    end = min(start + limit, total)
    selected = all_lines[start:end]

    # Apply byte cap within the selected window
    byte_count = 0
    final_lines = []
    for line in selected:
        byte_count += len(line.encode())
        if byte_count > READ_MAX_BYTES:
            final_lines.append(f"[... byte limit reached]\n")
            break
        final_lines.append(line)

    numbered = "".join(
        f"{start + i + 1}\t{line}" for i, line in enumerate(final_lines)
    )

    tail = end
    if tail < total:
        numbered += f"\nUse offset={tail + 1} to continue reading ({total - tail} lines remaining)."

    return numbered

def _read_image(path: Path) -> list:
    """Return a content block list with an image attachment."""
    data = path.read_bytes()
    # Resize if PIL is available; otherwise pass through
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        if max(w, h) > IMAGE_MAX_PX:
            ratio = IMAGE_MAX_PX / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            fmt = img.format or "PNG"
            img.save(buf, format=fmt)
            data = buf.getvalue()
    except ImportError:
        pass

    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.standard_b64encode(data).decode()
    return [
        {"type": "text", "text": f"Image: {path}"},
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
    ]