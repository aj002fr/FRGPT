"""One-to-one live/hist print matching for the V3 session store."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.core import TICK_SIZE_BY_SYMBOL

logger = logging.getLogger(__name__)

MATCH_WINDOW = pd.Timedelta(milliseconds=1)
MATCH_RATE_FLOOR = 0.5
TRADE_COLUMNS = [
    "ts",
    "price",
    "size",
    "side",
    "instrument_id",
    "raw_symbol",
    "dashboard_symbol",
]


def normalize_side(side: object) -> str:
    token = str(side or "").strip().upper()
    if token in ("B", "BID"):
        return "B"
    if token in ("A", "ASK"):
        return "A"
    return "N"


def normalize_size(size: object) -> int:
    return int(round(float(size)))


def normalize_price(price: object, symbol: str) -> float:
    tick = float(TICK_SIZE_BY_SYMBOL.get(symbol, 1.0 / 64.0))
    return round(float(price) / tick) * tick


def empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def _as_trades(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_trades()
    out = frame.copy()
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    return out.reset_index(drop=True)


def _is_missing_seq(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _matched_incoming(stored: pd.DataFrame, incoming: pd.DataFrame, symbol: str) -> np.ndarray:
    """True for each incoming row that pairs to one unused stored row."""
    n_in = len(incoming)
    matched = np.zeros(n_in, dtype=bool)
    if stored.empty or incoming.empty:
        return matched

    s_side = stored["side"].map(normalize_side).to_numpy()
    s_size = stored["size"].map(normalize_size).to_numpy()
    s_price = np.array([normalize_price(value, symbol) for value in stored["price"]], dtype=float)
    s_raw = (
        stored["raw_symbol"].fillna("").astype(str).to_numpy()
        if "raw_symbol" in stored.columns
        else np.array([""] * len(stored))
    )
    s_ts = stored["ts"].to_numpy(dtype="datetime64[ns]")
    i_side = incoming["side"].map(normalize_side).to_numpy()
    i_size = incoming["size"].map(normalize_size).to_numpy()
    i_price = np.array([normalize_price(value, symbol) for value in incoming["price"]], dtype=float)
    i_raw = (
        incoming["raw_symbol"].fillna("").astype(str).to_numpy()
        if "raw_symbol" in incoming.columns
        else np.array([""] * n_in)
    )
    i_ts = incoming["ts"].to_numpy(dtype="datetime64[ns]")
    window = np.timedelta64(1, "ms")
    used = np.zeros(len(stored), dtype=bool)

    if "sequence" in stored.columns and "sequence" in incoming.columns:
        seq_to_j: Dict[object, Optional[int]] = {}
        for j, seq in enumerate(stored["sequence"].to_numpy()):
            if _is_missing_seq(seq):
                continue
            seq_to_j[seq] = None if seq in seq_to_j else j
        for i, seq in enumerate(incoming["sequence"].to_numpy()):
            if _is_missing_seq(seq):
                continue
            j = seq_to_j.get(seq)
            if j is None or used[j]:
                continue
            if i_side[i] != s_side[j] or i_size[i] != s_size[j] or i_price[i] != s_price[j]:
                continue
            if i_raw[i] and s_raw[j] and i_raw[i] != s_raw[j]:
                continue
            if abs(s_ts[j] - i_ts[i]) > window:
                continue
            used[j] = True
            matched[i] = True

    buckets: Dict[Tuple[str, int, float], list] = defaultdict(list)
    for j in range(len(stored)):
        buckets[(str(s_side[j]), int(s_size[j]), float(s_price[j]))].append(j)

    for i in range(n_in):
        if matched[i]:
            continue
        best_j = None
        best_dt = None
        for j in buckets[(str(i_side[i]), int(i_size[i]), float(i_price[i]))]:
            if used[j]:
                continue
            if i_raw[i] and s_raw[j] and i_raw[i] != s_raw[j]:
                continue
            delta = abs(s_ts[j] - i_ts[i])
            if delta > window:
                continue
            if best_dt is None or delta < best_dt:
                best_j = j
                best_dt = delta
        if best_j is not None:
            used[best_j] = True
            matched[i] = True
    return matched


def _concat_keep(stored: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (stored, extra) if frame is not None and not frame.empty]
    if not frames:
        return empty_trades()
    out = pd.concat(frames, ignore_index=True)
    out["ts"] = pd.to_datetime(out["ts"], utc=True)
    out.sort_values("ts", inplace=True)
    out.reset_index(drop=True, inplace=True)
    return out


def apply_live(stored: Optional[pd.DataFrame], live: Optional[pd.DataFrame], symbol: str) -> pd.DataFrame:
    """Keep stored rows; always insert unmatched live prints."""
    stored_df = _as_trades(stored)
    live_df = _as_trades(live)
    if live_df.empty:
        return stored_df
    if stored_df.empty:
        return live_df.sort_values("ts").reset_index(drop=True)
    matched = _matched_incoming(stored_df, live_df, symbol)
    return _concat_keep(stored_df, live_df.loc[~matched])


def apply_hist(stored: Optional[pd.DataFrame], hist: Optional[pd.DataFrame], symbol: str) -> pd.DataFrame:
    """Keep stored rows; insert hist only for holes (left-of-store always)."""
    stored_df = _as_trades(stored)
    hist_df = _as_trades(hist)
    if hist_df.empty:
        return stored_df
    if stored_df.empty:
        return hist_df.sort_values("ts").reset_index(drop=True)

    matched = _matched_incoming(stored_df, hist_df, symbol)
    tmin = stored_df["ts"].min()
    tmax = stored_df["ts"].max()
    is_left = hist_df["ts"] < (tmin - MATCH_WINDOW)
    is_right = hist_df["ts"] > (tmax + MATCH_WINDOW)
    is_interior = ~is_left & ~is_right
    interior_n = int(is_interior.sum())
    interior_matched = int((is_interior.to_numpy() & matched).sum())
    rate = (interior_matched / interior_n) if interior_n else 1.0
    if interior_n and rate < MATCH_RATE_FLOOR:
        logger.warning(
            "V3 hist overlap match rate %.3f < %.2f for %s; not inserting interior holes",
            rate,
            MATCH_RATE_FLOOR,
            symbol,
        )
        keep = is_left.to_numpy()
    else:
        keep = (~matched) & (~is_right.to_numpy())
    return _concat_keep(stored_df, hist_df.loc[keep])


def reconcile_symbol(
    stored: Optional[pd.DataFrame],
    *,
    live: Optional[pd.DataFrame] = None,
    historical: Optional[pd.DataFrame] = None,
    symbol: str,
) -> pd.DataFrame:
    """Apply live first, then hist, for one dashboard symbol."""
    return apply_hist(apply_live(stored, live, symbol), historical, symbol)


def reconcile_by_symbol(
    stored: Dict[str, pd.DataFrame],
    symbols: Iterable[str],
    *,
    live: Optional[Dict[str, pd.DataFrame]] = None,
    historical: Optional[Dict[str, pd.DataFrame]] = None,
) -> Dict[str, pd.DataFrame]:
    live = live or {}
    historical = historical or {}
    return {
        symbol: reconcile_symbol(
            stored.get(symbol),
            live=live.get(symbol),
            historical=historical.get(symbol),
            symbol=symbol,
        )
        for symbol in symbols
    }
