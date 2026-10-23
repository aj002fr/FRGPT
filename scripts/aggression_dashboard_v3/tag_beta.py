"""Tagged day-level reference beta for Aggression V3.

One beta per day tag (2Y/3Y/5Y/7Y/10Y/30Y auction, other auction, month-end, tier-1, regular).
Each is the flow-history OLS of day bps on net DV01, fitted on closed days of that tag only.
Day points come from the V1 1-minute flow store, opened read-only; tags come from the
economic-events last-good file. Postgres is never queried.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.core import (
    EASTERN,
    LIVE_STORE_DIR,
    PROJECT_ROOT,
    load_dv01_table,
    normalize_symbol,
    trading_day_start,
)

logger = logging.getLogger(__name__)

TAG_BETA_VERSION = "tag_day_ols_v1"
TAG_BETA_PATH = LIVE_STORE_DIR / "tag_beta.json"
V1_FLOW_DB = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_live" / "flow_history.sqlite"
EVENTS_CACHE_DIR_ENV = "EVENTS_CACHE_DIR"

TENOR_TAGS = ("2Y", "3Y", "5Y", "7Y", "10Y", "30Y")
TAGS = TENOR_TAGS + ("AUCTION_OTHER", "MONTH_END", "TIER1", "REGULAR")
TIER1_FAMILIES = frozenset({"nfp", "cpi", "fomc", "pce", "gdp"})
MONTH_END_DAYS = 3
MIN_TAG_DAYS = 2
BPS_PER_POINT = 64.0 * 0.25  # flow-history house rule: 4 ticks of 1/64 = 1 bp
RECHECK_DAYS = 5
FULL_BUILD_CHUNK_DAYS = 31
_TENOR_RE = re.compile(r"(\d+)\s*-?\s*Y", re.I)

_LOCK = threading.Lock()


# --- clock ---------------------------------------------------------------------------------------


def session_date_for(now: Optional[datetime] = None) -> date:
    """Close date of the session the live page shows: 18:00 ET opens it, Friday/Saturday roll to Friday."""
    open_et = trading_day_start(now).astimezone(EASTERN)
    while open_et.weekday() in (4, 5):
        open_et -= timedelta(days=1)
    return (open_et + timedelta(days=1)).date()


def _session_day(ts_et: pd.Series) -> pd.Series:
    """Close date for each ET stamp; prints in the Mon-Thu halt or the weekend close are NaT."""
    weekday = ts_et.dt.weekday
    tod = ts_et.dt.hour * 60 + ts_et.dt.minute
    closed = (
        (weekday == 5)
        | ((weekday == 6) & (tod < 18 * 60))
        | ((weekday == 4) & (tod >= 17 * 60))
        | ((weekday <= 3) & (tod >= 17 * 60) & (tod < 18 * 60))
    )
    day = ts_et.dt.normalize().dt.tz_localize(None)
    day = day.where(tod < 18 * 60, day + pd.Timedelta(days=1))
    return day.where(~closed)


# --- tags ----------------------------------------------------------------------------------------


def _events_dir() -> Path:
    override = os.environ.get(EVENTS_CACHE_DIR_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "market_data_puller" / "cache"


def load_events(folder: Optional[Path] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Newest events last-good file. Empty list and None when there is none."""
    root = folder or _events_dir()
    try:
        files = sorted(root.glob("events_tagged_*.json"), key=lambda path: path.stat().st_mtime)
    except OSError:
        files = []
    if not files:
        return [], None
    try:
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        logger.exception("V3 tag beta could not read %s", files[-1])
        return [], None
    return list(payload.get("events") or []), files[-1].name


