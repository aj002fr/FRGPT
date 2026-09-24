#!/usr/bin/env python3
"""Plotly payloads for the trade-to-trade V3 monitor."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.core import TICK_PLOT_MAX_POINTS

EASTERN = ZoneInfo("America/New_York")
BUYSELL_DISPLAY_HALFLIFE = pd.Timedelta(seconds=30)
BUYSELL_DISPLAY_MAX_POINTS = 4000
COMPACT_BUCKETS = 800
COMPACT_TAIL = pd.Timedelta(minutes=5)
COMPACT_DECIMALS = {"total_aggress": 1, "buy_aggress": 1, "sell_aggress": 1, "buysell_ratio": 3}
COMPACT_PRICE_DECIMALS = 8
TAPE_FIELDS = ("total_aggress", "price", "buy_aggress", "sell_aggress", "implied_price")
TAPE_PANEL_FIELDS = {
    "p1": ("total_aggress", "price"),
    "p3": ("buy_aggress", "sell_aggress", "total_aggress", "price"),
    "p4": ("price", "implied_price"),
}
BANNER_SERIES_MIN_GAP = pd.Timedelta(seconds=5)
BANNER_HOVER_FIELDS = ("x", "as_of", "status", "state", "headline", "contribution_line", "reason")
PANEL2_CAPTION = (
    "Signed imbalance of aggressive flow, time-smoothed for the chart "
    "(half-life 30s). Spec EMA(10 prints) is print-scale and not drawn. "
    "0 = two-way. +50 ≈ 3:1 buy-heavy. -50 ≈ 3:1 sell-heavy. "
    "±100 = only one side"
)
STATE_TIMELINE_CAPTION = (
    "Six-state of the 15-minute window ending at each time. "
    "Gaps are warming or unavailable. Attribution only; not a long/short signal."
)
READY_STATE_COLORS = {
    "QUIET": "#d5dbe3",
    "BALANCED": "#e0b422",
    "AGGRESS_DOMINANT": "#6b3fa0",
    "IMPLIED_PASSIVE_DOMINANT": "#5a6e78",
    "JOINTLY_DRIVEN": "#1b3f73",
    "CONTESTED": "#d46a3a",
}
STATE_HEADLINE = {
    "QUIET": "QUIET",
    "BALANCED": "BALANCED",
    "AGGRESS_DOMINANT": "AGGRESS DOMINANT",
    "IMPLIED_PASSIVE_DOMINANT": "IMPLIED PASSIVE DOMINANT",
    "JOINTLY_DRIVEN": "JOINTLY DRIVEN",
    "CONTESTED": "CONTESTED",
}


def _to_et_iso(ts: Any) -> str:
    value = pd.Timestamp(ts)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    return value.tz_convert(EASTERN).tz_localize(None).isoformat()


def _downsample(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or len(df) <= TICK_PLOT_MAX_POINTS:
        return df
    return df.iloc[-TICK_PLOT_MAX_POINTS:]


def _df_columns_to_points(df: pd.DataFrame, *columns: str) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    work = _downsample(df)
    records: List[Dict[str, Any]] = []
    for idx, row in work[list(columns)].dropna(how="all").iterrows():
        item: Dict[str, Any] = {"x": _to_et_iso(idx)}
        for column in columns:
            value = row.get(column)
            item[column] = None if pd.isna(value) else float(value)
        records.append(item)
    return records


def _build_axis_alignment(aligned: pd.DataFrame) -> Dict[str, Any]:
    aggress = aligned["total_aggress"].dropna()
    price = aligned["price"].dropna()
    if aligned.empty or aggress.nunique() < 2 or price.nunique() < 2:
        return {}
    a_min = float(aggress.min())
    a_max = float(aggress.max())
    p_min = float(price.min())
    p_max = float(price.max())
    a_span = a_max - a_min
    p_span = p_max - p_min
    if a_span <= 0 or p_span <= 0:
        return {}
    a_min -= a_span * 0.05
    a_max += a_span * 0.05
    p_min -= p_span * 0.05
    p_max += p_span * 0.05
    beta = (p_max - p_min) / (a_max - a_min)
    alpha = p_min - a_min * beta
    return {
        "aggress_range": [a_min, a_max],
        "price_range": [p_min, p_max],
        "alpha": alpha,
        "beta": beta,
        "formula": f"visual range only; not beta_ref",
    }


def _total_aggress_spec(aligned: pd.DataFrame) -> Dict[str, Any]:
    return {
        "title": "TotalAggress vs Price/Yield",
        "traces": [
            {"field": "total_aggress", "name": "TotalAggress", "axis": "y", "color": "#1f4ed8", "shape": "linear"},
            {"field": "price", "name": "Last trade", "axis": "y2", "color": "#d62728"},
        ],
        "layout": {"yaxis_title": "TotalAggress", "yaxis2_title": "Last trade"},
        "axis_align": _build_axis_alignment(aligned),
    }


def build_total_aggress_panel(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "TotalAggress vs Price/Yield", "data": []}
    aligned = frame[["total_aggress", "price"]].dropna(subset=["total_aggress"])
    return {**_total_aggress_spec(aligned), "data": _df_columns_to_points(aligned, "total_aggress", "price")}


def _prepare_buysell_display(frame: pd.DataFrame) -> pd.DataFrame:
    """Chart-only path: time-weight the raw ±100 print ratio, then thin the line."""
    source = "buysell_raw" if "buysell_raw" in frame.columns else "buysell_ratio"
    work = frame[[source, "price"]].copy()
    work.index = pd.to_datetime(work.index, utc=True)
    work = work.sort_index()
    work = work.groupby(level=0, sort=True).agg({source: "mean", "price": "last"})
    times = pd.DatetimeIndex(work.index)
    smooth = work[source].ewm(
        halflife=BUYSELL_DISPLAY_HALFLIFE,
        times=times,
        ignore_na=True,
    ).mean()
    display = pd.DataFrame({"buysell_ratio": smooth, "price": work["price"]})
    if len(display) <= BUYSELL_DISPLAY_MAX_POINTS:
        return display
    span_sec = (display.index[-1] - display.index[0]).total_seconds()
    if span_sec <= 0:
        return display.iloc[-BUYSELL_DISPLAY_MAX_POINTS:]
    freq = pd.Timedelta(seconds=max(1.0, span_sec / BUYSELL_DISPLAY_MAX_POINTS))
    return display.resample(freq, origin="epoch").last().dropna(how="all")


def _buysell_spec() -> Dict[str, Any]:
    return {
        "title": "BuySell Ratio vs Price/Yield",
        "caption": PANEL2_CAPTION,
        "traces": [
            {"field": "buysell_ratio", "name": "BuySell Ratio", "axis": "y", "color": "#2e7d5b", "width": 2.0},
            {"field": "price", "name": "Last trade", "axis": "y2", "color": "#e0962f"},
        ],
        "layout": {
            "yaxis_title": "BuySell Ratio",
            "yaxis2_title": "Last trade",
            "yaxis_range": [-100, 100],
        },
    }


def build_buysell_ratio_panel(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "BuySell Ratio vs Price/Yield", "data": [], "caption": PANEL2_CAPTION}
    panel = _prepare_buysell_display(frame)
    return {**_buysell_spec(), "data": _df_columns_to_points(panel, "buysell_ratio", "price")}


def _volume_frame(frame: pd.DataFrame) -> pd.DataFrame:
    panel = frame[["buy_aggress", "sell_aggress", "total_aggress", "price"]].copy()
    panel["sell_aggress"] = -panel["sell_aggress"].abs()
    return panel


def build_volume_panel(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "Buy Aggress, Sell Aggress, TotalAggress, and price", "data": []}
    panel = _volume_frame(frame)
    return {**_volume_spec(), "data": _df_columns_to_points(panel, "buy_aggress", "sell_aggress", "total_aggress", "price")}


def _volume_spec() -> Dict[str, Any]:
    return {
        "title": "Buy Aggress, Sell Aggress, TotalAggress, and price",
        "traces": [
            {"field": "buy_aggress", "name": "Buy Aggress", "type": "bar", "axis": "y", "color": "#2ca02c"},
            {"field": "sell_aggress", "name": "Sell Aggress", "type": "bar", "axis": "y", "color": "#d62728"},
            {"field": "price", "name": "Last trade", "type": "line", "axis": "y2", "color": "#8b3a3a"},
            {"field": "total_aggress", "name": "TotalAggress", "type": "line", "axis": "y3", "color": "#1a1a1a"},
        ],
        "layout": {
            "yaxis_title": "Print aggress",
            "yaxis2_title": "Last trade",
            "yaxis3_title": "TotalAggress",
        },
    }


def _is_ready_state(row: Dict[str, Any]) -> bool:
    state = str(row.get("state") or "")
    status = str(row.get("status") or state)
    return status == "READY" and state in READY_STATE_COLORS


def collapse_ready_state_runs(series: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse consecutive READY states. WARMING/UNAVAILABLE leave a gap."""
    runs: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    for row in series or []:
        x = row.get("x")
        if x is None:
            continue
        if _is_ready_state(row):
            state = str(row["state"])
            if current is not None and current["state"] == state:
                current["x1"] = x
            else:
                if current is not None:
                    runs.append(current)
                current = {
                    "x0": x,
                    "x1": x,
                    "state": state,
                    "headline": row.get("headline") or STATE_HEADLINE.get(state, state.replace("_", " ")),
                    "color": READY_STATE_COLORS[state],
                }
        elif current is not None:
            current["x1"] = x
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)
    return runs


