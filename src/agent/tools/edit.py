
import difflib
import re
import unicodedata
from pathlib import Path

EDIT_SCHEMA = {
    "name": "edit",
    "description": (
        "Make precise edits to an existing file by replacing exact text snippets. "
        "Each entry in edits[] specifies oldText (the text to find) and newText (its replacement). "
        "oldText is matched using fuzzy matching that tolerates minor whitespace/indentation "
        "differences, but must be unique within the file. "
        "All edits are applied against the ORIGINAL file content in parallel — earlier edits "
        "do NOT shift the offsets of later edits, so you can safely include multiple "
        "non-overlapping edits in a single call. "
        "Use one edit call with multiple edits[] entries rather than multiple sequential calls "
        "when editing several locations in the same file. "
        "Keep oldText as small as possible while still being unique."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to edit."},
            "edits": {
                "type": "array",
                "description": "List of text replacements to apply.",
                "items": {
                    "type": "object",
                    "properties": {
                        "oldText": {"type": "string", "description": "Exact text to replace (unique in file)."},
                        "newText": {"type": "string", "description": "Replacement text."},
                    },
                    "required": ["oldText", "newText"],
                },
            },
        },
        "required": ["path", "edits"],
    },
}

def _normalize(text: str) -> str:
    """Collapse runs of whitespace for fuzzy comparison."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\r\n", "\n", text)
    return text.strip()


def _fuzzy_find(haystack: str, needle: str) -> tuple[int, int] | None:
    """Return (start, end) byte offsets of needle in haystack using fuzzy match.

    Tries exact match first, then normalised match.
    Returns None if not found or ambiguous.
    """
    # Exact
    idx = haystack.find(needle)
    if idx != -1 and haystack.find(needle, idx + 1) == -1:
        return idx, idx + len(needle)
    if idx == -1:
        # Normalised
        norm_haystack = _normalize(haystack)
        norm_needle = _normalize(needle)
        norm_idx = norm_haystack.find(norm_needle)
        if norm_idx == -1:
            return None
        # Map back to original — find via sequence matcher
        matcher = difflib.SequenceMatcher(None, haystack, needle, autojunk=False)
        best = matcher.find_longest_match(0, len(haystack), 0, len(needle))
        if best.size < len(needle) * 0.8:
            return None
        # Expand around best match
        start = best.a
        end = best.a + best.size
        return start, end
    # Ambiguous exact match
    return None

async def _edit_handler(inp: dict) -> str:
    path = Path(inp["path"])
    if not path.exists():
        return f"Error: file not found: {path}"

    edits = inp.get("edits", [])
    if not edits:
        return "Error: edits list is empty"

    raw = path.read_bytes()
    # Detect line endings
    crlf = b"\r\n" in raw
    content = raw.decode("utf-8", errors="replace")
    # Normalise to LF for processing
    working = content.replace("\r\n", "\n")

    results: list[tuple[int, int, str]] = []  # (start, end, newText)
    for edit in edits:
        old = edit["oldText"].replace("\r\n", "\n")
        new = edit["newText"].replace("\r\n", "\n")
        span = _fuzzy_find(working, old)
        if span is None:
            return f"Error: oldText not found (or ambiguous) in {path}:\n{old!r}"
        results.append((span[0], span[1], new))

    # Check for overlaps
    results.sort(key=lambda x: x[0])
    for i in range(len(results) - 1):
        if results[i][1] > results[i + 1][0]:
            return "Error: edits overlap — ensure oldText snippets are non-overlapping"

    # Apply in reverse order to keep offsets valid
    for start, end, new in reversed(results):
        working = working[:start] + new + working[end:]

    if crlf:
        working = working.replace("\n", "\r\n")

    path.write_bytes(working.encode("utf-8"))
    return f"Edited {path} ({len(edits)} change(s))"
