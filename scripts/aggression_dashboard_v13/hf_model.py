#!/usr/bin/env python3
"""Fair-value model helpers for the aggression dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

EASTERN = ZoneInfo("America/New_York")
HF_SESSION_MIN_PERIODS = 30
HF_TENDAY_BUSINESS_DAYS = 10
FAIR_VALUE_LARGE_RESIDUAL_Z = 2.0


def align_flow_and_price(
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
    *,
    daily_reset: bool = False,
    rezero: bool = True,
    ffill_aggress: bool = True,
) -> pd.DataFrame:
    """Align TotalAggress to price timestamps.

    When ``ffill_aggress`` is true (OLS / default), hold the last cumsum across
    the price grid. Panel 1 turns that off so mid-only minutes do not paint a
    stale ruler.
    """
    if flow_df.empty:
        return pd.DataFrame()

    aggress_source = daily_reset_aggress(flow_df) if daily_reset else flow_df["total_aggress"]

    if not price_df.empty and isinstance(price_df.index, pd.DatetimeIndex):
        price = price_df["price"].copy()
        combined_idx = flow_df.index.union(price.index).sort_values()
        if ffill_aggress:
            aggress = aggress_source.reindex(combined_idx).ffill().fillna(0.0)
            aligned = pd.DataFrame({
                "total_aggress": aggress.reindex(price.index),
                "price": price,
            }).dropna()
        else:
            aligned = pd.DataFrame({
                "total_aggress": aggress_source.reindex(combined_idx),
                "price": price.reindex(combined_idx).ffill(),
            }).dropna(how="all")
    else:
        aligned = pd.DataFrame({
            "total_aggress": aggress_source,
            "price": flow_df.get("trade_price", pd.Series(index=flow_df.index, dtype=float)),
        }).dropna(how="all" if not ffill_aggress else "any")

    if rezero and not aligned.empty:
        first = aligned["total_aggress"].dropna()
        if not first.empty:
            aligned["total_aggress"] = aligned["total_aggress"] - first.iloc[0]
    return aligned


def daily_reset_aggress(flow_df: pd.DataFrame) -> pd.Series:
    """Return cumulative aggression reset at each New York calendar day."""
    if flow_df.empty:
        return pd.Series(dtype=float)
    if "signed_flow_bucket" in flow_df:
        bucket_flow = flow_df["signed_flow_bucket"].fillna(0.0)
    else:
        bucket_flow = flow_df["total_aggress"].diff().fillna(0.0)
    idx = pd.DatetimeIndex(bucket_flow.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    et_dates = pd.Series(idx.tz_convert(EASTERN).date, index=bucket_flow.index)
    reset = bucket_flow.groupby(et_dates).cumsum()
    return reset - reset.groupby(et_dates).transform("first")


def _empty_model(aligned: pd.DataFrame, *, model_name: str, reason: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    model_df = aligned.copy()
    model_df["predicted"] = np.nan
    model_df["residual"] = np.nan
    model_df["residual_zscore"] = np.nan
    model_df["large_residual"] = False
    return model_df, {
        "ok": False,
        "reason": reason,
        "model": model_name,
        "min_periods": HF_SESSION_MIN_PERIODS,
        "n": int(len(model_df)),
    }


def _ols_params(
    x: pd.Series,
    y: pd.Series,
    *,
    min_periods: int = HF_SESSION_MIN_PERIODS,
) -> Tuple[float, float, str | None]:
    sample = pd.DataFrame({"x": x.astype(float), "y": y.astype(float)}).dropna()
    if len(sample) < min_periods or sample["x"].nunique() < 2:
        return np.nan, np.nan, f"Need at least {min_periods} points and non-constant aggression."
    x_arr = sample["x"].to_numpy()
    y_arr = sample["y"].to_numpy()
    x_mean = float(x_arr.mean())
    y_mean = float(y_arr.mean())
    denom = float(np.sum((x_arr - x_mean) ** 2))
    if denom == 0:
        return np.nan, np.nan, "Aggression variance is zero."
    beta = float(np.sum((x_arr - x_mean) * (y_arr - y_mean)) / denom)
    alpha = y_mean - beta * x_mean
    return alpha, beta, None


def _apply_fixed_model(
    aligned: pd.DataFrame,
    *,
    alpha: float,
    beta: float,
    model_name: str,
    fit_ts: str | None,
    large_residual_z: float,
    extra_meta: Dict[str, Any] | None = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    model_df = aligned.copy()
    model_df["predicted"] = alpha + beta * model_df["total_aggress"].astype(float)
    model_df["residual"] = model_df["price"].astype(float) - model_df["predicted"]
    residual_std = float(model_df["residual"].dropna().std(ddof=1)) if model_df["residual"].notna().sum() > 1 else 0.0
    denom = residual_std if residual_std else np.nan
    model_df["residual_zscore"] = model_df["residual"] / denom
    model_df["large_residual"] = model_df["residual_zscore"].abs() >= large_residual_z

    valid = model_df["residual"].notna() & model_df["price"].notna()
    valid_residual = model_df.loc[valid, "residual"]
    valid_price = model_df.loc[valid, "price"]
    ss_res = float(np.sum(valid_residual.to_numpy() ** 2)) if len(valid_residual) else 0.0
    ss_tot = float(np.sum((valid_price.to_numpy() - valid_price.mean()) ** 2)) if len(valid_price) else 0.0
    meta = {
        "ok": True,
        "model": model_name,
        "alpha": float(alpha),
        "beta": float(beta),
        "r2": float(1.0 - ss_res / ss_tot) if ss_tot else 0.0,
        "residual_mean": float(valid_residual.mean()) if len(valid_residual) else 0.0,
        "residual_std": residual_std,
        "valid_points": int(valid.sum()),
        "large_residual_z": float(large_residual_z),
        "large_residual_count": int(model_df["large_residual"].sum()),
        "fit_ts": fit_ts,
        "n": int(len(model_df)),
    }
    if extra_meta:
        meta.update(extra_meta)
    return model_df, meta


def fit_session_model(
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
    *,
    large_residual_z: float = FAIR_VALUE_LARGE_RESIDUAL_Z,
    fit_ts: datetime | None = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Fit one daily-reset intraday model and return predicted/residual series."""
    model_name = "intraday OLS fit once per session"
    aligned = align_flow_and_price(flow_df, price_df, daily_reset=True)
    alpha, beta, reason = _ols_params(aligned.get("total_aggress", pd.Series(dtype=float)), aligned.get("price", pd.Series(dtype=float)))
    if reason:
        return _empty_model(aligned, model_name=model_name, reason=reason)
    return _apply_fixed_model(
        aligned,
        alpha=alpha,
        beta=beta,
        model_name=model_name,
        fit_ts=(fit_ts or datetime.now(timezone.utc)).isoformat(),
        large_residual_z=large_residual_z,
        extra_meta={"min_periods": HF_SESSION_MIN_PERIODS, "refit_cadence": "once_per_session"},
    )


