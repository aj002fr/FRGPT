from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "event_parent_components.json"


@lru_cache(maxsize=1)
def load_event_parent_components() -> Dict[str, Any]:
    """Load the canonical parent-event -> component mapping from JSON."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing event parent/component config: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_parents() -> List[str]:
    """Return the list of parent keys (e.g. 'NFP', 'CPI', 'JOLTS')."""
    return list(load_event_parent_components().get("parents", {}).keys())


def get_components(parent: str) -> List[Dict[str, Any]]:
    """Return the component dicts for a given parent key (case-insensitive)."""
    parents = load_event_parent_components().get("parents", {})
    if parent in parents:
        return parents[parent].get("components", [])
    lower = {k.lower(): k for k in parents}
    key = lower.get(str(parent).lower())
    return parents[key].get("components", []) if key else []


def get_parent_of(event_name: str) -> Optional[str]:
    """Return the parent key that owns the given component event_name (case-insensitive)."""
    needle = str(event_name).strip().lower()
    for parent_key, parent in load_event_parent_components().get("parents", {}).items():
        for comp in parent.get("components", []):
            if comp.get("event_name", "").strip().lower() == needle:
                return parent_key
    return None


def get_headline_component(parent: str) -> Optional[Dict[str, Any]]:
    """Return the headline component for a parent, or the first component."""
    comps = get_components(parent)
    if not comps:
        return None
    for comp in comps:
        if comp.get("headline"):
            return comp
    return comps[0]


def iter_components() -> List[Dict[str, Any]]:
    """Flatten every component across all parents, annotated with its parent key."""
    rows: List[Dict[str, Any]] = []
    for parent_key, parent in load_event_parent_components().get("parents", {}).items():
        for comp in parent.get("components", []):
            row = dict(comp)
            row["parent"] = parent_key
            row.setdefault("category", parent.get("category"))
            row.setdefault("tier", parent.get("tier"))
            rows.append(row)
    return rows
