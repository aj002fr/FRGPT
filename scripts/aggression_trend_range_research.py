#!/usr/bin/env python3
"""Offline trend-vs-range research for a potential Aggression V2.1 dashboard.

Hard constraints:
- Read-only against V2 / TBBO artifacts.
- Does NOT modify scripts/aggression_dashboard_v2/ or live V2 on 5052.
- V2.1 dashboard copy is out of scope here; this script only produces a gate report.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_trend_range"
TICK_SIZE = 1.0 / 64.0
EPS = 1e-9
HORIZONS = (15, 30, 60)
USE_COLS = [
    "bucket_ts",
    "price",
    "et_date",
    "agg_cum_daily",
    "agg_1m",
    "agg_5m",
    "buysell_ratio",
    "vol_per_min",
    "mom_5m",
    "realized_vol",
    "trade_intensity",
    "large_trade_share",
    "avg_trade_size",
]


def _load_features(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=USE_COLS, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    df = df.sort_values("bucket_ts").drop_duplicates("bucket_ts", keep="last")
    df = df.set_index("bucket_ts")
    if "et_date" not in df or df["et_date"].isna().all():
        df["et_date"] = df.index.tz_convert("America/New_York").strftime("%Y-%m-%d")
    else:
        df["et_date"] = df["et_date"].astype(str)
    return df


def _rolling_path_efficiency(price: pd.Series, window: int) -> pd.Series:
    net = price.diff(window).abs()
    path = price.diff().abs().rolling(window, min_periods=max(5, window // 3)).sum()
    return net / path.clip(lower=EPS)


def _rolling_autocorr(returns: pd.Series, window: int, lag: int = 1) -> pd.Series:
    def _ac(x: np.ndarray) -> float:
        if len(x) < lag + 5:
            return np.nan
        a = x[:-lag]
        b = x[lag:]
        if np.std(a) < EPS or np.std(b) < EPS:
            return np.nan
        return float(np.corrcoef(a, b)[0, 1])

    return returns.rolling(window, min_periods=max(10, window // 2)).apply(_ac, raw=True)


def _adx_lite(price: pd.Series, window: int = 14) -> pd.Series:
    """Simple ADX proxy from close-to-close directional moves (no true high/low)."""
    up = price.diff().clip(lower=0.0)
    down = (-price.diff()).clip(lower=0.0)
    atr = price.diff().abs().rolling(window, min_periods=window).mean().clip(lower=EPS)
    plus_di = 100.0 * up.rolling(window, min_periods=window).mean() / atr
    minus_di = 100.0 * down.rolling(window, min_periods=window).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).clip(lower=EPS)
    return dx.rolling(window, min_periods=window).mean()


def _swing_structure(price: pd.Series, swing: int = 5) -> pd.Series:
    """Label local swing structure as TREND_UP / TREND_DOWN / RANGE / UNKNOWN.

    Uses a centered window only to mark swing pivots; the regime state is then
    updated causally as each pivot is observed in time order.
    """
    roll_max = price.rolling(swing * 2 + 1, center=True, min_periods=swing).max()
    roll_min = price.rolling(swing * 2 + 1, center=True, min_periods=swing).min()
    is_high = (price >= roll_max) & price.notna()
    is_low = (price <= roll_min) & price.notna()

    last_high: float | None = None
    last_low: float | None = None
    high_dir: str | None = None  # HH or LH
    low_dir: str | None = None  # HL or LL
    state = "UNKNOWN"
    out: list[str] = []
    for ts, px in price.items():
        if bool(is_high.loc[ts]) and np.isfinite(px):
            if last_high is not None:
                if px > last_high:
                    high_dir = "HH"
                elif px < last_high:
                    high_dir = "LH"
            last_high = float(px)
        if bool(is_low.loc[ts]) and np.isfinite(px):
            if last_low is not None:
                if px > last_low:
                    low_dir = "HL"
                elif px < last_low:
                    low_dir = "LL"
            last_low = float(px)
        if high_dir == "HH" and low_dir == "HL":
            state = "TREND_UP"
        elif high_dir == "LH" and low_dir == "LL":
            state = "TREND_DOWN"
        elif high_dir is not None and low_dir is not None:
            if (high_dir == "HH" and low_dir == "LL") or (high_dir == "LH" and low_dir == "HL"):
                state = "RANGE"
            elif state.startswith("TREND"):
                state = "TRANSITION"
            else:
                state = "RANGE"
        out.append(state)
    return pd.Series(out, index=price.index, dtype=object)


def _structure_from_efficiency(
    efficiency_30: pd.Series,
    efficiency_60: pd.Series,
    mom_30_ticks: pd.Series,
    range_ticks_30: pd.Series,
    adx: pd.Series,
) -> pd.Series:
    """Primary causal labeler: path efficiency + net momentum + range width."""
    labels = pd.Series("RANGE", index=efficiency_30.index, dtype=object)
    strong = (efficiency_30 >= 0.45) | ((efficiency_60 >= 0.40) & (efficiency_30 >= 0.30))
    weak = (efficiency_30 <= 0.25) & (efficiency_60 <= 0.30)
    wide = range_ticks_30 >= 6.0
    narrow = range_ticks_30 <= 4.0
    adx_trend = adx >= 20.0
    adx_range = adx <= 15.0

    up = strong & (mom_30_ticks > 1.0) & (wide | adx_trend)
    down = strong & (mom_30_ticks < -1.0) & (wide | adx_trend)
    rng = (weak | (narrow & ~strong)) | (adx_range & ~strong)

    labels = labels.mask(rng, "RANGE")
    labels = labels.mask(up, "TREND_UP")
    labels = labels.mask(down, "TREND_DOWN")
    # Ambiguous mid-efficiency with mixed signs.
    mid = ~(up | down | rng)
    labels = labels.mask(mid & (mom_30_ticks.abs() < 1.0), "RANGE")
    labels = labels.mask(mid & (mom_30_ticks >= 1.0) & (efficiency_30 >= 0.35), "TREND_UP")
    labels = labels.mask(mid & (mom_30_ticks <= -1.0) & (efficiency_30 >= 0.35), "TREND_DOWN")
    labels = labels.mask(efficiency_30.isna() | mom_30_ticks.isna(), "UNKNOWN")
    return labels


def annotate_trend_range(df: pd.DataFrame, effort_window: int = 5) -> pd.DataFrame:
    """Annotate minute bars with structure labels and trader effort proxies."""
    out = df.copy()
    price = out["price"].astype(float)
    agg_1m = out["agg_1m"].astype(float) if "agg_1m" in out else out["agg_cum_daily"].diff().fillna(0.0)
    out["delta_price"] = price.diff().fillna(0.0)
    out["delta_price_ticks"] = out["delta_price"] / TICK_SIZE
    out["mom_30_ticks"] = price.diff(30) / TICK_SIZE
    out["mom_60_ticks"] = price.diff(60) / TICK_SIZE
    out["range_high_30"] = price.rolling(30, min_periods=10).max()
    out["range_low_30"] = price.rolling(30, min_periods=10).min()
    out["range_ticks_30"] = (out["range_high_30"] - out["range_low_30"]) / TICK_SIZE
    out["path_efficiency_30"] = _rolling_path_efficiency(price, 30)
    out["path_efficiency_60"] = _rolling_path_efficiency(price, 60)
    rets = price.diff()
    out["ret_autocorr_15"] = _rolling_autocorr(rets, 15, lag=1)
    out["adx_14"] = _adx_lite(price, 14)

    roll_flow = agg_1m.rolling(effort_window, min_periods=1).sum().abs()
    roll_ticks = out["delta_price_ticks"].rolling(effort_window, min_periods=1).sum().abs()
    out["effort_per_tick"] = roll_flow / roll_ticks.clip(lower=1.0)

    # buysell_ratio is in [-100, 100] ~= 100 * (buy-sell)/(buy+sell)
    ratio = out["buysell_ratio"].astype(float).fillna(0.0)
    out["imbalance"] = ratio.abs() / 100.0
    out["two_sidedness"] = 0.5 * (1.0 - out["imbalance"])  # min(buy,sell)/(buy+sell)
    out["abs_buysell_ratio"] = ratio.abs()

    # Block / large-print proxies (trader "black trades" = large trades)
    out["large_trade_share"] = out.get("large_trade_share", pd.Series(0.0, index=out.index)).astype(float).fillna(0.0)
    out["avg_trade_size"] = out.get("avg_trade_size", pd.Series(0.0, index=out.index)).astype(float).fillna(0.0)
    out["large_trade_share_5m"] = out["large_trade_share"].rolling(effort_window, min_periods=1).mean()
    out["avg_trade_size_5m"] = out["avg_trade_size"].rolling(effort_window, min_periods=1).mean()

    # Session-relative effort / block activity (causal expanding median by et_date)
    def _expanding_med(s: pd.Series) -> pd.Series:
        return s.expanding(min_periods=10).median()

    out["effort_session_med"] = out.groupby("et_date", sort=False)["effort_per_tick"].transform(_expanding_med)
    out["effort_vs_session"] = out["effort_per_tick"] / out["effort_session_med"].clip(lower=EPS)
    out["large_share_session_med"] = out.groupby("et_date", sort=False)["large_trade_share_5m"].transform(_expanding_med)
    out["large_share_vs_session"] = out["large_trade_share_5m"] / out["large_share_session_med"].clip(lower=EPS)
    out["avg_size_session_med"] = out.groupby("et_date", sort=False)["avg_trade_size_5m"].transform(_expanding_med)
    out["avg_size_vs_session"] = out["avg_trade_size_5m"] / out["avg_size_session_med"].clip(lower=EPS)

    out["swing_label"] = _swing_structure(price, swing=5)
    out["regime"] = _structure_from_efficiency(
        out["path_efficiency_30"],
        out["path_efficiency_60"],
        out["mom_30_ticks"],
        out["range_ticks_30"],
        out["adx_14"],
    )
    # Blend: if swing and efficiency agree on trend direction, keep; if swing says RANGE and
    # efficiency is weak, force RANGE; else prefer efficiency (more causal / less look-ahead).
    swing = out["swing_label"]
    agree_up = (out["regime"] == "TREND_UP") & swing.isin(["TREND_UP", "TRANSITION", "UNKNOWN"])
    agree_down = (out["regime"] == "TREND_DOWN") & swing.isin(["TREND_DOWN", "TRANSITION", "UNKNOWN"])
    out["regime_blended"] = out["regime"]
    out.loc[agree_up, "regime_blended"] = "TREND_UP"
    out.loc[agree_down, "regime_blended"] = "TREND_DOWN"
    out.loc[(swing == "RANGE") & (out["path_efficiency_30"] <= 0.30), "regime_blended"] = "RANGE"

    # Trader overlays: primary = block/large prints; effort remains secondary context.
    # Healthy trend: structure trend + fewer/less elevated large prints + one-sided flow.
    out["trader_trend_support"] = (
        out["regime_blended"].isin(["TREND_UP", "TREND_DOWN"])
        & (out["large_share_vs_session"] <= 1.0)
        & (out["imbalance"] >= 0.20)
        & (out["effort_vs_session"] <= 1.25)
    )
    # Range fight with blocks: structure range + elevated large-trade share.
    out["trader_range_fight"] = (
        (out["regime_blended"] == "RANGE")
        & (out["large_share_vs_session"] >= 1.15)
        & (out["large_trade_share_5m"] >= 0.45)
    )
    out["trader_range_blocks"] = out["trader_range_fight"]
    return out


def _add_forward_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        regime_dir = d["regime_blended"].map(
            {"TREND_UP": 1.0, "TREND_DOWN": -1.0, "RANGE": 0.0, "TRANSITION": 0.0, "UNKNOWN": np.nan}
        )
        mom_sign = np.sign(d["mom_30_ticks"].replace(0, np.nan))
        # Prefer explicit regime direction when in a trend; else momentum sign.
        direction = regime_dir.where(regime_dir.abs() > 0, mom_sign)
        for h in HORIZONS:
            future = d["price"].shift(-h)
            chg = future - d["price"]
            d[f"future_change_{h}m"] = chg
            d[f"future_change_ticks_{h}m"] = chg / TICK_SIZE
            valid = future.notna() & direction.notna() & (direction != 0)
            persist = (direction * chg) > 0
            d[f"persist_hit_{h}m"] = persist.where(valid, np.nan)
            hi = d["range_high_30"]
            lo = d["range_low_30"]
            stay = (future <= hi) & (future >= lo)
            d[f"stay_in_band_{h}m"] = stay.where(future.notna() & hi.notna() & lo.notna(), np.nan)
            d[f"abs_progress_ticks_{h}m"] = (chg.abs() / TICK_SIZE).where(future.notna(), np.nan)
        parts.append(d)
    return pd.concat(parts).sort_index()


def _chronological_split(dates: list[str], train_frac: float = 0.7) -> tuple[list[str], list[str]]:
    ordered = sorted(dates)
    if len(ordered) <= 1:
        return ordered, []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac)))
    return ordered[:cut], ordered[cut:]


def _safe_mean(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.mean()) if len(s) else float("nan")


def _safe_median(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.median()) if len(s) else float("nan")


def _regime_stats(df: pd.DataFrame, regime_col: str, split_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for regime, sub in df.groupby(regime_col, sort=False):
        if regime in ("UNKNOWN",):
            continue
        base: dict[str, Any] = {
            "split": split_name,
            "regime_col": regime_col,
            "regime": str(regime),
            "n": int(len(sub)),
            "median_effort_per_tick": _safe_median(sub["effort_per_tick"]),
            "median_effort_vs_session": _safe_median(sub["effort_vs_session"]),
            "median_two_sidedness": _safe_median(sub["two_sidedness"]),
            "median_imbalance": _safe_median(sub["imbalance"]),
            "median_large_trade_share_5m": _safe_median(sub["large_trade_share_5m"]),
            "median_large_share_vs_session": _safe_median(sub["large_share_vs_session"]),
            "median_avg_trade_size_5m": _safe_median(sub["avg_trade_size_5m"]),
            "median_path_efficiency_30": _safe_median(sub["path_efficiency_30"]),
            "median_adx_14": _safe_median(sub["adx_14"]),
            "median_range_ticks_30": _safe_median(sub["range_ticks_30"]),
        }
        for h in HORIZONS:
            base[f"persist_rate_{h}m"] = _safe_mean(sub[f"persist_hit_{h}m"].astype(float))
            base[f"stay_in_band_rate_{h}m"] = _safe_mean(sub[f"stay_in_band_{h}m"].astype(float))
            base[f"median_abs_progress_ticks_{h}m"] = _safe_median(sub[f"abs_progress_ticks_{h}m"])
        rows.append(base)
    return rows


def _baseline_chance(df: pd.DataFrame) -> dict[str, float]:
    out: dict[str, float] = {"n": float(len(df))}
    for h in HORIZONS:
        out[f"persist_rate_{h}m"] = _safe_mean(df[f"persist_hit_{h}m"].astype(float))
        out[f"stay_in_band_rate_{h}m"] = _safe_mean(df[f"stay_in_band_{h}m"].astype(float))
    return out


def _trader_claim_table(df: pd.DataFrame, split_name: str) -> list[dict[str, Any]]:
    """Compare effort / block-trade proxies for TREND vs RANGE (structure labels)."""
    rows = []
    trend = df[df["regime_blended"].isin(["TREND_UP", "TREND_DOWN"])]
    rng = df[df["regime_blended"] == "RANGE"]
    for name, sub in (("TREND", trend), ("RANGE", rng)):
        row = {
            "split": split_name,
            "bucket": name,
            "n": int(len(sub)),
            "median_effort_per_tick": _safe_median(sub["effort_per_tick"]),
            "median_effort_vs_session": _safe_median(sub["effort_vs_session"]),
            "median_two_sidedness": _safe_median(sub["two_sidedness"]),
            "median_imbalance": _safe_median(sub["imbalance"]),
            "median_large_trade_share_5m": _safe_median(sub["large_trade_share_5m"]),
            "median_large_share_vs_session": _safe_median(sub["large_share_vs_session"]),
            "median_avg_trade_size_5m": _safe_median(sub["avg_trade_size_5m"]),
            "median_avg_size_vs_session": _safe_median(sub["avg_size_vs_session"]),
            "share_trader_trend_support": _safe_mean(sub["trader_trend_support"].astype(float)),
            "share_trader_range_fight": _safe_mean(sub["trader_range_fight"].astype(float)),
        }
        for h in HORIZONS:
            row[f"persist_rate_{h}m"] = _safe_mean(sub[f"persist_hit_{h}m"].astype(float))
            row[f"stay_in_band_rate_{h}m"] = _safe_mean(sub[f"stay_in_band_{h}m"].astype(float))
        rows.append(row)
    # Predictive overlays
    for label, mask, hit_prefix in (
        ("trader_trend_support", df["trader_trend_support"], "persist_hit"),
        ("structure_trend_only", df["regime_blended"].isin(["TREND_UP", "TREND_DOWN"]), "persist_hit"),
        ("trader_range_fight", df["trader_range_fight"], "stay_in_band"),
        ("structure_range_only", df["regime_blended"] == "RANGE", "stay_in_band"),
    ):
        sub = df[mask]
        row: dict[str, Any] = {
            "split": split_name,
            "bucket": label,
            "n": int(len(sub)),
            "median_effort_per_tick": _safe_median(sub["effort_per_tick"]),
            "median_effort_vs_session": _safe_median(sub["effort_vs_session"]),
            "median_two_sidedness": _safe_median(sub["two_sidedness"]),
            "median_imbalance": _safe_median(sub["imbalance"]),
            "median_large_trade_share_5m": _safe_median(sub["large_trade_share_5m"]),
            "median_large_share_vs_session": _safe_median(sub["large_share_vs_session"]),
            "median_avg_trade_size_5m": _safe_median(sub["avg_trade_size_5m"]),
            "median_avg_size_vs_session": _safe_median(sub["avg_size_vs_session"]),
        }
        for h in HORIZONS:
            hit_col = f"{hit_prefix}_{h}m"
            rate_col = (
                f"persist_rate_{h}m" if hit_prefix == "persist_hit" else f"stay_in_band_rate_{h}m"
            )
            row[rate_col] = _safe_mean(sub[hit_col].astype(float)) if hit_col in sub.columns else float("nan")
        rows.append(row)
    return rows


def _episode_downsample(df: pd.DataFrame, every: int = 15) -> pd.DataFrame:
    """Reduce minute autocorrelation for episode-ish counts (every N minutes)."""
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        parts.append(day.iloc[::every])
    return pd.concat(parts).sort_index() if parts else df.iloc[0:0]


def evaluate_gate(holdout_stats: pd.DataFrame, trader_holdout: pd.DataFrame, baseline: dict[str, float]) -> dict[str, Any]:
    """Decision gate aligned with plan: n>=50 and ~+8-10pp lift or stable trader separation that predicts."""
    blended = holdout_stats[holdout_stats["regime_col"] == "regime_blended"].copy()
    trend = blended[blended["regime"].isin(["TREND_UP", "TREND_DOWN"])]
    rng = blended[blended["regime"] == "RANGE"]

    # Pool TREND_UP/DOWN for persistence
    trend_n = int(trend["n"].sum()) if not trend.empty else 0
    range_n = int(rng["n"].sum()) if not rng.empty else 0

    def _wavg(frame: pd.DataFrame, col: str) -> float:
        if frame.empty or frame["n"].sum() == 0:
            return float("nan")
        return float(np.average(frame[col].fillna(0.0), weights=frame["n"].clip(lower=1)))

    persist_30_trend = _wavg(trend, "persist_rate_30m")
    persist_30_base = float(baseline.get("persist_rate_30m", np.nan))
    stay_30_range = _wavg(rng, "stay_in_band_rate_30m")
    stay_30_base = float(baseline.get("stay_in_band_rate_30m", np.nan))

    persist_lift = (persist_30_trend - persist_30_base) * 100.0 if np.isfinite(persist_30_trend) and np.isfinite(persist_30_base) else float("nan")
    stay_lift = (stay_30_range - stay_30_base) * 100.0 if np.isfinite(stay_30_range) and np.isfinite(stay_30_base) else float("nan")

    # Trader claim (updated): RANGE has more block/large prints than TREND;
    # effort remains secondary context.
    t_claim = trader_holdout[trader_holdout["bucket"].isin(["TREND", "RANGE"])].set_index("bucket")
    effort_sep = False
    block_sep = False
    size_sep = False
    if {"TREND", "RANGE"}.issubset(t_claim.index):
        effort_sep = float(t_claim.loc["TREND", "median_effort_vs_session"]) < float(
            t_claim.loc["RANGE", "median_effort_vs_session"]
        )
        block_sep = float(t_claim.loc["RANGE", "median_large_share_vs_session"]) > float(
            t_claim.loc["TREND", "median_large_share_vs_session"]
        )
        size_sep = float(t_claim.loc["RANGE", "median_avg_size_vs_session"]) > float(
            t_claim.loc["TREND", "median_avg_size_vs_session"]
        )

    # Predictive overlay lift
    overlay = trader_holdout.set_index("bucket")
    structure_persist = float(overlay.loc["structure_trend_only", "persist_rate_30m"]) if "structure_trend_only" in overlay.index else float("nan")
    trader_persist = float(overlay.loc["trader_trend_support", "persist_rate_30m"]) if "trader_trend_support" in overlay.index else float("nan")
    trader_n = int(overlay.loc["trader_trend_support", "n"]) if "trader_trend_support" in overlay.index else 0
    overlay_lift = (trader_persist - structure_persist) * 100.0 if np.isfinite(trader_persist) and np.isfinite(structure_persist) else float("nan")

    # Also score range-fight stay-in-band lift (block-trade conditioned)
    structure_stay = float(overlay.loc["structure_range_only", "stay_in_band_rate_30m"]) if "structure_range_only" in overlay.index else float("nan")
    fight_stay = float(overlay.loc["trader_range_fight", "stay_in_band_rate_30m"]) if "trader_range_fight" in overlay.index else float("nan")
    fight_n = int(overlay.loc["trader_range_fight", "n"]) if "trader_range_fight" in overlay.index else 0
    fight_lift = (fight_stay - structure_stay) * 100.0 if np.isfinite(fight_stay) and np.isfinite(structure_stay) else float("nan")

    structure_ok = (
        trend_n >= 50
        and range_n >= 50
        and (
            (np.isfinite(persist_lift) and persist_lift >= 8.0)
            or (np.isfinite(stay_lift) and stay_lift >= 8.0)
        )
    )
    trader_sep_ok = bool(block_sep or size_sep)
    trader_ok = trader_sep_ok and (
        (trader_n >= 50 and np.isfinite(overlay_lift) and overlay_lift >= 8.0)
        or (fight_n >= 50 and np.isfinite(fight_lift) and fight_lift >= 8.0)
    )
    passed = bool(structure_ok or trader_ok)

    return {
        "passed": passed,
        "structure_ok": bool(structure_ok),
        "trader_ok": bool(trader_ok),
        "holdout_trend_n": trend_n,
        "holdout_range_n": range_n,
        "persist_rate_30m_trend": persist_30_trend,
        "persist_rate_30m_baseline": persist_30_base,
        "persist_lift_pp": persist_lift,
        "stay_in_band_30m_range": stay_30_range,
        "stay_in_band_30m_baseline": stay_30_base,
        "stay_lift_pp": stay_lift,
        "trader_effort_sep_ok": bool(effort_sep),
        "trader_block_sep_ok": bool(block_sep),
        "trader_avg_size_sep_ok": bool(size_sep),
        "trader_trend_support_n": trader_n,
        "trader_vs_structure_persist_lift_pp": overlay_lift,
        "trader_range_fight_n": fight_n,
        "trader_range_fight_stay_lift_pp": fight_lift,
        "gate_rules": {
            "min_n": 50,
            "min_lift_pp": 8.0,
            "pass_if": "structure persistence/stay lift OR block-trade separation + overlay lift",
            "trader_proxy": "large_trade_share / avg_trade_size (block prints), not tape color",
        },
        "recommendation": (
            "PASS: build Aggression V2.1 only after explicit user approval"
            if passed
            else "FAIL: do not build V2.1; keep research artifacts and leave V2 untouched"
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    features = _load_features(Path(args.features))
    annotated = annotate_trend_range(features, effort_window=args.effort_window)
    labeled = _add_forward_outcomes(annotated)

    # Drop warmup unknowns for scoring
    scored = labeled[labeled["regime_blended"] != "UNKNOWN"].copy()
    dates = sorted(scored["et_date"].dropna().unique().tolist())
    train_dates, holdout_dates = _chronological_split(dates, train_frac=args.train_frac)
    train = scored[scored["et_date"].isin(train_dates)]
    holdout = scored[scored["et_date"].isin(holdout_dates)]

    # Episode-ish downsample for gate n counts (still report full-minute stats too)
    holdout_ep = _episode_downsample(holdout, every=args.episode_every)
    train_ep = _episode_downsample(train, every=args.episode_every)

    regime_rows = []
    for split_name, frame in (("train", train), ("holdout", holdout), ("holdout_episode", holdout_ep), ("train_episode", train_ep)):
        regime_rows.extend(_regime_stats(frame, "regime", split_name))
        regime_rows.extend(_regime_stats(frame, "regime_blended", split_name))
    regime_df = pd.DataFrame(regime_rows)

    trader_rows = []
    for split_name, frame in (("train", train), ("holdout", holdout), ("holdout_episode", holdout_ep)):
        trader_rows.extend(_trader_claim_table(frame, split_name))
    trader_df = pd.DataFrame(trader_rows)

    baseline_holdout = _baseline_chance(holdout)
    baseline_holdout_ep = _baseline_chance(holdout_ep)

    gate = evaluate_gate(
        regime_df[regime_df["split"] == "holdout_episode"],
        trader_df[trader_df["split"] == "holdout_episode"],
        baseline_holdout_ep,
    )
    gate["baseline_holdout_minute"] = baseline_holdout
    gate["baseline_holdout_episode"] = baseline_holdout_ep
    gate["train_dates"] = train_dates
    gate["holdout_dates"] = holdout_dates
    gate["n_train_minutes"] = int(len(train))
    gate["n_holdout_minutes"] = int(len(holdout))
    gate["n_holdout_episodes"] = int(len(holdout_ep))

    # Artifacts
    regime_path = output_dir / "trend_range_regime_stats.csv"
    trader_path = output_dir / "trend_range_trader_claim.csv"
    sample_path = output_dir / "trend_range_annotated_sample.csv"
    summary_path = output_dir / "trend_range_summary.json"
    gate_path = output_dir / "trend_range_gate.json"

    regime_df.to_csv(regime_path, index=False)
    trader_df.to_csv(trader_path, index=False)
    scored.tail(25000).to_csv(sample_path, index=True)
    gate_path.write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")

    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "features": str(Path(args.features)),
        "rows_scored": int(len(scored)),
        "dates": dates,
        "train_dates": train_dates,
        "holdout_dates": holdout_dates,
        "gate": gate,
        "artifacts": {
            "regime_stats": str(regime_path),
            "trader_claim": str(trader_path),
            "annotated_sample": str(sample_path),
            "summary": str(summary_path),
            "gate": str(gate_path),
        },
        "notes": [
            "Structure labels are primary; large_trade_share/avg_trade_size test the trader block-print claim.",
            "Holdout is chronological by et_date (last ~30%).",
            "Episode downsample (default every 15m) reduces minute autocorrelation for gate n.",
            "V2 dashboard code was not modified.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Trend vs range + trader effort research (V2.1 gate).")
    parser.add_argument("--features", default=str(FEATURE_MATRIX))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--effort-window", type=int, default=5)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--episode-every", type=int, default=15)
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps({
        "ok": summary["ok"],
        "rows_scored": summary["rows_scored"],
        "holdout_dates": summary["holdout_dates"][:3] + (["..."] if len(summary["holdout_dates"]) > 3 else []),
        "n_holdout_dates": len(summary["holdout_dates"]),
        "gate": summary["gate"],
        "artifacts": summary["artifacts"],
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