def build_state_timeline_panel(series: List[Dict[str, Any]] | None) -> Dict[str, Any]:
    runs = collapse_ready_state_runs(series or [])
    return {
        "title": "Six-state through lookback",
        "caption": STATE_TIMELINE_CAPTION,
        "data": runs,
        "layout": {"yaxis_title": "", "xaxis_title": "Time (ET)"},
    }


def _large_residual_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if "large_residual" not in frame.columns:
        return frame.iloc[0:0]
    large = frame.loc[frame["large_residual"] == True]  # noqa: E712
    return large.loc[large["price"].notna()]


def _path_spec(title: str, marker_color: str) -> Dict[str, Any]:
    return {
        "title": title,
        "traces": [
            {"field": "price", "name": "Actual last trade", "axis": "y", "color": "#1f4ed8"},
            {"field": "implied_price", "name": "Implied under beta_ref", "axis": "y", "color": "#d62728", "dash": "dash"},
        ],
        "layout": {"yaxis_title": "Last trade"},
        "large_residual_color": marker_color,
    }


def _path_panel(title: str, frame: pd.DataFrame, *, marker_color: str) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": title, "data": [], "large_residuals": [], "traces": [], "layout": {"yaxis_title": "Last trade"}}
    large = _large_residual_rows(frame)
    return {
        **_path_spec(title, marker_color),
        "data": _df_columns_to_points(frame, "price", "implied_price"),
        "large_residuals": [{"x": _to_et_iso(idx), "y": float(row["price"])} for idx, row in large.iterrows()],
    }


