#!/usr/bin/env python3
"""Plotly payloads for the trade-to-trade V3 monitor."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.banner import (
    BUY_COLOR,
    BUY_OFFSETTING_COLOR,
    GROUP_STYLE,
    LABEL,
    QUIET_COLOR,
    SELL_COLOR,
    SELL_OFFSETTING_COLOR,
    STATE_STYLE,
)
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
    "Top row: STATE (coarse). Bottom row: STATE2 (fine). Each point is the 5-minute window, "
    "or the 15-minute window from 18:00 to 03:00 ET, ending at that time. "
    "Blue = buy-side pressure, red = sell-side pressure, grey = quiet. "
    "Gaps are warming or unavailable. Attribution only; not a long/short signal."
)
READY_STATE_COLORS = {state: color for state, (_headline, color, _family, _group) in STATE_STYLE.items()}
# Panels drawn from the shared print columns, and the fields each one reads.
PRINT_PANEL_FIELDS: Dict[str, Tuple[str, ...]] = {
    "p1": ("total_aggress", "price"),
    "p3": ("buy_aggress", "sell_aggress", "total_aggress", "price"),
    "p4": ("price", "implied_price"),
}
COLUMN_TRIM_CHUNK = 4096
# STATE2 colours are shared across states, so that strip row's legend names colour groups.
STATE2_COLOR_LEGEND = {
    BUY_COLOR: "Buy side",
    BUY_OFFSETTING_COLOR: "Buy side, offsetting",
    SELL_COLOR: "Sell side",
    SELL_OFFSETTING_COLOR: "Sell side, offsetting",
    QUIET_COLOR: "QUIET",
}


def coarse_state_legend() -> List[Dict[str, str]]:
    """Every coarse STATE colour, in index order, whether or not it appears in the lookback."""
    return [{"legend": headline, "color": color} for _group, (headline, color) in GROUP_STYLE.items()]


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


def build_total_aggress_panel(frame: pd.DataFrame, *, with_data: bool = True) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "TotalAggress vs Price/Yield", "data": []}
    aligned = frame[["total_aggress", "price"]].dropna(subset=["total_aggress"])
    return {
        "title": "TotalAggress vs Price/Yield",
        "data": _df_columns_to_points(aligned, "total_aggress", "price") if with_data else [],
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


def build_volume_panel(frame: pd.DataFrame, *, with_data: bool = True) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": "Buy Aggress, Sell Aggress, TotalAggress, and price", "data": []}
    data: List[Dict[str, Any]] = []
    if with_data:
        panel = frame[["buy_aggress", "sell_aggress", "total_aggress", "price"]].copy()
        panel["sell_aggress"] = -panel["sell_aggress"].abs()
        data = _df_columns_to_points(panel, "buy_aggress", "sell_aggress", "total_aggress", "price")
    return {
        "title": "Buy Aggress, Sell Aggress, TotalAggress, and price",
        "data": data,
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


def state_run(row: Dict[str, Any], key: str, x: Any) -> Optional[Dict[str, Any]]:
    """One-point run for a READY banner row on the STATE ("group") or STATE2 ("state") strip row."""
    state = str(row.get("state") or "")
    if str(row.get("status") or "") != "READY" or state not in STATE_STYLE:
        return None
    headline, color, _family, group = STATE_STYLE[state]
    if key == "group":
        group_headline, group_color = GROUP_STYLE[group]
        return {"x0": x, "x1": x, "state": group, "headline": group_headline, "color": group_color, "legend": group_headline}
    return {"x0": x, "x1": x, "state": state, "headline": headline, "color": color, "legend": STATE2_COLOR_LEGEND[color]}


def collapse_ready_state_runs(series: List[Dict[str, Any]], key: str = "state") -> List[Dict[str, Any]]:
    """Collapse consecutive READY windows with the same state on one strip row. WARMING/UNAVAILABLE leave a gap."""
    runs: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    for row in series or []:
        x = row.get("x")
        if x is None:
            continue
        run = state_run(row, key, x)
        if run is not None:
            if current is not None and current["state"] == run["state"]:
                current["x1"] = x
            else:
                if current is not None:
                    runs.append(current)
                current = run
        elif current is not None:
            current["x1"] = x
            runs.append(current)
            current = None
    if current is not None:
        runs.append(current)
    return runs


def advance_runs(
    runs: List[Dict[str, Any]],
    run: Optional[Dict[str, Any]],
    x: Any,
    is_open: bool,
) -> Tuple[Dict[str, Any], bool]:
    """Apply one live banner row to a strip row. Returns the edge for viewers and whether the last run stays open."""
    if run is not None:
        if is_open and runs and runs[-1].get("state") == run["state"]:
            runs[-1]["x1"] = x
            return {"action": "extend", "run": dict(runs[-1])}, True
        runs.append(run)
        return {"action": "open", "run": dict(run)}, True
    if is_open and runs:
        runs[-1]["x1"] = x
        return {"action": "close", "run": dict(runs[-1])}, False
    return {"action": "gap"}, False


def apply_strip_edge(runs: List[Dict[str, Any]], edge: Optional[Dict[str, Any]]) -> None:
    """Mirror a live strip edge into a stored run list."""
    if not edge:
        return
    action = edge.get("action")
    run = edge.get("run")
    if action in ("extend", "close") and runs and run:
        runs[-1] = dict(run)
    elif action == "open" and run:
        runs.append(dict(run))


def build_state_timeline_panel(series: List[Dict[str, Any]] | None) -> Dict[str, Any]:
    rows = series or []
    return {
        "title": "States through lookback",
        "caption": STATE_TIMELINE_CAPTION,
        "data": collapse_ready_state_runs(rows, "state"),
        "group_data": collapse_ready_state_runs(rows, "group"),
        "state_legend": coarse_state_legend(),
        "layout": {"yaxis_title": "", "xaxis_title": "Time (ET)"},
    }


def _path_panel(title: str, frame: pd.DataFrame, *, marker_color: str, with_data: bool = True) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"title": title, "data": [], "large_residuals": [], "traces": [], "layout": {"yaxis_title": "Last trade"}}
    large = frame.loc[frame.get("large_residual", False) == True] if "large_residual" in frame.columns else frame.iloc[0:0]
    return {
        "title": title,
        "data": _df_columns_to_points(frame, "price", "implied_price") if with_data else [],
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


def state_styles_payload() -> Dict[str, Any]:
    """Headline, colour, family and STATE per STATE2, sent once so hover rows can carry only `state`."""
    return {
        "states": {state: list(style) for state, style in STATE_STYLE.items()},
        "groups": {group: list(style) for group, style in GROUP_STYLE.items()},
        "label": LABEL,
    }


def _column_values(values: Iterable[Any]) -> List[Optional[float]]:
    array = pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype=float)
    return [None if np.isnan(value) else float(value) for value in array]


def _row_spec(rows: np.ndarray, start: int, n: int) -> Dict[str, Any]:
    """Panel rows as a slice of the shared columns: from `from` to the end, minus `skip`."""
    if len(rows) == 0:
        return {"from": n - start, "skip": []}
    first = int(rows[0])
    skip = np.setdiff1d(np.arange(first, n), rows, assume_unique=True)
    return {"from": first - start, "skip": [int(i) - start for i in skip]}


def _shares_print_index(frame: pd.DataFrame, path: pd.DataFrame) -> bool:
    if path is None or path.empty or len(path) != len(frame):
        return False
    if "price" not in path.columns or "implied_price" not in path.columns:
        return False
    if not pd.DatetimeIndex(path.index).equals(pd.DatetimeIndex(frame.index)):
        return False
    ours = pd.to_numeric(frame["price"], errors="coerce").to_numpy(dtype=float)
    theirs = pd.to_numeric(path["price"], errors="coerce").to_numpy(dtype=float)
    return bool(np.array_equal(ours, theirs, equal_nan=True))


def build_print_columns(
    frame: pd.DataFrame,
    path: pd.DataFrame,
) -> Tuple[Dict[str, List[Any]], Dict[str, Dict[str, Any]]]:
    """One column per print field for Panels 1, 3 and 4, plus the row spec of each panel.

    Each spec selects exactly the rows `_df_columns_to_points` would draw: Panel 1 drops prints
    without TotalAggress and keeps the last TICK_PLOT_MAX_POINTS; Panels 3 and 4 keep the last
    TICK_PLOT_MAX_POINTS prints and drop the ones with every field missing. Panel 4 shares the
    columns only when its path has the same prints and prices; otherwise it is left out.
    """
    n = len(frame)
    limit = TICK_PLOT_MAX_POINTS
    values = {
        "total_aggress": _column_values(frame["total_aggress"]),
        "price": _column_values(frame["price"]),
        "buy_aggress": _column_values(frame["buy_aggress"]),
        "sell_aggress": _column_values(-pd.to_numeric(frame["sell_aggress"], errors="coerce").abs()),
    }
    shared_path = _shares_print_index(frame, path)
    if shared_path:
        values["implied_price"] = _column_values(path["implied_price"])

    def present(field: str) -> np.ndarray:
        return np.array([value is not None for value in values[field]], dtype=bool)

    tail = np.arange(max(0, n - limit), n)
    has_total = present("total_aggress")
    p1_rows = np.flatnonzero(has_total)[-limit:]
    p3_any = has_total | present("price") | present("buy_aggress") | present("sell_aggress")
    rows = {"p1": p1_rows, "p3": tail[p3_any[tail]]}
    if shared_path:
        p4_any = present("price") | present("implied_price")
        rows["p4"] = tail[p4_any[tail]]
    firsts = [int(r[0]) for r in rows.values() if len(r)]
    start = min(firsts) if firsts else n
    stamps = pd.DatetimeIndex(frame.index[start:])
    columns: Dict[str, List[Any]] = {"x": [_to_et_iso(ts) for ts in stamps]}
    for field, column in values.items():
        columns[field] = column[start:]
    return columns, {key: _row_spec(r, start, n) for key, r in rows.items()}


def print_panel_rows(columns: Dict[str, List[Any]], spec: Dict[str, Any], fields: Iterable[str]) -> List[Dict[str, Any]]:
    """The panel's row dicts, as `_df_columns_to_points` builds them. Mirrors `expandPrintColumns` in dashboard.js."""
    skip = set(spec.get("skip") or [])
    fields = tuple(fields)
    return [
        {"x": columns["x"][i], **{field: columns[field][i] for field in fields}}
        for i in range(int(spec.get("from") or 0), len(columns["x"]))
        if i not in skip
    ]


