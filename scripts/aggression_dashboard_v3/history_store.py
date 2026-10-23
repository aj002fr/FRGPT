#!/usr/bin/env python3
"""V3 history: each completed session is built once per view, then served as range slices.

A new day is built from the close snapshot written during 17:00–18:00 ET.
Older days still read the trade cache. Neither path calls Databento.
Writes stay under `workspace/analysis/aggression_v3_live/history/`. Live beta comes from `tag_beta.json` (read-only here); legacy `beta_ref.json` is still read only to seed slope caches.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import OrderedDict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.banner import (
    GROUP_STYLE,
    LABEL,
    MISSING_ENDPOINT_REASON,
    STATE_IDS,
    STATE_STYLE,
    STATUS_NAMES,
    STATUS_READY,
    _payload,
    contribution_line,
    window_minutes,
    window_states,
)
from scripts.aggression_dashboard_v3.beta_ref import (
    BETA_VERSION,
    _entries_from_disk,
    _session_key,
    beta_to_yield_bp_per_1m,
    fit_session_slope,
    session_set_mean,
)
from scripts.aggression_dashboard_v3.core import (
    BETA_REF_SESSIONS,
    EASTERN,
    LIVE_STORE_DIR,
    SYMBOL_PARENT_MAP,
    _cache_path_for_contract_trades,
    _daily_ranges,
    build_print_frame,
    completed_session_starts,
    globex_session_end,
    is_valid_globex_open,
    load_cached_trades_window,
    load_dv01_table,
    tick_size,
    trading_day_start,
)
from scripts.aggression_dashboard_v3.panels import _build_axis_alignment, coarse_state_legend, state_run

logger = logging.getLogger(__name__)

HISTORY_DIR = LIVE_STORE_DIR / "history"
HISTORY_VERSION = "h1"
# view -> (price symbol, aggression symbols, DV01 adjust); symbol order matches live view keys.
HISTORY_VIEWS: Dict[str, Tuple[str, Tuple[str, ...], bool]] = {
    "curve": ("TY", ("FV", "TU", "TY", "US"), True),
    "ty": ("TY", ("TY",), True),
}
HISTORY_MAX_SESSIONS = 10
PANEL_BUCKETS = 4000
STRIP_MAX_BINS = 2000
HOVER_MAX_ROWS = 4000
BACKFILL_SESSIONS = 25
FAILED_RETRY_HOURS = 20
# Wing files that never arrive stop blocking a build this long after the close.
WING_WAIT_HOURS = 72
COVERAGE_GAP_MIN = 30
FRAME_CACHE_SLOTS = 12
SLOPES_FILE = "slopes.json"
HISTORY_STRIP_CAPTION = (
    "Top row: STATE (coarse). Bottom row: STATE2 (fine). Each segment is the 5-minute window state, "
    "or the 15-minute window from 18:00 to 03:00 ET; "
    "long ranges show the most common state per time bin, and zooming in re-loads per-second detail. "
    "Gaps are warming, unavailable or missing data. Attribution only; not a long/short signal."
)
STATUS_REASONS = {
    "WARMING": (
        "Window warming: no print at the earlier endpoint since the session open, "
        "or a print gap longer than the window (5 minutes, or 15 minutes from 18:00 to 03:00 ET)."
    ),
    "UNAVAILABLE": MISSING_ENDPOINT_REASON,
}
GROUP_IDS: Tuple[str, ...] = tuple(GROUP_STYLE)
_STATE_GROUP = np.array([GROUP_IDS.index(STATE_STYLE[state][3]) for state in STATE_IDS], dtype=np.int8)
# Any STATE2 id of a group gives `state_run` that group's style on the STATE row.
_GROUP_EXAMPLE = {group: next(s for s in STATE_IDS if STATE_STYLE[s][3] == group) for group in GROUP_IDS}
NS = 1_000_000_000


class HistoryRangeError(ValueError):
    """The requested range is empty, inverted or covers too many sessions."""


def view_label(view: str) -> str:
    price_symbol, symbols, dv01_adjust = HISTORY_VIEWS[view]
    return f"{price_symbol} price vs {'+'.join(symbols)} (DV01 {'on' if dv01_adjust else 'off'})"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _et_naive(value: datetime) -> str:
    return _utc(value).astimezone(EASTERN).replace(tzinfo=None).isoformat(timespec="seconds")


def _et_iso(ns: np.ndarray) -> List[str]:
    if len(ns) == 0:
        return []
    idx = pd.to_datetime(np.asarray(ns, dtype=np.int64), unit="ns", utc=True).tz_convert(EASTERN)
    return list(idx.strftime("%Y-%m-%dT%H:%M:%S"))


def _utc_iso(ns: np.ndarray) -> List[str]:
    if len(ns) == 0:
        return []
    idx = pd.to_datetime(np.asarray(ns, dtype=np.int64), unit="ns", utc=True)
    return list(idx.strftime("%Y-%m-%dT%H:%M:%S+00:00"))


def _hhmm(value: Any) -> str:
    return pd.Timestamp(value).tz_convert(EASTERN).strftime("%H:%M")


def session_label(start: datetime) -> str:
    """Trading day of the session, named by its 17:00 ET close."""
    return globex_session_end(start).astimezone(EASTERN).strftime("%a %b %d")


def session_paths(view: str, start: datetime, root: Optional[Path] = None) -> Tuple[Path, Path]:
    folder = (root or HISTORY_DIR) / view
    stem = _utc(start).astimezone(EASTERN).strftime("%Y%m%d")
    return folder / f"{stem}.parquet", folder / f"{stem}.json"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def read_meta(view: str, start: datetime, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    meta_path = session_paths(view, start, root)[1]
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("V3 history meta unreadable: %s", meta_path)
        return None
    if meta.get("version") != HISTORY_VERSION:
        return None
    return meta


# --- trade cache -----------------------------------------------------------------------------------


def close_snapshot_dir(start: datetime, root: Optional[Path] = None) -> Path:
    """Folder for the in-memory tape saved during the 17:00–18:00 ET break."""
    stem = _utc(start).astimezone(EASTERN).strftime("%Y%m%d")
    return (root or HISTORY_DIR) / "close_tape" / stem


_SNAPSHOT_COLUMNS = ("ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol")


def write_close_snapshot(
    trades_by_symbol: Dict[str, pd.DataFrame],
    session_start: datetime,
    root: Optional[Path] = None,
) -> Path:
    """Write the session already in memory. The history worker reads this instead of the UTC day files."""
    folder = close_snapshot_dir(session_start, root)
    folder.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    for symbol, df in trades_by_symbol.items():
        if df is None or df.empty:
            continue
        out = df.copy()
        if "dashboard_symbol" not in out.columns:
            out["dashboard_symbol"] = symbol
        for col in _SNAPSHOT_COLUMNS:
            if col not in out.columns:
                out[col] = "" if col != "size" else 0
        path = folder / f"{symbol}.parquet"
        _atomic_write_parquet(path, out.loc[:, list(_SNAPSHOT_COLUMNS)])
        written.append(symbol)
    _atomic_write_text(
        folder / "snapshot.json",
        json.dumps({
            "session_open": _utc(session_start).isoformat(),
            "symbols": written,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }, indent=1),
    )
    logger.info("V3 history close snapshot %s: %s", session_label(session_start), ",".join(written) or "empty")
    return folder


def close_snapshot_covers(view: str, start: datetime, root: Optional[Path] = None) -> bool:
    """True when the close snapshot has the view's price symbol, so the day parquets are not required."""
    price_symbol = HISTORY_VIEWS[view][0]
    path = close_snapshot_dir(start, root) / f"{price_symbol}.parquet"
    return path.is_file() and path.stat().st_size > 0