# ---- compact live snapshot ------------------------------------------------------------------
# Columns instead of per-point dicts, and min/max decimation of history older than COMPACT_TAIL.
# Times are ET wall-clock microseconds, delta-encoded; dashboard.js expandCompactPayload rebuilds
# the row format above, so every kept point is a real print.

def _et_wall_us(index: Any) -> np.ndarray:
    idx = pd.DatetimeIndex(pd.to_datetime(index, utc=True))
    return idx.tz_convert(EASTERN).tz_localize(None).as_unit("us").asi8


def keep_positions(values: Sequence[np.ndarray], n: int, buckets: int) -> np.ndarray:
    """Rows [0, n) to keep: first, last, and the first min and max of each field per bucket."""
    if n <= buckets * 4:
        return np.arange(n, dtype=np.int64)
    edges = np.linspace(0, n, buckets + 1).astype(np.int64)
    sizes = np.diff(edges)
    bucket = np.repeat(np.arange(buckets), sizes)
    nonempty = sizes > 0
    keep = [edges[:-1][nonempty], edges[1:][nonempty] - 1]
    starts = np.r_[0, np.cumsum(sizes[nonempty])[:-1]]
    for column in values:
        v = np.asarray(column[:n], dtype=float)
        finite = np.isfinite(v)
        for sign in (1.0, -1.0):
            order = np.lexsort((np.where(finite, sign * v, np.inf), bucket))
            first = order[starts]
            keep.append(first[finite[first]])
    return np.unique(np.concatenate(keep))


