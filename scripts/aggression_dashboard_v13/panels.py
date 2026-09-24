#!/usr/bin/env python3
"""Plotly payload builders for the curve aggression dashboard."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.aggression_dashboard_v13.core import TICK_PLOT_MAX_POINTS
from scripts.aggression_dashboard_v13.hf_model import align_flow_and_price

EASTERN = ZoneInfo("America/New_York")


def _to_et_iso(ts: Any) -> str:
    """Convert a UTC timestamp/index value to timezone-naive Eastern ISO text."""
    value = pd.Timestamp(ts)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    return value.tz_convert(EASTERN).tz_localize(None).isoformat()


def _series_to_points(series: pd.Series) -> List[Dict[str, Any]]:
    """Convert a pandas series to JSON-safe `{x, y}` points."""
    clean = series.dropna()
    return [
        {"x": _to_et_iso(idx), "y": float(value)}
        for idx, value in clean.items()
    ]


def _df_columns_to_points(df: pd.DataFrame, *columns: str) -> List[Dict[str, Any]]:
    """Convert selected DataFrame columns to records with ISO timestamps."""
    if df.empty:
        return []
    records: List[Dict[str, Any]] = []
    for idx, row in df[list(columns)].dropna(how="all").iterrows():
        item: Dict[str, Any] = {"x": _to_et_iso(idx)}
        for column in columns:
            value = row.get(column)
            item[column] = None if pd.isna(value) else float(value)
        records.append(item)
    return records


def _build_axis_alignment(aligned: pd.DataFrame) -> Dict[str, Any]:
    """Compute dual-axis ranges that make flow/price overlap meaningful."""
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

    margin = 0.05
    a_margin = a_span * margin
    p_margin = p_span * margin
    a_min -= a_margin
    a_max += a_margin
    p_min -= p_margin
    p_max += p_margin

    beta = (p_max - p_min) / (a_max - a_min)
    alpha = p_min - a_min * beta
    return {
        "aggress_range": [a_min, a_max],
        "price_range": [p_min, p_max],
        "alpha": alpha,
        "beta": beta,
        "formula": f"P_implied = {alpha:.6f} + Aggress * {beta:.6e}",
        "beta_ticks_per_1m_dv01": beta * 1_000_000 * 32,
    }


def build_total_aggress_panel(flow_df: pd.DataFrame, price_df: pd.DataFrame) -> Dict[str, Any]:
    """Panel 1: per-print TotalAggress vs selected price symbol."""
    if flow_df.empty or "total_aggress" not in flow_df.columns:
        return {"title": "TotalAggress vs Price/Yield (per print)", "data": []}
    work = flow_df.copy()
    work.index = pd.to_datetime(work.index, utc=True)
    work.sort_index(inplace=True)
    if len(work) > TICK_PLOT_MAX_POINTS:
        work = work.iloc[-TICK_PLOT_MAX_POINTS:]
    price = pd.Series(dtype=float)
    if price_df is not None and not price_df.empty and "price" in price_df.columns:
        price = price_df["price"].copy()
        price.index = pd.to_datetime(price.index, utc=True)
        price = price.sort_index()
    aligned = pd.DataFrame({
        "total_aggress": work["total_aggress"].astype(float),
        "price": price.reindex(work.index, method="ffill") if len(price) else pd.Series(index=work.index, dtype=float),
    }, index=work.index)
    if aligned["price"].isna().any() and "trade_price" in work.columns:
        aligned["price"] = aligned["price"].fillna(work["trade_price"].astype(float))
    aligned = aligned.dropna(subset=["total_aggress"])
    return {
        "title": "TotalAggress vs Price/Yield (per print)",
        "data": _df_columns_to_points(aligned, "total_aggress", "price"),
        "traces": [
            {
                "field": "total_aggress",
                "name": "TotalAggress",
                "axis": "y",
                "color": "#1f4ed8",
                "shape": "linear",
                "connectgaps": True,
            },
            {"field": "price", "name": "Price/Yield", "axis": "y2", "color": "#d62728"},
        ],
        "layout": {
            "yaxis_title": "TotalAggress",
            "yaxis2_title": "Price/Yield",
        },
        "axis_align": _build_axis_alignment(aligned),
    }


def build_buysell_ratio_panel(flow_df: pd.DataFrame, price_df: pd.DataFrame) -> Dict[str, Any]:
    """Panel 2: Buy/Sell ratio vs selected price symbol."""
    if flow_df.empty:
        return {"title": "BuySell Ratio vs Price/Yield", "data": []}
    ratio = flow_df[["buysell_ratio"]].copy()
    if not price_df.empty:
        price = price_df["price"].resample("1min").last().ffill()
        combined_idx = ratio.index.union(price.index).sort_values()
        ratio = ratio.reindex(combined_idx).ffill().reindex(price.index)
        ratio["price"] = price
    else:
        ratio["price"] = flow_df.get("trade_price")
    return {
        "title": "BuySell Ratio vs Price/Yield",
        "data": _df_columns_to_points(ratio, "buysell_ratio", "price"),
        "traces": [
            {"field": "buysell_ratio", "name": "BuySell Ratio", "axis": "y", "color": "#2e7d5b"},
            {"field": "price", "name": "Price/Yield", "axis": "y2", "color": "#e0962f"},
        ],
        "layout": {
            "yaxis_title": "BuySell Ratio",
            "yaxis2_title": "Price/Yield",
        },
    }


def build_volume_panel(flow_df: pd.DataFrame, price_df: pd.DataFrame) -> Dict[str, Any]:
    """Panel 3: Trade buys/sells bars with TotalAggress and price overlays."""
    if flow_df.empty:
        return {"title": "Trade Buys & Sells vs TotalAggress and Price/Yield Over Time", "data": []}
    panel = flow_df[["buy_flow_bucket", "sell_flow_bucket", "total_aggress"]].copy()
    panel["sell_flow_bucket"] = -panel["sell_flow_bucket"].abs()
    if not price_df.empty:
        price = price_df["price"].resample("1min").last().ffill()
        combined_idx = panel.index.union(price.index).sort_values()
        panel = panel.reindex(combined_idx).ffill().reindex(price.index)
        panel["price"] = price
    else:
        panel["price"] = flow_df.get("trade_price")
    return {
        "title": "Trade Buys & Sells vs TotalAggress and Price/Yield Over Time",
        "data": _df_columns_to_points(
            panel,
            "buy_flow_bucket",
            "sell_flow_bucket",
            "total_aggress",
            "price",
        ),
        "traces": [
            {"field": "buy_flow_bucket", "name": "Buy Aggress", "type": "bar", "axis": "y", "color": "#2ca02c"},
            {"field": "sell_flow_bucket", "name": "Sell Aggress", "type": "bar", "axis": "y", "color": "#d62728"},
            {"field": "price", "name": "Price/Yield", "type": "line", "axis": "y2", "color": "#8b3a3a"},
            {"field": "total_aggress", "name": "TotalAggress", "type": "line", "axis": "y3", "color": "#1a1a1a"},
        ],
        "layout": {
            "yaxis_title": "Volume",
            "yaxis2_title": "Price/Yield",
            "yaxis3_title": "TotalAggress",
        },
    }


def _build_fair_value_panel(
    title: str,
    model_df: pd.DataFrame,
    meta: Dict[str, Any],
    *,
    large_residual_color: str = "#7f7f7f",
) -> Dict[str, Any]:
    if model_df.empty:
        return {
            "title": title,
            "data": [],
            "large_residuals": [],
            "traces": [],
            "layout": {"yaxis_title": "Price/Yield", "xaxis_title": "Time (ET)", "legend_title": "Legend"},
            "meta": meta,
        }
    return {
        "title": title,
        "data": _df_columns_to_points(model_df, "price", "predicted"),
        "large_residuals": [
            {"x": _to_et_iso(idx), "y": float(row["price"])}
            for idx, row in model_df[model_df.get("large_residual", False)].iterrows()
            if not pd.isna(row.get("price"))
        ] if not model_df.empty and "large_residual" in model_df else [],
        "traces": [
            {"field": "price", "name": "Actual Price/Yield", "axis": "y", "color": "#1f4ed8"},
            {"field": "predicted", "name": "Predicted Price/Yield", "axis": "y", "color": "#d62728", "dash": "dash"},
        ],
        "layout": {"yaxis_title": "Price/Yield", "xaxis_title": "Time (ET)", "legend_title": "Legend"},
        "large_residual_color": large_residual_color,
        "meta": meta,
    }


def build_fair_value_panels(
    fair_value_models: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Panels 4 and 5: intraday and 10-business-day fair-value references."""
    intraday = fair_value_models.get("intraday") or {}
    tenday = fair_value_models.get("tenday") or {}
    intraday_meta = dict(intraday.get("meta") or {})
    tenday_meta = dict(tenday.get("meta") or {})
    p4 = _build_fair_value_panel(
        "Intraday Fair Value (fit once per session)",
        intraday.get("data", pd.DataFrame()),
        intraday_meta,
        large_residual_color="#7f7f7f",
    )
    p5 = _build_fair_value_panel(
        "10-Business-Day Fair Value",
        tenday.get("data", pd.DataFrame()),
        tenday_meta,
        large_residual_color="#d62728",
    )
    return p4, p5, {"intraday": intraday_meta, "tenday": tenday_meta}


def build_dashboard_payload(
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the complete API response for all dashboard panels."""
    fair_value_models = meta.get("fair_value_models") or {}
    p4, p5, model_meta = build_fair_value_panels(fair_value_models)
    response_meta = dict(meta)
    response_meta.pop("fair_value_models", None)
    response_meta.pop("tick_flow", None)
    response_meta["fair_value"] = model_meta
    response_meta["hf_model"] = model_meta.get("intraday", {})
    return {
        "success": True,
        "panels": {
            "p1": build_total_aggress_panel(meta.get("tick_flow", flow_df), price_df),
            "p2": build_buysell_ratio_panel(flow_df, price_df),
            "p3": build_volume_panel(flow_df, price_df),
            "p4": p4,
            "p5": p5,
        },
        "meta": response_meta,
    }