def _by_date(events: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        if event.get("date"):
            out.setdefault(str(event["date"])[:10], []).append(event)
    return out


def is_month_end(day: date, n: int = MONTH_END_DAYS) -> bool:
    first = pd.Timestamp(year=day.year, month=day.month, day=1)
    business = pd.bdate_range(first, first + pd.offsets.MonthEnd(0))
    return day in {stamp.date() for stamp in business[-n:]}


def day_tag(day: date, events_by_date: Dict[str, List[Dict[str, Any]]]) -> Tuple[str, str]:
    """One tag per day: auction (tenor, else other), then month-end, then tier-1, else regular."""
    hits = events_by_date.get(day.isoformat(), [])
    auctions = [event for event in hits if event.get("family") == "auction"]
    if auctions:
        tenors = set()
        for event in auctions:
            match = _TENOR_RE.search(str(event.get("event_name") or ""))
            tenors.add(f"{match.group(1)}Y" if match else "?")
        names = ", ".join(sorted({str(event.get("event_name")) for event in auctions}))
        if len(tenors) == 1 and next(iter(tenors)) in TENOR_TAGS:
            return next(iter(tenors)), names
        return "AUCTION_OTHER", names
    if is_month_end(day):
        return "MONTH_END", "last 3 business days"
    tier1 = sorted({str(event.get("event_name")) for event in hits if event.get("family") in TIER1_FAMILIES})
    if tier1:
        return "TIER1", ", ".join(tier1)
    return "REGULAR", ""


# --- day points ----------------------------------------------------------------------------------


def _open_v1(path: Path) -> sqlite3.Connection:
    """Plain path (SQLite URIs reject the UNC share) with query_only; never creates the file."""
    if not Path(path).is_file():
        raise sqlite3.OperationalError(f"V1 flow store not found: {path}")
    conn = sqlite3.connect(str(path), timeout=5)
    conn.execute("PRAGMA query_only=ON")
    return conn


class V1ReadError(RuntimeError):
    pass


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def read_day_points(
    first_day: date,
    last_day: date,
    *,
    price_symbol: str,
    aggression_symbols: Iterable[str],
    dv01_adjust: bool,
    dv01_table: Dict[str, float],
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Closed-session points in [first_day, last_day]: x = net aggression, y = day bps, flow-history style.

    Day bps uses the first and last minute trade_price of the price symbol, as the flow-history cards do.
    Net aggression is contracts times V3's DV01 table (or contracts), so it is in V3's own unit.
    """
    if last_day < first_day:
        return []
    db_path = db_path or V1_FLOW_DB
    symbols = tuple(dict.fromkeys(normalize_symbol(s) for s in aggression_symbols))
    wanted = tuple(dict.fromkeys(symbols + (normalize_symbol(price_symbol),)))
    placeholders = ", ".join("?" for _ in wanted)
    sql = (
        "SELECT ts_utc, symbol, buy_contracts, sell_contracts, trade_price FROM v1_aggress_flow_1m "
        f"WHERE symbol IN ({placeholders}) AND ts_utc >= ? AND ts_utc < ? ORDER BY ts_utc"
    )
    frames = []
    cursor = first_day
    try:
        conn = _open_v1(db_path)
    except sqlite3.Error as exc:
        raise V1ReadError(f"could not open {db_path}: {exc}") from exc
    try:
        while cursor <= last_day:
            chunk_end = min(last_day, cursor + timedelta(days=FULL_BUILD_CHUNK_DAYS - 1))
            start = datetime.combine(cursor - timedelta(days=1), time(18, 0), tzinfo=EASTERN)
            end = datetime.combine(chunk_end, time(17, 0), tzinfo=EASTERN)
            rows = conn.execute(sql, (*wanted, _utc_text(start), _utc_text(end))).fetchall()
            if rows:
                frames.append(pd.DataFrame(rows, columns=["ts_utc", "symbol", "buy", "sell", "price"]))
            cursor = chunk_end + timedelta(days=1)
    except sqlite3.Error as exc:
        raise V1ReadError(f"read failed: {exc}") from exc
    finally:
        conn.close()
    if not frames:
        return []
    frame = pd.concat(frames, ignore_index=True)
    ts = pd.to_datetime(frame["ts_utc"], utc=True).dt.tz_convert(EASTERN)
    frame["day"] = _session_day(ts)
    frame = frame.dropna(subset=["day"])
    unit = frame["symbol"].map(lambda s: float(dv01_table.get(s, 1.0)) if dv01_adjust else 1.0)
    frame["net"] = (frame["buy"].astype(float).fillna(0.0) - frame["sell"].astype(float).fillna(0.0)) * unit
    frame.loc[~frame["symbol"].isin(symbols), "net"] = 0.0
    priced = frame.loc[(frame["symbol"] == normalize_symbol(price_symbol)) & frame["price"].notna()]
    first_px = priced.groupby("day")["price"].first()
    last_px = priced.groupby("day")["price"].last()
    net = frame.groupby("day")["net"].sum()
    points = []
    for day in sorted(first_px.index):
        stamp = pd.Timestamp(day).date()
        if stamp < first_day or stamp > last_day:
            continue
        points.append({
            "date": stamp.isoformat(),
            "x_net": float(net.get(day, 0.0)),
            "y_bps": float(last_px[day] - first_px[day]) * BPS_PER_POINT,
        })
    return points


# --- fit -----------------------------------------------------------------------------------------


def fit_day_ols(points: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """OLS of day bps on net aggression, the flow-history card regression."""
    rows = list(points)
    n = len(rows)
    if n < MIN_TAG_DAYS:
        return {"n": n, "slope_bps_per_mm": None, "intercept_bps": None, "beta_ref": None,
                "reason": f"Fewer than {MIN_TAG_DAYS} closed days with this tag."}
    x = np.array([row["x_net"] for row in rows], dtype=float)
    y = np.array([row["y_bps"] for row in rows], dtype=float)
    var_x = float(((x - x.mean()) ** 2).sum())
    if var_x == 0:
        return {"n": n, "slope_bps_per_mm": None, "intercept_bps": float(y.mean()), "beta_ref": None,
                "reason": "Net aggression does not vary across this tag's days."}
    slope = float(((x - x.mean()) * (y - y.mean())).sum() / var_x)
    slope_mm = slope * 1e6
    beta = slope_mm * 4.0 * (1.0 / 64.0) / 1e6
    return {
        "n": n,
        "slope_bps_per_mm": slope_mm,
        "intercept_bps": float(y.mean() - slope * x.mean()),
        "beta_ref": beta if beta > 0 else None,
        "reason": None if beta > 0 else "Day slope is not positive.",
        "last_day": max(row["date"] for row in rows),
    }


def fit_tags(points: List[Dict[str, Any]], before: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
    cutoff = before.isoformat() if before is not None else None
    usable = [row for row in points if cutoff is None or row["date"] < cutoff]
    return {tag: fit_day_ols(row for row in usable if row["tag"] == tag) for tag in TAGS}


# --- store ---------------------------------------------------------------------------------------


def _identity(price_symbol: str, aggression_symbols: Iterable[str], dv01_adjust: bool) -> str:
    return json.dumps([normalize_symbol(price_symbol), list(aggression_symbols), bool(dv01_adjust)])


def _read_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": TAG_BETA_VERSION, "entries": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("V3 tag beta could not read %s", path)
        return {"version": TAG_BETA_VERSION, "entries": {}}
    if raw.get("version") != TAG_BETA_VERSION:
        return {"version": TAG_BETA_VERSION, "entries": {}}
    return raw


def _write_store(path: Path, store: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def update_table(
    *,
    price_symbol: str,
    aggression_symbols: Iterable[str],
    dv01_adjust: bool,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
    db_path: Optional[Path] = None,
    events_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Bring one selection's day points through the last closed session and refit its tags.

    First call reads the V1 history once. Later calls read only the last few closed days.
    """
    path = path or TAG_BETA_PATH
    db_path = db_path or V1_FLOW_DB
    symbols = tuple(aggression_symbols)
    current = session_date_for(now)
    through = current - timedelta(days=1)
    key = _identity(price_symbol, symbols, dv01_adjust)
    with _LOCK:
        store = _read_store(path)
        entry = store["entries"].get(key)
    if entry and entry.get("checked_through", "") >= through.isoformat():
        return entry
    events, events_file = load_events(events_dir)
    if events_file is None:
        return {"ok": False, "reason": "No economic-events last-good file to tag days."}
    by_date = _by_date(events)
    dv01_table = load_dv01_table()
    points = {row["date"]: row for row in (entry or {}).get("points", [])}
    if points:
        first = date.fromisoformat(entry["checked_through"]) - timedelta(days=RECHECK_DAYS)
    else:
        first = date(2000, 1, 3)
        try:
            conn = _open_v1(db_path)
            try:
                row = conn.execute(
                    "SELECT ts_utc FROM v1_aggress_flow_1m WHERE symbol = ? ORDER BY ts_utc LIMIT 1",
                    (normalize_symbol(price_symbol),),
                ).fetchone()
            finally:
                conn.close()
            if row:
                first = pd.Timestamp(row[0]).tz_convert(EASTERN).date()
        except sqlite3.Error as exc:
            logger.warning("V3 tag beta could not read the V1 flow store: %s", exc)
            return {"ok": False, "reason": f"V1 flow store unreadable: {exc}"}
    try:
        fresh = read_day_points(
            first, through,
            price_symbol=price_symbol,
            aggression_symbols=symbols,
            dv01_adjust=dv01_adjust,
            dv01_table=dv01_table,
            db_path=db_path,
        )
    except V1ReadError as exc:
        logger.warning("V3 tag beta %s", exc)
        return {"ok": False, "reason": f"V1 flow store {exc}"}
    for row in fresh:
        points[row["date"]] = row
    ordered = []
    for day_key in sorted(points):
        tag, detail = day_tag(date.fromisoformat(day_key), by_date)
        ordered.append({**points[day_key], "tag": tag, "detail": detail})
    entry = {
        "price_symbol": normalize_symbol(price_symbol),
        "aggression_symbols": list(symbols),
        "dv01_adjust": bool(dv01_adjust),
        "checked_through": through.isoformat(),
        "updated_utc": datetime.now(timezone.utc).isoformat(),
        "events_file": events_file,
        "points": ordered,
        "tags": fit_tags(ordered),
    }
    with _LOCK:
        store = _read_store(path)
        store["entries"][key] = entry
        _write_store(path, store)
    logger.info("V3 tag beta table %s through %s: %s days", key, through, len(ordered))
    return entry


def beta_for_session(
    *,
    price_symbol: str,
    aggression_symbols: Iterable[str],
    dv01_adjust: bool,
    now: Optional[datetime] = None,
    path: Optional[Path] = None,
    events_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Pick today's tag and its stored beta. File read only; no V1 query and no refit of stored tags."""
    path = path or TAG_BETA_PATH
    symbols = tuple(aggression_symbols)
    current = session_date_for(now)
    base = {
        "version": TAG_BETA_VERSION,
        "price_symbol": normalize_symbol(price_symbol),
        "aggression_symbols": list(symbols),
        "dv01_adjust": bool(dv01_adjust),
        "session_date": current.isoformat(),
        "window": "tag_day_ols",
    }
    with _LOCK:
        entry = _read_store(path)["entries"].get(_identity(price_symbol, symbols, dv01_adjust))
    if not entry or entry.get("checked_through", "") < (current - timedelta(days=1)).isoformat():
        return {**base, "ok": False, "stale": True, "beta_ref": None, "reason": "Reference beta is still computing."}
    events, _ = load_events(events_dir)
    tag, detail = day_tag(current, _by_date(events))
    tags = entry.get("tags") or {}
    fit = tags.get(tag) or {}
    if any(row["date"] >= current.isoformat() for row in entry.get("points", [])):
        fit = fit_tags(entry["points"], before=current).get(tag, {})
    out = {
        **base,
        "stale": False,
        "tag": tag,
        "tag_detail": detail,
        "n_sessions_used": fit.get("n", 0),
        "slope_bps_per_mm": fit.get("slope_bps_per_mm"),
        "intercept_bps": fit.get("intercept_bps"),
        "last_tag_day": fit.get("last_day"),
        "checked_through": entry.get("checked_through"),
    }
    beta = fit.get("beta_ref")
    if beta is None:
        return {**out, "ok": False, "beta_ref": None, "reason": f"{tag}: {fit.get('reason') or 'no beta'}"}
    return {**out, "ok": True, "reason": None, "beta_ref": float(beta), "positive": True}