def compact_positions(frame: pd.DataFrame, fields: Sequence[str], *, buckets: int = COMPACT_BUCKETS,
                      tail: Optional[pd.Timedelta] = COMPACT_TAIL) -> np.ndarray:
    n = len(frame)
    if n == 0:
        return np.arange(0, dtype=np.int64)
    head = n
    if tail is not None:
        idx = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
        head = int(idx.searchsorted(idx[-1] - tail, side="left"))
    values = [pd.to_numeric(frame[f], errors="coerce").to_numpy(dtype=float) for f in fields if f in frame.columns]
    return np.concatenate([keep_positions(values, head, buckets), np.arange(head, n, dtype=np.int64)])


def _rounded(values: np.ndarray, decimals: int) -> List[Optional[float]]:
    out = np.round(np.asarray(values, dtype=float), decimals)
    return [v if math.isfinite(v) else None for v in out.tolist()]


def compact_block(frame: pd.DataFrame, fields: Sequence[str], positions: Optional[np.ndarray] = None) -> Dict[str, Any]:
    sub = frame if positions is None else frame.iloc[positions]
    us = _et_wall_us(sub.index)
    dt = np.diff(us, prepend=us[:1]) if len(us) else us
    cols = {
        field: _rounded(pd.to_numeric(sub[field], errors="coerce").to_numpy(dtype=float),
                        COMPACT_DECIMALS.get(field, COMPACT_PRICE_DECIMALS))
        for field in fields if field in sub.columns
    }
    return {"x0": int(us[0]) if len(us) else 0, "dt": dt.tolist(), "cols": cols}


def _iso_from_us(us: int) -> str:
    return pd.Timestamp(int(us), unit="us").strftime("%Y-%m-%dT%H:%M:%S.%f")