def load_close_snapshot(
    symbols: Iterable[str],
    start: datetime,
    root: Optional[Path] = None,
) -> Tuple[Dict[str, pd.DataFrame], List[Dict[str, Any]]]:
    """Trades saved at the close, clipped to the session. A missing wing is empty, not a failed cache file."""
    folder = close_snapshot_dir(start, root)
    end = globex_session_end(start)
    start_ts = pd.Timestamp(_utc(start))
    end_ts = pd.Timestamp(end)
    trades: Dict[str, pd.DataFrame] = {}
    status: List[Dict[str, Any]] = []
    for symbol in symbols:
        path = folder / f"{symbol}.parquet"
        if not path.is_file():
            trades[symbol] = pd.DataFrame(columns=list(_SNAPSHOT_COLUMNS))
            status.append({
                "symbol": symbol, "file_date": _et_naive(start)[:10], "state": "empty", "rows": 0,
            })
            continue
        df = pd.read_parquet(path)
        if df.empty or "ts" not in df.columns:
            trades[symbol] = df
            status.append({
                "symbol": symbol, "file_date": path.name, "state": "empty", "rows": 0,
            })
            continue
        ts = pd.to_datetime(df["ts"], utc=True)
        df = df.loc[(ts >= start_ts) & (ts <= end_ts)].copy()
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        trades[symbol] = df
        status.append({
            "symbol": symbol, "file_date": path.name, "state": "ok" if len(df) else "empty", "rows": int(len(df)),
        })
    return trades, status


def in_close_save_window(now: datetime) -> bool:
    """Weekday 17:00–18:00 ET, the hour the just-closed session is still in memory."""
    now_et = _utc(now).astimezone(EASTERN)
    return now_et.weekday() < 5 and time(17, 0) <= now_et.time() < time(18, 0)


def cache_ready(view: str, start: datetime, now: Optional[datetime] = None) -> bool:
    """The session's trade cache files exist for the price symbol, and for the wings or long enough after the close."""
    price_symbol, symbols, _dv01 = HISTORY_VIEWS[view]
    end = globex_session_end(start)
    chunks = [chunk_start for chunk_start, _ in _daily_ranges(start, end)]

    def has_files(symbol: str) -> bool:
        return all(_cache_path_for_contract_trades(SYMBOL_PARENT_MAP[symbol], chunk).exists() for chunk in chunks)

    if not has_files(price_symbol):
        return False
    if all(has_files(symbol) for symbol in symbols):
        return True
    now_utc = _utc(now or datetime.now(timezone.utc))
    return now_utc - end >= timedelta(hours=WING_WAIT_HOURS)


def _unreadable(path: Path) -> bool:
    try:
        pd.read_parquet(path, columns=["ts"])
    except Exception:
        return True
    return False


