#!/usr/bin/env python3
"""Label frozen historical trades and prices with the live V3 banner states.

Each raw shard under the autoresearch TBBO store is one Globex session of
FV, TU, TY and US prints. This applies the live monitor's print frame,
10-session change-on-change beta, and STATE / STATE2 classifier, then keeps
the last print of each second, which is the grain the live history store uses.

Writes only under workspace/analysis/aggression_v3_historical_states/.
Does not call Databento, write aggression_v3_live, or start or restart 5053.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import os
import sys
import time
from datetime import datetime, time as clock, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.banner import (
    GROUP_STYLE,
    STATE_IDS,
    STATE_STYLE,
    STATUS_NAMES,
    STATUS_READY,
    window_states,
)
from scripts.aggression_dashboard_v3.beta_ref import _session_key, fit_session_slope, session_set_mean
from scripts.aggression_dashboard_v3.core import (
    BETA_REF_SESSIONS,
    EASTERN,
    LIVE_STORE_DIR,
    build_print_frame,
    completed_session_starts,
    load_dv01_table,
    tick_size,
)
from scripts.aggression_dashboard_v3.history_store import HISTORY_VIEWS

logger = logging.getLogger("v3.historical_states")

LABEL_VERSION = "v3_live_states_h1"
NS = 1_000_000_000
SHARD_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_autoresearch_data" / "shards" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_historical_states"
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
DAY_CAP_SECONDS = 600


def _reject_live_store(root: Path) -> Path:
    resolved = root.resolve()
    live = LIVE_STORE_DIR.resolve()
    if resolved == live or live in resolved.parents:
        raise RuntimeError(f"Refusing to write the live V3 store: {resolved}")
    return resolved


def shard_dates(shard_dir: Path = SHARD_DIR) -> List[str]:
    """Close dates that have a raw shard, oldest first. The name is the session's 17:00 ET date."""
    if not shard_dir.is_dir():
        return []
    return sorted(path.stem for path in shard_dir.glob("*.parquet") if _is_date(path.stem))


def _is_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def session_start_for_close_date(close_date: str) -> datetime:
    """18:00 ET on the evening before the shard's close date, in UTC."""
    close = datetime.strptime(close_date, "%Y-%m-%d").date()
    opened = datetime.combine(close - timedelta(days=1), clock(18, 0), tzinfo=EASTERN)
    return opened.astimezone(timezone.utc)


def load_shard_trades(path: Path) -> Dict[str, pd.DataFrame]:
    """Per-symbol trades with the columns build_print_frame expects."""
    frame = pd.read_parquet(path, columns=["price", "size", "side", "dashboard_symbol"])
    frame = frame.reset_index()
    stamp = "ts_event" if "ts_event" in frame.columns else frame.columns[0]
    frame["ts"] = pd.to_datetime(frame[stamp], utc=True)
    trades: Dict[str, pd.DataFrame] = {}
    for symbol, part in frame.groupby("dashboard_symbol", sort=False):
        trades[str(symbol)] = part.loc[:, ["ts", "price", "size", "side"]].copy()
    return trades


