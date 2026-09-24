#!/usr/bin/env python3
"""Plotly payloads for the trade-to-trade V3 monitor."""

from __future__ import annotations

from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.aggression_dashboard_v3.core import TICK_PLOT_MAX_POINTS

EASTERN = ZoneInfo("America/New_York")
BUYSELL_DISPLAY_HALFLIFE = pd.Timedelta(seconds=30)
BUYSELL_DISPLAY_MAX_POINTS = 4000
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


def build_total_aggress_panel(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "TotalAggress vs Price/Yield", "data": []}
    aligned = frame[["total_aggress", "price"]].dropna(subset=["total_aggress"])
    return {
        "title": "TotalAggress vs Price/Yield",
        "data": _df_columns_to_points(aligned, "total_aggress", "price"),
        "traces": [
            {"field": "total_aggress", "name": "TotalAggress", "axis": "y", "color": "#1f4ed8", "shape": "linear"},
            {"field": "price", "name": "Last trade", "axis": "y2", "color": "#d62728"},
        ],
        "layout": {"yaxis_title": "TotalAggress", "yaxis2_title": "Last trade"},
        "axis_align": _build_axis_alignment(aligned),
    }


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


def build_buysell_ratio_panel(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "BuySell Ratio vs Price/Yield", "data": [], "caption": PANEL2_CAPTION}
    panel = _prepare_buysell_display(frame)
    return {
        "title": "BuySell Ratio vs Price/Yield",
        "caption": PANEL2_CAPTION,
        "data": _df_columns_to_points(panel, "buysell_ratio", "price"),
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


def build_volume_panel(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "Buy Aggress, Sell Aggress, TotalAggress, and price", "data": []}
    panel = frame[["buy_aggress", "sell_aggress", "total_aggress", "price"]].copy()
    panel["sell_aggress"] = -panel["sell_aggress"].abs()
    return {
        "title": "Buy Aggress, Sell Aggress, TotalAggress, and price",
        "data": _df_columns_to_points(panel, "buy_aggress", "sell_aggress", "total_aggress", "price"),
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


def _path_panel(title: str, frame: pd.DataFrame, *, marker_color: str) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": title, "data": [], "large_residuals": [], "traces": [], "layout": {"yaxis_title": "Last trade"}}
    large = frame.loc[frame.get("large_residual", False) == True] if "large_residual" in frame.columns else frame.iloc[0:0]
    return {
        "title": title,
        "data": _df_columns_to_points(frame, "price", "implied_price"),
        "large_residuals": [
            {"x": _to_et_iso(idx), "y": float(row["price"])}
            for idx, row in large.iterrows()
            if not pd.isna(row.get("price"))
        ],
        "traces": [
            {"field": "price", "name": "Actual last trade", "axis": "y", "color": "#1f4ed8"},
            {"field": "implied_price", "name": "Implied under beta_ref", "axis": "y", "color": "#d62728", "dash": "dash"},
        ],
        "layout": {"yaxis_title": "Last trade"},
        "large_residual_color": marker_color,
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
