"""Canonical hashing, frozen snapshots, and atomic publication."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_object(value: Any) -> str:
    return digest_bytes(canonical_bytes(value))


def canonical_spec_hash(spec: Any) -> str:
    """Hash a methodology spec while excluding its self-referential hash slot."""
    normalized = deepcopy(spec)
    try:
        normalized["hashes"]["spec_hash"] = "0" * 64
    except (KeyError, TypeError) as exc:
        raise ValueError("spec must contain hashes.spec_hash") from exc
    return digest_object(normalized)


def effective_spec_hash(base_spec_hash: str, execution_contract: Any) -> str:
    """Bind an immutable methodology hash to its canonical execution addendum."""
    if (
        not isinstance(base_spec_hash, str)
        or len(base_spec_hash) != 64
        or any(char not in "0123456789abcdef" for char in base_spec_hash)
    ):
        raise ValueError("base_spec_hash must be lowercase SHA-256")
    return digest_bytes(base_spec_hash.encode("ascii") + canonical_bytes(execution_contract))


def digest_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: str | Path, content: bytes) -> str:
    """Publish bytes with fsync + replace; return the published SHA-256."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return digest_bytes(content)


def freeze_json(path: str | Path, value: Any) -> str:
    """Create a canonical immutable snapshot; refuse conflicting overwrites."""
    content = canonical_bytes(value) + b"\n"
    destination = Path(path)
    if destination.exists():
        existing = destination.read_bytes()
        if existing != content:
            raise FileExistsError(f"frozen snapshot differs: {destination}")
        return digest_bytes(existing)
    atomic_write(destination, content)
    return digest_bytes(content)


def verify_file(path: str | Path, expected_hash: str) -> None:
    if digest_file(path) != expected_hash:
        raise ValueError(f"hash mismatch: {path}")

