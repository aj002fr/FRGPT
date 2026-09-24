"""Safe, deterministic artifact helpers for the isolated research program."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_ROOT = REPO_ROOT / "workspace" / "analysis" / "move_exhaustion_v2"
_DATA_STORE_CFG = yaml.safe_load(
    (Path(__file__).resolve().parent / "config" / "frozen" / "data_store.yaml").read_text(
        encoding="utf-8"
    )
)
STORE_ROOT = Path(_DATA_STORE_CFG["writes"]["derived_store"])
SOURCE_ROOT = Path(_DATA_STORE_CFG["source"]["mbp1_archive_root"])


def ensure_under(path: Path, root: Path) -> Path:
    """Resolve an output and reject paths outside its approved root."""
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Output {resolved} is outside approved root {root}") from exc
    return resolved


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write canonical indented JSON via same-directory atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def utc_run_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}"