def block_rows(block: Optional[Dict[str, Any]], fields: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Python twin of dashboard.js compactRows (tests and Python readers)."""
    if not block or not block.get("dt"):
        return []
    fields = list(fields or block["cols"].keys())
    us = block["x0"] + np.cumsum(np.asarray(block["dt"], dtype=np.int64))
    rows = []
    for i, value in enumerate(us.tolist()):
        row: Dict[str, Any] = {"x": _iso_from_us(value)}
        for field in fields:
            column = block["cols"].get(field)
            row[field] = column[i] if column is not None else None
        rows.append(row)
    return rows


def expand_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Row-format copy of a compact payload, as dashboard.js expandCompactPayload builds it."""
    out = {**payload, "panels": {key: dict(panel) for key, panel in (payload.get("panels") or {}).items()}}
    tape = out.pop("tape", None)
    for key, panel in out["panels"].items():
        if panel.get("compact"):
            panel["data"] = block_rows(panel.pop("compact")) + list(panel.get("data") or [])
        elif tape and panel.pop("from_tape", False):
            panel["data"] = block_rows(tape, TAPE_PANEL_FIELDS[key]) + list(panel.get("data") or [])
        if panel.get("large_residuals_compact"):
            rows = block_rows(panel.pop("large_residuals_compact"), ["price"])
            panel["large_residuals"] = [{"x": row["x"], "y": row["price"]} for row in rows]
    return out


def last_panel_row(panel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = panel.get("data") or []
    if data:
        return data[-1]
    rows = block_rows(panel.get("compact"))
    return rows[-1] if rows else None


def _compact_path_panel(title: str, frame: pd.DataFrame, *, marker_color: str, tape_shared: bool) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return _path_panel(title, frame, marker_color=marker_color)
    panel = {**_path_spec(title, marker_color), "data": [], "large_residuals": []}
    if tape_shared:
        panel["from_tape"] = True
    else:
        fields = ("price", "implied_price")
        panel["compact"] = compact_block(frame, fields, compact_positions(frame, fields))
    large = _large_residual_rows(frame)
    if not large.empty:
        panel["large_residuals_compact"] = compact_block(large, ("price",), compact_positions(large, ("price",), tail=None))
    return panel


def slim_banner_series(series: Optional[List[Dict[str, Any]]], min_gap: pd.Timedelta = BANNER_SERIES_MIN_GAP) -> List[Dict[str, Any]]:
    """Hover rows at least `min_gap` apart (plus the last), with only the fields the hover banner reads."""
    rows = list(series or [])
    kept: List[Dict[str, Any]] = []
    last_ts = None
    for i, row in enumerate(rows):
        try:
            ts = pd.Timestamp(str(row.get("x")).replace(" ", "T"))
        except (TypeError, ValueError):
            continue
        if last_ts is not None and ts - last_ts < min_gap and i != len(rows) - 1:
            continue
        kept.append({key: row.get(key) for key in BANNER_HOVER_FIELDS if row.get(key) is not None})
        last_ts = ts
    return kept


def build_compact_dashboard_payload(
    session_frame: pd.DataFrame,
    session_path: pd.DataFrame,
    tenday_path: pd.DataFrame,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Live snapshot payload: same panels as build_dashboard_payload, compact data."""
    if session_frame is None or session_frame.empty:
        return build_dashboard_payload(session_frame, session_path, tenday_path, meta)
    tape = _volume_frame(session_frame)
    tape_shared = (
        session_path is not None and not session_path.empty and "implied_price" in session_path.columns
        and session_path.index.equals(session_frame.index)
    )
    if tape_shared:
        tape["implied_price"] = session_path["implied_price"].to_numpy()
    aligned = session_frame[["total_aggress", "price"]].dropna(subset=["total_aggress"])
    buysell = _prepare_buysell_display(session_frame)
    return {
        "success": True,
        "compact": 1,
        "tape": compact_block(tape, TAPE_FIELDS, compact_positions(tape, TAPE_FIELDS)),
        "panels": {
            "p1": {**_total_aggress_spec(aligned), "data": [], "from_tape": True},
            "p2": {**_buysell_spec(), "data": [], "compact": compact_block(buysell, ("buysell_ratio", "price"))},
            "p3": {**_volume_spec(), "data": [], "from_tape": True},
            "p4": _compact_path_panel("Intraday path under beta_ref", session_path, marker_color="#7f7f7f", tape_shared=tape_shared),
            "p5": _compact_path_panel("Last 10 sessions under the same beta_ref", tenday_path, marker_color="#d62728", tape_shared=False),
        },
        "meta": meta,
    }


def build_dashboard_payload(
    session_frame: pd.DataFrame,
    session_path: pd.DataFrame,
    tenday_path: pd.DataFrame,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "success": True,
        "panels": {
            "p1": build_total_aggress_panel(session_frame),
            "p2": build_buysell_ratio_panel(session_frame),
            "p3": build_volume_panel(session_frame),
            "p4": _path_panel("Intraday path under beta_ref", session_path, marker_color="#7f7f7f"),
            "p5": _path_panel("Last 10 sessions under the same beta_ref", tenday_path, marker_color="#d62728"),
        },
        "meta": meta,
    }
