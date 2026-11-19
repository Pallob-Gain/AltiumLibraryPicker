"""File validation helpers."""

from __future__ import annotations

from pathlib import Path

# .SchLib / .PcbLib / .IntLib are OLE compound documents.
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def is_ole_compound(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == OLE_MAGIC
    except OSError:
        return False


def check_library(path: str, ext: str) -> str | None:
    """Return an error message, or None if the file looks like a valid Altium library."""
    if not path:
        return "No file selected."
    p = Path(path)
    if p.suffix.lower() != ext.lower():
        return f"Expected a {ext} file, got '{p.suffix}'."
    if not p.is_file():
        return f"File not found: {p}"
    if not is_ole_compound(p):
        return f"{p.name} is not a binary Altium library (ASCII-format or corrupted file?)."
    return None
