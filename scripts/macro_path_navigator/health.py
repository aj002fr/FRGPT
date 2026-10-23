from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from scripts.macro_path_navigator.constants import LOCAL_STATUS_DIR


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_seconds(value: Optional[str]) -> Optional[float]:
    dt = parse_iso(value)
    if dt is None:
        return None
    return max(0.0, (utc_now() - dt).total_seconds())


def write_marker(name: str, payload: Dict[str, Any]) -> None:
    LOCAL_STATUS_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_STATUS_DIR / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def feed_status(last_ts: Optional[str], stale_after: float, down_after: Optional[float] = None, error: Optional[str] = None) -> Dict[str, Any]:
    age = age_seconds(last_ts)
    if error and last_ts is None:
        state = "down"
    elif age is None:
        state = "down"
    elif down_after is not None and age > down_after:
        state = "down"
    elif age > stale_after:
        state = "stale"
    else:
        state = "live"
    return {
        "status": state,
        "last_ts": last_ts,
        "age_seconds": age,
        "error": error,
    }


@dataclass
class NavigatorHealth:
    feeds: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)
    attribution: Dict[str, Any] = field(default_factory=dict)
    last_error: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update_feed(self, name: str, status: Dict[str, Any]) -> None:
        with self._lock:
            self.feeds[name] = status
            write_marker(f"macro_path_{name}.txt", {"updated_at": iso_now(), **status})

    def update_state(self, **kwargs: Any) -> None:
        with self._lock:
            self.state.update(kwargs)
            write_marker("macro_path_heartbeat.txt", {"updated_at": iso_now(), **self.state})

    def set_error(self, error: Optional[str]) -> None:
        with self._lock:
            self.last_error = error
            if error:
                write_marker("macro_path_last_error.txt", {"updated_at": iso_now(), "error": error})

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            feeds = dict(self.feeds)
            state = dict(self.state)
            attribution = dict(self.attribution)
            error = self.last_error
        feed_states = [row.get("status") for row in feeds.values()]
        overall = "down" if "down" in feed_states or error else "stale" if "stale" in feed_states else "ok"
        return {
            "ok": overall == "ok",
            "status": overall,
            "updated_at": iso_now(),
            "feeds": feeds,
            "state": state,
            "attribution": attribution,
            "last_error": error,
        }

