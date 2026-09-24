"""Display-only provisional caution context for isolated V1.2.

Guardrail: pure computation only. No V1/V2/V3 app imports, no I/O, no
long/short emission.

Surfaces three frozen research annotations:
1. RANGE caution — large + widening residual (path-efficiency provisional cell)
2. Soft B=4 breakout lean — restrictive PASS factors from the factor×ATR sweep
3. LIFO friendly — |z|≥1 + low trade intensity → continued-range lean
   (foundations H5; validation restrictive PASS; FINAL CI failed — not shippable PnL)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

TICK_SIZE = 1.0 / 64.0
EPS = 1e-12
LARGE_ABS_Z = 1.0
SOFT_Z_GE = 1.5
WIDENING_WINDOW_MIN = 15
HORIZON_MIN = 30
MIN_POINTS_OLS = 30
DECLUSTER_STRIDE_MIN = 15
PERSIST_AGE_10 = 10.0
PERSIST_AGE_20 = 20.0
AGE_GE_30 = 30.0
FAST_EXPANDING_MIN = 20
FAST_QUANTILE = 0.70
TRADE_INT_LO_QUANTILE = 0.33

LABEL = "PROVISIONAL / RANGE-CAUTION / NON-DIRECTIONAL"
SOFT_BREAKOUT_LABEL = "PROVISIONAL / SOFT-BREAKOUT-LEAN / B4-ONLY / NON-DIRECTIONAL"
LIFO_FRIENDLY_LABEL = "PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL"
STRATEGY_STATUS = "PROVISIONAL / RESEARCH-ONLY"

# Frozen discovery cutoffs from aggression_v1_flow_price_dispersion/regime_cutoffs.json
REGIME_CUTOFFS: dict[str, float] = {
    "horizon": 30.0,
    "trend_eff_q": 0.16666666666666666,
    "range_eff_q": 0.06666666666666667,
    "trend_disp_q": 0.6666666666666666,
    "range_disp_q": 0.3333333333333333,
    "trend_abs_net_q": 2.0,
    "range_abs_net_q": 1.0,
}

RESEARCH_CONTEXT: dict[str, Any] = {
    "cell_id": "state_large_widening_range",
    "validation_lift_pp": 8.131844184146008,
    "discovery_lift_pp": -0.009421,
    "validation_range_rate": 0.62,
    "validation_baseline_range_rate": 0.5386815581585399,
    "bh_fdr_pass": False,
    "gate": "PROVISIONAL",
    "directional": False,
    "note": (
        "Validation showed +8pp RANGE lift on path-efficiency regimes; discovery was flat "
        "and BH-FDR failed. Under ATR + 8bps breakout labels, large+widening had no useful "
        "edge. Observational monitor only — not a trade signal."
    ),
}

# Restrictive PASSes from aggression_v1_dispersion_factor_breakout (2026-07-16).
# Explicit: all 18 PASSes were BREAKOUT at soft B=4 bps only; primary (30,8,1) = 0 PASS.
SOFT_BREAKOUT_RESEARCH: dict[str, Any] = {
    "source_doc": "docs/aggression_v1_dispersion_factor_breakout_experiments.md",
    "artifact_dir": "workspace/analysis/aggression_v1_dispersion_factor_breakout/",
    "primary_config": {"h": 30, "B": 8.0, "k": 1.0},
    "primary_restrictive_pass_n": 0,
    "restrictive_pass_n": 18,
    "pass_breakout_bps": 4.0,
    "pass_configs": [
        {"h": 30, "B": 4.0, "k": 0.5},
        {"h": 60, "B": 4.0, "k": 0.5},
        {"h": 60, "B": 4.0, "k": 1.0},
    ],
    "pass_factors": [
        "large_persist_10",
        "large_persist_20",
        "age_ge_30",
        "fast_15m",
        "fast_5m",
        "z_ge_1_5",
        "z_ge_0_5",
        "zq80_100",
        "same_sign_streak_hi",
        "narrowing",
        "large_narrowing",
    ],
    "display_fire_rule": (
        "SOFT_BREAKOUT_LEAN when large_persist_10 OR large_persist_20 OR age_ge_30 "
        "OR (z_ge_1_5 AND fast_15m)"
    ),
    "gate": "RESTRICTIVE_PASS_ON_B4_ONLY",
    "directional": False,
    "note": (
        "Explicit: restrictive gate passed only for soft 4bps non-event breakouts "
        "(mostly k=0.5 ATR filter). Primary headline (30m, 8bps, k=1) had 0 PASS. "
        "Display-only soft-breakout lean — not long/short, not an 8bps signal."
    ),
}

# Foundations H5: low trade intensity while |z|>=1 → CONTINUED_RANGE (primary 30/8/1).
LIFO_FRIENDLY_RESEARCH: dict[str, Any] = {
    "source_doc": "docs/aggression_v1_dispersion_foundations_experiments.md",
    "cell_id": "H5_trade_int_lo_range_all",
    "primary_config": {"h": 30, "B": 8.0, "k": 1.0},
    "discovery_lift_pp": 7.806939,
    "validation_lift_pp": 10.637269544295414,
    "final_lift_pp": 8.147740663085512,
    "final_ci_clears": False,
    "validation_restrictive_pass": True,
    "gate": "VALIDATION_RESTRICTIVE_PASS_FINAL_CI_FAIL",
    "directional": False,
    "display_fire_rule": (
        "LIFO_FRIENDLY when |v1_residual_z| >= 1 AND trade_intensity <= session "
        "expanding p33 (causal)"
    ),
    "meaning": (
        "Environment lean toward ATR continued-range — friendlier for LIFO / "
        "mean-reversion style participation; not a long/short entry."
    ),
    "note": (
        "Validation cleared restrictive on primary ATR/bps CONTINUED_RANGE; FINAL "
        "point estimate held (+8pp) but one-sided CI failed. Display alert only — "
        "not executable PnL authorization."
    ),
}


def _ols_alpha_beta(x: pd.Series, y: pd.Series) -> tuple[float, float] | None:
    sample = pd.DataFrame({"x": x.astype(float), "y": y.astype(float)}).dropna()
    if len(sample) < MIN_POINTS_OLS or sample["x"].nunique() < 2:
        return None
    x_arr = sample["x"].to_numpy()
    y_arr = sample["y"].to_numpy()
    x_mean = float(x_arr.mean())
    y_mean = float(y_arr.mean())
    denom = float(np.sum((x_arr - x_mean) ** 2))
    if denom <= EPS:
        return None
    beta = float(np.sum((x_arr - x_mean) * (y_arr - y_mean)) / denom)
    alpha = y_mean - beta * x_mean
    return alpha, beta


def _session_expanding_z(series: pd.Series, min_periods: int = 30) -> pd.Series:
    hist = series.shift(1)
    mean = hist.expanding(min_periods=min_periods).mean()
    std = hist.expanding(min_periods=min_periods).std(ddof=1).replace(0.0, np.nan)
    return (series - mean) / std


def fit_causal_v1_residual(df: pd.DataFrame) -> pd.DataFrame:
    """Prior-day OLS residual; first day uses expanding same-day prior minutes."""
    out = df.copy()
    required = {"et_date", "price", "total_aggress", "day_open_price"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"fit_causal_v1_residual missing columns: {sorted(missing)}")
    out["price_delta"] = out["price"].astype(float) - out["day_open_price"].astype(float)
    out["v1_alpha"] = np.nan
    out["v1_beta"] = np.nan
    out["v1_predicted"] = np.nan
    out["v1_residual"] = np.nan
    out["v1_residual_z"] = np.nan

    hist_x: list[pd.Series] = []
    hist_y: list[pd.Series] = []
    parts: list[pd.DataFrame] = []
    for et_date in sorted(out["et_date"].dropna().unique().tolist()):
        day = out[out["et_date"] == et_date].sort_index().copy()
        params = _ols_alpha_beta(pd.concat(hist_x), pd.concat(hist_y)) if hist_x else None
        if params is None:
            alphas: list[float] = []
            betas: list[float] = []
            preds: list[float] = []
            residuals: list[float] = []
            for i in range(len(day)):
                prior = day.iloc[:i]
                fit = _ols_alpha_beta(prior["total_aggress"], prior["price_delta"])
                if fit is None:
                    alphas.append(np.nan)
                    betas.append(np.nan)
                    preds.append(np.nan)
                    residuals.append(np.nan)
                    continue
                alpha, beta = fit
                pred = float(day["day_open_price"].iloc[i]) + alpha + beta * float(day["total_aggress"].iloc[i])
                resid = float(day["price"].iloc[i]) - pred
                alphas.append(alpha)
                betas.append(beta)
                preds.append(pred)
                residuals.append(resid)
            day["v1_alpha"] = alphas
            day["v1_beta"] = betas
            day["v1_predicted"] = preds
            day["v1_residual"] = residuals
        else:
            alpha, beta = params
            day["v1_alpha"] = alpha
            day["v1_beta"] = beta
            day["v1_predicted"] = day["day_open_price"] + alpha + beta * day["total_aggress"]
            day["v1_residual"] = day["price"] - day["v1_predicted"]
        resid = pd.to_numeric(day["v1_residual"], errors="coerce")
        day["v1_residual_z"] = _session_expanding_z(resid, min_periods=MIN_POINTS_OLS)
        parts.append(day)
        hist_x.append(day["total_aggress"])
        hist_y.append(day["price_delta"])
    if not parts:
        return out
    return pd.concat(parts).sort_index()


def annotate_caution_flags(df: pd.DataFrame) -> pd.DataFrame:
    """Add RANGE-caution and soft B=4 breakout-lean factor columns."""
    out = df.copy()
    residual = pd.to_numeric(out.get("v1_residual"), errors="coerce")
    residual_z = pd.to_numeric(out.get("v1_residual_z"), errors="coerce")
    abs_z = residual_z.abs()
    abs_resid = residual.abs()
    out["abs_v1_residual_z"] = abs_z
    out["abs_v1_residual"] = abs_resid
    out["residual_widening_15m"] = abs_resid > abs_resid.shift(WIDENING_WINDOW_MIN)
    out["large_residual"] = abs_z >= LARGE_ABS_Z
    out["cautious_range"] = out["large_residual"].fillna(False) & out["residual_widening_15m"].fillna(False)

    # Soft-breakout dynamics (match factor-sweep definitions where practical).
    out["abs_residual_chg_15m"] = residual.diff(WIDENING_WINDOW_MIN).abs()
    large_thr = (
        abs_resid.groupby(out["et_date"], sort=False).transform(
            lambda s: s.shift(1).expanding(min_periods=FAST_EXPANDING_MIN).quantile(FAST_QUANTILE)
        )
        if "et_date" in out
        else pd.Series(np.nan, index=out.index)
    )
    large_state = abs_resid >= large_thr.fillna(np.inf)
    age: list[float] = []
    current = 0
    prev_date = None
    dates = out["et_date"] if "et_date" in out else pd.Series([None] * len(out), index=out.index)
    for et_date, is_large in zip(dates, large_state.fillna(False)):
        if et_date != prev_date:
            current = 0
            prev_date = et_date
        current = current + 1 if bool(is_large) else 0
        age.append(float(current))
    out["residual_large_age_min"] = age

    fast15_thr = (
        out["abs_residual_chg_15m"]
        .groupby(out["et_date"], sort=False)
        .transform(
            lambda s: s.shift(1).expanding(min_periods=FAST_EXPANDING_MIN).quantile(FAST_QUANTILE)
        )
        if "et_date" in out
        else pd.Series(np.nan, index=out.index)
    )
    out["z_ge_1_5"] = abs_z >= SOFT_Z_GE
    out["fast_15m"] = (out["abs_residual_chg_15m"] >= fast15_thr).fillna(False)
    out["large_persist_10"] = out["large_residual"].fillna(False) & (
        out["residual_large_age_min"] >= PERSIST_AGE_10
    )
    out["large_persist_20"] = out["large_residual"].fillna(False) & (
        out["residual_large_age_min"] >= PERSIST_AGE_20
    )
    out["age_ge_30"] = out["residual_large_age_min"] >= AGE_GE_30
    out["soft_breakout_lean"] = (
        out["large_persist_10"]
        | out["large_persist_20"]
        | out["age_ge_30"]
        | (out["z_ge_1_5"].fillna(False) & out["fast_15m"])
    )

    # LIFO friendly: large residual + low trade intensity (foundations H5).
    if "trade_intensity" not in out.columns:
        out["trade_intensity"] = np.nan
        out["trade_int_lo"] = False
        out["lifo_friendly"] = False
    else:
        trade_int = pd.to_numeric(out["trade_intensity"], errors="coerce")
        out["trade_intensity"] = trade_int
        lo_thr = (
            trade_int.groupby(out["et_date"], sort=False).transform(
                lambda s: s.shift(1).expanding(min_periods=FAST_EXPANDING_MIN).quantile(TRADE_INT_LO_QUANTILE)
            )
            if "et_date" in out
            else pd.Series(np.nan, index=out.index)
        )
        out["trade_int_lo"] = (trade_int <= lo_thr).fillna(False)
        out["lifo_friendly"] = out["large_residual"].fillna(False) & out["trade_int_lo"]
    return out


def _active_soft_factors(row: pd.Series) -> list[str]:
    flags = (
        ("large_persist_10", bool(row.get("large_persist_10", False))),
        ("large_persist_20", bool(row.get("large_persist_20", False))),
        ("age_ge_30", bool(row.get("age_ge_30", False))),
        ("z_ge_1_5", bool(row.get("z_ge_1_5", False))),
        ("fast_15m", bool(row.get("fast_15m", False))),
    )
    return [name for name, on in flags if on]


def _empty_context() -> dict[str, Any]:
    return {
        "state": "NORMAL",
        "ready": False,
        "residual": None,
        "residual_z": None,
        "abs_residual_z": None,
        "widening_15m": False,
        "large": False,
        "ts_utc": None,
        "et_date": None,
        "price": None,
        "thresholds": {
            "large_abs_z": LARGE_ABS_Z,
            "widening_window_min": WIDENING_WINDOW_MIN,
            "horizon_min": HORIZON_MIN,
            "decluster_stride_min": DECLUSTER_STRIDE_MIN,
        },
        "research_context": dict(RESEARCH_CONTEXT),
        "label": LABEL,
        "strategy_status": STRATEGY_STATUS,
        "non_directional": True,
        "reasons": [],
        "soft_breakout": {
            "state": "NORMAL",
            "active": False,
            "active_factors": [],
            "residual_large_age_min": None,
            "fast_15m": False,
            "z_ge_1_5": False,
            "large_persist_10": False,
            "large_persist_20": False,
            "age_ge_30": False,
            "label": SOFT_BREAKOUT_LABEL,
            "research_context": dict(SOFT_BREAKOUT_RESEARCH),
            "non_directional": True,
            "reasons": [],
        },
        "lifo_friendly": {
            "state": "NORMAL",
            "active": False,
            "trade_intensity": None,
            "trade_int_lo": False,
            "large": False,
            "label": LIFO_FRIENDLY_LABEL,
            "research_context": dict(LIFO_FRIENDLY_RESEARCH),
            "non_directional": True,
            "alert_text": "",
            "reasons": [],
        },
    }


def latest_caution_context(df: pd.DataFrame) -> dict[str, Any]:
    """Return the latest-bar RANGE-caution + soft B=4 breakout-lean annotation."""
    if df.empty or "cautious_range" not in df:
        return _empty_context()
    row = df.dropna(subset=["v1_residual_z"]).iloc[-1] if "v1_residual_z" in df else df.iloc[-1]
    large = bool(row.get("large_residual", False))
    widening = bool(row.get("residual_widening_15m", False))
    cautious = bool(row.get("cautious_range", False))
    soft = bool(row.get("soft_breakout_lean", False))
    soft_factors = _active_soft_factors(row)
    lifo = bool(row.get("lifo_friendly", False))
    trade_int_lo = bool(row.get("trade_int_lo", False))
    trade_int_val = row.get("trade_intensity", np.nan)
    reasons: list[str] = []
    if large:
        reasons.append(f"|residual_z| >= {LARGE_ABS_Z}")
    if widening:
        reasons.append(f"abs(residual) widened over {WIDENING_WINDOW_MIN}m")
    if cautious:
        reasons.append("large + widening → CAUTIOUS_RANGE (provisional)")
    soft_reasons: list[str] = []
    if soft_factors:
        soft_reasons.append("active: " + ", ".join(soft_factors))
    if soft:
        soft_reasons.append(
            "soft B=4 restrictive-PASS theme → SOFT_BREAKOUT_LEAN (not 8bps; not directional)"
        )
    lifo_reasons: list[str] = []
    if large:
        lifo_reasons.append(f"|z| >= {LARGE_ABS_Z}")
    if trade_int_lo:
        lifo_reasons.append("trade intensity in low tercile (session expanding p33)")
    if lifo:
        lifo_reasons.append("LIFO FRIENDLY — range lean / mean-reversion environment (provisional)")
    age_val = row.get("residual_large_age_min", np.nan)
    ts = row.name
    return {
        "state": "CAUTIOUS_RANGE" if cautious else "NORMAL",
        "ready": bool(np.isfinite(row.get("v1_residual_z", np.nan))),
        "residual": float(row["v1_residual"]) if np.isfinite(row.get("v1_residual", np.nan)) else None,
        "residual_z": float(row["v1_residual_z"]) if np.isfinite(row.get("v1_residual_z", np.nan)) else None,
        "abs_residual_z": float(abs(row["v1_residual_z"])) if np.isfinite(row.get("v1_residual_z", np.nan)) else None,
        "widening_15m": widening,
        "large": large,
        "ts_utc": pd.Timestamp(ts).tz_convert("UTC").isoformat() if getattr(ts, "tzinfo", None) else str(ts),
        "et_date": str(row.get("et_date") or ""),
        "price": float(row["price"]) if np.isfinite(row.get("price", np.nan)) else None,
        "thresholds": {
            "large_abs_z": LARGE_ABS_Z,
            "widening_window_min": WIDENING_WINDOW_MIN,
            "horizon_min": HORIZON_MIN,
            "decluster_stride_min": DECLUSTER_STRIDE_MIN,
            "soft_breakout_z": SOFT_Z_GE,
            "persist_age_10": PERSIST_AGE_10,
            "persist_age_20": PERSIST_AGE_20,
            "age_ge_30": AGE_GE_30,
            "trade_int_lo_quantile": TRADE_INT_LO_QUANTILE,
        },
        "research_context": dict(RESEARCH_CONTEXT),
        "label": LABEL,
        "strategy_status": STRATEGY_STATUS,
        "non_directional": True,
        "reasons": reasons,
        "soft_breakout": {
            "state": "SOFT_BREAKOUT_LEAN" if soft else "NORMAL",
            "active": soft,
            "active_factors": soft_factors,
            "residual_large_age_min": float(age_val) if np.isfinite(age_val) else None,
            "fast_15m": bool(row.get("fast_15m", False)),
            "z_ge_1_5": bool(row.get("z_ge_1_5", False)),
            "large_persist_10": bool(row.get("large_persist_10", False)),
            "large_persist_20": bool(row.get("large_persist_20", False)),
            "age_ge_30": bool(row.get("age_ge_30", False)),
            "label": SOFT_BREAKOUT_LABEL,
            "research_context": dict(SOFT_BREAKOUT_RESEARCH),
            "non_directional": True,
            "reasons": soft_reasons,
        },
        "lifo_friendly": {
            "state": "LIFO_FRIENDLY" if lifo else "NORMAL",
            "active": lifo,
            "trade_intensity": float(trade_int_val) if np.isfinite(trade_int_val) else None,
            "trade_int_lo": trade_int_lo,
            "large": large,
            "label": LIFO_FRIENDLY_LABEL,
            "research_context": dict(LIFO_FRIENDLY_RESEARCH),
            "non_directional": True,
            "alert_text": "LIFO FRIENDLY" if lifo else "",
            "reasons": lifo_reasons,
        },
    }


def score_forward_regime(
    prices: pd.Series,
    lock_ts: pd.Timestamp,
    *,
    cutoffs: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Score RANGE/TREND/AMBIGUOUS/INCOMPLETE over the next HORIZON_MIN minutes."""
    cfg = dict(REGIME_CUTOFFS if cutoffs is None else cutoffs)
    h = int(cfg.get("horizon", HORIZON_MIN))
    if prices.empty:
        return {"outcome": "INCOMPLETE", "reason": "empty_prices"}
    series = prices.copy().sort_index()
    idx = pd.DatetimeIndex(series.index)
    series.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    lock = pd.Timestamp(lock_ts)
    lock = lock.tz_localize("UTC") if lock.tzinfo is None else lock.tz_convert("UTC")
    if lock not in series.index:
        # nearest prior bar
        prior = series.index[series.index <= lock]
        if len(prior) == 0:
            return {"outcome": "INCOMPLETE", "reason": "lock_before_series"}
        lock = prior[-1]
    end = lock + pd.Timedelta(minutes=h)
    window = series.loc[lock:end]
    if len(window) < h + 1:
        return {
            "outcome": "INCOMPLETE",
            "reason": "insufficient_forward_bars",
            "n_bars": int(len(window)),
            "needed": h + 1,
        }
    # Use exactly h steps ahead from lock.
    path = window.iloc[: h + 1].astype(float)
    net_ticks = float((path.iloc[-1] - path.iloc[0]) / TICK_SIZE)
    abs_net = abs(net_ticks)
    diffs = path.diff().dropna()
    path_ticks = float(diffs.abs().sum() / TICK_SIZE)
    range_ticks = float((path.max() - path.min()) / TICK_SIZE)
    path_eff = abs_net / max(path_ticks, EPS)
    disp = abs_net / max(range_ticks, EPS)
    trend = (
        path_eff >= float(cfg["trend_eff_q"])
        and disp >= float(cfg["trend_disp_q"])
        and abs_net >= float(cfg["trend_abs_net_q"])
    )
    rng = (
        path_eff <= float(cfg["range_eff_q"])
        and disp <= float(cfg["range_disp_q"])
        and abs_net <= float(cfg["range_abs_net_q"])
    )
    if rng:
        outcome = "RANGE"
    elif trend and net_ticks > 0:
        outcome = "TREND_UP"
    elif trend and net_ticks < 0:
        outcome = "TREND_DOWN"
    else:
        outcome = "AMBIGUOUS"
    return {
        "outcome": outcome,
        "net_ticks": net_ticks,
        "abs_net_ticks": abs_net,
        "path_ticks": path_ticks,
        "range_ticks": range_ticks,
        "path_eff": path_eff,
        "disp_to_range": disp,
        "n_bars": int(len(path)),
        "lock_ts_utc": lock.isoformat(),
        "end_ts_utc": path.index[-1].isoformat(),
        "cutoffs": cfg,
    }


def decluster_fire_mask(mask: pd.Series, stride_min: int = DECLUSTER_STRIDE_MIN) -> pd.Series:
    """Keep True only at the start of each de-clustered episode."""
    out = pd.Series(False, index=mask.index)
    last_ts: pd.Timestamp | None = None
    for ts, flag in mask.fillna(False).items():
        if not flag:
            continue
        ts_u = pd.Timestamp(ts)
        if last_ts is None or (ts_u - last_ts) >= pd.Timedelta(minutes=stride_min):
            out.loc[ts] = True
            last_ts = ts_u
    return out
