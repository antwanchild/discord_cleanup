"""
file_utils.py — Standalone file persistence helpers with no app-level imports.
"""
import json
import os
import tempfile


def atomic_write_text(path: str, content: str) -> None:
    """Atomically writes text content to a file using a same-directory temporary file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(temp_path, path)
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        finally:
            raise


def atomic_write_json(path: str, payload: dict) -> None:
    """Atomically writes JSON content to disk."""
    atomic_write_text(path, json.dumps(payload, indent=2))