def load_session_trades(
    symbols: Iterable[str],
    start: datetime,
) -> Tuple[Dict[str, pd.DataFrame], List[Dict[str, Any]]]:
    """Cached trades for one session, read one UTC-day file at a time so each file's status is known."""
    end = globex_session_end(start)
    symbols = tuple(symbols)
    parts: Dict[str, List[pd.DataFrame]] = {symbol: [] for symbol in symbols}
    fronts: Dict[str, List[Any]] = {symbol: [] for symbol in symbols}
    status: List[Dict[str, Any]] = []
    for chunk_start, chunk_end in _daily_ranges(start, end):
        loaded = load_cached_trades_window(symbols, chunk_start, chunk_end)
        for symbol in symbols:
            path = _cache_path_for_contract_trades(SYMBOL_PARENT_MAP[symbol], chunk_start)
            df = loaded.get(symbol)
            rows = 0 if df is None else int(len(df))
            if rows:
                parts[symbol].append(df)
                fronts[symbol].extend(df.attrs.get("front_contracts", []))
                state = "ok"
            elif not path.exists():
                state = "missing"
            else:
                state = "unreadable" if _unreadable(path) else "empty"
            status.append({
                "symbol": symbol,
                "file_date": chunk_start.strftime("%Y-%m-%d"),
                "start": chunk_start,
                "end": chunk_end,
                "state": state,
                "rows": rows,
            })
    trades: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        if parts[symbol]:
            combined = pd.concat(parts[symbol], ignore_index=True).sort_values("ts", kind="stable")
            combined.reset_index(drop=True, inplace=True)
            combined.attrs["front_contracts"] = fronts[symbol]
            trades[symbol] = combined
        else:
            trades[symbol] = pd.DataFrame(
                columns=["ts", "price", "size", "side", "instrument_id", "raw_symbol", "dashboard_symbol"]
            )
    return trades, status


def _cache_warnings(status: List[Dict[str, Any]], price_symbol: str) -> List[str]:
    warnings: List[str] = []
    for item in status:
        state = item["state"]
        if state == "ok" or (state == "empty" and item["symbol"] == price_symbol):
            continue
        span = f"{_hhmm(item['start'])}–{_hhmm(item['end'])} ET"
        if state == "unreadable":
            warnings.append(f"{item['symbol']} trade cache file for {item['file_date']} is unreadable: no {item['symbol']} prints {span}.")
        elif state == "missing":
            warnings.append(f"{item['symbol']} trade cache file for {item['file_date']} is missing: no {item['symbol']} prints {span}.")
        else:
            warnings.append(f"No cached {item['symbol']} prints {span}.")
    return warnings


def _coverage_warnings(price_ts: pd.DatetimeIndex, start: datetime, end: datetime, price_symbol: str) -> List[str]:
    """Stretches of the session with no price-symbol prints longer than COVERAGE_GAP_MIN."""
    edges = np.concatenate([
        [pd.Timestamp(_utc(start)).value],
        pd.DatetimeIndex(price_ts).asi8,
        [pd.Timestamp(_utc(end)).value],
    ])
    at = np.flatnonzero(np.diff(edges) > COVERAGE_GAP_MIN * 60 * NS)
    gaps = [(pd.Timestamp(edges[i], tz="UTC"), pd.Timestamp(edges[i + 1], tz="UTC")) for i in at.tolist()]
    warnings = [f"No cached {price_symbol} prints {_hhmm(a)}–{_hhmm(b)} ET." for a, b in gaps[:3]]
    if len(gaps) > 3:
        warnings.append(f"{len(gaps) - 3} more {price_symbol} print gaps longer than {COVERAGE_GAP_MIN} minutes.")
    return warnings


# --- slopes and beta -------------------------------------------------------------------------------


class SlopeCache:
    """Whole-session change-on-change slopes for one view, seeded read-only from beta_ref.json."""

    def __init__(self, view: str, root: Optional[Path] = None):
        self.view = view
        self.path = (root or HISTORY_DIR) / view / SLOPES_FILE
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.dirty = False
        self.reload()

    def reload(self) -> None:
        self.rows = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if raw.get("version") == BETA_VERSION:
                    self.rows = {k: v for k, v in (raw.get("rows") or {}).items() if isinstance(v, dict)}
            except Exception:
                logger.warning("V3 history slope cache unreadable: %s", self.path)
        self.seed_from_beta_ref()

    def seed_from_beta_ref(self, entries: Optional[Dict[str, Dict[str, Any]]] = None) -> int:
        """Copy live whole-session slopes for this view; they win over slopes fitted from the cache."""
        price_symbol, symbols, dv01_adjust = HISTORY_VIEWS[self.view]
        seeded = 0
        for entry in (entries if entries is not None else _entries_from_disk()).values():
            if (
                entry.get("version") != BETA_VERSION
                or entry.get("price_symbol") != price_symbol
                or tuple(entry.get("aggression_symbols") or ()) != symbols
                or bool(entry.get("dv01_adjust")) != dv01_adjust
            ):
                continue
            for row in entry.get("session_rows") or []:
                key = row.get("session_start")
                beta = row.get("beta")
                if not key or not row.get("ok") or beta is None or not np.isfinite(float(beta)):
                    continue
                current = self.rows.get(key)
                if current and current.get("source") == "beta_ref" and current.get("beta") == float(beta):
                    continue
                self.rows[key] = {"ok": True, "beta": float(beta), "n": row.get("n"), "source": "beta_ref"}
                self.dirty = True
                seeded += 1
        return seeded

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.rows.get(key)

    def put(self, key: str, fit: Dict[str, Any], source: str) -> None:
        current = self.rows.get(key)
        if current and current.get("source") == "beta_ref":
            return
        beta = fit.get("beta")
        self.rows[key] = {
            "ok": bool(fit.get("ok")) and beta is not None and bool(np.isfinite(float(beta))),
            "beta": None if beta is None else float(beta),
            "n": fit.get("n"),
            "reason": fit.get("reason"),
            "source": source,
        }
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        _atomic_write_text(self.path, json.dumps({"version": BETA_VERSION, "rows": self.rows}, indent=1))
        self.dirty = False


