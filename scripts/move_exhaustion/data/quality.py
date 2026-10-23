"""Data quality checks for move-exhaustion panels."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, PRIMARY_SYMBOL, TICK_SIZE


def row_level_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add quality flag columns; do not drop rows."""
    out = df.copy()
    price = pd.to_numeric(out.get("price"), errors="coerce")
    out["flag_price_nonfinite"] = ~np.isfinite(price)
    out["flag_price_out_of_band"] = (price <= 80) | (price >= 150)
    if {"spread_mean", "displayed_depth_mean"}.issubset(out.columns):
        spread = pd.to_numeric(out["spread_mean"], errors="coerce")
        depth = pd.to_numeric(out["displayed_depth_mean"], errors="coerce")
        out["flag_wide_spread"] = spread > spread.quantile(0.99)
        out["flag_zero_depth"] = depth <= 0
    else:
        out["flag_wide_spread"] = False
        out["flag_zero_depth"] = False
    return out


def daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Per-date inventory for the primary TY panel."""
    rows = []
    for et_date, day in df.groupby("et_date", sort=True):
        price = pd.to_numeric(day["price"], errors="coerce")
        rows.append(
            {
                "et_date": et_date,
                "n_rows": int(len(day)),
                "n_valid_price": int(price.notna().sum()),
                "first_ts": str(day["bucket_ts"].min()),
                "last_ts": str(day["bucket_ts"].max()),
                "price_min": float(price.min()) if price.notna().any() else None,
                "price_max": float(price.max()) if price.notna().any() else None,
                "daily_range_ticks": (
                    float((price.max() - price.min()) / TICK_SIZE[PRIMARY_SYMBOL])
                    if price.notna().any()
                    else None
                ),
                "median_spread": (
                    float(pd.to_numeric(day["spread_mean"], errors="coerce").median())
                    if "spread_mean" in day
                    else None
                ),
                "total_trade_intensity": (
                    float(pd.to_numeric(day["trade_intensity"], errors="coerce").sum())
                    if "trade_intensity" in day
                    else None
                ),
            }
        )
    return pd.DataFrame(rows)


def aggressor_sign_validation(df: pd.DataFrame) -> dict[str, Any]:
    """Check that positive agg_1m coincides with higher same-bar price change.

    Uses contemporaneous (same-minute) returns. Next-bar returns often mean-revert
    and are not a reliable aggressor-sign diagnostic on 1m panels.
    """
    if "agg_1m" not in df.columns:
        return {"ok": False, "reason": "agg_1m_missing"}
    work = df.sort_values(["et_date", "bucket_ts"]).copy()
    work["same_bar_dprice"] = work.groupby("et_date")["price"].diff()
    signed = pd.to_numeric(work["agg_1m"], errors="coerce")
    buy = work.loc[signed > 0, "same_bar_dprice"].dropna()
    sell = work.loc[signed < 0, "same_bar_dprice"].dropna()
    if buy.empty or sell.empty:
        return {"ok": False, "reason": "insufficient_signed_rows"}
    buy_mean = float(buy.mean())
    sell_mean = float(sell.mean())
    return {
        "ok": bool(buy_mean > sell_mean),
        "horizon": "same_bar",
        "mean_dprice_on_buy_bars": buy_mean,
        "mean_dprice_on_sell_bars": sell_mean,
        "n_buy": int(len(buy)),
        "n_sell": int(len(sell)),
        "edge": buy_mean - sell_mean,
        "note": "Sign should not be systematically reversed; edge need not be large.",
    }


def assert_no_forbidden_writes(path: Any) -> None:
    text = str(path).replace("\\", "/")
    from scripts.move_exhaustion.data.contracts import FORBIDDEN_LIVE_GLOBS

    for frag in FORBIDDEN_LIVE_GLOBS:
        if frag in text:
            raise RuntimeError(f"Refusing write into forbidden live path: {path}")
