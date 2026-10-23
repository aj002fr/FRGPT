"""Resolved-path allowlists and read-only SQLite access."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

_FORBIDDEN = (
    "_live", "aggression_dashboard", "tailscale", "telegram", "sod",
)


def contained_path(
    path: str | Path,
    roots: list[str | Path] | tuple[str | Path, ...],
    *,
    must_exist: bool = True,
) -> Path:
    """Resolve a path and require it to remain under an allowed real root."""
    candidate = Path(path)
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    resolved = candidate.resolve(strict=must_exist)
    text = str(resolved).lower()
    if any(token in text for token in _FORBIDDEN):
        raise PermissionError(f"forbidden research path: {resolved}")
    allowed = [Path(root).resolve(strict=True) for root in roots]
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise PermissionError(f"path escapes allowlist: {resolved}")
    return resolved


def open_sqlite_readonly(
    path: str | Path,
    roots: list[str | Path] | tuple[str | Path, ...],
) -> sqlite3.Connection:
    resolved = contained_path(path, roots)
    if resolved.as_posix().startswith("//"):
        # Python's Windows SQLite build rejects URI authorities; preserve UNC
        # backslashes so the path is interpreted as a filename.
        uri = f"file:{resolved}?mode=ro&immutable=1"
    else:
        uri = f"file:{quote(resolved.as_posix(), safe='/:')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def assert_no_forbidden_source(source: str) -> None:
    lowered = source.lower()
    forbidden = (
        "aggression_dashboard", "_live", "socket.", "requests.", "urllib.",
        "subprocess.", "os.system", "taskkill", "telegram", "tailscale",
    )
    hits = [token for token in forbidden if token in lowered]
    if hits:
        raise PermissionError(f"forbidden candidate source tokens: {hits}")