def _session_frame(
    view: str,
    start: datetime,
    dv01_table: Dict[str, float],
    root: Optional[Path] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any], List[Dict[str, Any]]]:
    price_symbol, symbols, dv01_adjust = HISTORY_VIEWS[view]
    if close_snapshot_covers(view, start, root) and not cache_ready(view, start):
        trades, status = load_close_snapshot(symbols, start, root)
    else:
        trades, status = load_session_trades(symbols, start)
    frame, meta = build_print_frame(
        trades,
        price_symbol=price_symbol,
        dv01_table=dv01_table,
        dv01_adjust=dv01_adjust,
        session_start=start,
    )
    return frame, meta, status


def _slope_fit(frame: pd.DataFrame, status: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Whole-session slope; a session with a missing or unreadable cache file fails, as live leaves it out."""
    bad = sorted({f"{item['symbol']} {item['file_date']}" for item in status if item["state"] in ("missing", "unreadable")})
    if bad:
        return {
            "ok": False,
            "beta": None,
            "n": int(len(frame)),
            "reason": "Trade cache file missing or unreadable: " + ", ".join(bad),
        }
    return fit_session_slope(frame)


def prior_beta(
    view: str,
    start: datetime,
    slopes: SlopeCache,
    dv01_table: Dict[str, float],
    now: Optional[datetime] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Reference beta as of this session open: today's tag day-OLS when the table is ready.

    Falls back to the superseded 10-session whole-session mean only when the tag table
    is missing or still computing for this selection.
    """
    from scripts.aggression_dashboard_v3 import tag_beta as tb

    price_symbol, symbols, dv01_adjust = HISTORY_VIEWS[view]
    during = start.astimezone(timezone.utc) + timedelta(hours=1)
    tagged = tb.beta_for_session(
        price_symbol=price_symbol,
        aggression_symbols=symbols,
        dv01_adjust=dv01_adjust,
        now=during,
    )
    if tagged.get("ok") and not tagged.get("stale"):
        beta_ref = float(tagged["beta_ref"])
        return {
            "beta_ref": beta_ref,
            "beta_display_yield_bp_per_1m": beta_to_yield_bp_per_1m(beta_ref, price_symbol, dv01_table),
            "n_sessions_used": int(tagged.get("n_sessions_used") or 0),
            "sessions_used": [],
            "sessions_missing": [],
            "tag": tagged.get("tag"),
            "tag_detail": tagged.get("tag_detail"),
            "beta_version": tagged.get("version"),
            "slope_bps_per_mm": tagged.get("slope_bps_per_mm"),
            "last_tag_day": tagged.get("last_tag_day"),
        }
    used: List[str] = []
    missing: List[str] = []
    betas: List[float] = []
    for prior in completed_session_starts(start, n=BETA_REF_SESSIONS):
        key = _session_key(prior)
        row = slopes.get(key)
        if row is None and (cache_ready(view, prior, now) or close_snapshot_covers(view, prior, root)):
            frame, _meta, status = _session_frame(view, prior, dv01_table, root)
            slopes.put(key, _slope_fit(frame, status), "cache_fit")
            row = slopes.get(key)
        if row and row.get("ok"):
            betas.append(float(row["beta"]))
            used.append(key)
        else:
            missing.append(key)
    beta_ref, n_used = session_set_mean(betas)
    return {
        "beta_ref": beta_ref,
        "beta_display_yield_bp_per_1m": (
            beta_to_yield_bp_per_1m(beta_ref, price_symbol, dv01_table) if beta_ref is not None else None
        ),
        "n_sessions_used": n_used,
        "sessions_used": used,
        "sessions_missing": missing,
        "beta_version": BETA_VERSION,
    }


# --- build -----------------------------------------------------------------------------------------


def build_session(
    view: str,
    start: datetime,
    *,
    slopes: Optional[SlopeCache] = None,
    dv01_table: Optional[Dict[str, float]] = None,
    now: Optional[datetime] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build one completed session for one view: per-second rows plus meta. Returns the meta."""
    price_symbol, symbols, dv01_adjust = HISTORY_VIEWS[view]
    start = _utc(start)
    end = globex_session_end(start)
    now_utc = _utc(now or datetime.now(timezone.utc))
    dv01_table = dv01_table or load_dv01_table()
    slopes = slopes or SlopeCache(view, root)
    parquet_path, meta_path = session_paths(view, start, root)
    from_snapshot = close_snapshot_covers(view, start, root) and not cache_ready(view, start, now_utc)
    frame, frame_meta, status = _session_frame(view, start, dv01_table, root)
    price_prints = int(frame_meta.get("per_symbol", {}).get(price_symbol, {}).get("trade_count", 0))
    warnings = _cache_warnings(status, price_symbol)
    meta: Dict[str, Any] = {
        "version": HISTORY_VERSION,
        "view": view,
        "view_label": view_label(view),
        "price_symbol": price_symbol,
        "aggression_symbols": list(symbols),
        "dv01_adjust": dv01_adjust,
        "session_key": _session_key(start),
        "session_open": start.isoformat(),
        "session_close": end.isoformat(),
        "session_open_et": _et_naive(start),
        "session_close_et": _et_naive(end),
        "label": session_label(start),
        "prints": int(frame_meta.get("trade_count", 0)),
        "price_prints": price_prints,
        "front_contracts": {
            symbol: info.get("front_contracts", []) for symbol, info in frame_meta.get("per_symbol", {}).items()
        },
        "built_at": now_utc.isoformat(),
        "source": "close_snapshot" if from_snapshot else "trade_cache",
        "ok": False,
        "reason": None,
        "rows": 0,
        "warnings": warnings,
    }

    def finish_failed(reason: str) -> Dict[str, Any]:
        meta["reason"] = reason
        slopes.save()
        if parquet_path.exists():
            parquet_path.unlink()
        _atomic_write_text(meta_path, json.dumps(meta, indent=1, default=str))
        logger.warning("V3 history %s %s failed: %s", view, meta["label"], reason)
        return meta

    if frame.empty or price_prints == 0:
        where = "close snapshot" if from_snapshot else "trade cache"
        return finish_failed(f"No {price_symbol} prints in the {where} for this session.")
    own = _slope_fit(frame, status)
    slopes.put(meta["session_key"], own, "history_build")
    meta["own_slope"] = {key: own.get(key) for key in ("ok", "beta", "n", "reason")}
    beta = prior_beta(view, start, slopes, dv01_table, now_utc, root)
    meta.update(beta)
    if beta["beta_ref"] is None or beta["beta_ref"] <= 0:
        return finish_failed("No positive reference beta (tag table or prior-session fallback).")
    if beta.get("tag"):
        warnings.append(
            f"Tag beta {beta['tag']}: {beta['n_sessions_used']} earlier days"
            + (f" through {beta['last_tag_day']}" if beta.get("last_tag_day") else "")
            + "."
        )
    elif beta["n_sessions_used"] < BETA_REF_SESSIONS:
        warnings.append(f"Fallback beta from {beta['n_sessions_used']} of {BETA_REF_SESSIONS} prior sessions.")

    ts_ns = pd.DatetimeIndex(frame.index).asi8
    status_code, state, d, a, r = window_states(
        ts_ns,
        frame["price"].to_numpy(dtype=float),
        frame["total_aggress"].to_numpy(dtype=float),
        frame["session_id"].astype(str).to_numpy(),
        beta["beta_ref"],
        tick_size(price_symbol),
    )
    seconds = ts_ns // NS
    keep = np.ones(len(ts_ns), dtype=bool)
    keep[:-1] = seconds[1:] != seconds[:-1]
    rows = pd.DataFrame({
        "t": ts_ns[keep],
        "price": frame["price"].to_numpy(dtype=float)[keep],
        "total_aggress": frame["total_aggress"].to_numpy(dtype=float)[keep],
        "status": status_code[keep],
        "state": state[keep],
        "d": d[keep].astype(np.float32),
        "a": a[keep].astype(np.float32),
        "r": r[keep].astype(np.float32),
    })
    price_ts = pd.DatetimeIndex(frame.index[frame["dashboard_symbol"] == price_symbol])
    warnings.extend(_coverage_warnings(price_ts, start, end, price_symbol))
    meta.update({
        "ok": True,
        "rows": int(len(rows)),
        "ready_rows": int((rows["status"] == STATUS_READY).sum()),
        "time_start": _et_naive(pd.Timestamp(ts_ns[0], tz="UTC").floor("s").to_pydatetime()),
        "time_end": _et_naive(pd.Timestamp(ts_ns[-1], tz="UTC").floor("s").to_pydatetime()),
    })
    slopes.save()
    _atomic_write_parquet(parquet_path, rows)
    _atomic_write_text(meta_path, json.dumps(meta, indent=1, default=str))
    logger.info(
        "V3 history %s %s built: %s rows, beta %.3e from %s sessions, %s warnings",
        view,
        meta["label"],
        len(rows),
        beta["beta_ref"],
        beta["n_sessions_used"],
        len(warnings),
    )
    return meta


# --- discovery -------------------------------------------------------------------------------------


def _built_at(meta: Dict[str, Any]) -> Optional[datetime]:
    try:
        return _utc(datetime.fromisoformat(str(meta.get("built_at"))))
    except (TypeError, ValueError):
        return None


def missing_sessions(view: str, now: Optional[datetime] = None, root: Optional[Path] = None) -> List[datetime]:
    """Recent completed sessions to build: never built (or failed long enough ago) and cached. Oldest first."""
    now_utc = _utc(now or datetime.now(timezone.utc))
    out: List[datetime] = []
    for start in completed_session_starts(now_utc, n=BACKFILL_SESSIONS):
        meta = read_meta(view, start, root)
        if meta is not None:
            if meta.get("ok"):
                continue
            built = _built_at(meta)
            if built is not None and now_utc - built < timedelta(hours=FAILED_RETRY_HOURS):
                continue
        if cache_ready(view, start, now_utc) or close_snapshot_covers(view, start, root):
            out.append(start)
    return out


def _pending_reason(view: str, start: datetime, now: datetime, root: Optional[Path] = None) -> str:
    """What the page says before a session is built."""
    ready = cache_ready(view, start, now) or close_snapshot_covers(view, start, root)
    saving = in_close_save_window(now) and trading_day_start(now) == _utc(start)
    if saving:
        return "Building from the session that just closed."
    if ready:
        return "Queued for the next market-closed build window."
    return (
        "Missed the 17:00–18:00 ET save. "
        "It will be built from the trade cache on a later closed window."
    )


def _session_info(
    view: str,
    start: datetime,
    meta: Optional[Dict[str, Any]],
    now: datetime,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    end = globex_session_end(start)
    info: Dict[str, Any] = {
        "session_key": _session_key(start),
        "label": session_label(start),
        "open_et": _et_naive(start),
        "close_et": _et_naive(end),
    }
    if end > now:
        info.update(status="live", reason="In progress; the live view above covers it until it closes.")
    elif meta is None:
        info.update(status="pending", reason=_pending_reason(view, start, now, root))
    elif not meta.get("ok"):
        info.update(status="failed", reason=meta.get("reason"), warnings=meta.get("warnings") or [])
    else:
        info.update(
            status="built",
            beta_ref=meta.get("beta_ref"),
            beta_display_yield_bp_per_1m=meta.get("beta_display_yield_bp_per_1m"),
            n_sessions_used=meta.get("n_sessions_used"),
            prints=meta.get("prints"),
            rows=meta.get("rows"),
            warnings=meta.get("warnings") or [],
        )
    return info


def list_sessions(view: str, now: Optional[datetime] = None, root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Built sessions on disk plus recent completed sessions, oldest first."""
    now_utc = _utc(now or datetime.now(timezone.utc))
    starts = {_session_key(start): start for start in completed_session_starts(now_utc, n=BACKFILL_SESSIONS)}
    folder = (root or HISTORY_DIR) / view
    if folder.exists():
        for meta_path in folder.glob("*.json"):
            if meta_path.name == SLOPES_FILE:
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                start = _utc(datetime.fromisoformat(meta["session_open"]))
            except Exception:
                continue
            if meta.get("version") == HISTORY_VERSION:
                starts[_session_key(start)] = start
    return [
        _session_info(view, start, read_meta(view, start, root), now_utc, root)
        for start in sorted(starts.values())
    ]


def quick_ranges(sessions: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
    """The last built session, and the five completed sessions ending with it, as the page requests them.

    The scheduler pre-warms exactly these ranges.
    """
    completed = [info for info in sessions if info.get("status") != "live"]
    built_at = [i for i, info in enumerate(completed) if info.get("status") == "built"]
    if not built_at:
        return {"last_session": None, "last_5": None}
    last = completed[built_at[-1]]
    window = completed[max(0, built_at[-1] - 4): built_at[-1] + 1]
    return {
        "last_session": {"start": last["open_et"], "end": last["close_et"], "label": last["label"]},
        "last_5": {
            "start": window[0]["open_et"],
            "end": last["close_et"],
            "label": f"{window[0]['label']} – {last['label']}",
            "sessions": len(window),
        },
    }


# --- serve -----------------------------------------------------------------------------------------


def _next_open(start: datetime) -> datetime:
    start_et = start.astimezone(EASTERN)
    return datetime.combine(start_et.date() + timedelta(days=1), time(18, 0), tzinfo=EASTERN).astimezone(timezone.utc)


def sessions_in_range(start: datetime, end: datetime, limit: int = HISTORY_MAX_SESSIONS) -> List[datetime]:
    """Globex session opens whose session overlaps [start, end]."""
    start, end = _utc(start), _utc(end)
    if end <= start:
        raise HistoryRangeError("The end time must be after the start time.")
    opens: List[datetime] = []
    cursor = trading_day_start(start)
    while cursor < end:
        if is_valid_globex_open(cursor) and globex_session_end(cursor) > start:
            opens.append(cursor)
            if len(opens) > limit:
                raise HistoryRangeError(f"Pick a range of at most {limit} sessions.")
        cursor = _next_open(cursor)
    return opens


class SessionFrameCache:
    """Recently served session files, reloaded when the file changes."""

    def __init__(self, slots: int = FRAME_CACHE_SLOTS):
        self._slots = slots
        self._frames: "OrderedDict[tuple, Tuple[float, pd.DataFrame]]" = OrderedDict()

    def get(self, view: str, start: datetime, root: Optional[Path] = None) -> Optional[pd.DataFrame]:
        path = session_paths(view, start, root)[0]
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        key = (str(root or HISTORY_DIR), view, _session_key(start))
        hit = self._frames.get(key)
        if hit is not None and hit[0] == mtime:
            self._frames.move_to_end(key)
            return hit[1]
        frame = pd.read_parquet(path)
        self._frames[key] = (mtime, frame)
        self._frames.move_to_end(key)
        while len(self._frames) > self._slots:
            self._frames.popitem(last=False)
        return frame


def _decimate_indices(columns: List[np.ndarray], buckets: int) -> np.ndarray:
    """Min/max decimation: first, last, min and max of each column per equal-count bucket."""
    n = len(columns[0]) if columns else 0
    if n <= buckets * 4:
        return np.arange(n)
    edges = np.floor(np.arange(buckets + 1) * (n / buckets)).astype(np.int64)
    picks: List[int] = []
    for lo, hi in zip(edges[:-1].tolist(), edges[1:].tolist()):
        if hi <= lo:
            continue
        picks.extend((lo, hi - 1))
        for values in columns:
            chunk = values[lo:hi]
            finite = np.isfinite(chunk)
            if not finite.any():
                continue
            picks.append(lo + int(np.where(finite, chunk, np.inf).argmin()))
            picks.append(lo + int(np.where(finite, chunk, -np.inf).argmax()))
    return np.unique(np.asarray(picks, dtype=np.int64))


def _cell_ends(t_ns: np.ndarray, session_idx: np.ndarray) -> np.ndarray:
    """A row's state holds until the next row of the same session, else for one second."""
    ends = t_ns + NS
    if len(t_ns) > 1:
        same = session_idx[1:] == session_idx[:-1]
        ends[:-1] = np.where(same, t_ns[1:], ends[:-1])
    return ends


def _binned_cells(
    t_ns: np.ndarray,
    ends: np.ndarray,
    codes: np.ndarray,
    n_codes: int,
    start_ns: int,
    end_ns: int,
    max_bins: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Most common READY code per time bin; a bin without rows keeps the state held into it."""
    span = max(int(end_ns - start_ns), NS)
    width = max(NS, int(math.ceil(span / max_bins / NS)) * NS)
    n_bins = int(math.ceil(span / width))
    starts = start_ns + np.arange(n_bins, dtype=np.int64) * width
    stops = np.minimum(starts + width, end_ns)
    idx = (t_ns - start_ns) // width
    inside = (idx >= 0) & (idx < n_bins)
    ready = inside & (codes >= 0)
    counts = np.zeros((n_bins, n_codes), dtype=np.int32)
    np.add.at(counts, (idx[ready], codes[ready].astype(np.int64)), 1)
    out = np.where(counts.sum(axis=1) > 0, counts.argmax(axis=1), -1).astype(np.int64)
    has_rows = np.zeros(n_bins, dtype=bool)
    has_rows[idx[inside]] = True
    prev = np.searchsorted(t_ns, starts, side="left") - 1
    prev_c = np.clip(prev, 0, None)
    held = (~has_rows) & (prev >= 0) & (ends[prev_c] > starts) & (codes[prev_c] >= 0)
    out[held] = codes[prev_c][held]
    return starts, stops, out


def _runs_from_cells(starts: np.ndarray, stops: np.ndarray, codes: np.ndarray, key: str) -> List[Dict[str, Any]]:
    n = len(codes)
    if n == 0:
        return []
    change = np.ones(n, dtype=bool)
    change[1:] = (codes[1:] != codes[:-1]) | (starts[1:] != stops[:-1])
    first = np.flatnonzero(change)
    last = np.r_[first[1:], n] - 1
    keep = codes[first] >= 0
    first, last = first[keep], last[keep]
    x0s, x1s = _et_iso(starts[first]), _et_iso(stops[last])
    runs: List[Dict[str, Any]] = []
    for i, position in enumerate(first.tolist()):
        code = int(codes[position])
        state = STATE_IDS[code] if key == "state" else _GROUP_EXAMPLE[GROUP_IDS[code]]
        run = state_run({"status": "READY", "state": state}, key, x0s[i])
        run["x1"] = x1s[i]
        runs.append(run)
    return runs


def _strip_panel(
    t_ns: np.ndarray,
    session_idx: np.ndarray,
    state: np.ndarray,
    start_ns: int,
    end_ns: int,
    max_bins: int,
) -> Dict[str, Any]:
    codes = state.astype(np.int64)
    group_codes = np.where(codes >= 0, _STATE_GROUP[np.clip(codes, 0, None)], -1).astype(np.int64)
    ends = _cell_ends(t_ns, session_idx)
    bin_sec = 0
    if len(t_ns) <= max_bins:
        fine = (t_ns, ends, codes)
        coarse = (t_ns, ends, group_codes)
    else:
        starts, stops, fine_codes = _binned_cells(t_ns, ends, codes, len(STATE_IDS), start_ns, end_ns, max_bins)
        _, _, coarse_codes = _binned_cells(t_ns, ends, group_codes, len(GROUP_IDS), start_ns, end_ns, max_bins)
        fine = (starts, stops, fine_codes)
        coarse = (starts, stops, coarse_codes)
        bin_sec = int((stops[0] - starts[0]) // NS) if len(starts) else 0
    return {
        "title": "History states",
        "caption": HISTORY_STRIP_CAPTION,
        "data": _runs_from_cells(*fine, "state"),
        "group_data": _runs_from_cells(*coarse, "group"),
        "state_legend": coarse_state_legend(),
        "layout": {"yaxis_title": "", "xaxis_title": "Time (ET)"},
        "uirevision": "history",
        "bin_sec": bin_sec,
    }


def _hover_rows(
    t_ns: np.ndarray,
    status: np.ndarray,
    state: np.ndarray,
    d: np.ndarray,
    a: np.ndarray,
    r: np.ndarray,
    max_rows: int,
) -> List[Dict[str, Any]]:
    n = len(t_ns)
    if n == 0:
        return []
    pick = np.arange(n) if n <= max_rows else np.unique(np.linspace(0, n - 1, max_rows).astype(np.int64))
    xs, as_of = _et_iso(t_ns[pick]), _utc_iso(t_ns[pick])
    rows: List[Dict[str, Any]] = []
    for j, i in enumerate(pick.tolist()):
        code = int(status[i])
        if code == STATUS_READY:
            state_id = STATE_IDS[int(state[i])]
            headline, color, _family, group = STATE_STYLE[state_id]
            group_headline, group_color = GROUP_STYLE[group]
            rows.append({
                "x": xs[j],
                "as_of": as_of[j],
                "status": "READY",
                "state": state_id,
                "headline": headline,
                "color": color,
                "group_headline": group_headline,
                "group_color": group_color,
                "contribution_line": contribution_line(
                    float(d[i]),
                    float(a[i]),
                    float(r[i]),
                    window_minutes(pd.Timestamp(int(t_ns[i]), unit="ns", tz="UTC")),
                ),
            })
        else:
            name = STATUS_NAMES[code]
            rows.append({
                "x": xs[j],
                "as_of": as_of[j],
                "status": name,
                "state": name,
                "headline": name,
                "group_headline": name,
                "reason": STATUS_REASONS.get(name),
            })
    return rows


def _last_state_banner(row: Dict[str, Any], meta: Dict[str, Any], price_symbol: str) -> Dict[str, Any]:
    code = int(row["status"])
    name = STATUS_NAMES[code]
    as_of = pd.Timestamp(int(row["t"]), tz="UTC").isoformat()
    beta_ref = meta.get("beta_ref")
    beta_display = meta.get("beta_display_yield_bp_per_1m")
    tick = tick_size(price_symbol)
    if code == STATUS_READY:
        return _payload(
            "READY",
            STATE_IDS[int(row["state"])],
            d=float(row["d"]),
            a=float(row["a"]),
            b=float(row["r"]),
            beta_ref=beta_ref,
            beta_display=beta_display,
            as_of=as_of,
            tick=tick,
        )
    return _payload(
        name,
        name,
        reason=STATUS_REASONS.get(name),
        beta_ref=beta_ref,
        beta_display=beta_display,
        as_of=as_of,
        tick=tick,
    )


def serve_history(
    view: str,
    start: datetime,
    end: datetime,
    *,
    frame_cache: Optional[SessionFrameCache] = None,
    now: Optional[datetime] = None,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Payload for one view over [start, end]: Panel 1, the two-row strip, hover rows and last-state banners."""
    if view not in HISTORY_VIEWS:
        raise HistoryRangeError(f"Unknown history view: {view}")
    price_symbol = HISTORY_VIEWS[view][0]
    start, end = _utc(start), _utc(end)
    now_utc = _utc(now or datetime.now(timezone.utc))
    opens = [session_start for session_start in sessions_in_range(start, end) if session_start <= now_utc]
    frame_cache = frame_cache or SessionFrameCache()
    start_ns = int(pd.Timestamp(start).value)
    end_ns = int(pd.Timestamp(end).value)
    sessions: List[Dict[str, Any]] = []
    warnings: List[str] = []
    slices: List[Tuple[int, pd.DataFrame, Dict[str, Any]]] = []
    for position, session_start in enumerate(opens):
        meta = read_meta(view, session_start, root)
        info = _session_info(view, session_start, meta, now_utc)
        sessions.append(info)
        label = info["label"]
        if info["status"] != "built":
            warnings.append(f"{label}: {info['status']} — {info.get('reason') or 'no reason recorded'}")
            continue
        warnings.extend(f"{label}: {text}" for text in info.get("warnings") or [])
        frame = frame_cache.get(view, session_start, root)
        if frame is None:
            continue
        mask = (frame["t"].to_numpy() >= start_ns) & (frame["t"].to_numpy() <= end_ns)
        if mask.any():
            slices.append((position, frame.loc[mask], meta))

    payload: Dict[str, Any] = {
        "ok": True,
        "view": view,
        "view_label": view_label(view),
        "label": LABEL,
        "range": {
            "start_et": _et_naive(start),
            "end_et": _et_naive(end),
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "sessions": sessions,
        "warnings": warnings,
    }
    if not slices:
        payload.update({
            "panels": {
                "p1": {"title": "TotalAggress vs Price/Yield", "data": [], "uirevision": "history"},
                "p1_states": _strip_panel(*(np.array([], dtype=np.int64),) * 3, start_ns, end_ns, STRIP_MAX_BINS),
            },
            "flow_dominance": None,
            "hover": [],
            "resolution": {"rows": 0, "panel_points": 0, "strip_bin_sec": 0, "hover_rows": 0},
        })
        return payload

    t_ns = np.concatenate([part["t"].to_numpy(dtype=np.int64) for _, part, _ in slices])
    session_idx = np.concatenate([np.full(len(part), position) for position, part, _ in slices])
    price = np.concatenate([part["price"].to_numpy(dtype=float) for _, part, _ in slices])
    aggress = np.concatenate([part["total_aggress"].to_numpy(dtype=float) for _, part, _ in slices])
    status = np.concatenate([part["status"].to_numpy(dtype=np.int8) for _, part, _ in slices])
    state = np.concatenate([part["state"].to_numpy(dtype=np.int8) for _, part, _ in slices])
    d = np.concatenate([part["d"].to_numpy(dtype=float) for _, part, _ in slices])
    a = np.concatenate([part["a"].to_numpy(dtype=float) for _, part, _ in slices])
    r = np.concatenate([part["r"].to_numpy(dtype=float) for _, part, _ in slices])

    picks = _decimate_indices([aggress, price], PANEL_BUCKETS)
    xs = _et_iso(t_ns[picks])
    points: List[Dict[str, Any]] = []
    previous_session = None
    for j, i in enumerate(picks.tolist()):
        if previous_session is not None and session_idx[i] != previous_session:
            break_at = globex_session_end(opens[int(previous_session)])
            points.append({"x": _et_naive(break_at), "total_aggress": None, "price": None})
        previous_session = session_idx[i]
        points.append({
            "x": xs[j],
            "total_aggress": float(aggress[i]),
            "price": float(price[i]) if np.isfinite(price[i]) else None,
        })
    aligned = pd.DataFrame({"total_aggress": aggress[picks], "price": price[picks]})
    panel_1 = {
        "title": "TotalAggress vs Price/Yield",
        "data": points,
        "traces": [
            {"field": "total_aggress", "name": "TotalAggress", "axis": "y", "color": "#1f4ed8", "connectgaps": False},
            {"field": "price", "name": "Last trade", "axis": "y2", "color": "#d62728", "connectgaps": False},
        ],
        "layout": {"yaxis_title": "TotalAggress", "yaxis2_title": "Last trade"},
        "axis_align": _build_axis_alignment(aligned),
        "uirevision": "history",
    }
    strip = _strip_panel(t_ns, session_idx, state, start_ns, end_ns, STRIP_MAX_BINS)
    hover = _hover_rows(t_ns, status, state, d, a, r, HOVER_MAX_ROWS)
    last_part, last_meta = slices[-1][1], slices[-1][2]
    last_row = {column: last_part[column].to_numpy()[-1] for column in last_part.columns}
    banner = _last_state_banner(last_row, last_meta, price_symbol)
    payload.update({
        "panels": {"p1": panel_1, "p1_states": strip},
        "flow_dominance": banner,
        "hover": hover,
        "resolution": {
            "rows": int(len(t_ns)),
            "panel_points": len(points),
            "strip_bin_sec": strip.get("bin_sec", 0),
            "hover_rows": len(hover),
        },
    })
    return payload