def _names_for(status: np.ndarray, state: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Coarse STATE id, coarse headline, STATE2 id, STATE2 headline. Blank when the row is not READY."""
    n = len(status)
    state_id = np.full(n, "", dtype=object)
    state_headline = np.full(n, "", dtype=object)
    state2_id = np.full(n, "", dtype=object)
    state2_headline = np.full(n, "", dtype=object)
    ready = np.nonzero(status == STATUS_READY)[0]
    if len(ready) == 0:
        return state_id, state_headline, state2_id, state2_headline
    ids = np.asarray(STATE_IDS, dtype=object)
    fine = ids[state[ready]]
    state2_id[ready] = fine
    coarse = np.empty(len(ready), dtype=object)
    fine_head = np.empty(len(ready), dtype=object)
    coarse_head = np.empty(len(ready), dtype=object)
    for position, name in enumerate(fine):
        headline, _color, _family, group = STATE_STYLE[str(name)]
        fine_head[position] = headline
        coarse[position] = group
        coarse_head[position] = GROUP_STYLE[group][0]
    state_id[ready] = coarse
    state_headline[ready] = coarse_head
    state2_headline[ready] = fine_head
    return state_id, state_headline, state2_id, state2_headline


def _timestamp_ns(index: pd.Index) -> np.ndarray:
    """Nanoseconds since the epoch. Shard clocks are microseconds; the live classifier expects nanoseconds."""
    stamps = pd.DatetimeIndex(index)
    if stamps.tz is None:
        stamps = stamps.tz_localize("UTC")
    else:
        stamps = stamps.tz_convert("UTC")
    return stamps.as_unit("ns").asi8


def label_print_frame(
    frame: pd.DataFrame,
    beta: Optional[float],
    tau: float,
) -> pd.DataFrame:
    """Last print of each second, labeled with the live window_states rules."""
    columns = [
        "t",
        "price",
        "total_aggress",
        "status",
        "status_name",
        "state_index",
        "state",
        "state_headline",
        "state2",
        "state2_headline",
        "d",
        "a",
        "r",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    ts_ns = _timestamp_ns(frame.index)
    beta_value = float(beta) if beta is not None and np.isfinite(beta) and float(beta) > 0 else np.nan
    status, state, d, a, r = window_states(
        ts_ns,
        frame["price"].to_numpy(dtype=float),
        frame["total_aggress"].to_numpy(dtype=float),
        frame["session_id"].astype(str).to_numpy(),
        beta_value,
        tau,
    )
    seconds = ts_ns // NS
    keep = np.ones(len(ts_ns), dtype=bool)
    if len(keep) > 1:
        keep[:-1] = seconds[1:] != seconds[:-1]
    status_k = status[keep]
    state_k = state[keep]
    state_id, state_headline, state2_id, state2_headline = _names_for(status_k, state_k)
    status_name = np.asarray(STATUS_NAMES, dtype=object)[status_k]
    return pd.DataFrame({
        "t": ts_ns[keep],
        "price": frame["price"].to_numpy(dtype=float)[keep],
        "total_aggress": frame["total_aggress"].to_numpy(dtype=float)[keep],
        "status": status_k.astype(np.int8),
        "status_name": pd.Categorical(status_name, categories=list(STATUS_NAMES)),
        "state_index": state_k.astype(np.int8),
        "state": pd.Categorical(state_id),
        "state_headline": pd.Categorical(state_headline),
        "state2": pd.Categorical(state2_id),
        "state2_headline": pd.Categorical(state2_headline),
        "d": d[keep].astype(np.float32),
        "a": a[keep].astype(np.float32),
        "r": r[keep].astype(np.float32),
    })


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, path)


def _paths(root: Path, view: str, close_date: str) -> Tuple[Path, Path]:
    folder = root / view
    return folder / f"{close_date}.parquet", folder / f"{close_date}.json"


def _read_meta(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if meta.get("version") != LABEL_VERSION:
        return None
    return meta


def _count_states(rows: pd.DataFrame) -> Dict[str, int]:
    ready = rows.loc[rows["status"] == STATUS_READY, "state2"]
    counts = ready.astype(str).value_counts()
    return {str(name): int(counts.get(name, 0)) for name in STATE_IDS}


def label_one(
    close_date: str,
    trades: Dict[str, pd.DataFrame],
    view: str,
    slopes: Dict[str, Dict[str, Any]],
    dv01_table: Dict[str, float],
    root: Path,
) -> Dict[str, Any]:
    """Label one view. ``slopes`` is that view's own session slopes, not shared with the other view."""
    """Label one view of one session and record its own slope for later betas."""
    price_symbol, symbols, dv01_adjust = HISTORY_VIEWS[view]
    start = session_start_for_close_date(close_date)
    key = _session_key(start)
    selected = {symbol: trades.get(symbol, pd.DataFrame()) for symbol in symbols}
    frame, frame_meta = build_print_frame(
        selected,
        price_symbol=price_symbol,
        dv01_table=dv01_table,
        dv01_adjust=dv01_adjust,
        session_start=start,
    )
    fit = fit_session_slope(frame)
    slopes[key] = {
        "ok": bool(fit.get("ok")) and fit.get("beta") is not None and bool(np.isfinite(float(fit["beta"]))),
        "beta": None if fit.get("beta") is None else float(fit["beta"]),
        "n": fit.get("n"),
        "reason": fit.get("reason"),
    }
    prior_keys = [_session_key(prior) for prior in completed_session_starts(start, n=BETA_REF_SESSIONS)]
    betas = [float(slopes[item]["beta"]) for item in prior_keys if slopes.get(item, {}).get("ok")]
    beta_ref, n_used = session_set_mean(betas)
    rows = label_print_frame(
        frame,
        beta_ref,
        tick_size(price_symbol),
    )
    parquet_path, meta_path = _paths(root, view, close_date)
    meta: Dict[str, Any] = {
        "version": LABEL_VERSION,
        "view": view,
        "close_date": close_date,
        "session_key": key,
        "session_open": start.isoformat(),
        "price_symbol": price_symbol,
        "aggression_symbols": list(symbols),
        "dv01_adjust": dv01_adjust,
        "prints": int(frame_meta.get("trade_count") or 0),
        "rows": int(len(rows)),
        "ready_rows": int((rows["status"] == STATUS_READY).sum()) if len(rows) else 0,
        "own_slope": slopes[key],
        "beta_ref": beta_ref,
        "n_sessions_used": n_used,
        "prior_sessions": prior_keys,
        "state_counts": _count_states(rows) if len(rows) else {},
        "classifier": "scripts.aggression_dashboard_v3.banner.window_states",
        "labeled_at": datetime.now(timezone.utc).isoformat(),
    }
    if len(rows):
        _atomic_parquet(parquet_path, rows)
    elif parquet_path.exists():
        parquet_path.unlink()
    _atomic_text(meta_path, json.dumps(meta, indent=1))
    return meta


def _load_saved_slopes(root: Path, views: Iterable[str]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Per-view slopes already written, so a resumed run still has the prior ten sessions."""
    slopes: Dict[str, Dict[str, Dict[str, Any]]] = {view: {} for view in views}
    for view in views:
        folder = root / view
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            meta = _read_meta(path)
            if meta and meta.get("session_key") and isinstance(meta.get("own_slope"), dict):
                slopes[view][str(meta["session_key"])] = meta["own_slope"]
    return slopes


def _already_labeled(root: Path, view: str, close_date: str) -> bool:
    parquet_path, meta_path = _paths(root, view, close_date)
    meta = _read_meta(meta_path)
    if not meta or not meta.get("own_slope"):
        return False
    if int(meta.get("prints") or 0) > 0:
        return parquet_path.is_file()
    return True


def _set_below_normal() -> None:
    if os.name != "nt":
        return
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)


def label_history(
    *,
    dates: Optional[Iterable[str]] = None,
    views: Optional[Iterable[str]] = None,
    max_seconds: float = 480,
    shard_dir: Path = SHARD_DIR,
    root: Path = OUTPUT_DIR,
) -> Dict[str, Any]:
    """Label sessions until the time cap. Already labeled sessions are skipped and their slopes kept."""
    if max_seconds > DAY_CAP_SECONDS:
        raise RuntimeError(
            f"A labeling run longer than {DAY_CAP_SECONDS // 60} minutes waits for the 17:00-18:00 ET break or the weekend."
        )
    root = _reject_live_store(Path(root))
    view_list = tuple(views or HISTORY_VIEWS)
    unknown = [view for view in view_list if view not in HISTORY_VIEWS]
    if unknown:
        raise ValueError(f"Unknown view: {unknown}")
    chosen = list(dates) if dates is not None else shard_dates(shard_dir)
    chosen = [item for item in chosen if _is_date(item)]
    _set_below_normal()
    dv01_table = load_dv01_table()
    slopes = _load_saved_slopes(root, view_list)
    started = time.perf_counter()
    labeled = skipped = 0
    stopped = None
    for close_date in chosen:
        if time.perf_counter() - started > max_seconds:
            stopped = "TIME_CAP"
            break
        pending = [view for view in view_list if not _already_labeled(root, view, close_date)]
        if not pending:
            skipped += 1
            continue
        path = shard_dir / f"{close_date}.parquet"
        if not path.is_file():
            continue
        trades = load_shard_trades(path)
        for view in pending:
            label_one(close_date, trades, view, slopes[view], dv01_table, root)
            labeled += 1
    summary = {
        "version": LABEL_VERSION,
        "labeled_views": labeled,
        "skipped_sessions": skipped,
        "stopped": stopped,
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "output": str(root),
    }
    _atomic_text(root / "label_run.json", json.dumps(summary, indent=1))
    logger.info("V3 historical states: %s", summary)
    return summary


def walk_forward_tag_beta(
    points: List[Dict[str, Any]],
    close_date: str,
    events_by_date: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Same-tag day OLS for one close date, using only points dated before that close.

    This is the historical replacement for the shard labeler's 10-session mean.
    The shard path in ``label_one`` stays on that mean; the 2010 backfill calls this.
    """
    from scripts.aggression_dashboard_v3.tag_beta import day_tag, fit_tags

    day = datetime.strptime(close_date, "%Y-%m-%d").date()
    tag, detail = day_tag(day, events_by_date)
    fit = fit_tags(points, before=day).get(tag) or {}
    used = [row["date"] for row in points if row.get("tag") == tag and str(row.get("date")) < close_date]
    return {
        "tag": tag,
        "tag_detail": detail,
        "cutoff": close_date,
        "n_sessions_used": int(fit.get("n") or 0),
        "slope_bps_per_mm": fit.get("slope_bps_per_mm"),
        "intercept_bps": fit.get("intercept_bps"),
        "beta_ref": fit.get("beta_ref"),
        "last_tag_day": fit.get("last_day"),
        "reason": fit.get("reason"),
        "fit_dates": used,
    }


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    parser = argparse.ArgumentParser(description="Label historical V3 trades and prices with live banner states.")
    parser.add_argument("--dates", nargs="*", help="Close dates YYYY-MM-DD. Default: every raw shard.")
    parser.add_argument("--views", nargs="*", choices=sorted(HISTORY_VIEWS), help="Default: curve and ty.")
    parser.add_argument("--max-seconds", type=float, default=480)
    args = parser.parse_args(argv)
    summary = label_history(dates=args.dates, views=args.views, max_seconds=args.max_seconds)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