def _cap_spec(spec: Dict[str, Any], n: int, limit: int) -> None:
    skip = spec.get("skip") or []
    extra = (n - int(spec["from"]) - len(skip)) - int(limit)
    if extra <= 0:
        return
    position = int(spec["from"])
    if not skip:
        spec["from"] = position + extra
        return
    skipped = set(skip)
    while extra > 0:
        if position not in skipped:
            extra -= 1
        position += 1
    spec["from"] = position
    spec["skip"] = [i for i in skip if i >= position]


def append_print_rows(payload: Dict[str, Any], points: List[Dict[str, Any]], limit: int = TICK_PLOT_MAX_POINTS) -> None:
    """Append live prints to the shared columns, unfiltered, and keep each panel's last `limit` rows."""
    columns = payload.get("prints")
    if not columns:
        return
    for point in points:
        for field, column in columns.items():
            column.append(point.get(field))
    n = len(columns["x"])
    specs = [
        panel["rows"]
        for key in PRINT_PANEL_FIELDS
        if (panel := (payload.get("panels") or {}).get(key)) and panel.get("rows")
    ]
    for spec in specs:
        _cap_spec(spec, n, limit)
    cut = min((int(spec["from"]) for spec in specs), default=0)
    if cut < COLUMN_TRIM_CHUNK:
        return
    for column in columns.values():
        del column[:cut]
    for spec in specs:
        spec["from"] = int(spec["from"]) - cut
        spec["skip"] = [i - cut for i in spec.get("skip") or []]


