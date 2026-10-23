"""Append-only human-feedback audit and derived V2.6 daily statistics."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v26_live"
FEEDBACK_PATH = STORE_DIR / "feedback.jsonl"
ET = ZoneInfo("America/New_York")
ALERT_KINDS = {"lifo", "c018"}


class FeedbackStore:
    def __init__(self, path: Path = FEEDBACK_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(
        self,
        *,
        episode_id: str,
        alert_kind: str,
        correct: bool | None,
        note: str | None,
        previous_correct: bool | None,
        previous_status: str | None,
        feedback_ts: str | None = None,
    ) -> dict[str, Any]:
        if alert_kind not in ALERT_KINDS:
            raise ValueError(f"Unsupported alert_kind: {alert_kind}")
        timestamp = feedback_ts or datetime.now(timezone.utc).isoformat()
        entry = {
            "feedback_id": str(uuid.uuid4()),
            "feedback_ts_utc": timestamp,
            "episode_id": episode_id,
            "alert_kind": alert_kind,
            "correct": correct,
            "note": note,
            "previous_correct": previous_correct,
            "previous_status": previous_status,
            "last_grade_wins": True,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
                handle.flush()
        return entry

    def daily_payload(
        self,
        lifo_episodes: Iterable[dict[str, Any]],
        c018_episodes: Iterable[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now_utc = now or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        today_et = now_utc.astimezone(ET).date().isoformat()
        by_kind = {
            "lifo": [dict(ep) for ep in lifo_episodes if ep.get("et_date") == today_et],
            "c018": [dict(ep) for ep in c018_episodes if ep.get("et_date") == today_et],
        }
        pending_prompts = sorted(
            [
                ep
                for rows in by_kind.values()
                for ep in rows
                if ep.get("human_feedback_status") is None
            ],
            key=lambda ep: str(ep.get("lock_ts_utc") or ""),
        )
        recent = sorted(
            [ep for rows in by_kind.values() for ep in rows],
            key=lambda ep: str(ep.get("lock_ts_utc") or ""),
            reverse=True,
        )
        return {
            "date_et": today_et,
            "strategy_status": "PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE",
            "stats": {
                kind: self._stats(rows)
                for kind, rows in by_kind.items()
            },
            "pending_prompts": pending_prompts,
            "recent_episodes": recent[:50],
            "audit_path": str(self.path),
            "right_definition": "human grade only",
        }

    @staticmethod
    def _stats(episodes: list[dict[str, Any]]) -> dict[str, Any]:
        correct = [ep for ep in episodes if ep.get("human_correct") is True]
        wrong = [ep for ep in episodes if ep.get("human_correct") is False]
        graded = len(correct) + len(wrong)
        pending = len(episodes) - graded
        return {
            "issued": len(episodes),
            "pending_grade": pending,
            "graded": graded,
            "marked_correct": len(correct),
            "marked_wrong": len(wrong),
            "hit_rate_graded": len(correct) / graded if graded else None,
        }


__all__ = ["ALERT_KINDS", "FEEDBACK_PATH", "FeedbackStore", "STORE_DIR"]
