"""V2.7 live features: V2 session construction plus B1 10-minute local nowcast columns.

Does not edit V2 feature code. `build_live_features` from V2 is read-only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v2.features import build_live_features as build_session_features

ANCHOR_MIN = 10
FEATURE_SET = [
    "agg_cum_10m",
    "agg_1m",
    "agg_5m",
    "buysell_ratio",
    "trade_intensity",
    "avg_trade_size",
    "large_trade_share",
    "realized_vol_5",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "US_agg_5m",
    "spread_mean",
    "size_imbalance_mean",
    "displayed_depth_mean",
    "trade_size_vs_depth",
    "depth_adjusted_signed_flow",
    "minutes_since_anchor",
]


def add_local_nowcast_columns(data: pd.DataFrame) -> pd.DataFrame:
    """Add B1_W10 columns. Caller must pass a single-session / single-date frame."""
    out = data.copy()
    if out.empty or "price" not in out.columns:
        return out
    price = out["price"].astype(float)
    agg_1m = out["agg_1m"].astype(float) if "agg_1m" in out.columns else pd.Series(np.nan, index=out.index)
    out["agg_cum_10m"] = agg_1m.rolling(ANCHOR_MIN, min_periods=ANCHOR_MIN).sum()
    out["realized_vol_5"] = price.diff().rolling(5, min_periods=5).std()
    out["local_anchor"] = price.shift(ANCHOR_MIN)
    out["minutes_since_anchor"] = np.where(out["local_anchor"].notna(), float(ANCHOR_MIN), np.nan)
    out["target_local_delta"] = price - out["local_anchor"]
    return out


def add_local_nowcast_by_date(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "et_date" not in df.columns:
        return add_local_nowcast_columns(df)
    frames = [
        add_local_nowcast_columns(day.sort_values("bucket_ts") if "bucket_ts" in day.columns else day)
        for _, day in df.groupby("et_date", sort=False)
    ]
    return pd.concat(frames, ignore_index=True)


def build_live_features(tbbo: pd.DataFrame, bucket: str = "1min") -> pd.DataFrame:
    data = build_session_features(tbbo, bucket)
    if data.empty:
        return data
    return add_local_nowcast_columns(data)


__all__ = [
    "ANCHOR_MIN",
    "FEATURE_SET",
    "add_local_nowcast_by_date",
    "add_local_nowcast_columns",
    "build_live_features",
]
