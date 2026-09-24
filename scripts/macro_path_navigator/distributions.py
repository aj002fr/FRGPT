from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from rules.treasury_price_conversion import bps_to_points, points_to_bps
from scripts.macro_path_navigator.constants import ECONOMIC_EVENTS_DB, ET_TZ, STATE_DIR
from scripts.macro_path_navigator.events import EVENT_NAME_ALIASES, infer_theme, parse_event_datetime
from src.servers.distro_analytics.db_reader import get_pg_connection

ET = ZoneInfo(ET_TZ)
DIST_CACHE_DIR = STATE_DIR / "dist_cache"

WINDOWS = {
    "2h": 2.0,
    "eod": "eod",
}

CANONICAL_EVENT_ALIASES = {
    "NFP": "NFP",
    "NON FARM": "NFP",
    "NONFARM": "NFP",
    "UNEMPLOYMENT": "UER",
    "UER": "UER",
    "JOLTS": "JOLTS Job Openings",
    "ADP": "ADP Monthly Employment Change",
    "JOBLESS": "Initial Jobless Claims",
    "INITIAL CLAIMS": "Initial Jobless Claims",
    "CPI": "CPI MoM",
    "CORE CPI": "Core CPI MoM",
    "PPI": "PPI MoM",
    "CORE PPI": "Core PPI MoM",
    "RETAIL SALES": "Retail Sales",
    "RETAIL CONTROL": "Retail Sales Control Group",
    "DURABLE": "Durable Goods Orders",
    "GDP": "GDP Annualized QoQ",
    "PHILLY": "Philly Fed Index",
    "EMPIRE": "NY Fed Empire State Manufacturing Index",
    "ISM MANUFACTURING": "ISM Manufacturing Prices Paid",
    "ISM SERVICES": "ISM Services",
    "CONSUMER CONFIDENCE": "U-Mich Current Conditions",
}


def canonical_event_name(name: str) -> str:
    upper = (name or "").upper()
    for key, canonical in CANONICAL_EVENT_ALIASES.items():
        if key in upper:
            return canonical
    # Strip month suffixes like "NFP JUN" only after alias matching.
    parts = upper.split()
    month_codes = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"}
    if parts and parts[-1] in month_codes:
        return " ".join(name.split()[:-1]) or name
    return name


def _release_time_for_event(name: str) -> time:
    upper = (name or "").upper()
    if "JOLTS" in upper or "ISM" in upper or "CONSUMER CONFIDENCE" in upper:
        return time(10, 0)
    if "ADP" in upper:
        return time(8, 15)
    if "PMI" in upper or "CHICAGO" in upper:
        return time(9, 45)
    return time(8, 30)


def _event_dt(value: str, event_name: str) -> Optional[datetime]:
    dt = parse_event_datetime(value, default_time=(8, 30))
    if dt is None:
        return None
    if dt.hour == 0 and dt.minute == 0:
        rt = _release_time_for_event(event_name)
        dt = dt.replace(hour=rt.hour, minute=rt.minute, second=0, microsecond=0)
    return dt


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    return conn


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
    series = pd.Series(values, dtype="float64")
    std = float(series.std())
    return std if std > 0 else None


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


