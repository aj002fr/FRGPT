#!/usr/bin/env python3
"""Research-only V1-style flow–price dispersion → RANGE/TREND study.

Guardrail: does NOT import, write to, or restart V1/V2/V3 apps. Reads the
existing TBBO feature matrix and writes only under
workspace/analysis/aggression_v1_flow_price_dispersion/. No dashboard, SOD,
Tailscale, port, or *_live/ changes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import (  # noqa: E402
    benjamini_hochberg,
    bootstrap_ci,
    one_sided_p,
)
from scripts.aggression_flow_effort import (  # noqa: E402
    FlowEffortConfig,
    annotate_flow_effort,
)
from scripts.aggression_trend_range_research import (  # noqa: E402
    annotate_trend_range,
)

FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v1_flow_price_dispersion"
TICK_SIZE = 1.0 / 64.0
EPS = 1e-12
EASTERN = "America/New_York"
HORIZONS = (5, 15, 30, 60)
WINDOWS = (1, 3, 5, 10, 15, 30, 60)
N_BOOT = 2_000
BH_Q = 0.10
MIN_N = 50
MIN_DATES = 8
MIN_LIFT_PP = 8.0
RTH_START = 8 * 60
RTH_END = 15 * 60
MIN_POINTS_OLS = 30
MIN_ROWS_PER_DAY = 400
EPISODE_STRIDE_MIN = 15


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


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


def _session_expanding_quantile(series: pd.Series, q: float, min_periods: int = 20) -> pd.Series:
    return series.shift(1).expanding(min_periods=min_periods).quantile(q)


def _is_rth(ts: pd.Timestamp) -> bool:
    if pd.isna(ts):
        return False
    et = ts.tz_convert(EASTERN)
    mins = et.hour * 60 + et.minute
    return RTH_START <= mins < RTH_END


def _event_times(path: Path) -> np.ndarray:
    if not path.exists():
        return np.array([], dtype="datetime64[ns]")
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            events = pd.read_sql_query(
                "SELECT event_date FROM economic_events "
                "WHERE country LIKE '%United States%' OR country='US' OR country IS NULL",
                conn,
            )
    except Exception:
        return np.array([], dtype="datetime64[ns]")
    values = []
    for raw in events.get("event_date", []):
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.isna(ts):
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT")
        else:
            ts = ts.tz_convert(EASTERN)
        if pd.notna(ts):
            values.append(ts.tz_convert("UTC").tz_localize(None))
    return np.asarray(sorted(set(values)), dtype="datetime64[ns]")


def _near_event(index: pd.DatetimeIndex, event_times: np.ndarray, minutes: int = 30) -> pd.Series:
    if not len(event_times):
        return pd.Series(False, index=index)
    values = pd.DatetimeIndex(index).tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    positions = np.searchsorted(event_times, values)
    out = np.zeros(len(values), dtype=bool)
    window = np.timedelta64(minutes, "m")
    for i, value in enumerate(values):
        if np.isnat(value):
            continue
        for j in (positions[i] - 1, positions[i]):
            if 0 <= j < len(event_times) and abs(value - event_times[j]) <= window:
                out[i] = True
                break
    return pd.Series(out, index=index)


def load_features(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    df = df.sort_values("bucket_ts").drop_duplicates("bucket_ts", keep="last")
    df["et_date"] = df["et_date"].astype(str)
    by_day = df.groupby("et_date").agg(
        rows=("price", "count"),
        price_min=("price", "min"),
        price_max=("price", "max"),
        price_std=("price", "std"),
    )
    clean = by_day[
        (by_day["rows"] >= MIN_ROWS_PER_DAY)
        & (by_day["price_min"] > 80)
        & (by_day["price_max"] < 150)
        & (by_day["price_std"] > 0.005)
    ].index.tolist()
    out = df[df["et_date"].isin(clean)].copy()
    out = out.set_index("bucket_ts").sort_index()
    return out, {
        "rows_raw": int(len(df)),
        "rows_clean": int(len(out)),
        "dates_raw": int(df["et_date"].nunique()),
        "dates_clean": int(out["et_date"].nunique()),
        "clean_dates": sorted(clean),
        "excluded_dates": [d for d in by_day.index.tolist() if d not in clean],
    }


def chronological_splits(dates: list[str]) -> dict[str, list[str]]:
    ordered = sorted(dates)
    if len(ordered) < 6:
        cut_final = max(1, len(ordered) // 5)
        research = ordered[:-cut_final] if cut_final else ordered
        final = ordered[-cut_final:] if cut_final else []
        cut_val = max(1, len(research) // 3) if len(research) >= 3 else 1
        return {
            "discovery": research[:-cut_val],
            "validation": research[-cut_val:],
            "final": final,
            "all": ordered,
        }
    n_final = max(4, int(np.ceil(len(ordered) * 0.20)))
    research = ordered[:-n_final]
    final = ordered[-n_final:]
    n_val = max(4, int(np.ceil(len(research) * 0.30)))
    return {
        "discovery": research[:-n_val],
        "validation": research[-n_val:],
        "final": final,
        "all": ordered,
    }


def fit_causal_v1_residual(df: pd.DataFrame) -> pd.DataFrame:
    """V1-style implied-price residual with prior-day OLS only (no same-day look-ahead)."""
    out = df.copy()
    out["total_aggress"] = out["agg_cum_daily"].astype(float)
    out["price_delta"] = out["price"].astype(float) - out["day_open_price"].astype(float)
    out["v1_alpha"] = np.nan
    out["v1_beta"] = np.nan
    out["v1_predicted"] = np.nan
    out["v1_residual"] = np.nan
    out["v1_residual_z"] = np.nan

    hist_x: list[pd.Series] = []
    hist_y: list[pd.Series] = []
    parts = []
    for et_date in sorted(out["et_date"].dropna().unique().tolist()):
        day = out[out["et_date"] == et_date].sort_index().copy()
        params = None
        if hist_x:
            params = _ols_alpha_beta(pd.concat(hist_x), pd.concat(hist_y))
        if params is None:
            # Warm start: expanding same-day fit using only prior minutes.
            alphas = []
            betas = []
            preds = []
            residuals = []
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
    return pd.concat(parts).sort_index()


def add_dispersion_dynamics(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    price = out["price"].astype(float)
    aggress = out["total_aggress"].astype(float)
    residual = out["v1_residual"].astype(float)
    out["delta_price_ticks"] = price.diff().fillna(0.0) / TICK_SIZE
    out["delta_aggress"] = aggress.diff().fillna(0.0)
    out["abs_v1_residual"] = residual.abs()
    out["abs_v1_residual_z"] = out["v1_residual_z"].abs()

    for w in WINDOWS:
        residual_chg = residual.diff(w)
        price_step = price.diff(w) / TICK_SIZE
        flow_step = aggress.diff(w)
        out[f"residual_chg_{w}m"] = residual_chg
        out[f"abs_residual_chg_{w}m"] = residual_chg.abs()
        out[f"residual_vel_{w}m"] = residual_chg / float(w)
        out[f"residual_accel_{w}m"] = residual_chg.diff(w)
        flow_scale = (
            flow_step.abs()
            .groupby(out["et_date"], sort=False)
            .transform(lambda s: s.shift(1).expanding(min_periods=10).median())
            .clip(lower=EPS)
        )
        price_scale = (
            price_step.abs()
            .groupby(out["et_date"], sort=False)
            .transform(lambda s: s.shift(1).expanding(min_periods=10).median())
            .clip(lower=EPS)
        )
        out[f"flow_price_gap_{w}m"] = (flow_step / flow_scale) - (price_step / price_scale)
        out[f"gap_widening_{w}m"] = out[f"flow_price_gap_{w}m"].diff(min(5, w)).fillna(0.0) > 0.0
        out[f"residual_widening_{w}m"] = residual_chg.abs() > residual.shift(w).abs()
        out[f"flow_leads_{w}m"] = (flow_step.abs() / flow_scale) > (price_step.abs() / price_scale) * 1.25
        out[f"price_leads_{w}m"] = (price_step.abs() / price_scale) > (flow_step.abs() / flow_scale) * 1.25
        agree = (np.sign(flow_step) == np.sign(price_step)) & (flow_step.abs() > EPS) & (price_step.abs() > EPS)
        out[f"flow_price_agree_{w}m"] = agree
        out[f"flow_price_disagree_{w}m"] = (
            (np.sign(flow_step) != np.sign(price_step))
            & (np.sign(flow_step) != 0)
            & (np.sign(price_step) != 0)
        )
        roll_flow = out["agg_1m"].astype(float).rolling(w, min_periods=1).sum().abs()
        roll_ticks = out["delta_price_ticks"].rolling(w, min_periods=1).sum().abs()
        out[f"effort_per_tick_{w}m"] = roll_flow / roll_ticks.clip(lower=1.0)

    # Persistence / age of large residual state using causal session quantile.
    large_thr = (
        out["abs_v1_residual"]
        .groupby(out["et_date"], sort=False)
        .transform(lambda s: s.shift(1).expanding(min_periods=20).quantile(0.70))
    )
    out["large_residual_state"] = out["abs_v1_residual"] >= large_thr.fillna(np.inf)
    age = []
    current = 0
    prev_date = None
    for et_date, is_large in zip(out["et_date"], out["large_residual_state"].fillna(False)):
        if et_date != prev_date:
            current = 0
            prev_date = et_date
        current = current + 1 if bool(is_large) else 0
        age.append(float(current))
    out["residual_large_age_min"] = age

    # Causal session percentile via prior expanding rank approximation (fast).
    for col in ("abs_v1_residual", "abs_residual_chg_5m", "abs_residual_chg_15m", "effort_per_tick_5m"):
        if col not in out:
            continue
        ranks = []
        for _, day in out.groupby("et_date", sort=False):
            s = day[col].astype(float)
            hist = s.shift(1)
            # Approximate percentile: compare to expanding mean/std -> normal CDF proxy, then clamp.
            mean = hist.expanding(min_periods=20).mean()
            std = hist.expanding(min_periods=20).std(ddof=1).replace(0.0, np.nan)
            z = (s - mean) / std
            # Map z to approx percentile with logistic; keep NaN early.
            pct = 1.0 / (1.0 + np.exp(-z))
            ranks.append(pct)
        out[f"{col}_pct"] = pd.concat(ranks).sort_index()
    return out


def add_forward_regime_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        price = d["price"].astype(float)
        abs_diff = price.diff().abs()
        signed_diff = price.diff()
        for h in HORIZONS:
            future = price.shift(-h)
            net = (future - price) / TICK_SIZE
            # Forward path length ~= sum of |diff| over next h steps.
            fwd_path = abs_diff.shift(-1).rolling(h, min_periods=h).sum().shift(-(h - 1)) / TICK_SIZE
            fwd_high = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).max().iloc[::-1]
            fwd_low = price.iloc[::-1].rolling(h + 1, min_periods=h + 1).min().iloc[::-1]
            fwd_range = (fwd_high - fwd_low) / TICK_SIZE
            fwd_rv = signed_diff.shift(-1).rolling(h, min_periods=h).std(ddof=1).shift(-(h - 1)) / TICK_SIZE
            # Reversal count / sign consistency approximated from forward signed diffs.
            fwd_sign = np.sign(signed_diff.shift(-1))
            sign_flip = (fwd_sign != fwd_sign.shift(-1)) & (fwd_sign != 0) & (fwd_sign.shift(-1) != 0)
            fwd_reversals = sign_flip.rolling(h, min_periods=h).sum().shift(-(h - 1))
            same_sign = (fwd_sign == np.sign(net)) & (fwd_sign != 0)
            fwd_sign_consistency = same_sign.rolling(h, min_periods=h).mean().shift(-(h - 1))
            d[f"fwd_net_ticks_{h}m"] = net
            d[f"fwd_abs_net_ticks_{h}m"] = net.abs()
            d[f"fwd_path_ticks_{h}m"] = fwd_path
            d[f"fwd_range_ticks_{h}m"] = fwd_range
            d[f"fwd_rv_{h}m"] = fwd_rv
            d[f"fwd_reversals_{h}m"] = fwd_reversals
            d[f"fwd_sign_consistency_{h}m"] = fwd_sign_consistency
            d[f"fwd_path_eff_{h}m"] = d[f"fwd_abs_net_ticks_{h}m"] / d[f"fwd_path_ticks_{h}m"].clip(lower=EPS)
            d[f"fwd_disp_to_range_{h}m"] = d[f"fwd_abs_net_ticks_{h}m"] / d[f"fwd_range_ticks_{h}m"].clip(lower=EPS)
            # Strict session-end censoring.
            if len(d) >= h:
                fwd_cols = [c for c in d.columns if c.startswith("fwd_") and c.endswith(f"_{h}m")]
                d.loc[d.index[-h]:, fwd_cols] = np.nan
        parts.append(d)
    return pd.concat(parts).sort_index()


def learn_regime_cutoffs(df: pd.DataFrame, discovery_dates: list[str], horizon: int = 30) -> dict[str, float]:
    disc = df[df["et_date"].isin(set(discovery_dates))].dropna(
        subset=[f"fwd_path_eff_{horizon}m", f"fwd_disp_to_range_{horizon}m", f"fwd_abs_net_ticks_{horizon}m"]
    )
    eff = disc[f"fwd_path_eff_{horizon}m"]
    disp = disc[f"fwd_disp_to_range_{horizon}m"]
    abs_net = disc[f"fwd_abs_net_ticks_{horizon}m"]
    return {
        "horizon": float(horizon),
        "trend_eff_q": float(eff.quantile(0.70)),
        "range_eff_q": float(eff.quantile(0.30)),
        "trend_disp_q": float(disp.quantile(0.70)),
        "range_disp_q": float(disp.quantile(0.30)),
        "trend_abs_net_q": float(abs_net.quantile(0.60)),
        "range_abs_net_q": float(abs_net.quantile(0.40)),
    }


def apply_regime_labels(df: pd.DataFrame, cutoffs: dict[str, float]) -> pd.DataFrame:
    out = df.copy()
    h = int(cutoffs["horizon"])
    eff = out[f"fwd_path_eff_{h}m"]
    disp = out[f"fwd_disp_to_range_{h}m"]
    abs_net = out[f"fwd_abs_net_ticks_{h}m"]
    net = out[f"fwd_net_ticks_{h}m"]
    trend = (eff >= cutoffs["trend_eff_q"]) & (disp >= cutoffs["trend_disp_q"]) & (abs_net >= cutoffs["trend_abs_net_q"])
    rng = (eff <= cutoffs["range_eff_q"]) & (disp <= cutoffs["range_disp_q"]) & (abs_net <= cutoffs["range_abs_net_q"])
    label = pd.Series("AMBIGUOUS", index=out.index, dtype=object)
    label = label.mask(rng, "RANGE")
    label = label.mask(trend & (net > 0), "TREND_UP")
    label = label.mask(trend & (net < 0), "TREND_DOWN")
    label = label.mask(eff.isna() | disp.isna() | abs_net.isna(), np.nan)
    out["future_regime"] = label
    out["future_is_trend"] = out["future_regime"].isin(["TREND_UP", "TREND_DOWN"])
    out["future_is_range"] = out["future_regime"] == "RANGE"
    out["future_is_directional"] = out["future_is_trend"]
    out["future_trend_sign"] = out["future_regime"].map({"TREND_UP": 1.0, "TREND_DOWN": -1.0})
    return out


def attach_benchmarks(df: pd.DataFrame) -> pd.DataFrame:
    """Attach existing structure / flow-effort ingredients as comparison-only features."""
    features = df.copy()
    model_df = pd.DataFrame(
        {
            "price": features["price"],
            "fair_value": features["v1_predicted"] if "v1_predicted" in features else features["price"],
            "residual": features["v1_residual"] if "v1_residual" in features else 0.0,
            "residual_z": features["v1_residual_z"] if "v1_residual_z" in features else 0.0,
        },
        index=features.index,
    )
    try:
        annotated = annotate_flow_effort(features, model_df, FlowEffortConfig())
        for col in (
            "effort_per_tick",
            "flow_price_gap",
            "gap_widening",
            "absorption",
            "in_range",
            "range_break",
            "false_break",
            "flow_price_aligned",
            "cross_tenor_aligned",
            "cross_tenor_disagrees",
            "high_effort",
        ):
            if col in annotated:
                features[f"bench_{col}"] = annotated[col]
    except Exception:
        pass
    try:
        structure = annotate_trend_range(features)
        if "regime_blended" in structure:
            features["bench_structure"] = structure["regime_blended"]
        if "path_efficiency_30" in structure:
            features["bench_path_eff_30"] = structure["path_efficiency_30"]
    except Exception:
        pass
    return features


def decluster_episodes(df: pd.DataFrame, mask: pd.Series, stride_min: int = EPISODE_STRIDE_MIN) -> pd.DataFrame:
    selected = df[mask.fillna(False)].copy()
    if selected.empty:
        return selected
    keep_idx = []
    last_ts = None
    last_date = None
    for ts, row in selected.sort_index().iterrows():
        et_date = row["et_date"]
        if last_ts is None or et_date != last_date or (ts - last_ts) >= pd.Timedelta(minutes=stride_min):
            keep_idx.append(ts)
            last_ts = ts
            last_date = et_date
    return selected.loc[keep_idx].copy()


def _rate(series: pd.Series) -> float:
    s = series.dropna()
    return float(s.mean()) if len(s) else np.nan


def evaluate_cell(
    episodes: pd.DataFrame,
    *,
    cell_id: str,
    family: str,
    split_name: str,
    outcome_col: str = "future_is_trend",
    baseline_rate: float | None = None,
) -> dict[str, Any]:
    if episodes.empty or outcome_col not in episodes:
        return {
            "cell_id": cell_id,
            "family": family,
            "split": split_name,
            "n": 0,
            "n_dates": 0,
            "rate": np.nan,
            "lift_pp": np.nan,
            "mean": np.nan,
            "ci_low": np.nan,
            "ci_high": np.nan,
            "one_sided_lower": np.nan,
            "p_one_sided": np.nan,
        }
    y = episodes[outcome_col].astype(float)
    groups = episodes["et_date"].astype(str).tolist()
    values = (y - (baseline_rate if baseline_rate is not None else 0.0)).to_numpy()
    # For rate lift, bootstrap the binary outcomes then subtract baseline.
    ci = bootstrap_ci(y.to_numpy(), n_boot=N_BOOT, seed=abs(hash(cell_id)) % (2**31 - 1), groups=groups)
    rate = float(ci["mean"]) if ci["n"] else np.nan
    lift = rate - baseline_rate if baseline_rate is not None and np.isfinite(rate) else np.nan
    lift_values = (y - baseline_rate).to_numpy() if baseline_rate is not None else y.to_numpy()
    lift_ci = bootstrap_ci(lift_values, n_boot=N_BOOT, seed=(abs(hash(cell_id)) + 17) % (2**31 - 1), groups=groups)
    p = one_sided_p(lift_values)
    return {
        "cell_id": cell_id,
        "family": family,
        "split": split_name,
        "n": int(len(episodes)),
        "n_dates": int(episodes["et_date"].nunique()),
        "rate": rate,
        "baseline_rate": baseline_rate,
        "lift_pp": float(lift * 100.0) if np.isfinite(lift) else np.nan,
        "mean": float(lift_ci["mean"]) if lift_ci["n"] else np.nan,
        "ci_low": float(lift_ci["ci_low"]) if lift_ci["n"] else np.nan,
        "ci_high": float(lift_ci["ci_high"]) if lift_ci["n"] else np.nan,
        "one_sided_lower": float(lift_ci["one_sided_lower"]) if lift_ci["n"] else np.nan,
        "p_one_sided": float(p) if np.isfinite(p) else np.nan,
    }


def build_state_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    abs_z = df["abs_v1_residual_z"]
    large = abs_z >= 1.0
    small = abs_z < 0.5
    widening = df.get("residual_widening_15m", pd.Series(False, index=df.index)).fillna(False)
    narrowing = ~widening & abs_z.notna()
    positive = df["v1_residual"] > 0
    negative = df["v1_residual"] < 0
    flow_leads = df.get("flow_leads_15m", pd.Series(False, index=df.index)).fillna(False)
    price_leads = df.get("price_leads_15m", pd.Series(False, index=df.index)).fillna(False)
    agree = df.get("flow_price_agree_15m", pd.Series(False, index=df.index)).fillna(False)
    disagree = df.get("flow_price_disagree_15m", pd.Series(False, index=df.index)).fillna(False)
    fast = df.get("abs_residual_chg_5m_pct", pd.Series(np.nan, index=df.index)) >= 0.70
    slow = df.get("abs_residual_chg_5m_pct", pd.Series(np.nan, index=df.index)) <= 0.30
    persistent = df["residual_large_age_min"] >= 10
    return {
        "large_residual": large,
        "small_residual": small,
        "widening": widening,
        "narrowing": narrowing,
        "positive_residual": positive,
        "negative_residual": negative,
        "large_widening": large & widening,
        "large_narrowing": large & narrowing,
        "large_positive": large & positive,
        "large_negative": large & negative,
        "flow_leads": flow_leads,
        "price_leads": price_leads,
        "agree": agree,
        "disagree": disagree,
        "large_flow_leads": large & flow_leads,
        "large_price_leads": large & price_leads,
        "large_disagree": large & disagree,
        "fast_change": fast.fillna(False),
        "slow_change": slow.fillna(False),
        "large_fast": large & fast.fillna(False),
        "large_persistent": large & persistent,
        "accel_up": (df.get("residual_accel_5m", pd.Series(0.0, index=df.index)).fillna(0.0) > 0) & large,
        "accel_down": (df.get("residual_accel_5m", pd.Series(0.0, index=df.index)).fillna(0.0) < 0) & large,
    }


def run_quantile_and_state_experiments(
    df: pd.DataFrame,
    dates: list[str],
    split_name: str,
    baseline_rate: float,
) -> list[dict[str, Any]]:
    subset = df[df["et_date"].isin(set(dates))].copy()
    subset = subset[subset["future_regime"].isin(["RANGE", "TREND_UP", "TREND_DOWN"])]
    rows: list[dict[str, Any]] = []
    # Monotonicity by residual magnitude deciles on discovery-defined thresholds.
    for q_lo, q_hi, label in ((0.0, 0.2, "q0_20"), (0.2, 0.4, "q20_40"), (0.4, 0.6, "q40_60"), (0.6, 0.8, "q60_80"), (0.8, 1.01, "q80_100")):
        pct = subset["abs_v1_residual"].rank(pct=True)
        mask = (pct > q_lo) & (pct <= q_hi)
        episodes = decluster_episodes(subset, mask)
        rows.append(
            evaluate_cell(
                episodes,
                cell_id=f"mono_abs_residual_{label}",
                family="monotonicity",
                split_name=split_name,
                outcome_col="future_is_trend",
                baseline_rate=baseline_rate,
            )
        )
        rows.append(
            evaluate_cell(
                episodes,
                cell_id=f"mono_abs_residual_range_{label}",
                family="monotonicity_range",
                split_name=split_name,
                outcome_col="future_is_range",
                baseline_rate=1.0 - baseline_rate if np.isfinite(baseline_rate) else np.nan,
            )
        )
    for name, mask in build_state_masks(subset).items():
        episodes = decluster_episodes(subset, mask)
        rows.append(
            evaluate_cell(
                episodes,
                cell_id=f"state_{name}_trend",
                family="state_grid",
                split_name=split_name,
                outcome_col="future_is_trend",
                baseline_rate=baseline_rate,
            )
        )
        rows.append(
            evaluate_cell(
                episodes,
                cell_id=f"state_{name}_range",
                family="state_grid_range",
                split_name=split_name,
                outcome_col="future_is_range",
                baseline_rate=1.0 - baseline_rate if np.isfinite(baseline_rate) else np.nan,
            )
        )
    # Interaction with benchmark structure / flow-effort when available.
    if "bench_structure" in subset:
        for structure in ("RANGE", "TREND_UP", "TREND_DOWN"):
            mask = subset["bench_structure"].eq(structure) & subset["abs_v1_residual_z"].ge(1.0)
            episodes = decluster_episodes(subset, mask)
            rows.append(
                evaluate_cell(
                    episodes,
                    cell_id=f"interact_large_x_{structure}_trend",
                    family="interaction",
                    split_name=split_name,
                    outcome_col="future_is_trend",
                    baseline_rate=baseline_rate,
                )
            )
    if "bench_gap_widening" in subset:
        mask = subset["bench_gap_widening"].fillna(False) & subset["abs_v1_residual_z"].ge(1.0)
        episodes = decluster_episodes(subset, mask)
        rows.append(
            evaluate_cell(
                episodes,
                cell_id="interact_large_x_bench_gap_widening_trend",
                family="interaction",
                split_name=split_name,
                outcome_col="future_is_trend",
                baseline_rate=baseline_rate,
            )
        )
    if "bench_high_effort" in subset:
        mask = subset["bench_high_effort"].fillna(False) & subset["abs_v1_residual_z"].ge(1.0)
        episodes = decluster_episodes(subset, mask)
        rows.append(
            evaluate_cell(
                episodes,
                cell_id="interact_large_x_bench_high_effort_trend",
                family="interaction",
                split_name=split_name,
                outcome_col="future_is_trend",
                baseline_rate=baseline_rate,
            )
        )
    return rows


def fit_incremental_models(
    df: pd.DataFrame,
    discovery: list[str],
    validation: list[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    usable = df[df["future_regime"].isin(["RANGE", "TREND_UP", "TREND_DOWN"])].copy()
    usable["y_trend"] = usable["future_is_trend"].astype(int)
    price_feats = ["mom_5m", "realized_vol", "minutes_since_open", "session_sin", "session_cos"]
    flow_feats = ["agg_5m", "agg_30m", "buysell_ratio", "agg_accel", "trade_intensity"]
    disp_feats = [
        "v1_residual",
        "v1_residual_z",
        "abs_v1_residual",
        "residual_chg_5m",
        "residual_chg_15m",
        "residual_vel_5m",
        "residual_accel_5m",
        "flow_price_gap_15m",
        "effort_per_tick_5m",
        "residual_large_age_min",
    ]
    specs = {
        "price_only": [c for c in price_feats if c in usable.columns],
        "price_flow": [c for c in price_feats + flow_feats if c in usable.columns],
        "price_dispersion": [c for c in price_feats + disp_feats if c in usable.columns],
        "full": [c for c in price_feats + flow_feats + disp_feats if c in usable.columns],
    }
    train = usable[usable["et_date"].isin(set(discovery))]
    test = usable[usable["et_date"].isin(set(validation))]
    metrics = []
    preds = test[["et_date"]].copy()
    preds["y_trend"] = test["y_trend"].to_numpy()
    for name, cols in specs.items():
        if len(cols) < 2 or train.empty or test.empty:
            continue
        model = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
            ]
        )
        y_train = train["y_trend"]
        if y_train.nunique() < 2:
            continue
        model.fit(train[cols].astype(float), y_train)
        proba = model.predict_proba(test[cols].astype(float))[:, 1]
        preds[f"proba_{name}"] = proba
        y_true = test["y_trend"].to_numpy()
        try:
            auc = float(roc_auc_score(y_true, proba))
        except ValueError:
            auc = np.nan
        try:
            ll = float(log_loss(y_true, proba, labels=[0, 1]))
        except ValueError:
            ll = np.nan
        try:
            brier = float(brier_score_loss(y_true, proba))
        except ValueError:
            brier = np.nan
        metrics.append(
            {
                "model": name,
                "n_train": int(len(train)),
                "n_test": int(len(test)),
                "n_features": int(len(cols)),
                "auc": auc,
                "log_loss": ll,
                "brier": brier,
                "features": ",".join(cols),
            }
        )
    direction_summary: dict[str, Any] = {"ok": False}
    if "proba_price_dispersion" in preds.columns:
        selected_idx = preds.index[preds["proba_price_dispersion"] >= 0.55]
        dir_train = train[train["future_is_trend"]].copy()
        dir_test = test.loc[test.index.intersection(selected_idx)].copy()
        if not dir_train.empty and not dir_test.empty and dir_train["future_trend_sign"].notna().sum() >= 50:
            y_dir = (dir_train["future_trend_sign"] > 0).astype(int)
            cols = specs["price_dispersion"]
            if y_dir.nunique() == 2 and dir_test["future_trend_sign"].notna().any():
                dmodel = Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
                    ]
                )
                dmodel.fit(dir_train[cols].astype(float), y_dir)
                p_up = dmodel.predict_proba(dir_test[cols].astype(float))[:, 1]
                pred_sign = np.where(p_up >= 0.5, 1.0, -1.0)
                actual = dir_test["future_trend_sign"].to_numpy()
                valid = np.isfinite(actual)
                hit = float(np.mean(pred_sign[valid] == actual[valid])) if valid.any() else np.nan
                direction_summary = {
                    "ok": True,
                    "n": int(valid.sum()),
                    "n_dates": int(dir_test["et_date"].nunique()),
                    "hit_rate": hit,
                    "baseline": 0.5,
                    "lift_pp": float((hit - 0.5) * 100.0) if np.isfinite(hit) else np.nan,
                }
    return pd.DataFrame(metrics), {"predictions_rows": int(len(preds)), "direction": direction_summary, "metrics": metrics}


def apply_gates(cells: pd.DataFrame) -> pd.DataFrame:
    out = cells.copy()
    if out.empty:
        out["bh_adjusted_p"] = []
        out["bh_significant"] = []
        out["gate"] = []
        return out
    pvals = out["p_one_sided"].to_numpy(dtype=float)
    bh = benjamini_hochberg(pvals, q=BH_Q)
    out["bh_adjusted_p"] = bh["adjusted_p"]
    out["bh_significant"] = bh["reject"]
    gates = []
    for _, row in out.iterrows():
        n_ok = int(row.get("n", 0) or 0) >= MIN_N
        dates_ok = int(row.get("n_dates", 0) or 0) >= MIN_DATES
        lift_ok = np.isfinite(row.get("lift_pp")) and float(row["lift_pp"]) >= MIN_LIFT_PP
        ci_ok = np.isfinite(row.get("one_sided_lower")) and float(row["one_sided_lower"]) > 0
        bh_ok = bool(row.get("bh_significant", False))
        if n_ok and dates_ok and lift_ok and ci_ok and bh_ok:
            gates.append("PASS")
        elif n_ok and lift_ok and (ci_ok or bh_ok):
            gates.append("PROVISIONAL")
        else:
            gates.append("FAIL")
    out["gate"] = gates
    return out


def robustness_slices(df: pd.DataFrame, dates: list[str], baseline_rate: float) -> list[dict[str, Any]]:
    subset = df[df["et_date"].isin(set(dates))].copy()
    subset = subset[subset["future_regime"].isin(["RANGE", "TREND_UP", "TREND_DOWN"])]
    rows = []
    base_mask = subset["abs_v1_residual_z"].ge(1.0) & subset.get("residual_widening_15m", True)
    slices = {
        "all": pd.Series(True, index=subset.index),
        "rth": subset.get("is_rth", pd.Series(False, index=subset.index)).fillna(False),
        "overnight": ~subset.get("is_rth", pd.Series(False, index=subset.index)).fillna(False),
        "event": subset.get("event_near", pd.Series(False, index=subset.index)).fillna(False),
        "no_event": ~subset.get("event_near", pd.Series(False, index=subset.index)).fillna(False),
    }
    if "realized_vol" in subset:
        vol = subset["realized_vol"]
        q33, q66 = vol.quantile(0.33), vol.quantile(0.66)
        slices["vol_low"] = vol <= q33
        slices["vol_mid"] = (vol > q33) & (vol <= q66)
        slices["vol_high"] = vol > q66
    for name, mask in slices.items():
        episodes = decluster_episodes(subset, base_mask & mask)
        rows.append(
            evaluate_cell(
                episodes,
                cell_id=f"robust_large_widening_{name}",
                family="robustness",
                split_name="validation",
                outcome_col="future_is_trend",
                baseline_rate=baseline_rate,
            )
        )
    # One-minute decision lag.
    lagged = subset.copy()
    widen = lagged.get("residual_widening_15m", pd.Series(True, index=lagged.index))
    if not isinstance(widen, pd.Series):
        widen = pd.Series(bool(widen), index=lagged.index)
    lag_mask = lagged["abs_v1_residual_z"].shift(1).ge(1.0) & widen.shift(1).fillna(False).astype(bool)
    episodes = decluster_episodes(lagged, lag_mask)
    rows.append(
        evaluate_cell(
            episodes,
            cell_id="robust_large_widening_lag1m",
            family="robustness",
            split_name="validation",
            outcome_col="future_is_trend",
            baseline_rate=baseline_rate,
        )
    )
    return rows


def verify_v1_cache_parity() -> dict[str, Any]:
    """Optional formula-reproduction check against overlapping V1 cache dates."""
    cache_dir = PROJECT_ROOT / "workspace" / "analysis" / "_totalaggress_cache"
    if not cache_dir.exists():
        return {"ok": False, "reason": "cache_dir_missing"}
    files = sorted(cache_dir.glob("trades_v2_ZN_FUT_*.parquet"))
    if not files:
        return {"ok": False, "reason": "no_zn_cache_files"}
    # Lightweight check: ensure classify + DV01 cumsum is finite on one date.
    from scripts.totalaggress_vs_price_chart import classify_aggressor

    sample = pd.read_parquet(files[0])
    if sample.empty:
        return {"ok": False, "reason": "empty_sample", "file": files[0].name}
    sample = sample.copy()
    sample["sign"] = sample["side"].map(classify_aggressor)
    sample["signed_flow"] = sample["sign"] * sample["size"].astype(float) * 85.0
    total = float(sample["signed_flow"].sum())
    return {
        "ok": True,
        "file": files[0].name,
        "trades": int(len(sample)),
        "signed_flow_sum": total,
        "note": "Parity smoke check only; primary panel uses TBBO matrix agg_cum_daily.",
    }


def build_panel(feature_path: Path, events_db: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, quality = load_features(feature_path)
    panel = fit_causal_v1_residual(raw)
    panel = add_dispersion_dynamics(panel)
    panel = add_forward_regime_outcomes(panel)
    panel["is_rth"] = [ _is_rth(ts) for ts in panel.index ]
    panel["event_near"] = _near_event(panel.index, _event_times(events_db))
    panel = attach_benchmarks(panel)
    meta = {
        "quality": quality,
        "cache_parity": verify_v1_cache_parity(),
        "rows": int(len(panel)),
        "dates": sorted(panel["et_date"].dropna().unique().tolist()),
    }
    return panel, meta


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel, meta = build_panel(Path(args.features), Path(args.events_db))
    splits = chronological_splits(meta["dates"])
    cutoffs = learn_regime_cutoffs(panel, splits["discovery"], horizon=30)
    panel = apply_regime_labels(panel, cutoffs)

    # Lock FINAL: experiments use discovery/validation only until gate selection.
    research_dates = splits["discovery"] + splits["validation"]
    research = panel[panel["et_date"].isin(set(research_dates))].copy()
    labeled = research[research["future_regime"].isin(["RANGE", "TREND_UP", "TREND_DOWN"])]
    baseline_disc = _rate(labeled[labeled["et_date"].isin(set(splits["discovery"]))]["future_is_trend"])
    baseline_val = _rate(labeled[labeled["et_date"].isin(set(splits["validation"]))]["future_is_trend"])

    journal: list[dict[str, Any]] = []
    journal.extend(run_quantile_and_state_experiments(panel, splits["discovery"], "discovery", baseline_disc))
    journal.extend(run_quantile_and_state_experiments(panel, splits["validation"], "validation", baseline_val))
    journal.extend(robustness_slices(panel, splits["validation"], baseline_val))

    model_metrics, model_summary = fit_incremental_models(panel, splits["discovery"], splits["validation"])

    journal_df = pd.DataFrame(journal)
    val_cells = journal_df[journal_df["split"] == "validation"].copy()
    val_gated = apply_gates(val_cells)
    eligible = val_gated[val_gated["gate"].isin(["PASS", "PROVISIONAL"])].copy()

    # FINAL only inspected for PASS candidates.
    final_rows: list[dict[str, Any]] = []
    pass_ids = val_gated.loc[val_gated["gate"] == "PASS", "cell_id"].tolist()
    if pass_ids:
        final_base = _rate(
            panel[panel["et_date"].isin(set(splits["final"])) & panel["future_regime"].isin(["RANGE", "TREND_UP", "TREND_DOWN"])][
                "future_is_trend"
            ]
        )
        # Re-evaluate matching state cells on FINAL.
        final_journal = run_quantile_and_state_experiments(panel, splits["final"], "final", final_base)
        final_df = pd.DataFrame(final_journal)
        final_rows = final_df[final_df["cell_id"].isin(pass_ids)].to_dict(orient="records")
    else:
        final_rows = []

    # Regime definition audit.
    regime_counts = (
        labeled.groupby(["et_date", "future_regime"]).size().unstack(fill_value=0).reset_index()
        if not labeled.empty
        else pd.DataFrame()
    )
    sample_cols = [
        "et_date",
        "price",
        "total_aggress",
        "v1_alpha",
        "v1_beta",
        "v1_predicted",
        "v1_residual",
        "v1_residual_z",
        "abs_v1_residual",
        "residual_chg_5m",
        "residual_chg_15m",
        "flow_price_gap_15m",
        "effort_per_tick_5m",
        "residual_large_age_min",
        "future_regime",
        "future_is_trend",
        "future_is_range",
        "fwd_path_eff_30m",
        "fwd_disp_to_range_30m",
        "fwd_abs_net_ticks_30m",
        "is_rth",
        "event_near",
    ]
    for col in ("bench_structure", "bench_flow_price_gap", "bench_high_effort", "bench_gap_widening"):
        if col in panel.columns:
            sample_cols.append(col)

    panel_out = panel.reset_index().rename(columns={"bucket_ts": "bucket_ts"})
    panel_path = output_dir / "dispersion_panel.csv"
    panel_out[[c for c in ["bucket_ts", *sample_cols] if c in panel_out.columns]].to_csv(panel_path, index=False)

    journal_path = output_dir / "candidate_journal.csv"
    journal_df.to_csv(journal_path, index=False)
    leaderboard_path = output_dir / "validation_leaderboard.csv"
    val_gated.sort_values(["gate", "lift_pp", "n"], ascending=[True, False, False]).to_csv(leaderboard_path, index=False)
    final_path = output_dir / "locked_final_results.csv"
    pd.DataFrame(final_rows).to_csv(final_path, index=False)
    model_path = output_dir / "model_comparison.csv"
    model_metrics.to_csv(model_path, index=False)
    cutoffs_path = output_dir / "regime_cutoffs.json"
    _write_json(cutoffs_path, cutoffs)
    splits_path = output_dir / "split_manifest.json"
    _write_json(splits_path, splits)
    if not regime_counts.empty:
        regime_counts.to_csv(output_dir / "regime_definition_audit.csv", index=False)

    # Sensitivity plot (optional / best-effort).
    try:
        import matplotlib.pyplot as plt

        mono = val_gated[val_gated["cell_id"].str.startswith("mono_abs_residual_q") & ~val_gated["cell_id"].str.contains("range")]
        if not mono.empty:
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.bar(mono["cell_id"], mono["rate"].fillna(0.0))
            ax.axhline(baseline_val, color="red", linestyle="--", label="validation baseline")
            ax.set_title("Validation TREND rate by |V1 residual| quantile")
            ax.tick_params(axis="x", rotation=30)
            ax.legend()
            fig.tight_layout()
            fig.savefig(output_dir / "monotonicity_trend_rates.png", dpi=120)
            plt.close(fig)
    except Exception:
        pass

    n_pass = int((val_gated["gate"] == "PASS").sum()) if not val_gated.empty else 0
    n_prov = int((val_gated["gate"] == "PROVISIONAL").sum()) if not val_gated.empty else 0
    stop_go = "PASS" if n_pass > 0 else ("PROVISIONAL" if n_prov > 0 else "STOP")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stop_go": stop_go,
        "gate_counts": {"PASS": n_pass, "PROVISIONAL": n_prov, "FAIL": int((val_gated["gate"] == "FAIL").sum()) if not val_gated.empty else 0},
        "baseline_discovery_trend_rate": baseline_disc,
        "baseline_validation_trend_rate": baseline_val,
        "regime_cutoffs": cutoffs,
        "splits": splits,
        "meta": meta,
        "model_summary": model_summary,
        "top_validation": val_gated.sort_values(["lift_pp", "n"], ascending=False).head(15).to_dict(orient="records")
        if not val_gated.empty
        else [],
        "eligible_candidates": int(len(eligible)),
        "final_inspected": bool(pass_ids),
        "final_results": final_rows,
        "artifacts": {
            "panel": str(panel_path),
            "journal": str(journal_path),
            "leaderboard": str(leaderboard_path),
            "final": str(final_path),
            "models": str(model_path),
            "cutoffs": str(cutoffs_path),
            "splits": str(splits_path),
        },
        "isolation": {
            "modified_live_apps": False,
            "wrote_live_stores": False,
            "output_dir": str(output_dir),
        },
        "notes": [
            "Primary object is causal V1-style implied-price residual dynamics.",
            "Existing V3 gap/effort and structure labels are benchmarks/interactions only.",
            "FINAL outcomes inspected only for validation PASS cells.",
            "PROVISIONAL never authorizes a dashboard.",
        ],
    }
    summary_path = output_dir / "dispersion_research_summary.json"
    _write_json(summary_path, summary)
    gate_path = output_dir / "dispersion_gate.json"
    _write_json(
        gate_path,
        {
            "passed": stop_go == "PASS",
            "stop_go": stop_go,
            "gate_counts": summary["gate_counts"],
            "min_n": MIN_N,
            "min_dates": MIN_DATES,
            "min_lift_pp": MIN_LIFT_PP,
            "bh_q": BH_Q,
            "recommendation": (
                "PASS: review FINAL cells before any display work"
                if stop_go == "PASS"
                else "STOP/PROVISIONAL: no dashboard; keep research artifacts only"
            ),
        },
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V1 flow-price dispersion regime research (offline only).")
    parser.add_argument("--features", default=str(FEATURE_MATRIX))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    summary = run(args)
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "stop_go": summary["stop_go"],
                "gate_counts": summary["gate_counts"],
                "eligible_candidates": summary["eligible_candidates"],
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=_json_default,
        )
    )


if __name__ == "__main__":
    main()
