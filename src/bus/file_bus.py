"""File-based bus with atomic operations."""

import json
import os
from pathlib import Path
from typing import Any, Dict
import tempfile
import shutil


def ensure_dir(path: Path) -> None:
    """Ensure directory exists."""
    path.mkdir(parents=True, exist_ok=True)


def write_atomic(path: Path, data: Dict[str, Any]) -> None:
    """
    Write JSON data atomically using temp file + rename.
    
    Args:
        path: Target file path
        data: Dictionary to write as JSON
    """
    ensure_dir(path.parent)
    
    # Write to temp file first
    fd, temp_path = tempfile.mkstemp(
        dir=path.parent,
        suffix='.tmp',
        text=True
    )
    
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        
        # Atomic rename
        shutil.move(temp_path, path)
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Dict[str, Any]:
    """
    Read JSON file safely.
    
    Args:
        path: File path to read
        
    Returns:
        Parsed JSON data
        
    Raises:
        FileNotFoundError: If file doesn't exist
        json.JSONDecodeError: If invalid JSON
    """
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