def apply_session_model(
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
    model_meta: Dict[str, Any],
    *,
    large_residual_z: float = FAIR_VALUE_LARGE_RESIDUAL_Z,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Apply cached intraday alpha/beta to the current live series."""
    aligned = align_flow_and_price(flow_df, price_df, daily_reset=True)
    if not model_meta.get("ok"):
        return _empty_model(aligned, model_name="intraday OLS fit once per session", reason="Cached model is not ready.")
    model_df, meta = _apply_fixed_model(
        aligned,
        alpha=float(model_meta["alpha"]),
        beta=float(model_meta["beta"]),
        model_name=str(model_meta.get("model") or "intraday OLS fit once per session"),
        fit_ts=model_meta.get("fit_ts"),
        large_residual_z=large_residual_z,
        extra_meta={"min_periods": HF_SESSION_MIN_PERIODS, "refit_cadence": "once_per_session", "cached": True},
    )
    return model_df, meta


def fit_tenday_model(
    flow_df: pd.DataFrame,
    price_df: pd.DataFrame,
    *,
    large_residual_z: float = FAIR_VALUE_LARGE_RESIDUAL_Z,
    fit_ts: datetime | None = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Fit a pooled 10-business-day daily-reset fair-value model."""
    model_name = f"{HF_TENDAY_BUSINESS_DAYS}-business-day pooled daily-reset OLS"
    aligned = align_flow_and_price(flow_df, price_df, daily_reset=True)
    if aligned.empty:
        return _empty_model(aligned, model_name=model_name, reason="No aligned flow/price data.")

    idx = pd.DatetimeIndex(aligned.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    aligned = aligned.copy()
    aligned["et_date"] = idx.tz_convert(EASTERN).date
    day_open = aligned.groupby("et_date")["price"].transform("first")
    aligned["price_delta"] = aligned["price"] - day_open

    alpha, beta, reason = _ols_params(aligned["total_aggress"], aligned["price_delta"])
    if reason:
        model_df, meta = _empty_model(aligned.drop(columns=["price_delta"], errors="ignore"), model_name=model_name, reason=reason)
        meta["business_days"] = int(aligned["et_date"].nunique()) if "et_date" in aligned else 0
        return model_df, meta

    model_df = aligned.copy()
    model_df["predicted_delta"] = alpha + beta * model_df["total_aggress"].astype(float)
    model_df["predicted"] = day_open + model_df["predicted_delta"]
    model_df["residual"] = model_df["price"].astype(float) - model_df["predicted"]
    residual_std = float(model_df["residual"].dropna().std(ddof=1)) if model_df["residual"].notna().sum() > 1 else 0.0
    model_df["residual_zscore"] = model_df["residual"] / (residual_std if residual_std else np.nan)
    model_df["large_residual"] = model_df["residual_zscore"].abs() >= large_residual_z

    valid = model_df["residual"].notna() & model_df["price_delta"].notna()
    valid_residual = model_df.loc[valid, "residual"]
    valid_target = model_df.loc[valid, "price_delta"]
    ss_res = float(np.sum(valid_residual.to_numpy() ** 2)) if len(valid_residual) else 0.0
    ss_tot = float(np.sum((valid_target.to_numpy() - valid_target.mean()) ** 2)) if len(valid_target) else 0.0
    dates = [str(x) for x in sorted(model_df["et_date"].dropna().unique())]
    meta = {
        "ok": True,
        "model": model_name,
        "alpha": float(alpha),
        "beta": float(beta),
        "r2": float(1.0 - ss_res / ss_tot) if ss_tot else 0.0,
        "residual_mean": float(valid_residual.mean()) if len(valid_residual) else 0.0,
        "residual_std": residual_std,
        "valid_points": int(valid.sum()),
        "large_residual_z": float(large_residual_z),
        "large_residual_count": int(model_df["large_residual"].sum()),
        "fit_ts": (fit_ts or datetime.now(timezone.utc)).isoformat(),
        "n": int(len(model_df)),
        "business_days": len(dates),
        "dates": dates,
        "refit_cadence": "once_per_day",
    }
    return model_df.drop(columns=["price_delta"], errors="ignore"), meta

