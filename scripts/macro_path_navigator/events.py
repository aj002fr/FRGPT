from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from scripts.macro_path_navigator.constants import (
    ADHOC_EVENTS_PATH,
    ECONOMIC_EVENTS_DB,
    ET_TZ,
    FOMC_SPEAKER_LEAN_PATH,
)

ET = ZoneInfo(ET_TZ)

THEME_MAP = {
    "inflation": ("CPI", "PPI", "PCE", "Inflation", "Prices Paid"),
    "labor": ("NFP", "UER", "JOLTS", "Claims", "ADP", "Hourly Earnings", "Payrolls", "Participation"),
    "growth": ("ISM", "Retail", "Durable", "GDP", "Empire", "Philly", "PMI", "Home Sales"),
}

EVENT_NAME_ALIASES = {
    "CPI YoY": "Inflation Rate YoY",
    "Core CPI YoY": "Core Inflation Rate YoY",
    "Core PCE MoM": "Core PCE Price Index MoM",
    "Core PCE YoY": "Core PCE Price Index YoY",
    "PCE MoM": "PCE Price Index MoM",
    "PCE YoY": "PCE Price Index YoY",
    "ISM Services": "ISM Non-Manufacturing PMI",
    "Average Hourly Earnings MoM": "Average Hourly Earnings MoM",
}


@dataclass
class CycleWindow:
    start: datetime
    end: datetime
    start_label: str
    end_label: str


def parse_event_datetime(value: str, default_time: tuple[int, int] = (14, 30)) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).replace("T", " ").strip()
    try:
        if raw.endswith("Z") or "+" in raw[10:] or "-" in raw[10:]:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=ET)
            return dt.astimezone(ET)
        if len(raw) >= 19:
            dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d")
            dt = dt.replace(hour=default_time[0], minute=default_time[1])
        return dt.replace(tzinfo=ET)
    except Exception:
        return None


def infer_theme(name: str) -> str:
    for theme, markers in THEME_MAP.items():
        if any(marker.lower() in name.lower() for marker in markers):
            return theme
    return "other"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def load_fomc_anchors() -> List[Dict[str, Any]]:
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT event_name, event_date, importance
            FROM economic_events
            WHERE event_name = 'Fed Press Conference'
              AND country LIKE '%United States%'
              AND event_date >= '2022-01-01'
            ORDER BY event_date ASC
            """
        ).fetchall()
    anchors = []
    for row in rows:
        dt = parse_event_datetime(row["event_date"])
        if dt:
            anchors.append({"event_name": row["event_name"], "event_date": dt.isoformat(), "importance": row["importance"] or "high"})
    return anchors


def current_cycle(now: Optional[datetime] = None) -> CycleWindow:
    now = now or datetime.now(ET)
    anchors = load_fomc_anchors()
    parsed = [(parse_event_datetime(row["event_date"]), row) for row in anchors]
    parsed = [(dt, row) for dt, row in parsed if dt]
    past = [(dt, row) for dt, row in parsed if dt <= now]
    future = [(dt, row) for dt, row in parsed if dt > now]
    start_dt, start_row = past[-1] if past else (now - timedelta(days=45), {"event_name": "Synthetic cycle start"})
    end_dt, end_row = future[0] if future else (start_dt + timedelta(days=45), {"event_name": "Projected next FOMC"})
    return CycleWindow(
        start=start_dt,
        end=end_dt,
        start_label=str(start_row.get("event_name") or "FOMC"),
        end_label=str(end_row.get("event_name") or "Next FOMC"),
    )


def _rolling_proxy_std(conn: sqlite3.Connection, alias_name: str, before: str) -> Optional[float]:
    rows = conn.execute(
        """
        SELECT actual, consensus
        FROM economic_events
        WHERE event_name = ?
          AND event_date < ?
          AND actual IS NOT NULL
          AND consensus IS NOT NULL
        ORDER BY event_date DESC
        LIMIT 24
        """,
        (alias_name, before),
    ).fetchall()
    values = [float(r["actual"]) - float(r["consensus"]) for r in rows]
    if len(values) < 5:
        return None
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / max(len(values) - 1, 1)
    return var ** 0.5 if var > 0 else None


def _proxy_surprise(conn: sqlite3.Connection, event_name: str, event_dt: datetime) -> tuple[Optional[float], Optional[float], Optional[float], str]:
    alias = EVENT_NAME_ALIASES.get(event_name)
    if not alias:
        return None, None, None, "none"
    row = conn.execute(
        """
        SELECT actual, consensus
        FROM economic_events
        WHERE event_name = ?
          AND date(event_date) = date(?)
          AND actual IS NOT NULL
          AND consensus IS NOT NULL
        ORDER BY event_date DESC
        LIMIT 1
        """,
        (alias, event_dt.strftime("%Y-%m-%d")),
    ).fetchone()
    if not row:
        return None, None, None, "none"
    proxy_std = _rolling_proxy_std(conn, alias, event_dt.strftime("%Y-%m-%d %H:%M:%S"))
    actual = float(row["actual"])
    consensus = float(row["consensus"])
    z = (actual - consensus) / proxy_std if proxy_std else None
    return actual, consensus, z, "proxy-z" if z is not None else "partial"


def load_cycle_events(window: CycleWindow) -> List[Dict[str, Any]]:
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    start = window.start.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")
    end = window.end.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, event_name, actual, consensus, consensus_std_dev, tier, category, units
            FROM bbg_data
            WHERE timestamp >= ?
              AND timestamp < ?
            ORDER BY timestamp ASC, event_name ASC
            """,
            (start, end),
        ).fetchall()
        events: List[Dict[str, Any]] = []
        for row in rows:
            dt = parse_event_datetime(row["timestamp"], default_time=(8, 30))
            if not dt:
                continue
            actual = row["actual"]
            consensus = row["consensus"]
            std = row["consensus_std_dev"]
            surprise_status = "none"
            z = None
            if actual is not None and consensus is not None and std not in (None, 0):
                z = (float(actual) - float(consensus)) / float(std)
                surprise_status = "full-z"
            elif actual is not None:
                proxy_actual, proxy_consensus, proxy_z, proxy_status = _proxy_surprise(conn, row["event_name"], dt)
                actual = actual if actual is not None else proxy_actual
                consensus = consensus if consensus is not None else proxy_consensus
                z = proxy_z
                surprise_status = proxy_status
            events.append(
                {
                    "id": f"bbg:{row['event_name']}:{dt.isoformat()}",
                    "name": row["event_name"],
                    "ts": dt.isoformat(),
                    "type": "economic",
                    "theme": infer_theme(row["event_name"]),
                    "actual": actual,
                    "consensus": consensus,
                    "consensus_std_dev": std,
                    "z": z,
                    "surprise_status": surprise_status,
                    "tier": row["tier"],
                    "category": row["category"],
                    "units": row["units"],
                }
            )
    return events