def build_dashboard_payload(
    session_frame: pd.DataFrame,
    session_path: pd.DataFrame,
    meta: Dict[str, Any],
    *,
    columnar: bool = True,
) -> Dict[str, Any]:
    """Live picture. With `columnar`, Panels 1, 3 and 4 are row specs over one shared `prints` table."""
    meta = {**meta, "state_styles": state_styles_payload()}
    if not columnar or session_frame is None or session_frame.empty:
        return {
            "success": True,
            "panels": {
                "p1": build_total_aggress_panel(session_frame),
                "p2": build_buysell_ratio_panel(session_frame),
                "p3": build_volume_panel(session_frame),
                "p4": _path_panel("Intraday path under beta_ref", session_path, marker_color="#7f7f7f"),
            },
            "meta": meta,
        }
    columns, specs = build_print_columns(session_frame, session_path)
    p1 = build_total_aggress_panel(session_frame, with_data=False)
    p3 = build_volume_panel(session_frame, with_data=False)
    p4 = _path_panel(
        "Intraday path under beta_ref",
        session_path,
        marker_color="#7f7f7f",
        with_data="p4" not in specs,
    )
    for key, panel in (("p1", p1), ("p3", p3), ("p4", p4)):
        if key in specs:
            panel.pop("data", None)
            panel["rows"] = specs[key]
    return {
        "success": True,
        "prints": columns,
        "panels": {"p1": p1, "p2": build_buysell_ratio_panel(session_frame), "p3": p3, "p4": p4},
        "meta": meta,
    }