def event_instances(event_name: str, min_date: str = "2022-01-01") -> List[Dict[str, Any]]:
    canonical = canonical_event_name(event_name)
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, event_name, actual, consensus, consensus_std_dev, tier, category, units
            FROM bbg_data
            WHERE event_name = ?
              AND timestamp >= ?
              AND actual IS NOT NULL
            ORDER BY timestamp ASC
            """,
            (canonical, min_date),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            dt = _event_dt(row["timestamp"], canonical)
            if dt is None:
                continue
            actual = row["actual"]
            consensus = row["consensus"]
            std = row["consensus_std_dev"]
            z = None
            status = "none"
            if actual is not None and consensus is not None and std not in (None, 0):
                z = (float(actual) - float(consensus)) / float(std)
                status = "full-z"
            elif actual is not None:
                proxy_actual, proxy_consensus, proxy_z, proxy_status = _proxy_surprise(conn, canonical, dt)
                actual = actual if actual is not None else proxy_actual
                consensus = consensus if consensus is not None else proxy_consensus
                z = proxy_z
                status = proxy_status
            out.append({
                "event_name": canonical,
                "source_name": event_name,
                "ts": dt.isoformat(),
                "actual": actual,
                "consensus": consensus,
                "consensus_std_dev": std,
                "z": z,
                "surprise_status": status,
                "theme": infer_theme(canonical),
                "tier": row["tier"],
                "category": row["category"],
                "units": row["units"],
            })
    return out


def _window_end(start_et: datetime, window: str) -> datetime:
    key = str(window or "2h").lower()
    if key == "eod":
        return start_et.replace(hour=16, minute=0, second=0, microsecond=0)
    hours = float(WINDOWS.get(key, 2.0))
    return start_et + timedelta(hours=hours)


def _cache_path(event_name: str, window: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in canonical_event_name(event_name).lower()).strip("_")
    return DIST_CACHE_DIR / f"{safe}_{window.lower()}.json"


def reaction_moves(event_name: str, window: str = "2h", force: bool = False) -> List[Dict[str, Any]]:
    path = _cache_path(event_name, window)
    if path.exists() and not force:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached:
                return cached
        except Exception:
            pass

    instances = []
    for inst in event_instances(event_name):
        start_et = _event_dt(inst["ts"], inst["event_name"])
        if start_et is None:
            continue
        end_et = _window_end(start_et, window)
        if end_et <= start_et:
            continue
        instances.append({
            **inst,
            "_start_utc": start_et.astimezone(timezone.utc),
            "_end_utc": end_et.astimezone(timezone.utc),
            "_start_et": start_et,
            "_end_et": end_et,
        })

    if not instances:
        return []

    clauses = []
    params: list[Any] = []
    for inst in instances:
        clauses.append("(ts_event >= %s::timestamptz AND ts_event <= %s::timestamptz)")
        params.extend([
            inst["_start_utc"].strftime("%Y-%m-%d %H:%M:%S+00"),
            inst["_end_utc"].strftime("%Y-%m-%d %H:%M:%S+00"),
        ])
    sql = f"""
        SELECT ts_event, open, high, low, close, volume
        FROM ohlcv_1m
        WHERE symbol ILIKE %s
          AND ({' OR '.join(clauses)})
        ORDER BY ts_event ASC
    """
    params = ["TY%", *params]
    conn = get_pg_connection()
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()
    if df.empty:
        return []
    df["ts_event"] = pd.to_datetime(df["ts_event"], utc=True)
    df.set_index("ts_event", inplace=True)

    rows = []
    for inst in instances:
        win = df[(df.index >= inst["_start_utc"]) & (df.index <= inst["_end_utc"])]
        if win.empty:
            continue
        open_px = float(win.iloc[0]["open"])
        close_px = float(win.iloc[-1]["close"])
        high_px = float(win["high"].max())
        low_px = float(win["low"].min())
        signed_bps = points_to_bps(close_px - open_px)
        max_up_bps = points_to_bps(high_px - open_px)
        max_down_bps = points_to_bps(low_px - open_px)
        clean_inst = {k: v for k, v in inst.items() if not k.startswith("_")}
        rows.append({
            **clean_inst,
            "window": window,
            "start": inst["_start_et"].isoformat(),
            "end": inst["_end_et"].isoformat(),
            "signed_bps": float(signed_bps),
            "max_up_bps": float(max_up_bps),
            "max_down_bps": float(max_down_bps),
            "open": open_px,
            "close": close_px,
        })

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return rows


def compute_distro_stats(values: List[float]) -> Dict[str, Any]:
    clean = pd.Series([v for v in values if v is not None and math.isfinite(float(v))], dtype="float64")
    if clean.empty:
        return {"n": 0}
    qs = clean.quantile([0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    rally = clean[clean > 0]
    selloff = clean[clean < 0]
    return {
        "n": int(clean.size),
        "mean": float(clean.mean()),
        "median": float(clean.median()),
        "std": float(clean.std()) if clean.size > 1 else 0.0,
        "p1": float(qs.loc[0.01]),
        "p5": float(qs.loc[0.05]),
        "p25": float(qs.loc[0.25]),
        "p50": float(qs.loc[0.5]),
        "p75": float(qs.loc[0.75]),
        "p95": float(qs.loc[0.95]),
        "p99": float(qs.loc[0.99]),
        "rally_prob": float((clean > 0).mean()),
        "selloff_prob": float((clean < 0).mean()),
        "expected_rally": float(rally.mean()) if not rally.empty else 0.0,
        "expected_selloff": float(selloff.mean()) if not selloff.empty else 0.0,
    }


def _scenario_filter(rows: List[Dict[str, Any]], scenario: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    scenario = (scenario or "baseline").lower()
    if scenario in ("baseline", "all", ""):
        return rows, {"mode": "baseline", "description": "Unconditional historical baseline"}
    z_rows = [row for row in rows if row.get("z") is not None]
    if not z_rows:
        return rows, {"mode": "baseline", "description": f"Scenario '{scenario}' unavailable: no usable historical z-scores"}
    if scenario == "hot":
        filtered = [row for row in z_rows if float(row["z"]) >= 0.75]
        desc = "z >= +0.75"
    elif scenario == "cool":
        filtered = [row for row in z_rows if float(row["z"]) <= -0.75]
        desc = "z <= -0.75"
    elif scenario == "inline":
        filtered = [row for row in z_rows if abs(float(row["z"])) < 0.75]
        desc = "|z| < 0.75"
    else:
        return rows, {"mode": "baseline", "description": f"Unknown scenario '{scenario}', using baseline"}
    return filtered, {"mode": scenario, "description": desc}


def build_distribution(event_name: str, window: str = "2h", scenario: str = "baseline") -> Dict[str, Any]:
    all_rows = reaction_moves(event_name, window=window)
    filtered, conditioning = _scenario_filter(all_rows, scenario)
    values = [float(row["signed_bps"]) for row in filtered if row.get("signed_bps") is not None]
    statuses = sorted({row.get("surprise_status") or "none" for row in all_rows})
    return {
        "event_name": canonical_event_name(event_name),
        "source_name": event_name,
        "window": window,
        "scenario": scenario or "baseline",
        "conditioning": conditioning,
        "values_bps": values,
        "rows": filtered,
        "stats": compute_distro_stats(values),
        "n": len(all_rows),
        "n_eff": len(filtered),
        "surprise_statuses": statuses,
    }


def project_distribution(dist: Dict[str, Any], current_price: Optional[float]) -> Dict[str, Any]:
    stats = dist.get("stats") or {}
    if current_price is None or not stats or not stats.get("n"):
        return {}
    keys = ["p5", "p25", "p50", "p75", "p95"]
    return {
        key: float(current_price) + bps_to_points(float(stats[key]))
        for key in keys
        if key in stats and stats[key] is not None
    }


def upcoming_distribution_events(limit: int = 6) -> List[Dict[str, Any]]:
    now_et = datetime.now(ET)
    horizon = now_et + timedelta(days=14)
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT event_name, event_date, importance, actual, consensus
            FROM economic_events
            WHERE event_date >= ?
              AND event_date <= ?
              AND country LIKE '%United States%'
            ORDER BY event_date ASC
            LIMIT 80
            """,
            (
                now_et.strftime("%Y-%m-%d %H:%M:%S"),
                horizon.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        raw_name = str(row["event_name"])
        canonical = canonical_event_name(raw_name)
        # Keep only events with some historical bbg_data footprint.
        if not event_instances(canonical):
            continue
        dt = _event_dt(row["event_date"], canonical)
        if dt is None:
            continue
        key = (canonical, dt.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": f"upcoming:{canonical}:{dt.isoformat()}",
            "name": raw_name,
            "canonical_name": canonical,
            "ts": dt.isoformat(),
            "theme": infer_theme(canonical),
            "importance": row["importance"],
            "type": "economic",
        })
        if len(out) >= limit:
            break
    return out