def load_events_between(start: str, end: str) -> List[Dict[str, Any]]:
    start_dt = parse_event_datetime(start)
    end_dt = parse_event_datetime(end)
    if start_dt is None or end_dt is None:
        raise ValueError("start/end must be parseable datetimes")
    window = CycleWindow(start=start_dt, end=end_dt, start_label="range", end_label="range")
    return load_cycle_events(window)


def load_adhoc_events() -> List[Dict[str, Any]]:
    if not ADHOC_EVENTS_PATH.exists():
        return []
    try:
        raw = json.loads(ADHOC_EVENTS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def save_adhoc_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    ADHOC_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    events = load_adhoc_events()
    ts = payload.get("ts") or payload.get("datetime")
    if not ts:
        raise ValueError("ad-hoc event requires ts/datetime")
    event = {
        "id": payload.get("id") or f"adhoc:{datetime.now(timezone.utc).timestamp()}",
        "name": str(payload.get("name") or "Ad-hoc event"),
        "ts": ts,
        "type": str(payload.get("type") or "other"),
        "theme": str(payload.get("theme") or "other"),
        "prior_direction": payload.get("prior_direction"),
        "prior_magnitude_bps": payload.get("prior_magnitude_bps"),
        "assumption": True,
    }
    events.append(event)
    ADHOC_EVENTS_PATH.write_text(json.dumps(events, indent=2, sort_keys=True), encoding="utf-8")
    return event


def load_fomc_speaker_lean() -> Dict[str, Any]:
    if not FOMC_SPEAKER_LEAN_PATH.exists():
        return {"members": {}, "note": "Seed this file from the ITC Markets FOMC hawk/dove cheat sheet."}
    try:
        return json.loads(FOMC_SPEAKER_LEAN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"members": {}, "error": "Unable to parse FOMC speaker lean config."}


def cycle_payload() -> Dict[str, Any]:
    window = current_cycle()
    events = load_cycle_events(window)
    adhoc = [
        row for row in load_adhoc_events()
        if window.start.isoformat() <= str(row.get("ts", "")) <= window.end.isoformat()
    ]
    merged = sorted([*events, *adhoc], key=lambda row: str(row.get("ts") or ""))
    return {
        "cycle": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "start_label": window.start_label,
            "end_label": window.end_label,
        },
        "events": merged,
        "counts": {
            "economic": len(events),
            "adhoc": len(adhoc),
            "full_z": sum(1 for row in events if row.get("surprise_status") == "full-z"),
            "proxy_z": sum(1 for row in events if row.get("surprise_status") == "proxy-z"),
            "none": sum(1 for row in events if row.get("surprise_status") == "none"),
        },
    }

