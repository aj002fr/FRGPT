"""Read-only 1m flow folds for supply-week windows. Does not write the history store."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.aggression_dashboard.flow_history import DB_PATH
from scripts.aggression_flow_history_dash.session import to_eastern
from scripts.supply_weeks_live.weeks import (
    BPS_PER_POINT,
    EASTERN,
    WINDOW_NAMES,
    hours_from_week_open,
    week_close_et,
    week_open_et,
    window_bounds,
    window_price_bps,
)

PATH_STEP_HOURS = 5.0 / 60.0
CLOCK_HOURS = 120


def _read_bars(symbol: str, start: datetime, end: datetime, db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path), timeout=30)
    except sqlite3.Error:
        return []
    try:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            """
            SELECT ts_utc, ts_et, buy_flow, sell_flow, signed_flow, trade_price
            FROM v1_aggress_flow_1m
            WHERE symbol = ? AND ts_utc >= ? AND ts_utc < ?
            ORDER BY ts_utc
            """,
            (symbol, _utc_iso(start), _utc_iso(end)),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    out = []
    for ts_utc, ts_et, buy_flow, sell_flow, signed_flow, trade_price in rows:
        out.append(
            {
                "ts_utc": ts_utc,
                "ts_et": ts_et,
                "buy_flow": float(buy_flow or 0.0),
                "sell_flow": float(sell_flow or 0.0),
                "signed_flow": float(signed_flow or 0.0),
                "trade_price": None if trade_price is None else float(trade_price),
            }
        )
    return out


def _utc_iso(stamp: datetime) -> str:
    clock = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)
    return clock.astimezone(timezone.utc).isoformat()


def fold_week_flow(
    info: dict[str, Any],
    *,
    symbol: str = "TY",
    db_path: Path | None = None,
    price_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(db_path) if db_path is not None else DB_PATH
    start = week_open_et(info["week_start"])
    end = week_close_et(info["week_start"])
    bars = _read_bars(symbol, start, end, path)
    points: list[dict[str, Any]] = []
    for bar in bars:
        clock = to_eastern(bar.get("ts_et")) or to_eastern(bar.get("ts_utc"))
        if clock is None:
            continue
        points.append({**bar, "hours": hours_from_week_open(clock, info["week_start"])})

    windows: dict[str, dict[str, Any]] = {}
    for name in WINDOW_NAMES:
        lo, hi = window_bounds(info, name)
        slice_pts = [row for row in points if lo <= float(row["hours"]) < hi]
        buy = sum(float(row["buy_flow"]) for row in slice_pts)
        sell = sum(float(row["sell_flow"]) for row in slice_pts)
        prices = [row["trade_price"] for row in slice_pts if row.get("trade_price") is not None]
        flow_bps = None if len(prices) < 2 else (prices[-1] - prices[0]) * BPS_PER_POINT
        price_bps = window_price_bps(price_data or {}, lo, hi)
        windows[name] = {
            "buy_flow": buy,
            "sell_flow": sell,
            "net_flow": buy - sell,
            "total_flow": buy + sell,
            "n_bars": len(slice_pts),
            "window_bps": price_bps if price_bps is not None else flow_bps,
        }

    signed = 0.0
    path_hours: list[float] = []
    path_flow: list[float] = []
    next_mark = -1.0
    for row in points:
        signed += float(row["signed_flow"])
        hour = float(row["hours"])
        if hour + 1e-9 < next_mark:
            continue
        path_hours.append(round(hour, 4))
        path_flow.append(signed)
        next_mark = hour + PATH_STEP_HOURS
    if points and (not path_hours or path_hours[-1] != round(float(points[-1]["hours"]), 4)):
        path_hours.append(round(float(points[-1]["hours"]), 4))
        path_flow.append(signed)

    closed = week_close_et(info["week_start"]) <= datetime.now(EASTERN)
    return {
        "has_flow": bool(points),
        "windows": windows,
        "flow_path": {"hours": path_hours, "signed_flow": path_flow},
        "clock": hourly_clock(points, price_data, closed=closed),
    }


def hourly_clock(
    points: list[dict[str, Any]],
    price_data: dict[str, Any] | None,
    *,
    closed: bool,
) -> dict[str, list[float | None]]:
    """Cumulative buy, sell, net, and bps at each hour from Sunday 18:00 ET."""
    buy = sell = 0.0
    idx = 0
    last_hour = -1
    n = len(points)
    buys: list[float | None] = []
    sells: list[float | None] = []
    nets: list[float | None] = []
    for hour in range(CLOCK_HOURS):
        while idx < n and float(points[idx]["hours"]) < hour + 1:
            buy += float(points[idx]["buy_flow"])
            sell += float(points[idx]["sell_flow"])
            last_hour = hour
            idx += 1
        if n == 0 or (not closed and hour > last_hour):
            buys.append(None)
            sells.append(None)
            nets.append(None)
        else:
            buys.append(buy)
            sells.append(sell)
            nets.append(buy - sell)
    return {
        "buy": buys,
        "sell": sells,
        "net": nets,
        "bps": _bps_by_hour(price_data or {}, closed=closed),
    }


def _bps_by_hour(price_data: dict[str, Any], *, closed: bool) -> list[float | None]:
    hours = [float(value) for value in (price_data.get("hours") or [])]
    closes = [float(value) for value in (price_data.get("closes") or [])]
    n = min(len(hours), len(closes))
    if n < 2:
        return [None] * CLOCK_HOURS
    first = None
    last_px = None
    last_hour = -1
    idx = 0
    out: list[float | None] = []
    for hour in range(CLOCK_HOURS):
        while idx < n and hours[idx] < hour + 1:
            if first is None:
                first = closes[idx]
            last_px = closes[idx]
            last_hour = hour
            idx += 1
        if first is None or last_px is None or (not closed and hour > last_hour):
            out.append(None)
        else:
            out.append((last_px - first) * BPS_PER_POINT)
    return out


def _median(values: list[float]) -> float | None:
    series = pd.Series(values, dtype="float64")
    if series.empty:
        return None
    return float(series.median())


def _quantile(values: list[float], q: float) -> float | None:
    series = pd.Series(values, dtype="float64")
    if series.empty:
        return None
    return float(series.quantile(q))


def residual_bps(
    window_bps: float | None,
    net_flow: float | None,
    intercept_bps: float | None,
    slope_bps_per_mm: float | None,
) -> float | None:
    if window_bps is None or intercept_bps is None:
        return None
    slope = 0.0 if slope_bps_per_mm is None else float(slope_bps_per_mm)
    return float(window_bps) - float(intercept_bps) - slope * float(net_flow or 0.0) / 1e6


def _usable(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in samples if row.get("window_bps") is not None]


def fit_window_ols(samples: list[dict[str, Any]]) -> dict[str, Any]:
    usable = _usable(samples)
    xs = [float(row.get("net_flow") or 0.0) for row in usable]
    ys = [float(row["window_bps"]) for row in usable]
    buys = [float(row.get("buy_flow") or 0.0) for row in usable]
    sells = [float(row.get("sell_flow") or 0.0) for row in usable]
    empty = {
        "n": len(xs),
        "slope_bps_per_mm": None,
        "intercept_bps": None,
        "median_net": _median(xs),
        "median_bps": _median(ys),
        "median_buy": _median(buys),
        "median_sell": _median(sells),
        "implied_bps_at_median_net": None,
        "residual_median": None,
        "residual_p25": None,
        "residual_p75": None,
    }
    if len(xs) < 2:
        return empty
    x = pd.Series(xs, dtype="float64")
    y = pd.Series(ys, dtype="float64")
    var_x = float(((x - x.mean()) ** 2).sum())
    if var_x == 0:
        intercept = float(y.mean())
        slope_mm = None
    else:
        slope = float(((x - x.mean()) * (y - y.mean())).sum()) / var_x
        slope_mm = slope * 1e6
        intercept = float(y.mean() - slope * x.mean())
    median_net = _median(xs)
    implied = None
    if median_net is not None and slope_mm is not None:
        implied = intercept + slope_mm * median_net / 1e6
    residuals = [residual_bps(row["window_bps"], row.get("net_flow"), intercept, slope_mm) for row in usable]
    residuals = [value for value in residuals if value is not None]
    return {
        "n": len(xs),
        "slope_bps_per_mm": slope_mm,
        "intercept_bps": intercept,
        "median_net": median_net,
        "median_bps": _median(ys),
        "median_buy": _median(buys),
        "median_sell": _median(sells),
        "implied_bps_at_median_net": implied,
        "residual_median": _median(residuals),
        "residual_p25": _quantile(residuals, 0.25),
        "residual_p75": _quantile(residuals, 0.75),
    }


def summarize_windows(weeks: list[dict[str, Any]], *, month_end: str = "all") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in WINDOW_NAMES:
        samples = []
        for week in weeks:
            info = week.get("week_info") or {}
            if month_end == "month_end" and not info.get("is_month_end"):
                continue
            if month_end == "non_month_end" and info.get("is_month_end"):
                continue
            block = (week.get("flow") or {}).get("windows") or {}
            row = block.get(name)
            if not row:
                continue
            if int(row.get("n_bars") or 0) <= 0:
                continue
            samples.append(row)
        out[name] = fit_window_ols(samples)
    return out
