"""One-to-one live/hist print matching for the V3 session store."""

from __future__ import annotations

import bisect
import logging
from datetime import timezone
from typing import Dict, Iterable, List, Optional, Tuple

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


def _utc_ts(values: pd.Series) -> pd.Series:
    """``pd.to_datetime(values, utc=True)``, skipped when it would return the same column."""
    dtype = values.dtype
    if isinstance(dtype, pd.DatetimeTZDtype) and dtype.tz is timezone.utc:
        return values
    return pd.to_datetime(values, utc=True)


def _as_trades(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_trades()
    out = frame.copy()
    out["ts"] = _utc_ts(out["ts"])
    return out.reset_index(drop=True)


def _is_missing_seq(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


_WINDOW_NS = 1_000_000
_SIDE_CODE = {"B": 0, "A": 1, "N": 2}


def _side_codes(frame: pd.DataFrame) -> np.ndarray:
    codes, uniques = pd.factorize(frame["side"], use_na_sentinel=False)
    table = np.array([_SIDE_CODE[normalize_side(value)] for value in uniques], dtype=np.int64)
    return table[codes] if len(table) else np.zeros(len(frame), dtype=np.int64)


def _finite_floats(values: pd.Series) -> Optional[np.ndarray]:
    try:
        arr = values.to_numpy(dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite(arr).all() or (len(arr) and np.abs(arr).max() >= 2.0 ** 52):
        return None
    return arr


def _match_keys(frame: pd.DataFrame, symbol: str) -> Optional[Tuple[np.ndarray, ...]]:
    """(side code, size, price bits, raw symbol, ts ns), or None when the exact loop must decide."""
    sizes = _finite_floats(frame["size"])
    prices = _finite_floats(frame["price"])
    ts = frame["ts"].to_numpy(dtype="datetime64[ns]")
    if sizes is None or prices is None or np.isnat(ts).any():
        return None
    tick = float(TICK_SIZE_BY_SYMBOL.get(symbol, 1.0 / 64.0))
    price_norm = np.rint(prices / tick) * tick + 0.0
    raw = (
        frame["raw_symbol"].fillna("").astype(str).to_numpy()
        if "raw_symbol" in frame.columns
        else np.array([""] * len(frame), dtype=object)
    )
    return (
        _side_codes(frame),
        np.rint(sizes).astype(np.int64),
        price_norm.view(np.int64),
        raw,
        ts.view(np.int64),
    )


def _bucket_ids(side: np.ndarray, size: np.ndarray, price_bits: np.ndarray) -> np.ndarray:
    order = np.lexsort((price_bits, size, side))
    new_group = np.ones(len(order), dtype=bool)
    new_group[1:] = (
        (side[order][1:] != side[order][:-1])
        | (size[order][1:] != size[order][:-1])
        | (price_bits[order][1:] != price_bits[order][:-1])
    )
    ids = np.empty(len(order), dtype=np.int64)
    ids[order] = np.cumsum(new_group) - 1
    return ids


def _matched_incoming(stored: pd.DataFrame, incoming: pd.DataFrame, symbol: str) -> np.ndarray:
    """True for each incoming row that pairs to one unused stored row.

    Same result as ``_matched_incoming_reference``: each incoming row, in order, takes the
    unused raw-compatible stored row of its (side, size, price) bucket with the smallest
    |dt| <= 1 ms, lowest stored index on ties. Rows whose candidates no other incoming row
    shares are decided directly; only rows that compete for a stored row run the loop.
    """
    if stored.empty or incoming.empty:
        return np.zeros(len(incoming), dtype=bool)
    s_keys = _match_keys(stored, symbol)
    i_keys = _match_keys(incoming, symbol)
    if s_keys is None or i_keys is None:
        return _matched_incoming_reference(stored, incoming, symbol)
    sequences = None
    if "sequence" in stored.columns and "sequence" in incoming.columns:
        sequences = (stored["sequence"].to_numpy(), incoming["sequence"].to_numpy())
    return _match_on_keys(s_keys, i_keys, sequences)


def _match_on_keys(
    s_keys: Tuple[np.ndarray, ...],
    i_keys: Tuple[np.ndarray, ...],
    sequences: Optional[Tuple[np.ndarray, np.ndarray]] = None,
) -> np.ndarray:
    s_side, s_size, s_price, s_raw, s_ts = s_keys
    i_side, i_size, i_price, i_raw, i_ts = i_keys
    n_in = len(i_ts)
    n_s = len(s_ts)
    matched = np.zeros(n_in, dtype=bool)
    if n_s == 0 or n_in == 0:
        return matched
    used = np.zeros(n_s, dtype=bool)

    if sequences is not None:
        seq_to_j: Dict[object, Optional[int]] = {}
        for j, seq in enumerate(sequences[0]):
            if _is_missing_seq(seq):
                continue
            seq_to_j[seq] = None if seq in seq_to_j else j
        for i, seq in enumerate(sequences[1]):
            if _is_missing_seq(seq):
                continue
            j = seq_to_j.get(seq)
            if j is None or used[j]:
                continue
            if i_side[i] != s_side[j] or i_size[i] != s_size[j] or i_price[i] != s_price[j]:
                continue
            if i_raw[i] and s_raw[j] and i_raw[i] != s_raw[j]:
                continue
            if abs(int(s_ts[j]) - int(i_ts[i])) > _WINDOW_NS:
                continue
            used[j] = True
            matched[i] = True

    bucket = _bucket_ids(
        np.concatenate([s_side, i_side]),
        np.concatenate([s_size, i_size]),
        np.concatenate([s_price, i_price]),
    )
    s_bucket, i_bucket = bucket[:n_s], bucket[n_s:]
    uniq_ts = np.unique(np.concatenate([s_ts, i_ts]))
    span = np.int64(len(uniq_ts) + 1)
    s_rank = np.searchsorted(uniq_ts, s_ts, side="left")
    s_key = s_bucket * span + s_rank
    s_order = np.argsort(s_key, kind="stable")
    s_key_sorted = s_key[s_order]
    lo_rank = np.searchsorted(uniq_ts, i_ts - _WINDOW_NS, side="left")
    hi_rank = np.searchsorted(uniq_ts, i_ts + _WINDOW_NS, side="right") - 1
    lo = np.searchsorted(s_key_sorted, i_bucket * span + lo_rank, side="left")
    hi = np.searchsorted(s_key_sorted, i_bucket * span + hi_rank, side="right")
    counts = np.where(matched, 0, np.maximum(hi - lo, 0))
    total = int(counts.sum())
    if total == 0:
        return matched

    pair_i = np.repeat(np.arange(n_in), counts)
    starts = np.cumsum(counts) - counts
    pair_pos = np.arange(total) - np.repeat(starts, counts) + np.repeat(lo, counts)
    pair_j = s_order[pair_pos]
    ir = i_raw[pair_i]
    sr = s_raw[pair_j]
    valid = ~used[pair_j] & ((ir == "") | (sr == "") | (ir == sr))
    pair_i, pair_j = pair_i[valid], pair_j[valid]
    if len(pair_i) == 0:
        return matched
    delta = np.abs(s_ts[pair_j] - i_ts[pair_i])

    shared_j = np.bincount(pair_j, minlength=n_s) >= 2
    contested_i = np.zeros(n_in, dtype=bool)
    contested_i[pair_i[shared_j[pair_j]]] = True
    free = ~contested_i[pair_i]

    fi, fj, fd = pair_i[free], pair_j[free], delta[free]
    if len(fi):
        order = np.lexsort((fj, fd, fi))
        fi, fj = fi[order], fj[order]
        first = np.ones(len(fi), dtype=bool)
        first[1:] = fi[1:] != fi[:-1]
        matched[fi[first]] = True
        used[fj[first]] = True

    ci, cj, cd = pair_i[~free], pair_j[~free], delta[~free]
    if len(ci):
        order = np.lexsort((cj, ci))
        ci, cj, cd = ci[order].tolist(), cj[order].tolist(), cd[order].tolist()
        k, n_pairs = 0, len(ci)
        while k < n_pairs:
            i = ci[k]
            best_j = -1
            best_dt = 0
            while k < n_pairs and ci[k] == i:
                j = cj[k]
                if not used[j] and (best_j < 0 or cd[k] < best_dt):
                    best_j = j
                    best_dt = cd[k]
                k += 1
            if best_j >= 0:
                used[best_j] = True
                matched[i] = True
    return matched


def _matched_incoming_reference(stored: pd.DataFrame, incoming: pd.DataFrame, symbol: str) -> np.ndarray:
    """Row-by-row matcher: the golden reference and the fallback for bad inputs.

    Incoming rows claim in order; each takes the closest unused stored row
    within the window, ties going to the lowest stored row.
    """
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

    window_ns = int(window // np.timedelta64(1, "ns"))
    s_ns_arr = s_ts.astype(np.int64)
    s_ns = s_ns_arr.tolist()
    i_ns = i_ts.astype(np.int64).tolist()
    s_side_l, s_size_l, s_price_l, s_raw_l = s_side.tolist(), s_size.tolist(), s_price.tolist(), s_raw.tolist()
    i_side_l, i_size_l, i_price_l, i_raw_l = i_side.tolist(), i_size.tolist(), i_price.tolist(), i_raw.tolist()
    used_rows = used.tolist()

    buckets: Dict[Tuple[str, int, float], Tuple[List[int], List[int]]] = {}
    for j in np.argsort(s_ns_arr, kind="stable").tolist():
        key = (str(s_side_l[j]), int(s_size_l[j]), float(s_price_l[j]))
        entry = buckets.get(key)
        if entry is None:
            entry = buckets[key] = ([], [])
        entry[0].append(s_ns[j])
        entry[1].append(j)

    for i in range(n_in):
        if matched[i]:
            continue
        entry = buckets.get((str(i_side_l[i]), int(i_size_l[i]), float(i_price_l[i])))
        if entry is None:
            continue
        times, rows = entry
        t = i_ns[i]
        lo = bisect.bisect_left(times, t - window_ns)
        hi = bisect.bisect_right(times, t + window_ns, lo)
        raw_i = i_raw_l[i]
        best_j = None
        best_dt = None
        for k in range(lo, hi):
            j = rows[k]
            if used_rows[j]:
                continue
            if raw_i and s_raw_l[j] and raw_i != s_raw_l[j]:
                continue
            delta = abs(times[k] - t)
            if best_dt is None or delta < best_dt or (delta == best_dt and j < best_j):
                best_j = j
                best_dt = delta
        if best_j is not None:
            used_rows[best_j] = True
            matched[i] = True
    return matched


def _concat_keep(stored: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (stored, extra) if frame is not None and not frame.empty]
    if not frames:
        return empty_trades()
    out = pd.concat(frames, ignore_index=True)
    out["ts"] = _utc_ts(out["ts"])
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
