from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from scripts.macro_path_navigator.constants import IRAN_HEADLINES_DB

HIGH_SIGNAL_SOURCES = {
    "bbg_headline",
    "forwarded_firstsquawk",
    "forwarded_financialjuice",
    "forwarded_livesquawk",
    "pasted_financialjuice",
}


def is_high_signal_source(source: str) -> bool:
    source = (source or "").strip().lower()
    return source in HIGH_SIGNAL_SOURCES or source.startswith("forwarded_tweet_")


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _truncate(text: str, limit: int = 180) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _bucket_clusters(rows: List[Dict[str, Any]], bucket: str) -> List[Dict[str, Any]]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["ts_dt"] = pd.to_datetime(df["ts"], utc=True)
    clusters: List[Dict[str, Any]] = []
    for bucket_ts, group in df.groupby(pd.Grouper(key="ts_dt", freq=bucket)):
        if group.empty:
            continue
        samples = [
            {
                "ts": row["ts"],
                "source": row["source"],
                "text": _truncate(row["text"], 120),
            }
            for row in group.head(5).to_dict(orient="records")
        ]
        clusters.append({
            "ts": bucket_ts.isoformat(),
            "count": int(len(group)),
            "samples": samples,
        })
    return clusters


def load_headlines(
    start: str,
    end: str,
    limit: int = 500,
    bucket: Optional[str] = None,
) -> Dict[str, Any]:
    if not IRAN_HEADLINES_DB.exists():
        return {
            "rows": [],
            "clusters": [],
            "filtered_sources": sorted(HIGH_SIGNAL_SOURCES),
            "error": f"Missing DB: {IRAN_HEADLINES_DB}",
        }

    start_dt = _parse_ts(start)
    end_dt = _parse_ts(end)
    if end_dt <= start_dt:
        raise ValueError("end must be after start")

    with sqlite3.connect(str(IRAN_HEADLINES_DB)) as conn:
        conn.row_factory = sqlite3.Row
        raw_rows = conn.execute(
            """
            SELECT timestamp, source, text, telegram_message_id
            FROM headlines
            WHERE timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (start_dt.isoformat(), end_dt.isoformat(), int(limit)),
        ).fetchall()

    rows = []
    for row in raw_rows:
        source = str(row["source"] or "").lower()
        if not is_high_signal_source(source):
            continue
        rows.append({
            "ts": row["timestamp"],
            "source": source,
            "text": _truncate(row["text"]),
            "telegram_message_id": row["telegram_message_id"],
        })

    return {
        "rows": rows,
        "clusters": _bucket_clusters(rows, bucket) if bucket else [],
        "filtered_sources": sorted(HIGH_SIGNAL_SOURCES),
    }

