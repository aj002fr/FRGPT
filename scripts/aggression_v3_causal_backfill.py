#!/usr/bin/env python3
"""Causal V3 state backfill from 2010-06-06.

Pulls parent trades for ZF, ZT, ZN, and ZB, fits separate expanding same-tag
betas for TY versus TY and for the DV01-weighted curve versus TY, and writes
one state row per merged curve print. The state uses the curve beta. The TY
beta is stored beside it.

Writes only under workspace/analysis/aggression_v3_live/historical_backfill/.
Does not restart 5053 or write the live history, tag_beta.json, or beta_ref.json.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import date, datetime, time as clock, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.banner import (
    STATE_IDS,
    STATUS_NAMES,
    STATUS_READY,
    window_states,
)
from scripts.aggression_dashboard_v3.core import (
    DATASET,
    EASTERN,
    LIVE_STORE_DIR,
    SYMBOL_PARENT_MAP,
    _select_front_month_per_day,
    build_print_frame,
    globex_session_end,
    is_valid_globex_open,
    load_dv01_table,
    normalize_symbol,
    tick_size,
)
from scripts.aggression_dashboard_v3.history_store import HISTORY_VIEWS
from scripts.aggression_dashboard_v3.tag_beta import BPS_PER_POINT, day_tag
from scripts.aggression_flow_history_dash.events import EVENTS_DB, _load_yaman_events
from scripts.aggression_v3_historical_states import (
    _names_for,
    session_start_for_close_date,
    walk_forward_tag_beta,
)

logger = logging.getLogger("v3.causal_backfill")

BACKFILL_DIR = LIVE_STORE_DIR / "historical_backfill"
FIRST_CLOSE = date(2010, 6, 7)  # first Globex close after the 2010-06-06 Sunday open
PARENTS = ("ZF.FUT", "ZT.FUT", "ZN.FUT", "ZB.FUT")
DASHBOARD_BY_PARENT = {parent: symbol for symbol, parent in SYMBOL_PARENT_MAP.items()}
LABEL_VERSION = "v3_causal_backfill_v1"
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
TRADE_COLUMNS = ["ts", "price", "size", "side", "instrument_id", "raw_symbol"]


def backfill_root(root: Optional[Path] = None) -> Path:
    path = Path(root) if root is not None else BACKFILL_DIR
    if path.resolve() == LIVE_STORE_DIR.resolve():
        raise RuntimeError("Refusing to use the live V3 store root as the backfill directory.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def _set_below_normal() -> None:
    if os.name != "nt":
        return
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_closed_close_date(now: Optional[datetime] = None) -> date:
    """17:00 ET close date of the latest finished Globex session."""
    now_et = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    opened = session_start_for_close_date(now_et.date().isoformat())
    close = globex_session_end(opened).astimezone(EASTERN)
    if now_et >= close and is_valid_globex_open(opened):
        return close.date()
    cursor = now_et.date()
    for _ in range(8):
        cursor -= timedelta(days=1)
        start = session_start_for_close_date(cursor.isoformat())
        end = globex_session_end(start).astimezone(EASTERN)
        if is_valid_globex_open(start) and now_et >= end:
            return end.date()
    raise RuntimeError("No completed Globex session found.")


def iter_sessions(first: date, last: date) -> List[Tuple[date, datetime, datetime]]:
    """(close date, open UTC, close UTC) for valid Globex sessions in the close-date range."""
    out = []
    cursor = first
    while cursor <= last:
        start = session_start_for_close_date(cursor.isoformat())
        if is_valid_globex_open(start):
            end = globex_session_end(start)
            if end.astimezone(EASTERN).date() == cursor:
                out.append((cursor, start, end))
        cursor += timedelta(days=1)
    return out


def freeze_events(
    root: Path,
    *,
    first: date = FIRST_CLOSE,
    last: Optional[date] = None,
    db_path: Path = EVENTS_DB,
) -> Dict[str, Any]:
    """Write the tagged auction / tier-1 calendar used to label every backfill day."""
    last = last or latest_closed_close_date()
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        events = _load_yaman_events(conn, start=date(2010, 1, 1), end=last)
    finally:
        conn.close()
    payload = {
        "version": LABEL_VERSION,
        "source": str(db_path),
        "first_close": first.isoformat(),
        "last_close": last.isoformat(),
        "events": events,
    }
    path = root / "events" / "events_tagged_backfill.json"
    _atomic_text(path, json.dumps(payload))
    digest = _sha256(path)
    auctions = [event for event in events if event.get("family") == "auction"]
    manifest = {
        "path": str(path),
        "sha256": digest,
        "events": len(events),
        "auctions": len(auctions),
        "first_auction": min((event["date"] for event in auctions), default=None),
        "last_auction": max((event["date"] for event in auctions), default=None),
        "note": "Coupon auctions in this source begin in January 2012. Earlier auction days stay untagged.",
    }
    _atomic_text(root / "events" / "manifest.json", json.dumps(manifest, indent=1))
    logger.info("Froze %s events, sha256 %s", len(events), digest)
    return manifest


def load_events(root: Path) -> Dict[str, List[Dict[str, Any]]]:
    path = root / "events" / "events_tagged_backfill.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_date: Dict[str, List[Dict[str, Any]]] = {}
    for event in payload.get("events") or []:
        if event.get("date"):
            by_date.setdefault(str(event["date"])[:10], []).append(event)
    return by_date


def _month_starts(first: date, last: date) -> List[date]:
    cursor = date(first.year, first.month, 1)
    out = []
    while cursor <= last:
        out.append(cursor)
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return out


def _month_window(month: date, first_open: datetime, last_close: datetime) -> Tuple[datetime, datetime]:
    nxt = date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)
    start = datetime.combine(month, clock(0, 0), tzinfo=timezone.utc)
    end = datetime.combine(nxt, clock(0, 0), tzinfo=timezone.utc)
    return max(start, first_open), min(end, last_close)


def _trade_path(root: Path, parent: str, month: date) -> Path:
    return root / "trades" / parent.replace(".", "_") / f"{month.strftime('%Y-%m')}.parquet"


def _normalize_trades(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame = raw.reset_index()
    if "ts_event" in frame.columns:
        frame["ts"] = pd.to_datetime(frame["ts_event"], utc=True)
    else:
        frame["ts"] = pd.to_datetime(frame.iloc[:, 0], utc=True)
    price = pd.to_numeric(frame["price"], errors="coerce")
    price = price.where(price <= 1e6, price / 1e9)
    size = pd.to_numeric(frame["size"], errors="coerce")
    symbol = frame["symbol"] if "symbol" in frame.columns else ""
    if "raw_symbol" in frame.columns:
        symbol = frame["raw_symbol"].where(frame["raw_symbol"].astype(str).str.len() > 0, symbol)
    out = pd.DataFrame({
        "ts": frame["ts"],
        "price": price,
        "size": size,
        "side": frame["side"].astype(str) if "side" in frame.columns else "",
        "instrument_id": frame["instrument_id"].astype(str) if "instrument_id" in frame.columns else "",
        "raw_symbol": symbol.astype(str),
    })
    outright = ~out["raw_symbol"].str.contains("-", regex=False)
    sane = (out["price"] > 0) & (out["price"] < 1000) & (out["size"] > 0)
    out = out.loc[outright & sane].copy()
    out.sort_values("ts", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def _fetch_parent_month(parent: str, start: datetime, end: datetime) -> pd.DataFrame:
    import databento as db
    from scripts.databento_live_state import get_databento_env

    client = db.Historical(key=get_databento_env().get("api_key", ""))
    request = {
        "dataset": DATASET,
        "symbols": [parent],
        "stype_in": "parent",
        "schema": "trades",
        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    last_error: Optional[Exception] = None
    for attempt in range(4):
        try:
            data = client.timeseries.get_range(**request)
            return _normalize_trades(data.to_df())
        except Exception as exc:
            last_error = exc
            logger.warning("Fetch %s %s failed (%s): %s", parent, start.date(), attempt + 1, exc)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {parent} {start} -> {end}: {last_error}")


def ensure_month(
    root: Path,
    parent: str,
    month: date,
    first_open: datetime,
    last_close: datetime,
) -> Path:
    path = _trade_path(root, parent, month)
    marker = path.with_suffix(".json")
    start, end = _month_window(month, first_open, last_close)
    if path.is_file() and marker.is_file():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            saved = {}
        if saved.get("start") == start.isoformat() and saved.get("end") == end.isoformat():
            return path
    if start >= end:
        frame = pd.DataFrame(columns=TRADE_COLUMNS)
    else:
        logger.info("Pulling %s %s -> %s", parent, start.isoformat(), end.isoformat())
        frame = _fetch_parent_month(parent, start, end)
    _atomic_parquet(path, frame)
    _atomic_text(marker, json.dumps({
        "parent": parent,
        "month": month.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rows": int(len(frame)),
        "sha256": _sha256(path),
    }, indent=1))
    return path


def pull_trades(
    root: Path,
    *,
    first: date = FIRST_CLOSE,
    last: Optional[date] = None,
) -> Dict[str, Any]:
    last = last or latest_closed_close_date()
    sessions = iter_sessions(first, last)
    if not sessions:
        raise RuntimeError("No Globex sessions in the requested range.")
    first_open = sessions[0][1]
    last_close = sessions[-1][2]
    written = []
    for month in _month_starts(first, last):
        for parent in PARENTS:
            path = ensure_month(root, parent, month, first_open, last_close)
            written.append(str(path))
    summary = {"months": len(_month_starts(first, last)), "files": len(written), "last_close": last.isoformat()}
    _atomic_text(root / "trades" / "pull_summary.json", json.dumps(summary, indent=1))
    return summary


def _load_month(root: Path, parent: str, month: date) -> pd.DataFrame:
    path = _trade_path(root, parent, month)
    if not path.is_file():
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame = pd.read_parquet(path)
    if frame.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    return frame


def session_trades(
    root: Path,
    start: datetime,
    end: datetime,
    cache: Dict[Tuple[str, str], pd.DataFrame],
) -> Dict[str, pd.DataFrame]:
    """Front-month outrights for one Globex session, keyed by dashboard symbol."""
    months = {start.astimezone(timezone.utc).date().replace(day=1), end.astimezone(timezone.utc).date().replace(day=1)}
    selected: Dict[str, pd.DataFrame] = {}
    contracts: Dict[str, List[Dict[str, Any]]] = {}
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    for parent in PARENTS:
        parts = []
        for month in sorted(months):
            key = (parent, month.strftime("%Y-%m"))
            if key not in cache:
                cache[key] = _load_month(root, parent, month)
            parts.append(cache[key])
        combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=TRADE_COLUMNS)
        if combined.empty:
            selected[DASHBOARD_BY_PARENT[parent]] = combined
            continue
        window = combined.loc[(combined["ts"] >= start_ts) & (combined["ts"] < end_ts)].copy()
        if window.empty:
            selected[DASHBOARD_BY_PARENT[parent]] = window
            continue
        front, rows = _select_front_month_per_day(window)
        symbol = DASHBOARD_BY_PARENT[parent]
        front.attrs["front_contracts"] = rows
        selected[symbol] = front
        contracts[symbol] = rows
    selected["_contracts"] = contracts  # type: ignore[assignment]
    return selected


def _day_point(
    frame: pd.DataFrame,
    close_date: date,
    aggression_symbols: Iterable[str],
    events_by_date: Dict[str, List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    priced = frame.loc[frame["dashboard_symbol"] == "TY", "price"].dropna()
    if priced.empty:
        return None
    symbols = {normalize_symbol(symbol) for symbol in aggression_symbols}
    net = frame.loc[frame["dashboard_symbol"].isin(symbols), "delta_a"].sum()
    tag, detail = day_tag(close_date, events_by_date)
    return {
        "date": close_date.isoformat(),
        "x_net": float(net),
        "y_bps": float(priced.iloc[-1] - priced.iloc[0]) * BPS_PER_POINT,
        "tag": tag,
        "detail": detail,
    }


def label_prints(frame: pd.DataFrame, beta: Optional[float], tau: float) -> pd.DataFrame:
    """One row per print. ``beta`` is the curve reference used by the classifier."""
    columns = [
        "t", "price", "total_aggress", "status", "status_name", "state_index",
        "state", "state_headline", "state2", "state2_headline", "d", "a", "r",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    ts_ns = pd.DatetimeIndex(frame.index).asi8
    beta_value = float(beta) if beta is not None and np.isfinite(beta) and float(beta) > 0 else np.nan
    status, state, d, a, r = window_states(
        ts_ns,
        frame["price"].to_numpy(dtype=float),
        frame["total_aggress"].to_numpy(dtype=float),
        frame["session_id"].astype(str).to_numpy(),
        beta_value,
        tau,
    )
    state_id, state_headline, state2_id, state2_headline = _names_for(status, state)
    status_name = np.asarray(STATUS_NAMES, dtype=object)[status]
    return pd.DataFrame({
        "t": ts_ns,
        "price": frame["price"].to_numpy(dtype=float),
        "total_aggress": frame["total_aggress"].to_numpy(dtype=float),
        "status": status.astype(np.int8),
        "status_name": pd.Categorical(status_name, categories=list(STATUS_NAMES)),
        "state_index": state.astype(np.int8),
        "state": pd.Categorical(state_id),
        "state_headline": pd.Categorical(state_headline),
        "state2": pd.Categorical(state2_id),
        "state2_headline": pd.Categorical(state2_headline),
        "d": d.astype(np.float32),
        "a": a.astype(np.float32),
        "r": r.astype(np.float32),
    })


def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _load_points(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _labeled(root: Path, close_date: date) -> bool:
    meta = root / "states" / f"{close_date.isoformat()}.json"
    if not meta.is_file():
        return False
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("version") == LABEL_VERSION


def label_range(
    root: Path,
    *,
    first: date = FIRST_CLOSE,
    last: Optional[date] = None,
    dv01_table: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    last = last or latest_closed_close_date()
    events = load_events(root)
    dv01 = dv01_table or load_dv01_table()
    points = {
        "curve": _load_points(root / "day_points" / "curve.jsonl"),
        "ty": _load_points(root / "day_points" / "ty.jsonl"),
    }
    seen = {view: {row["date"] for row in rows} for view, rows in points.items()}
    cache: Dict[Tuple[str, str], pd.DataFrame] = {}
    labeled = skipped = 0
    for close_date, start, end in iter_sessions(first, last):
        if _labeled(root, close_date):
            skipped += 1
            continue
        pulled = session_trades(root, start, end, cache)
        contracts = pulled.pop("_contracts")
        trades = {symbol: pulled.get(symbol, pd.DataFrame()) for symbol in ("FV", "TU", "TY", "US")}
        frame, frame_meta = build_print_frame(
            trades,
            price_symbol="TY",
            dv01_table=dv01,
            dv01_adjust=True,
            session_start=start,
        )
        fits = {}
        for view, (_price, symbols, _adjust) in HISTORY_VIEWS.items():
            fits[view] = walk_forward_tag_beta(points[view], close_date.isoformat(), events)
            fits[view].pop("fit_dates", None)
        curve_beta = fits["curve"].get("beta_ref")
        rows = label_prints(frame, curve_beta, tick_size("TY"))
        if len(rows):
            rows["beta_curve"] = np.float64(curve_beta) if curve_beta else np.nan
            rows["beta_ty"] = np.float64(fits["ty"].get("beta_ref") or np.nan)
            rows["tag"] = fits["curve"]["tag"]
            _atomic_parquet(root / "states" / f"{close_date.isoformat()}.parquet", rows)
        meta = {
            "version": LABEL_VERSION,
            "close_date": close_date.isoformat(),
            "session_open": start.isoformat(),
            "session_close": end.isoformat(),
            "prints": int(frame_meta.get("trade_count") or 0),
            "rows": int(len(rows)),
            "ready_rows": int((rows["status"] == STATUS_READY).sum()) if len(rows) else 0,
            "curve_beta": fits["curve"],
            "ty_beta": fits["ty"],
            "front_contracts": contracts,
            "missing_symbols": [symbol for symbol, frame_in in trades.items() if frame_in is None or frame_in.empty],
            "classifier": "scripts.aggression_dashboard_v3.banner.window_states",
            "state_beta": "curve",
        }
        _atomic_text(root / "states" / f"{close_date.isoformat()}.json", json.dumps(meta, indent=1, default=str))
        for view, (_price, symbols, _adjust) in HISTORY_VIEWS.items():
            point = _day_point(frame, close_date, symbols, events)
            if point and point["date"] not in seen[view]:
                _append_jsonl(root / "day_points" / f"{view}.jsonl", point)
                points[view].append(point)
                seen[view].add(point["date"])
        labeled += 1
        if labeled % 20 == 0:
            logger.info("Labeled %s through %s", labeled, close_date)
        # Drop month frames that can no longer feed a later session in this process.
        if len(cache) > 16:
            cache.clear()
    summary = {"labeled": labeled, "skipped": skipped, "last_close": last.isoformat()}
    _atomic_text(root / "states" / "label_summary.json", json.dumps(summary, indent=1))
    return summary


def _audit_betas(root: Path) -> Dict[str, Any]:
    failures = []
    checked = 0
    for path in sorted((root / "states").glob("*.json")):
        if path.name == "label_summary.json":
            continue
        meta = json.loads(path.read_text(encoding="utf-8"))
        if meta.get("version") != LABEL_VERSION:
            continue
        checked += 1
        close_date = meta["close_date"]
        for name in ("curve_beta", "ty_beta"):
            fit = meta.get(name) or {}
            last_day = fit.get("last_tag_day")
            if last_day and str(last_day) >= close_date:
                failures.append(f"{close_date} {name} last day {last_day}")
    return {"sessions": checked, "lookahead_failures": failures}


def write_coverage(root: Path) -> Dict[str, Any]:
    audit = _audit_betas(root)
    by_year: Dict[str, Dict[str, int]] = {}
    by_tag: Dict[str, Dict[str, int]] = {}
    missing = 0
    for path in sorted((root / "states").glob("*.json")):
        if path.name == "label_summary.json":
            continue
        meta = json.loads(path.read_text(encoding="utf-8"))
        if meta.get("version") != LABEL_VERSION:
            continue
        year = meta["close_date"][:4]
        year_row = by_year.setdefault(year, {"sessions": 0, "ready_rows": 0, "warming": 0, "empty": 0})
        year_row["sessions"] += 1
        year_row["ready_rows"] += int(meta.get("ready_rows") or 0)
        curve = meta.get("curve_beta") or {}
        if not curve.get("beta_ref"):
            year_row["warming"] += 1
        if not meta.get("rows"):
            year_row["empty"] += 1
        if meta.get("missing_symbols"):
            missing += 1
        tag = curve.get("tag") or "UNKNOWN"
        tag_row = by_tag.setdefault(tag, {"sessions": 0, "with_beta": 0})
        tag_row["sessions"] += 1
        if curve.get("beta_ref"):
            tag_row["with_beta"] += 1
    events_manifest = {}
    manifest_path = root / "events" / "manifest.json"
    if manifest_path.is_file():
        events_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    promote = (
        not audit["lookahead_failures"]
        and audit["sessions"] > 0
        and by_year.get("2011", {}).get("sessions", 0) > 0
    )
    report = {
        "version": LABEL_VERSION,
        "audit": audit,
        "by_year": by_year,
        "by_tag": by_tag,
        "sessions_missing_a_leg": missing,
        "events": events_manifest,
        "burn_in": "2010",
        "primary_from": "2011",
        "promote_2011": promote,
        "auction_calendar_from": events_manifest.get("first_auction"),
        "limitations": [
            "2010 is burn-in. Tags with fewer than two prior days stay WARMING.",
            "Coupon auction tags are absent before the events source's first auction.",
            "Parent pulls drop calendar spreads, then keep the highest-volume outright per UTC day.",
            "The live fetch's 80-150 price gate is not applied; it would drop US prints above 150.",
        ],
    }
    _atomic_text(root / "reports" / "coverage.json", json.dumps(report, indent=1))
    lines = [
        "# V3 causal backfill coverage",
        "",
        f"Sessions checked: {audit['sessions']}. Lookahead failures: {len(audit['lookahead_failures'])}.",
        f"Promote 2011 onward: {promote}.",
        f"First stored coupon auction: {events_manifest.get('first_auction')}.",
        "",
        "## Year",
    ]
    for year, row in sorted(by_year.items()):
        lines.append(
            f"- {year}: {row['sessions']} sessions, {row['ready_rows']} ready rows, "
            f"{row['warming']} without a positive curve beta, {row['empty']} empty"
        )
    lines.extend(["", "## Tag"])
    for tag, row in sorted(by_tag.items()):
        lines.append(f"- {tag}: {row['sessions']} sessions, {row['with_beta']} with a positive curve beta")
    _atomic_text(root / "reports" / "coverage.md", "\n".join(lines) + "\n")
    return report


def compare_archived(root: Path, close_date: str, archive_stem: str) -> Dict[str, Any]:
    """Compare one backfill session with the archived per-second curve history.

    The archive is the live history grain (last print of each second) and its beta
    comes from the V1 minute store. This backfill is every print and its beta comes
    from the trade tape. Both facts belong in the comparison.
    """
    from scripts.aggression_dashboard_v3.history_store import HISTORY_DIR

    ours = second_status(pd.read_parquet(root / "states" / f"{close_date}.parquet"))
    archived = pd.read_parquet(HISTORY_DIR / "curve" / f"{archive_stem}.parquet")
    ours["second"] = ours["t"] // 1_000_000_000
    archived = archived.copy()
    archived["second"] = archived["t"] // 1_000_000_000
    merged = ours.merge(archived, on="second", how="inner", suffixes=("_backfill", "_archive"))
    meta = json.loads((root / "states" / f"{close_date}.json").read_text(encoding="utf-8"))
    archive_meta = json.loads((HISTORY_DIR / "curve" / f"{archive_stem}.json").read_text(encoding="utf-8"))
    report = {
        "close_date": close_date,
        "archive_stem": archive_stem,
        "overlap_seconds": int(len(merged)),
        "status_match": float((merged["status_backfill"] == merged["status_archive"]).mean()) if len(merged) else None,
        "state_match": float((merged["state_index"] == merged["state_archive"]).mean()) if len(merged) else None,
        "backfill_curve_beta": (meta.get("curve_beta") or {}).get("beta_ref"),
        "backfill_ty_beta": (meta.get("ty_beta") or {}).get("beta_ref"),
        "archive_beta": archive_meta.get("beta_ref"),
        "archive_beta_source": "V1 minute tag OLS" if archive_meta.get("tag") else "session fallback",
        "backfill_beta_source": "trade-print tag OLS from 2010",
    }
    _atomic_text(root / "reports" / f"parity_{close_date}.json", json.dumps(report, indent=1))
    return report


def second_status(rows: pd.DataFrame) -> pd.DataFrame:
    """Last print of each UTC second, for comparison with the live history grain."""
    if rows.empty:
        return rows
    frame = rows.copy()
    frame["second"] = frame["t"] // 1_000_000_000
    return frame.groupby("second", sort=True).tail(1)


def run(
    root: Optional[Path] = None,
    *,
    first: date = FIRST_CLOSE,
    last: Optional[date] = None,
) -> Dict[str, Any]:
    _set_below_normal()
    root = backfill_root(root)
    last = last or latest_closed_close_date()
    events = freeze_events(root, first=first, last=last)
    sessions = iter_sessions(first, last)
    first_open, last_bound = sessions[0][1], sessions[-1][2]
    labeled = {"labeled": 0, "skipped": 0}
    for month in _month_starts(first, last):
        for parent in PARENTS:
            ensure_month(root, parent, month, first_open, last_bound)
        month_last = min(last, _month_window(month, first_open, last_bound)[1].astimezone(EASTERN).date())
        month_first = max(first, month)
        if month_first <= month_last:
            step = label_range(root, first=month_first, last=month_last)
            labeled["labeled"] += step["labeled"]
            labeled["skipped"] += step["skipped"]
    coverage = write_coverage(root)
    summary = {
        "events": events,
        "labeled": labeled,
        "promote_2011": coverage["promote_2011"],
        "last_close": last.isoformat(),
    }
    _atomic_text(root / "reports" / "run_summary.json", json.dumps(summary, indent=1, default=str))
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description="Causal V3 historical backfill from 2010-06-06.")
    parser.add_argument("--root", type=Path, default=BACKFILL_DIR)
    parser.add_argument("--first", default=FIRST_CLOSE.isoformat())
    parser.add_argument("--last", default="")
    parser.add_argument("--step", choices=("all", "events", "pull", "label", "report"), default="all")
    args = parser.parse_args(argv)
    root = backfill_root(args.root)
    first = date.fromisoformat(args.first)
    last = date.fromisoformat(args.last) if args.last else None
    if args.step == "events":
        result = freeze_events(root, first=first, last=last or latest_closed_close_date())
    elif args.step == "pull":
        result = pull_trades(root, first=first, last=last)
    elif args.step == "label":
        result = label_range(root, first=first, last=last)
    elif args.step == "report":
        result = write_coverage(root)
    else:
        result = run(root, first=first, last=last)
    print(json.dumps(result, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
