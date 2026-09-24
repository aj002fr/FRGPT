#!/usr/bin/env python3
"""Research-only V1 flow–dispersion foundations study (H1–H10).

Tests whether TotalAggress–fair-value dispersion (width, duration, shape, speed)
plus flow and book-depth/passive proxies predict CONTINUED_RANGE vs BREAKOUT.

Writes only under workspace/analysis/aggression_v1_dispersion_foundations/.
Does not touch live V1/V2/V3 or retune V1.2.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import (  # noqa: E402
    benjamini_hochberg,
    bootstrap_ci,
    one_sided_p,
)
from scripts.aggression_v1_flow_price_dispersion_research import (  # noqa: E402
    EPISODE_STRIDE_MIN,
    EVENTS_DB,
    FEATURE_MATRIX,
    MIN_POINTS_OLS,
    TICK_SIZE,
    _event_times,
    _near_event,
    _ols_alpha_beta,
    _session_expanding_z,
    add_dispersion_dynamics,
    chronological_splits,
    fit_causal_v1_residual,
    load_features,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_dispersion_foundations"
TY_TICKS_PER_BP = 2.0
HORIZONS = (15, 30, 60)
BREAKOUT_BPS_GRID = (4, 6, 8, 12)
K_GRID = (0.5, 1.0, 1.5)
RTH_MINUTES = 420.0
PRIMARY = {"h": 30, "B": 8.0, "k": 1.0}
N_BOOT = 200
EASTERN = "America/New_York"
RTH_START = 8 * 60
RTH_END = 15 * 60

GATE_CONFIGS = [
    {"name": "very_loose", "min_n": 20, "min_dates": 3, "min_lift_pp": 2.0, "require_ci": False, "require_bh": False, "require_dates": True},
    {"name": "loose", "min_n": 30, "min_dates": 5, "min_lift_pp": 3.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "moderate", "min_n": 40, "min_dates": 6, "min_lift_pp": 5.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "standard", "min_n": 50, "min_dates": 8, "min_lift_pp": 5.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "strict", "min_n": 50, "min_dates": 8, "min_lift_pp": 8.0, "require_ci": True, "require_bh": False, "require_dates": True},
    {"name": "restrictive", "min_n": 50, "min_dates": 8, "min_lift_pp": 8.0, "require_ci": True, "require_bh": True, "require_dates": True},
]

HYPOTHESIS_CATALOG: dict[str, Any] = {
    "H1_width": {
        "hypothesis": "Larger |dispersion| → more resolution (breakout) or more chop",
        "thresholds": ["|z|>=0.5/1/1.5/2", "|z|<0.5", "|z| quintiles"],
    },
    "H2_duration": {
        "hypothesis": "Long-lived large residual → breakout vs fade",
        "thresholds": ["age 0-5/5-15/15-30/>=30", "persist>=10/20"],
    },
    "H3_shape": {
        "hypothesis": "Widening → range; narrowing → breakout; flat → neither",
        "thresholds": ["widen/narrow 5m/15m", "flat |Δz| 15m near zero"],
    },
    "H4_speed": {
        "hypothesis": "Fast residual change → breakout",
        "thresholds": ["|Δresid| 5m/15m p70+/p30-", "accel up/down × large"],
    },
    "H5_flow_in_episode": {
        "hypothesis": "Aggressive flow intensity during |z|>=1 predicts breakout",
        "thresholds": ["|agg_5m|/agg_rate/trade_intensity terciles while large"],
    },
    "H6_flow_sign": {
        "hypothesis": "Flow agreeing with residual sign → continuation; disagree → range",
        "thresholds": ["agree/disagree", "large×agree/disagree"],
    },
    "H7_passive_depth": {
        "hypothesis": "Thick depth while large → absorption/range; thin/trade≫depth → breakout",
        "thresholds": ["displayed_depth/size_imbalance/trade_size_vs_depth terciles × large"],
    },
    "H8_microstructure": {
        "hypothesis": "Wide spread / microprice stretch with dispersion → breakout risk",
        "thresholds": ["spread/microprice_dev terciles × large"],
    },
    "H9_combos": {
        "hypothesis": "Persist+fast+thin-depth generalizes soft-B4 theme",
        "thresholds": ["4-6 pre-registered combos only"],
    },
    "H10_fv_parity": {
        "hypothesis": "Live-style vs causal residual disagree → noise / different edge (diagnostic)",
        "thresholds": ["sign disagree", "|z_live-z_causal| high"],
        "primary": False,
    },
}


def fast_decluster(df: pd.DataFrame, mask: pd.Series, stride_min: int = EPISODE_STRIDE_MIN) -> pd.DataFrame:
    m = mask.reindex(df.index).fillna(False).to_numpy()
    if not m.any():
        return df.iloc[0:0].copy()
    sub = df.loc[m].sort_index()
    if sub.empty:
        return sub
    dates = sub["et_date"].to_numpy()
    minutes = sub.index.asi8 // (60 * 10**9)
    keep = np.zeros(len(sub), dtype=bool)
    last_t = None
    last_d = None
    for i in range(len(sub)):
        if last_t is None or dates[i] != last_d or (minutes[i] - last_t) >= stride_min:
            keep[i] = True
            last_t = int(minutes[i])
            last_d = dates[i]
    return sub.iloc[keep]


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def _is_rth(ts: pd.Timestamp) -> bool:
    et = ts.tz_convert(EASTERN)
    return RTH_START <= (et.hour * 60 + et.minute) < RTH_END


def prior_day_rth_atr_ticks(df: pd.DataFrame) -> pd.Series:
    rth = df[df.index.map(_is_rth)]
    if rth.empty:
        return pd.Series(np.nan, index=df.index)
    daily = rth.groupby("et_date")["price"].agg(["min", "max"])
    daily["atr_ticks"] = (daily["max"] - daily["min"]) / TICK_SIZE
    dates = sorted(daily.index.tolist())
    prior = {dates[i]: float(daily.loc[dates[i - 1], "atr_ticks"]) for i in range(1, len(dates))}
    return df["et_date"].map(prior).astype(float)


def add_forward_metrics(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        price = d["price"].astype(float)
        for h in HORIZONS:
            future = price.shift(-h)
            net = (future - price) / TICK_SIZE
            fwd_high = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).max().iloc[::-1]
            fwd_low = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).min().iloc[::-1]
            d[f"fwd_net_ticks_{h}m"] = net
            d[f"fwd_abs_net_ticks_{h}m"] = net.abs()
            d[f"fwd_range_ticks_{h}m"] = (fwd_high - fwd_low) / TICK_SIZE
            d[f"fwd_breakout_up_{h}m"] = (net >= 0).astype(float)
            if len(d) >= h:
                cols = [
                    f"fwd_net_ticks_{h}m",
                    f"fwd_abs_net_ticks_{h}m",
                    f"fwd_range_ticks_{h}m",
                    f"fwd_breakout_up_{h}m",
                ]
                d.loc[d.index[-h]:, cols] = np.nan
        parts.append(d)
    out = pd.concat(parts).sort_index()
    out["prior_atr_ticks"] = prior_day_rth_atr_ticks(out)
    for h in HORIZONS:
        out[f"atr_unit_{h}m"] = out["prior_atr_ticks"] * float(np.sqrt(h / RTH_MINUTES))
    return out


def fit_live_style_residual(df: pd.DataFrame) -> pd.DataFrame:
    """Same-day expanding OLS on price_delta ~ total_aggress (live-style diagnostic)."""
    out = df.copy()
    out["live_residual"] = np.nan
    out["live_residual_z"] = np.nan
    parts: list[pd.DataFrame] = []
    for et_date in sorted(out["et_date"].dropna().unique().tolist()):
        day = out[out["et_date"] == et_date].sort_index().copy()
        residuals: list[float] = []
        for i in range(len(day)):
            prior = day.iloc[:i]
            fit = _ols_alpha_beta(prior["total_aggress"], prior["price_delta"])
            if fit is None:
                residuals.append(np.nan)
                continue
            alpha, beta = fit
            pred = float(day["day_open_price"].iloc[i]) + alpha + beta * float(day["total_aggress"].iloc[i])
            residuals.append(float(day["price"].iloc[i]) - pred)
        day["live_residual"] = residuals
        day["live_residual_z"] = _session_expanding_z(
            pd.to_numeric(day["live_residual"], errors="coerce"),
            min_periods=MIN_POINTS_OLS,
        )
        parts.append(day)
    if not parts:
        return out
    return pd.concat(parts).sort_index()


def _expanding_q(series: pd.Series, dates: pd.Series, q: float, min_periods: int = 20) -> pd.Series:
    return series.groupby(dates, sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=min_periods).quantile(q)
    )


def _tercile_masks(
    series: pd.Series,
    dates: pd.Series,
    prefix: str,
    condition: pd.Series | None = None,
) -> dict[str, pd.Series]:
    lo = _expanding_q(series, dates, 0.33)
    hi = _expanding_q(series, dates, 0.67)
    base = series.notna() & lo.notna() & hi.notna()
    if condition is not None:
        base = base & condition.fillna(False)
    return {
        f"{prefix}_lo": base & (series <= lo),
        f"{prefix}_mid": base & (series > lo) & (series < hi),
        f"{prefix}_hi": base & (series >= hi),
    }


def build_hypothesis_masks(df: pd.DataFrame) -> dict[str, tuple[str, pd.Series]]:
    """Return factor -> (hypothesis_family, mask)."""
    abs_z = pd.to_numeric(df["v1_residual_z"], errors="coerce").abs()
    age = pd.to_numeric(df.get("residual_large_age_min"), errors="coerce").fillna(0.0)
    chg5 = pd.to_numeric(df.get("abs_residual_chg_5m"), errors="coerce")
    chg15 = pd.to_numeric(df.get("abs_residual_chg_15m"), errors="coerce")
    dates = df["et_date"]
    large = abs_z >= 1.0

    fast5 = _expanding_q(chg5, dates, 0.70)
    slow5 = _expanding_q(chg5, dates, 0.30)
    fast15 = _expanding_q(chg15, dates, 0.70)
    slow15 = _expanding_q(chg15, dates, 0.30)
    is_fast5 = (chg5 >= fast5).fillna(False)
    is_slow5 = (chg5 <= slow5).fillna(False)
    is_fast15 = (chg15 >= fast15).fillna(False)
    is_slow15 = (chg15 <= slow15).fillna(False)

    widen15 = df.get("residual_widening_15m", pd.Series(False, index=df.index)).fillna(False)
    widen5 = df.get("residual_widening_5m", pd.Series(False, index=df.index)).fillna(False)
    narrow15 = (~widen15) & abs_z.notna()
    narrow5 = (~widen5) & abs_z.notna()
    dz15 = abs_z.diff(15).abs()
    flat_thr = _expanding_q(dz15, dates, 0.33)
    flat15 = (dz15 <= flat_thr).fillna(False) & abs_z.notna()

    agree = df.get("flow_price_agree_15m", pd.Series(False, index=df.index)).fillna(False)
    disagree = df.get("flow_price_disagree_15m", pd.Series(False, index=df.index)).fillna(False)
    accel = pd.to_numeric(df.get("residual_accel_5m"), errors="coerce").fillna(0.0)

    out: dict[str, tuple[str, pd.Series]] = {}

    for thr, name in ((0.5, "z_ge_0_5"), (1.0, "z_ge_1"), (1.5, "z_ge_1_5"), (2.0, "z_ge_2")):
        out[name] = ("H1_width", abs_z >= thr)
    out["z_small"] = ("H1_width", abs_z < 0.5)
    z_rank = abs_z.groupby(dates).transform(lambda s: s.rank(pct=True))
    for q_lo, q_hi, name in (
        (0.0, 0.2, "zq0_20"),
        (0.2, 0.4, "zq20_40"),
        (0.4, 0.6, "zq40_60"),
        (0.6, 0.8, "zq60_80"),
        (0.8, 1.01, "zq80_100"),
    ):
        out[name] = ("H1_width", (z_rank > q_lo) & (z_rank <= q_hi))

    out["age_0_5"] = ("H2_duration", large & (age >= 0) & (age < 5))
    out["age_5_15"] = ("H2_duration", (age >= 5) & (age < 15))
    out["age_15_30"] = ("H2_duration", (age >= 15) & (age < 30))
    out["age_ge_30"] = ("H2_duration", age >= 30)
    out["large_persist_10"] = ("H2_duration", large & (age >= 10))
    out["large_persist_20"] = ("H2_duration", large & (age >= 20))

    out["widening_15m"] = ("H3_shape", widen15)
    out["narrowing_15m"] = ("H3_shape", narrow15)
    out["widening_5m"] = ("H3_shape", widen5)
    out["narrowing_5m"] = ("H3_shape", narrow5)
    out["flat_15m"] = ("H3_shape", flat15)
    out["large_widening"] = ("H3_shape", large & widen15)
    out["large_narrowing"] = ("H3_shape", large & narrow15)
    out["large_flat"] = ("H3_shape", large & flat15)

    out["fast_5m"] = ("H4_speed", is_fast5)
    out["slow_5m"] = ("H4_speed", is_slow5)
    out["fast_15m"] = ("H4_speed", is_fast15)
    out["slow_15m"] = ("H4_speed", is_slow15)
    out["large_fast_5m"] = ("H4_speed", large & is_fast5)
    out["large_fast_15m"] = ("H4_speed", large & is_fast15)
    out["accel_up_large"] = ("H4_speed", large & (accel > 0))
    out["accel_down_large"] = ("H4_speed", large & (accel < 0))

    for col, prefix in (
        ("agg_5m", "abs_agg5"),
        ("agg_rate", "agg_rate"),
        ("trade_intensity", "trade_int"),
    ):
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce").abs() if col == "agg_5m" else pd.to_numeric(df[col], errors="coerce")
        for name, mask in _tercile_masks(s, dates, f"H5_{prefix}", condition=large).items():
            out[name] = ("H5_flow_in_episode", mask)

    out["agree"] = ("H6_flow_sign", agree)
    out["disagree"] = ("H6_flow_sign", disagree)
    out["large_agree"] = ("H6_flow_sign", large & agree)
    out["large_disagree"] = ("H6_flow_sign", large & disagree)

    for col, prefix in (
        ("displayed_depth_mean", "depth"),
        ("size_imbalance_mean", "imbalance"),
        ("trade_size_vs_depth", "trade_vs_depth"),
        ("depth_adjusted_signed_flow", "depth_adj_flow"),
    ):
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if col in ("size_imbalance_mean", "depth_adjusted_signed_flow"):
            s = s.abs()
        for name, mask in _tercile_masks(s, dates, f"H7_{prefix}", condition=large).items():
            out[name] = ("H7_passive_depth", mask)

    for col, prefix in (
        ("spread_mean", "spread"),
        ("microprice_dev_mean", "micro_dev"),
    ):
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if "micro" in col:
            s = s.abs()
        for name, mask in _tercile_masks(s, dates, f"H8_{prefix}", condition=large).items():
            out[name] = ("H8_microstructure", mask)

    thin_depth = out.get("H7_depth_lo", ("", pd.Series(False, index=df.index)))[1]
    thick_depth = out.get("H7_depth_hi", ("", pd.Series(False, index=df.index)))[1]
    trade_gt_depth = out.get("H7_trade_vs_depth_hi", ("", pd.Series(False, index=df.index)))[1]
    out["combo_persist20_fast15"] = ("H9_combos", large & (age >= 20) & is_fast15)
    out["combo_persist10_fast15"] = ("H9_combos", large & (age >= 10) & is_fast15)
    out["combo_persist20_thin_depth"] = ("H9_combos", large & (age >= 20) & thin_depth)
    out["combo_fast15_thin_depth"] = ("H9_combos", large & is_fast15 & thin_depth)
    out["combo_widen_thick_depth"] = ("H9_combos", large & widen15 & thick_depth)
    out["combo_persist_fast_thin"] = ("H9_combos", large & (age >= 10) & is_fast15 & thin_depth)
    out["combo_z15_trade_gt_depth"] = ("H9_combos", (abs_z >= 1.5) & trade_gt_depth)

    if "live_residual_z" in df.columns:
        live_z = pd.to_numeric(df["live_residual_z"], errors="coerce")
        causal_z = pd.to_numeric(df["v1_residual_z"], errors="coerce")
        sign_disagree = (np.sign(live_z.fillna(0)) != np.sign(causal_z.fillna(0))) & live_z.notna() & causal_z.notna()
        z_gap = (live_z - causal_z).abs()
        gap_hi = _expanding_q(z_gap, dates, 0.70)
        out["fv_sign_disagree"] = ("H10_fv_parity", sign_disagree)
        out["fv_z_gap_hi"] = ("H10_fv_parity", (z_gap >= gap_hi).fillna(False))
        out["fv_agree_large"] = ("H10_fv_parity", large & (~sign_disagree) & live_z.notna())

    return out


def evaluate_cell(
    episodes: pd.DataFrame,
    *,
    cell_id: str,
    family: str,
    hypothesis: str,
    split_name: str,
    outcome_col: str,
    baseline_rate: float,
    h: int,
    B: float,
    k: float,
    factor: str,
    outcome: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cell_id": cell_id,
        "family": family,
        "hypothesis": hypothesis,
        "split": split_name,
        "horizon_m": h,
        "breakout_bps": B,
        "k": k,
        "factor": factor,
        "outcome": outcome,
        "n": 0,
        "n_dates": 0,
        "rate": np.nan,
        "baseline_rate": baseline_rate,
        "lift_pp": np.nan,
        "ci_low": np.nan,
        "ci_high": np.nan,
        "one_sided_lower": np.nan,
        "p_one_sided": np.nan,
        "breakout_up_share": np.nan,
    }
    if episodes.empty or outcome_col not in episodes:
        return base
    y = episodes[outcome_col].astype(float)
    n = int(len(episodes))
    base["n"] = n
    base["n_dates"] = int(episodes["et_date"].nunique())
    rate = float(y.mean()) if n else np.nan
    base["rate"] = rate
    lift = rate - baseline_rate if np.isfinite(rate) and np.isfinite(baseline_rate) else np.nan
    base["lift_pp"] = float(lift * 100.0) if np.isfinite(lift) else np.nan
    if n < 8 or split_name != "validation":
        return base
    groups = episodes["et_date"].astype(str).tolist()
    lift_values = (y - baseline_rate).to_numpy()
    lift_ci = bootstrap_ci(
        lift_values,
        n_boot=N_BOOT,
        seed=(abs(hash(cell_id)) + 13) % (2**31 - 1),
        groups=groups,
    )
    base["ci_low"] = float(lift_ci["ci_low"]) if lift_ci["n"] else np.nan
    base["ci_high"] = float(lift_ci["ci_high"]) if lift_ci["n"] else np.nan
    base["one_sided_lower"] = float(lift_ci["one_sided_lower"]) if lift_ci["n"] else np.nan
    base["p_one_sided"] = float(one_sided_p(lift_values))
    if outcome == "BREAKOUT" and f"fwd_breakout_up_{h}m" in episodes.columns:
        bo = episodes[episodes[outcome_col].astype(bool)]
        if len(bo) >= 8:
            base["breakout_up_share"] = float(bo[f"fwd_breakout_up_{h}m"].astype(float).mean())
    return base


def apply_gate(row: pd.Series, cfg: dict[str, Any]) -> str:
    n_ok = int(row.get("n", 0) or 0) >= cfg["min_n"]
    dates_ok = (not cfg["require_dates"]) or (int(row.get("n_dates", 0) or 0) >= cfg["min_dates"])
    lift = row.get("lift_pp")
    lift_ok = np.isfinite(lift) and float(lift) >= cfg["min_lift_pp"]
    ci = row.get("one_sided_lower")
    ci_ok = (not cfg["require_ci"]) or (np.isfinite(ci) and float(ci) > 0)
    bh_ok = (not cfg["require_bh"]) or bool(row.get("bh_significant", False))
    if n_ok and dates_ok and lift_ok and ci_ok and bh_ok:
        return "PASS"
    if n_ok and lift_ok and (ci_ok or bool(row.get("bh_significant", False))):
        return "PROVISIONAL"
    return "FAIL"


def label_config(df: pd.DataFrame, h: int, B: float, k: float) -> pd.DataFrame:
    abs_net = df[f"fwd_abs_net_ticks_{h}m"]
    fwd_range = df[f"fwd_range_ticks_{h}m"]
    atr_unit = df[f"atr_unit_{h}m"]
    event = df["event_near"].fillna(False)
    breakout_ticks = B * TY_TICKS_PER_BP
    breakout = (abs_net >= breakout_ticks) & (~event) & abs_net.notna()
    continued = (fwd_range <= (k * atr_unit)) & (~breakout) & fwd_range.notna() & atr_unit.notna() & (atr_unit > 0)
    label = pd.Series("OTHER", index=df.index, dtype=object)
    label = label.mask(continued, "CONTINUED_RANGE")
    label = label.mask(breakout, "BREAKOUT")
    label = label.mask(abs_net.isna() | fwd_range.isna(), np.nan)
    out = df.copy()
    out["future_regime_atr"] = label
    out["future_is_breakout"] = label == "BREAKOUT"
    out["future_is_continued_range"] = label == "CONTINUED_RANGE"
    return out


def regime_mix_row(df: pd.DataFrame, dates: list[str], h: int, B: float, k: float, split: str) -> dict[str, Any]:
    sub = df[df["et_date"].isin(set(dates)) & ~df["event_near"].fillna(False)]
    counts = sub["future_regime_atr"].value_counts(dropna=False).to_dict()
    return {
        "split": split,
        "horizon_m": h,
        "breakout_bps": B,
        "k": k,
        "n_rows": int(len(sub)),
        "n_dates": int(sub["et_date"].nunique()),
        "n_breakout": int(counts.get("BREAKOUT", 0)),
        "n_continued_range": int(counts.get("CONTINUED_RANGE", 0)),
        "n_other": int(counts.get("OTHER", 0)),
        "breakout_share": float(sub["future_is_breakout"].mean()) if len(sub) else np.nan,
        "range_share": float(sub["future_is_continued_range"].mean()) if len(sub) else np.nan,
    }


def run_split_all_configs(
    panel: pd.DataFrame,
    masks: dict[str, tuple[str, pd.Series]],
    dates: list[str],
    split_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nonevent = panel[panel["et_date"].isin(set(dates)) & ~panel["event_near"].fillna(False)].copy()
    for name, (_hyp, mask) in masks.items():
        nonevent[f"mask_{name}"] = mask.reindex(nonevent.index).fillna(False)

    journal: list[dict[str, Any]] = []
    mixes: list[dict[str, Any]] = []
    configs = [(h, float(B), float(k)) for h in HORIZONS for B in BREAKOUT_BPS_GRID for k in K_GRID]
    for i, (h, B, k) in enumerate(configs, start=1):
        labeled = label_config(nonevent, h, B, k)
        mixes.append(regime_mix_row(labeled, dates, h, B, k, split_name))
        extreme = labeled[labeled["future_regime_atr"].isin(["BREAKOUT", "CONTINUED_RANGE"])].copy()
        if extreme.empty:
            if i % 10 == 0:
                print(f"  {split_name} {i}/{len(configs)} (empty extreme)", flush=True)
            continue
        base_bo_ext = float(extreme["future_is_breakout"].mean())
        base_cr_ext = float(extreme["future_is_continued_range"].mean())
        base_bo_all = float(labeled["future_is_breakout"].mean())
        base_cr_all = float(labeled["future_is_continued_range"].mean())

        for factor, (hypothesis, _mask) in masks.items():
            mask_col = f"mask_{factor}"
            ep_ext = fast_decluster(extreme, extreme[mask_col])
            for outcome, col, base_rate, fam_suffix in (
                ("BREAKOUT", "future_is_breakout", base_bo_ext, "breakout_extreme"),
                ("CONTINUED_RANGE", "future_is_continued_range", base_cr_ext, "range_extreme"),
            ):
                journal.append(
                    evaluate_cell(
                        ep_ext,
                        cell_id=f"h{h}_B{B}_k{k}_{factor}_{fam_suffix}",
                        family=f"{hypothesis}_{fam_suffix}",
                        hypothesis=hypothesis,
                        split_name=split_name,
                        outcome_col=col,
                        baseline_rate=base_rate,
                        h=h,
                        B=B,
                        k=k,
                        factor=factor,
                        outcome=outcome,
                    )
                )
            ep_all = fast_decluster(labeled, labeled[mask_col])
            for outcome, col, base_rate, fam_suffix in (
                ("BREAKOUT", "future_is_breakout", base_bo_all, "breakout_all"),
                ("CONTINUED_RANGE", "future_is_continued_range", base_cr_all, "range_all"),
            ):
                journal.append(
                    evaluate_cell(
                        ep_all,
                        cell_id=f"h{h}_B{B}_k{k}_{factor}_{fam_suffix}",
                        family=f"{hypothesis}_{fam_suffix}",
                        hypothesis=hypothesis,
                        split_name=split_name,
                        outcome_col=col,
                        baseline_rate=base_rate,
                        h=h,
                        B=B,
                        k=k,
                        factor=factor,
                        outcome=outcome,
                    )
                )
        if i % 5 == 0 or i == len(configs):
            print(f"  {split_name} configs {i}/{len(configs)}; journal+= {len(journal)}", flush=True)
    return journal, mixes


def add_fdr_by_family(val: pd.DataFrame) -> pd.DataFrame:
    out = val.copy()
    out["bh_adjusted_p"] = np.nan
    out["bh_significant"] = False
    if out.empty:
        return out
    pieces = []
    keys = ["hypothesis", "horizon_m", "breakout_bps", "k", "family"]
    for _, part in out.groupby(keys, sort=False):
        p = part.copy()
        mask = p["p_one_sided"].notna()
        if mask.any():
            bh = benjamini_hochberg(p.loc[mask, "p_one_sided"].to_numpy(dtype=float), q=0.10)
            p.loc[mask, "bh_adjusted_p"] = bh["adjusted_p"]
            p.loc[mask, "bh_significant"] = bh["reject"]
        pieces.append(p)
    return pd.concat(pieces, ignore_index=True)


def gate_sweep(val: pd.DataFrame, out_dir: Path) -> pd.DataFrame:
    gate_dir = out_dir / "gate_threshold_sweep"
    gate_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for cfg in GATE_CONFIGS:
        g = val.copy()
        g["gate"] = g.apply(lambda r, c=cfg: apply_gate(r, c), axis=1)
        counts = g["gate"].value_counts().to_dict()
        summary.append(
            {
                **cfg,
                "n_PASS": int(counts.get("PASS", 0)),
                "n_PROVISIONAL": int(counts.get("PROVISIONAL", 0)),
                "n_FAIL": int(counts.get("FAIL", 0)),
            }
        )
        g.sort_values("lift_pp", ascending=False).to_csv(gate_dir / f"validation_{cfg['name']}.csv", index=False)
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(gate_dir / "gate_config_summary.csv", index=False)
    return summary_df


def factor_stability(val: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if val.empty:
        return pd.DataFrame()
    for (factor, outcome, hypothesis, family), part in val.groupby(
        ["factor", "outcome", "hypothesis", "family"], sort=False
    ):
        pos = part[part["lift_pp"] > 0]
        rows.append(
            {
                "factor": factor,
                "outcome": outcome,
                "hypothesis": hypothesis,
                "family": family,
                "n_configs": int(part[["horizon_m", "breakout_bps", "k"]].drop_duplicates().shape[0]),
                "n_positive_lift": int(pos[["horizon_m", "breakout_bps", "k"]].drop_duplicates().shape[0]),
                "median_lift_pp": float(part["lift_pp"].median()) if part["lift_pp"].notna().any() else np.nan,
                "max_lift_pp": float(part["lift_pp"].max()) if part["lift_pp"].notna().any() else np.nan,
                "best_cell_id": str(part.sort_values("lift_pp", ascending=False).iloc[0]["cell_id"]) if len(part) else "",
            }
        )
    return pd.DataFrame(rows).sort_values(["n_positive_lift", "median_lift_pp"], ascending=[False, False])


def write_by_family(val: pd.DataFrame, out_dir: Path) -> None:
    fam_dir = out_dir / "validation_by_family"
    fam_dir.mkdir(parents=True, exist_ok=True)
    for hyp, part in val.groupby("hypothesis", sort=False):
        part.sort_values("lift_pp", ascending=False).to_csv(fam_dir / f"{hyp}.csv", index=False)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(OUTPUT_DIR / "hypothesis_catalog.json", HYPOTHESIS_CATALOG)

    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    features["event_near"] = _near_event(features.index, _event_times(EVENTS_DB))
    print("Fitting causal residual + dynamics + live-style residual...", flush=True)
    panel = fit_causal_v1_residual(features)
    panel = add_dispersion_dynamics(panel)
    panel = fit_live_style_residual(panel)
    panel = add_forward_metrics(panel)
    splits = chronological_splits(meta["clean_dates"])

    print("Building hypothesis masks...", flush=True)
    masks = build_hypothesis_masks(panel)
    n_configs = len(HORIZONS) * len(BREAKOUT_BPS_GRID) * len(K_GRID)
    hyp_counts = pd.Series([h for h, _ in masks.values()]).value_counts().to_dict()
    print(f"Factors={len(masks)}; configs={n_configs}; by_hyp={hyp_counts}", flush=True)

    journal: list[dict[str, Any]] = []
    mix_rows: list[dict[str, Any]] = []
    for split_name in ("discovery", "validation"):
        print(f"Running {split_name}...", flush=True)
        j, m = run_split_all_configs(panel, masks, splits[split_name], split_name)
        journal.extend(j)
        mix_rows.extend(m)

    journal_df = pd.DataFrame(journal)
    mix_df = pd.DataFrame(mix_rows)
    mix_df.to_csv(OUTPUT_DIR / "regime_mix_grid.csv", index=False)
    journal_df.to_csv(OUTPUT_DIR / "candidate_journal.csv", index=False)

    val = journal_df[journal_df["split"] == "validation"].copy()
    val = add_fdr_by_family(val)
    val.sort_values("lift_pp", ascending=False).to_csv(OUTPUT_DIR / "validation_all_raw.csv", index=False)
    val.sort_values("lift_pp", ascending=False).to_csv(OUTPUT_DIR / "validation_leaderboard.csv", index=False)
    write_by_family(val, OUTPUT_DIR)

    gate_summary = gate_sweep(val, OUTPUT_DIR)
    stability = factor_stability(val)
    stability.to_csv(OUTPUT_DIR / "factor_stability.csv", index=False)

    primary = val[
        (val["horizon_m"] == PRIMARY["h"])
        & (val["breakout_bps"] == PRIMARY["B"])
        & (val["k"] == PRIMARY["k"])
    ].sort_values("lift_pp", ascending=False)

    cfg = next(c for c in GATE_CONFIGS if c["name"] == "restrictive")
    g = val.copy()
    g["gate"] = g.apply(lambda r, c=cfg: apply_gate(r, c), axis=1)
    restrictive_pass = g[g["gate"] == "PASS"]
    primary_restrictive = restrictive_pass[
        (restrictive_pass["horizon_m"] == PRIMARY["h"])
        & (restrictive_pass["breakout_bps"] == PRIMARY["B"])
        & (restrictive_pass["k"] == PRIMARY["k"])
    ]

    hyp_primary = []
    for hyp, part in primary.groupby("hypothesis", sort=False):
        top = part.sort_values("lift_pp", ascending=False).iloc[0]
        hyp_primary.append(
            {
                "hypothesis": hyp,
                "n_cells": int(len(part)),
                "max_lift_pp": float(part["lift_pp"].max()) if part["lift_pp"].notna().any() else np.nan,
                "n_bh_sig": int(part["bh_significant"].fillna(False).sum()),
                "best_factor": str(top["factor"]),
                "best_outcome": str(top["outcome"]),
            }
        )

    open_final = int(len(primary_restrictive)) > 0
    final_note = (
        "FINAL inspected: primary restrictive PASS exists."
        if open_final
        else "FINAL locked: no primary restrictive PASS."
    )

    summary = {
        "ok": True,
        "primary_config": PRIMARY,
        "definitions": {
            "BREAKOUT": "|net_h| >= B yield-bps and not event_near",
            "CONTINUED_RANGE": "range_h <= k * prior_day_RTH_ATR * sqrt(h/420) and not BREAKOUT",
            "ty_ticks_per_bp": TY_TICKS_PER_BP,
            "passive_proxy": "displayed_depth / size_imbalance / trade_size_vs_depth (not true resting identity)",
        },
        "n_journal_rows": int(len(journal_df)),
        "n_validation_rows": int(len(val)),
        "n_factors": int(len(masks)),
        "n_label_configs": int(n_configs),
        "hypothesis_counts": hyp_counts,
        "gate_summary": gate_summary.to_dict(orient="records"),
        "restrictive_pass_n": int(len(restrictive_pass)),
        "primary_restrictive_pass_n": int(len(primary_restrictive)),
        "restrictive_pass": restrictive_pass.sort_values("lift_pp", ascending=False).head(30).to_dict(orient="records"),
        "primary_restrictive_pass": primary_restrictive.to_dict(orient="records"),
        "primary_top_validation": primary.head(40).to_dict(orient="records"),
        "primary_by_hypothesis": hyp_primary,
        "regime_mix_primary": mix_df[
            (mix_df["horizon_m"] == PRIMARY["h"])
            & (mix_df["breakout_bps"] == PRIMARY["B"])
            & (mix_df["k"] == PRIMARY["k"])
        ].to_dict(orient="records"),
        "stability_top": stability.head(40).to_dict(orient="records") if len(stability) else [],
        "final_opened": open_final,
        "artifacts": {
            "catalog": str(OUTPUT_DIR / "hypothesis_catalog.json"),
            "journal": str(OUTPUT_DIR / "candidate_journal.csv"),
            "validation_all_raw": str(OUTPUT_DIR / "validation_all_raw.csv"),
            "regime_mix": str(OUTPUT_DIR / "regime_mix_grid.csv"),
            "gate_summary": str(OUTPUT_DIR / "gate_threshold_sweep" / "gate_config_summary.csv"),
            "stability": str(OUTPUT_DIR / "factor_stability.csv"),
            "by_family": str(OUTPUT_DIR / "validation_by_family"),
        },
        "notes": [
            "ALL cells retained regardless of gate.",
            "Flexible gates are exploratory only; only restrictive PASS on primary unlocks FINAL.",
            final_note,
            "Soft B=4 PASSes are exploratory and do not authorize V1.2 retune.",
        ],
    }
    _write_json(OUTPUT_DIR / "split_manifest.json", splits)
    _write_json(OUTPUT_DIR / "summary.json", summary)
    print(
        json.dumps(
            {
                "ok": True,
                "n_journal": len(journal_df),
                "restrictive_pass_n": int(len(restrictive_pass)),
                "primary_restrictive_pass_n": int(len(primary_restrictive)),
                "final_opened": open_final,
                "gate_summary": summary["gate_summary"],
            },
            indent=2,
            default=_json_default,
        )
    )


if __name__ == "__main__":
    main()
