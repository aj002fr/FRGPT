#!/usr/bin/env python3
"""Offline short-horizon V2 fair-value constructions (Track A/B) plus flip/sticky leans (Track C).

Guardrail: does not import, write to, or restart V1/V1.2/V2/V2.1/V2.2/V2.3/V2.6/V3.
Reads the published TBBO feature matrix only. Writes only under
workspace/analysis/aggression_v2_short_horizon_fv/.
Not a retune of the 2026-07-09 after-cost forward-return gate.
Not authorization for a live V2.2 fair-value swap.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.metrics import mean_squared_error, r2_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_short_horizon_fv"

TICK_SIZE = 1.0 / 64.0
DEADZONE_TICKS = 1.0
MIN_ROWS_PER_DAY = 500
MIN_TRAIN_DAYS = 5
MIN_FIT_ROWS = 200
MIN_POINTS_FOR_Z = 30
PRIOR_OBSERVATIONS = 60.0
LOCKED_HOLDOUT_FRAC = 0.20
DECLUSTER_MIN = 5
PATH_HORIZONS = (5, 10)
ADVERSE_TICK_CAP = 3.0
SWEET_DURATION_MIN = 5.0
SWEET_DURATION_MAX = 15.0
RIDGE_ALPHAS = (0.01, 0.1, 1.0, 10.0, 100.0)

TRACK_A_CORE = (3, 5, 8, 10)
TRACK_A_DIAGNOSTIC = (1, 15)
TRACK_A_HORIZONS = TRACK_A_CORE + TRACK_A_DIAGNOSTIC
TRACK_B_ANCHORS = (3, 5, 8, 10, 15, 30)
ENTER_Z = (1.0, 1.5, 2.0)
EXIT_Z = (0.5, 1.0)
MIN_HOLDS = (0, 5)

SESSION_FEATURES = [
    "agg_cum_daily",
    "agg_5m",
    "agg_30m",
    "buysell_ratio",
    "trade_intensity",
    "avg_trade_size",
    "large_trade_share",
    "realized_vol",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "US_agg_5m",
    "spread_mean",
    "size_imbalance_mean",
    "displayed_depth_mean",
    "trade_size_vs_depth",
    "depth_adjusted_signed_flow",
    "minutes_since_open",
]


@dataclass(frozen=True)
class Construction:
    id: str
    family: str
    features: tuple[str, ...]
    target_col: str
    fv_mode: str
    horizon: int | None = None
    anchor: int | None = None
    pack: str | None = None
    anchor_col: str | None = None
    core: bool = True


def make_ridge() -> Pipeline:
    return Pipeline([
        ("scale", StandardScaler()),
        ("model", RidgeCV(alphas=list(RIDGE_ALPHAS))),
    ])


def iter_walkforward_folds(dates: list[str], min_train_days: int = MIN_TRAIN_DAYS) -> list[tuple[list[str], str]]:
    ordered = list(dates)
    return [(ordered[:idx], ordered[idx]) for idx in range(min_train_days, len(ordered))]


def locked_holdout_dates(dates: list[str], frac: float = LOCKED_HOLDOUT_FRAC) -> list[str]:
    ordered = list(dates)
    if not ordered:
        return []
    start = int(np.floor(len(ordered) * (1.0 - frac)))
    start = min(max(start, 0), len(ordered) - 1)
    return ordered[start:]


def load_matrix(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    df.sort_values(["et_date", "bucket_ts"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def quality_filter(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    by_day = df.groupby("et_date").agg(
        rows=("price", "count"),
        price_min=("price", "min"),
        price_max=("price", "max"),
        target_std=("target_price_delta", "std"),
    )
    clean_dates = by_day[
        (by_day["rows"] >= MIN_ROWS_PER_DAY)
        & (by_day["price_min"] > 80)
        & (by_day["price_max"] < 150)
        & (by_day["target_std"] > 0.01)
        & (by_day["target_std"] < 1.0)
    ].index.tolist()
    required = ["target_price_delta", "price", "day_open_price", "et_date", "bucket_ts", *SESSION_FEATURES]
    out = df[df["et_date"].isin(clean_dates)].replace([np.inf, -np.inf], np.nan)
    out = out.dropna(subset=required).copy()
    return out, {
        "clean_dates": sorted(out["et_date"].astype(str).unique().tolist()),
        "excluded_dates": [d for d in by_day.index.tolist() if d not in clean_dates],
        "rows_after_quality_filter": int(len(out)),
    }


def _group_sort(day: pd.DataFrame) -> pd.DataFrame:
    return day.sort_values("bucket_ts")


def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Same-ET-date forward targets, local anchors, and B1 lookbacks. No cross-day leakage."""
    frames: list[pd.DataFrame] = []
    for _, day in df.groupby("et_date", sort=False):
        d = _group_sort(day).copy()
        price = d["price"].astype(float)
        for horizon in sorted(set(TRACK_A_HORIZONS + PATH_HORIZONS)):
            future = price.shift(-horizon)
            d[f"future_price_{horizon}m"] = future
            d[f"y_A_{horizon}m"] = future - price
        for anchor in TRACK_B_ANCHORS:
            loc = price.shift(anchor)
            d[f"local_anchor_{anchor}m"] = loc
            d[f"y_B_{anchor}m"] = price - loc
            d[f"minutes_since_anchor_{anchor}m"] = np.where(loc.notna(), float(anchor), np.nan)
            if "agg_1m" in d.columns:
                d[f"agg_cum_{anchor}m"] = d["agg_1m"].astype(float).rolling(anchor, min_periods=anchor).sum()
            else:
                d[f"agg_cum_{anchor}m"] = np.nan
        d["realized_vol_5"] = price.diff().rolling(5, min_periods=5).std()
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def construction_specs() -> list[Construction]:
    specs = [
        Construction(
            id="session",
            family="session",
            features=tuple(SESSION_FEATURES),
            target_col="target_price_delta",
            fv_mode="session",
        )
    ]
    for horizon in TRACK_A_HORIZONS:
        specs.append(Construction(
            id=f"A_h{horizon}",
            family="A",
            features=tuple(SESSION_FEATURES),
            target_col=f"y_A_{horizon}m",
            fv_mode="forward",
            horizon=horizon,
            core=horizon in TRACK_A_CORE,
        ))
    for anchor in TRACK_B_ANCHORS:
        specs.append(Construction(
            id=f"B0_W{anchor}",
            family="B",
            features=tuple(SESSION_FEATURES),
            target_col=f"y_B_{anchor}m",
            fv_mode="local",
            anchor=anchor,
            pack="B0",
            anchor_col=f"local_anchor_{anchor}m",
        ))
        b1 = [
            f"agg_cum_{anchor}m",
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
            f"minutes_since_anchor_{anchor}m",
        ]
        specs.append(Construction(
            id=f"B1_W{anchor}",
            family="B",
            features=tuple(b1),
            target_col=f"y_B_{anchor}m",
            fv_mode="local",
            anchor=anchor,
            pack="B1",
            anchor_col=f"local_anchor_{anchor}m",
        ))
    return specs


def residual_z_causal(residual: pd.Series, mu_hist: float, sigma_hist: float) -> pd.Series:
    resid = residual.astype(float)
    expanding_mean = resid.expanding(min_periods=MIN_POINTS_FOR_Z).mean()
    expanding_std = resid.expanding(min_periods=MIN_POINTS_FOR_Z).std(ddof=1)
    count = resid.expanding(min_periods=1).count()
    weight = PRIOR_OBSERVATIONS / (PRIOR_OBSERVATIONS + count)
    today_mean = expanding_mean.where(expanding_mean.notna(), mu_hist)
    today_std = expanding_std.where(expanding_std.notna(), sigma_hist).clip(lower=0.0)
    blended_mean = weight * mu_hist + (1.0 - weight) * today_mean
    blended_var = weight * (sigma_hist ** 2) + (1.0 - weight) * (today_std ** 2)
    blended_std = np.sqrt(blended_var).replace(0.0, np.nan)
    return (resid - blended_mean) / blended_std


def _fair_value(frame: pd.DataFrame, spec: Construction, pred_delta: np.ndarray) -> pd.Series:
    if spec.fv_mode == "session":
        return frame["day_open_price"].astype(float) + pred_delta
    if spec.fv_mode == "forward":
        return frame["price"].astype(float) + pred_delta
    if spec.fv_mode == "local":
        assert spec.anchor_col is not None
        return frame[spec.anchor_col].astype(float) + pred_delta
    raise ValueError(spec.fv_mode)


def fit_predict_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
    spec: Construction,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    feats = list(spec.features)
    need_train = [spec.target_col, *feats]
    need_test = ["price", "day_open_price", *feats]
    if spec.anchor_col:
        need_train.append(spec.anchor_col)
        need_test.append(spec.anchor_col)
    tr = train.dropna(subset=need_train)
    te = test.dropna(subset=need_test)
    meta = {
        "construction": spec.id,
        "test_date": str(test["et_date"].iloc[0]) if not test.empty else None,
        "train_rows": int(len(tr)),
        "test_rows": int(len(te)),
        "ok": False,
    }
    if len(tr) < MIN_FIT_ROWS or te.empty:
        return pd.DataFrame(), meta
    model = make_ridge()
    y = tr[spec.target_col].astype(float)
    model.fit(tr[feats].astype(float), y)
    train_pred = model.predict(tr[feats].astype(float))
    train_resid = y.to_numpy() - train_pred
    mu_hist = float(np.mean(train_resid)) if np.isfinite(np.mean(train_resid)) else 0.0
    sigma_hist = float(np.std(train_resid, ddof=1)) if len(train_resid) > 1 else 1.0
    if not np.isfinite(sigma_hist) or sigma_hist <= 0:
        sigma_hist = 1.0
    pred_delta = model.predict(te[feats].astype(float))
    out = te[["bucket_ts", "et_date", "price", "day_open_price"]].copy()
    if spec.anchor_col:
        out["local_anchor"] = te[spec.anchor_col].astype(float)
    else:
        out["local_anchor"] = np.nan
    out["construction"] = spec.id
    out["family"] = spec.family
    out["horizon"] = spec.horizon
    out["anchor"] = spec.anchor
    out["pack"] = spec.pack
    out["target"] = te[spec.target_col].astype(float) if spec.target_col in te.columns else np.nan
    out["predicted_delta"] = pred_delta
    out["fair_value"] = _fair_value(te, spec, pred_delta)
    out["residual"] = out["price"].astype(float) - out["fair_value"].astype(float)
    out["residual_z"] = residual_z_causal(out["residual"], mu_hist, sigma_hist).to_numpy()
    for horizon in PATH_HORIZONS:
        col = f"future_price_{horizon}m"
        out[col] = te[col].astype(float) if col in te.columns else np.nan
    if spec.horizon is not None:
        fut = f"future_price_{spec.horizon}m"
        out["future_price_h"] = te[fut].astype(float) if fut in te.columns else np.nan
    else:
        out["future_price_h"] = np.nan
    ridge = model.named_steps["model"]
    meta.update({
        "ok": True,
        "alpha": float(ridge.alpha_),
        "train_r2": float(r2_score(y, train_pred)),
        "mu_hist": mu_hist,
        "sigma_hist": sigma_hist,
    })
    return out, meta


def _safe_r2(y: pd.Series, yhat: pd.Series) -> float:
    mask = y.notna() & yhat.notna()
    if int(mask.sum()) < 8 or float(y[mask].std(ddof=1) or 0.0) == 0.0:
        return float("nan")
    return float(r2_score(y[mask], yhat[mask]))


def _safe_rmse(y: pd.Series, yhat: pd.Series) -> float:
    mask = y.notna() & yhat.notna()
    if int(mask.sum()) < 8:
        return float("nan")
    return float(mean_squared_error(y[mask], yhat[mask]) ** 0.5)


def _safe_mae_ticks(y: pd.Series, yhat: pd.Series) -> float:
    mask = y.notna() & yhat.notna()
    if int(mask.sum()) < 8:
        return float("nan")
    return float(np.mean(np.abs(y[mask] - yhat[mask])) / TICK_SIZE)


def _safe_auc(y: pd.Series, score: pd.Series) -> float:
    mask = y.notna() & score.notna() & (y != 0)
    if int(mask.sum()) < 20:
        return float("nan")
    labels = (y[mask] > 0).astype(int)
    if labels.nunique() < 2:
        return float("nan")
    return float(roc_auc_score(labels, score[mask]))


def fade_hit_rate(residual: pd.Series, future_change: pd.Series) -> float:
    mask = residual.notna() & future_change.notna() & (future_change != 0) & (residual != 0)
    if int(mask.sum()) < 8:
        return float("nan")
    hit = np.sign(-residual[mask]) == np.sign(future_change[mask])
    return float(hit.mean())


def lean_from_residual(residual: pd.Series, tick_deadzone: float = TICK_SIZE * DEADZONE_TICKS) -> pd.Series:
    resid = residual.astype(float)
    lean = np.sign(resid)
    lean = lean.where(resid.abs() >= tick_deadzone, 0.0)
    lean = lean.where(resid.notna(), np.nan)
    return lean


def apply_hysteresis(
    residual: pd.Series,
    residual_z: pd.Series,
    enter: float,
    exit_: float,
    min_hold: int,
    tick_deadzone: float = TICK_SIZE * DEADZONE_TICKS,
) -> pd.Series:
    resid = residual.astype(float).to_numpy()
    z = residual_z.astype(float).to_numpy()
    out = np.zeros(len(resid), dtype=float)
    state = 0.0
    held = 0
    for i in range(len(resid)):
        if not np.isfinite(resid[i]) or not np.isfinite(z[i]):
            out[i] = state
            if state != 0:
                held += 1
            continue
        sgn = 0.0 if abs(resid[i]) < tick_deadzone else float(np.sign(resid[i]))
        az = abs(z[i])
        if state == 0.0:
            if az >= enter and sgn != 0.0:
                state = sgn
                held = 1
            else:
                held = 0
        else:
            held += 1
            if held > min_hold:
                if az >= enter and sgn != 0.0 and sgn != state:
                    state = sgn
                    held = 1
                elif az < exit_:
                    state = 0.0
                    held = 0
        out[i] = state
    return pd.Series(out, index=residual.index)


def flip_metrics(lean: pd.Series) -> dict[str, float]:
    s = lean.dropna()
    if s.empty:
        return {
            "n_bars": 0,
            "flips": 0,
            "flips_per_hour": float("nan"),
            "median_minutes_between_changes": float("nan"),
            "median_lean_duration_min": float("nan"),
            "share_10m_windows_0_changes": float("nan"),
            "share_10m_windows_1_change": float("nan"),
            "share_10m_windows_ge3_changes": float("nan"),
        }
    changed = s.ne(s.shift(1))
    changed.iloc[0] = False
    flips = int(changed.sum())
    n_bars = int(len(s))
    hours = n_bars / 60.0
    change_idx = np.flatnonzero(changed.to_numpy())
    if len(change_idx) >= 2:
        gaps = np.diff(change_idx)
        median_gap = float(np.median(gaps))
    else:
        median_gap = float("nan")
    durations: list[int] = []
    run = 0
    prev = 0.0
    for val in s.to_numpy():
        if val != 0 and val == prev:
            run += 1
        elif val != 0:
            if run > 0:
                durations.append(run)
            run = 1
            prev = val
        else:
            if run > 0:
                durations.append(run)
            run = 0
            prev = 0.0
    if run > 0:
        durations.append(run)
    win = changed.astype(int).rolling(10, min_periods=10).sum()
    valid = win.dropna()
    return {
        "n_bars": n_bars,
        "flips": flips,
        "flips_per_hour": float(flips / hours) if hours > 0 else float("nan"),
        "median_minutes_between_changes": median_gap,
        "median_lean_duration_min": float(np.median(durations)) if durations else float("nan"),
        "share_10m_windows_0_changes": float((valid == 0).mean()) if len(valid) else float("nan"),
        "share_10m_windows_1_change": float((valid == 1).mean()) if len(valid) else float("nan"),
        "share_10m_windows_ge3_changes": float((valid >= 3).mean()) if len(valid) else float("nan"),
    }


def path_adverse_ticks(prices: np.ndarray, start_i: int, horizon: int, direction: float) -> float:
    if direction == 0 or not np.isfinite(direction):
        return float("nan")
    end = start_i + horizon
    if end >= len(prices):
        return float("nan")
    entry = prices[start_i]
    window = prices[start_i + 1 : end + 1]
    if window.size < horizon or not np.isfinite(entry) or not np.isfinite(window).all():
        return float("nan")
    signed = direction * (window - entry) / TICK_SIZE
    return float(max(0.0, -float(np.min(signed))))


def minutes_until_not_against(residuals: np.ndarray, start_i: int, start_sign: float, horizon: int = 10) -> float:
    if start_sign == 0 or not np.isfinite(start_sign):
        return float("nan")
    end = min(start_i + horizon, len(residuals) - 1)
    if start_i + 1 > end:
        return float("nan")
    for step, idx in enumerate(range(start_i + 1, end + 1), start=1):
        val = residuals[idx]
        if not np.isfinite(val):
            continue
        sgn = 0.0 if abs(val) < TICK_SIZE * DEADZONE_TICKS else float(np.sign(val))
        if sgn != start_sign:
            return float(step)
    if start_i + horizon >= len(residuals):
        return float("nan")
    return float("nan")


def score_episodes(
    day: pd.DataFrame,
    lean: pd.Series,
    decluster_min: int = DECLUSTER_MIN,
) -> dict[str, float]:
    prices = day["price"].astype(float).to_numpy()
    residuals = day["residual"].astype(float).to_numpy()
    lean_v = lean.reindex(day.index).to_numpy()
    last_fire = -10_000
    adv5: list[float] = []
    adv10: list[float] = []
    resolve: list[float] = []
    for i in range(len(day)):
        cur = lean_v[i]
        prev = lean_v[i - 1] if i else 0.0
        if not np.isfinite(cur) or cur == 0 or cur == prev:
            continue
        if i - last_fire < decluster_min:
            continue
        last_fire = i
        direction = -float(cur)
        a5 = path_adverse_ticks(prices, i, 5, direction)
        a10 = path_adverse_ticks(prices, i, 10, direction)
        if np.isfinite(a5):
            adv5.append(a5)
        if np.isfinite(a10):
            adv10.append(a10)
        start_sign = 0.0 if abs(residuals[i]) < TICK_SIZE * DEADZONE_TICKS else float(np.sign(residuals[i]))
        rmin = minutes_until_not_against(residuals, i, start_sign, 10)
        if np.isfinite(rmin):
            resolve.append(rmin)
    return {
        "n_episodes": int(len(adv5) or len(adv10)),
        "median_adverse_5m_ticks": float(np.median(adv5)) if adv5 else float("nan"),
        "median_adverse_10m_ticks": float(np.median(adv10)) if adv10 else float("nan"),
        "p_adverse_le_3_5m": float(np.mean(np.array(adv5) <= ADVERSE_TICK_CAP)) if adv5 else float("nan"),
        "p_adverse_le_3_10m": float(np.mean(np.array(adv10) <= ADVERSE_TICK_CAP)) if adv10 else float("nan"),
        "median_minutes_until_not_against_10m": float(np.median(resolve)) if resolve else float("nan"),
    }


def score_construction_fit(pred: pd.DataFrame, spec: Construction, session_pred: pd.DataFrame | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "construction": spec.id,
        "family": spec.family,
        "horizon": spec.horizon,
        "anchor": spec.anchor,
        "pack": spec.pack,
        "core": spec.core,
        "n": int(len(pred)),
        "n_dates": int(pred["et_date"].nunique()) if not pred.empty else 0,
    }
    if pred.empty:
        return row
    if spec.fv_mode == "forward":
        y_price = pred["future_price_h"]
        row["oos_r2_future_price"] = _safe_r2(y_price, pred["fair_value"])
        row["oos_rmse_future_price"] = _safe_rmse(y_price, pred["fair_value"])
        row["oos_mae_ticks_future_price"] = _safe_mae_ticks(y_price, pred["fair_value"])
        row["rw_r2_future_price"] = _safe_r2(y_price, pred["price"])
        row["rw_rmse_future_price"] = _safe_rmse(y_price, pred["price"])
        row["rw_mae_ticks_future_price"] = _safe_mae_ticks(y_price, pred["price"])
        row["r2_lift_vs_rw"] = row["oos_r2_future_price"] - row["rw_r2_future_price"]
        row["sign_auc"] = _safe_auc(pred["target"], pred["predicted_delta"])
        row["rank_ic"] = float(pred["predicted_delta"].corr(pred["target"], method="spearman"))
        if session_pred is not None and not session_pred.empty:
            merged = pred[["bucket_ts", "future_price_h"]].merge(
                session_pred[["bucket_ts", "fair_value"]].rename(columns={"fair_value": "session_fv"}),
                on="bucket_ts",
                how="inner",
            )
            row["session_r2_future_price"] = _safe_r2(merged["future_price_h"], merged["session_fv"])
    elif spec.fv_mode in {"session", "local"}:
        row["oos_r2_target"] = _safe_r2(pred["target"], pred["predicted_delta"])
        row["oos_rmse_target"] = _safe_rmse(pred["target"], pred["predicted_delta"])
        row["tracking_mae_ticks"] = _safe_mae_ticks(pred["price"], pred["fair_value"])
        if spec.fv_mode == "local" and session_pred is not None and not session_pred.empty and spec.anchor is not None:
            merged = pred[["bucket_ts", "target", "local_anchor"]].merge(
                session_pred[["bucket_ts", "fair_value"]].rename(columns={"fair_value": "session_fv"}),
                on="bucket_ts",
                how="inner",
            )
            session_local = merged["session_fv"] - merged["local_anchor"]
            row["session_r2_local_target"] = _safe_r2(merged["target"], session_local)
            row["local_r2_lift_vs_session"] = row["oos_r2_target"] - row["session_r2_local_target"]
    for horizon in PATH_HORIZONS:
        fut = pred.get(f"future_price_{horizon}m")
        if fut is None:
            continue
        change = fut.astype(float) - pred["price"].astype(float)
        row[f"fade_hit_{horizon}m"] = fade_hit_rate(pred["residual"], change)
    return row


def hysteresis_grid() -> list[tuple[str, float, float, int]]:
    rules = [("raw", float("nan"), float("nan"), 0)]
    for enter in ENTER_Z:
        for exit_ in EXIT_Z:
            for hold in MIN_HOLDS:
                rules.append((f"e{enter:g}_x{exit_:g}_h{hold}", enter, exit_, hold))
    return rules


def _mean_or_nan(values: list[float]) -> float:
    arr = np.array([v for v in values if np.isfinite(v)], dtype=float)
    return float(arr.mean()) if arr.size else float("nan")


def _median_or_nan(values: list[float]) -> float:
    arr = np.array([v for v in values if np.isfinite(v)], dtype=float)
    return float(np.median(arr)) if arr.size else float("nan")


def score_track_c(pred: pd.DataFrame) -> list[dict[str, Any]]:
    if pred.empty:
        return []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for (construction, _et_date), day in pred.groupby(["construction", "et_date"], sort=False):
        day = day.sort_values("bucket_ts").reset_index(drop=True)
        for rule_id, enter, exit_, hold in hysteresis_grid():
            if rule_id == "raw":
                lean = lean_from_residual(day["residual"])
            else:
                lean = apply_hysteresis(day["residual"], day["residual_z"], enter, exit_, hold)
            grouped.setdefault((str(construction), rule_id), []).append({
                "enter_z": enter if rule_id != "raw" else None,
                "exit_z": exit_ if rule_id != "raw" else None,
                "min_hold": hold if rule_id != "raw" else 0,
                "flips": flip_metrics(lean),
                "episodes": score_episodes(day, lean),
            })

    out: list[dict[str, Any]] = []
    for (construction, rule), items in grouped.items():
        flips_parts = [it["flips"] for it in items]
        ep_parts = [it["episodes"] for it in items]
        n_bars = int(sum(int(p["n_bars"]) for p in flips_parts))
        n_flips = int(sum(int(p["flips"]) for p in flips_parts))
        hours = n_bars / 60.0 if n_bars else 0.0
        rec = {
            "construction": construction,
            "rule": rule,
            "enter_z": items[0]["enter_z"],
            "exit_z": items[0]["exit_z"],
            "min_hold": items[0]["min_hold"],
            "n_dates": len(items),
            "n_bars": n_bars,
            "flips": n_flips,
            "flips_per_hour": float(n_flips / hours) if hours > 0 else float("nan"),
            "median_minutes_between_changes": _median_or_nan(
                [float(p["median_minutes_between_changes"]) for p in flips_parts]
            ),
            "median_lean_duration_min": _median_or_nan(
                [float(p["median_lean_duration_min"]) for p in flips_parts]
            ),
            "share_10m_windows_0_changes": _mean_or_nan(
                [float(p["share_10m_windows_0_changes"]) for p in flips_parts]
            ),
            "share_10m_windows_1_change": _mean_or_nan(
                [float(p["share_10m_windows_1_change"]) for p in flips_parts]
            ),
            "share_10m_windows_ge3_changes": _mean_or_nan(
                [float(p["share_10m_windows_ge3_changes"]) for p in flips_parts]
            ),
            "n_episodes": int(sum(int(p["n_episodes"]) for p in ep_parts)),
            "median_adverse_5m_ticks": _median_or_nan([float(p["median_adverse_5m_ticks"]) for p in ep_parts]),
            "median_adverse_10m_ticks": _median_or_nan([float(p["median_adverse_10m_ticks"]) for p in ep_parts]),
            "p_adverse_le_3_5m": _mean_or_nan([float(p["p_adverse_le_3_5m"]) for p in ep_parts]),
            "p_adverse_le_3_10m": _mean_or_nan([float(p["p_adverse_le_3_10m"]) for p in ep_parts]),
            "median_minutes_until_not_against_10m": _median_or_nan(
                [float(p["median_minutes_until_not_against_10m"]) for p in ep_parts]
            ),
        }
        rec["p_adverse_le_3_best"] = max(
            rec["p_adverse_le_3_5m"] if np.isfinite(rec["p_adverse_le_3_5m"]) else -np.inf,
            rec["p_adverse_le_3_10m"] if np.isfinite(rec["p_adverse_le_3_10m"]) else -np.inf,
        )
        if not np.isfinite(rec["p_adverse_le_3_best"]):
            rec["p_adverse_le_3_best"] = float("nan")
        duration = rec["median_lean_duration_min"]
        rec["too_flippy"] = bool(np.isfinite(duration) and duration < 3.0)
        rec["too_sticky"] = bool(np.isfinite(duration) and duration > 20.0)
        rec["duration_in_band"] = bool(
            np.isfinite(duration) and SWEET_DURATION_MIN <= duration <= SWEET_DURATION_MAX
        )
        out.append(rec)
    return out


def pick_sweet_spot(
    stability: pd.DataFrame,
    session_raw_flips_per_hour: float,
) -> dict[str, Any]:
    empty = {
        "found": False,
        "construction": None,
        "rule": None,
        "reason": "no cell met duration band and flip cap",
    }
    if stability.empty:
        return empty
    cand = stability[
        (stability["rule"] != "raw")
        & stability["duration_in_band"]
        & ~stability["too_flippy"]
        & ~stability["too_sticky"]
    ].copy()
    if np.isfinite(session_raw_flips_per_hour):
        cand = cand[cand["flips_per_hour"] <= session_raw_flips_per_hour + 1e-12]
    cand = cand[np.isfinite(cand["p_adverse_le_3_best"])]
    if cand.empty:
        return empty
    cand = cand.sort_values(
        ["p_adverse_le_3_best", "p_adverse_le_3_5m", "p_adverse_le_3_10m"],
        ascending=False,
    )
    best = cand.iloc[0]
    return {
        "found": True,
        "construction": str(best["construction"]),
        "rule": str(best["rule"]),
        "median_lean_duration_min": float(best["median_lean_duration_min"]),
        "flips_per_hour": float(best["flips_per_hour"]),
        "p_adverse_le_3_5m": float(best["p_adverse_le_3_5m"]),
        "p_adverse_le_3_10m": float(best["p_adverse_le_3_10m"]),
        "n_episodes": int(best["n_episodes"]),
        "reason": "best P(adverse<=3 ticks) among duration-band cells with flip rate no worse than session raw-sign",
    }


def verdicts(
    holdout_fit: pd.DataFrame,
    sweet: dict[str, Any],
) -> dict[str, Any]:
    a = holdout_fit[(holdout_fit["family"] == "A") & (holdout_fit["core"] == True)]
    a_ok = bool((a["r2_lift_vs_rw"] > 0).any()) if "r2_lift_vs_rw" in a and not a.empty else False
    b = holdout_fit[holdout_fit["family"] == "B"]
    session = holdout_fit[holdout_fit["construction"] == "session"]
    sess_hit5 = float(session["fade_hit_5m"].iloc[0]) if not session.empty and "fade_hit_5m" in session else float("nan")
    sess_hit10 = float(session["fade_hit_10m"].iloc[0]) if not session.empty and "fade_hit_10m" in session else float("nan")
    b_ok = False
    b_winner = None
    if not b.empty and "local_r2_lift_vs_session" in b:
        for rec in b.to_dict(orient="records"):
            lift = rec.get("local_r2_lift_vs_session")
            hit5 = rec.get("fade_hit_5m")
            hit10 = rec.get("fade_hit_10m")
            if not (isinstance(lift, float) and np.isfinite(lift) and lift > 0):
                continue
            hit5_ok = (not np.isfinite(sess_hit5)) or (isinstance(hit5, float) and np.isfinite(hit5) and hit5 + 1e-12 >= sess_hit5)
            hit10_ok = (not np.isfinite(sess_hit10)) or (isinstance(hit10, float) and np.isfinite(hit10) and hit10 + 1e-12 >= sess_hit10)
            if hit5_ok and hit10_ok:
                b_ok = True
                b_winner = rec["construction"]
                break
    return {
        "track_a_informative": a_ok,
        "track_b_informative": b_ok,
        "track_b_example": b_winner,
        "sweet_spot_found": bool(sweet.get("found")),
        "sweet_spot": sweet,
        "ship_gate": False,
        "note": (
            "Research-only fair-value / lean-stability readout. "
            "Not a tradability PASS and not authorization to change live V2.2."
        ),
    }


def write_plots(fit: pd.DataFrame, stability: pd.DataFrame, output_dir: Path) -> list[str]:
    paths: list[str] = []
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return paths
    a = fit[fit["family"] == "A"].sort_values("horizon")
    if not a.empty and "oos_r2_future_price" in a:
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.plot(a["horizon"], a["oos_r2_future_price"], marker="o", label="FV_A")
        ax.plot(a["horizon"], a["rw_r2_future_price"], marker="s", label="random walk")
        ax.set_xlabel("horizon (minutes)")
        ax.set_ylabel("OOS R2 of price t+h")
        ax.set_title("Track A vs random walk (holdout)")
        ax.legend()
        fig.tight_layout()
        path = output_dir / "track_a_r2.png"
        fig.savefig(path)
        plt.close(fig)
        paths.append(str(path))
    raw = stability[stability["rule"] == "raw"]
    if not raw.empty:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.scatter(raw["flips_per_hour"], raw["median_lean_duration_min"])
        for rec in raw.itertuples():
            ax.annotate(str(rec.construction), (rec.flips_per_hour, rec.median_lean_duration_min), fontsize=7)
        ax.axhspan(SWEET_DURATION_MIN, SWEET_DURATION_MAX, alpha=0.1)
        ax.set_xlabel("flips per hour")
        ax.set_ylabel("median lean duration (min)")
        ax.set_title("Raw residual lean: flip vs sticky")
        fig.tight_layout()
        path = output_dir / "flip_vs_sticky_raw.png"
        fig.savefig(path)
        plt.close(fig)
        paths.append(str(path))
    return paths


def run_research(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = load_matrix(Path(args.input))
    clean, quality = quality_filter(raw)
    if "agg_1m" not in clean.columns:
        raise ValueError("Matrix is missing agg_1m; B1 pack cannot be built.")
    clean = add_derived_columns(clean)
    dates = sorted(clean["et_date"].astype(str).unique().tolist())
    holdout_dates = locked_holdout_dates(dates)
    specs = construction_specs()
    preds: list[pd.DataFrame] = []
    fold_rows: list[dict[str, Any]] = []
    for train_dates, test_date in iter_walkforward_folds(dates):
        train = clean[clean["et_date"].isin(train_dates)]
        test = clean[clean["et_date"] == test_date]
        for spec in specs:
            pred, meta = fit_predict_fold(train, test, spec)
            fold_rows.append(meta)
            if not pred.empty:
                preds.append(pred)
    pred_df = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()
    holdout_pred = pred_df[pred_df["et_date"].astype(str).isin(holdout_dates)].copy() if not pred_df.empty else pred_df
    session_all = pred_df[pred_df["construction"] == "session"] if not pred_df.empty else pd.DataFrame()
    session_hold = holdout_pred[holdout_pred["construction"] == "session"] if not holdout_pred.empty else pd.DataFrame()

    fit_all: list[dict[str, Any]] = []
    fit_hold: list[dict[str, Any]] = []
    for spec in specs:
        block = pred_df[pred_df["construction"] == spec.id] if not pred_df.empty else pd.DataFrame()
        hold = holdout_pred[holdout_pred["construction"] == spec.id] if not holdout_pred.empty else pd.DataFrame()
        fit_all.append(score_construction_fit(block, spec, session_all))
        fit_hold.append(score_construction_fit(hold, spec, session_hold))
    fit_all_df = pd.DataFrame(fit_all)
    fit_hold_df = pd.DataFrame(fit_hold)

    stability_df = pd.DataFrame(score_track_c(holdout_pred)) if not holdout_pred.empty else pd.DataFrame()

    session_raw = stability_df[(stability_df["construction"] == "session") & (stability_df["rule"] == "raw")] if not stability_df.empty else pd.DataFrame()
    session_raw_flips = float(session_raw["flips_per_hour"].iloc[0]) if not session_raw.empty else float("nan")
    sweet = pick_sweet_spot(stability_df, session_raw_flips)
    decision = verdicts(fit_hold_df, sweet)
    plot_paths = write_plots(fit_hold_df, stability_df, output_dir)

    pred_path = output_dir / "oos_predictions.csv"
    hold_pred_path = output_dir / "holdout_predictions.csv"
    fit_all_path = output_dir / "construction_metrics_all_oos.csv"
    fit_hold_path = output_dir / "construction_metrics_holdout.csv"
    stability_path = output_dir / "track_c_stability.csv"
    folds_path = output_dir / "walkforward_folds.csv"
    summary_path = output_dir / "summary.json"
    if not pred_df.empty:
        keep = [
            "bucket_ts", "et_date", "construction", "family", "horizon", "anchor", "pack",
            "price", "fair_value", "residual", "residual_z", "target", "predicted_delta",
            "local_anchor", "future_price_h", "future_price_5m", "future_price_10m",
        ]
        pred_df[keep].to_csv(pred_path, index=False)
        holdout_pred[keep].to_csv(hold_pred_path, index=False)
    fit_all_df.to_csv(fit_all_path, index=False)
    fit_hold_df.to_csv(fit_hold_path, index=False)
    if not stability_df.empty:
        stability_df.to_csv(stability_path, index=False)
    pd.DataFrame(fold_rows).to_csv(folds_path, index=False)

    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(Path(args.input)),
        "output_dir": str(output_dir),
        "rows_raw": int(len(raw)),
        "rows_clean": int(len(clean)),
        "clean_dates": dates,
        "holdout_dates": holdout_dates,
        "quality": {k: quality[k] for k in ("clean_dates", "excluded_dates", "rows_after_quality_filter")},
        "grids": {
            "track_a_horizons": list(TRACK_A_HORIZONS),
            "track_a_core": list(TRACK_A_CORE),
            "track_b_anchors": list(TRACK_B_ANCHORS),
            "enter_z": list(ENTER_Z),
            "exit_z": list(EXIT_Z),
            "min_holds": list(MIN_HOLDS),
        },
        "verdicts": decision,
        "session_raw_flips_per_hour": session_raw_flips,
        "artifacts": {
            "oos_predictions": str(pred_path),
            "holdout_predictions": str(hold_pred_path),
            "construction_metrics_all_oos": str(fit_all_path),
            "construction_metrics_holdout": str(fit_hold_path),
            "track_c_stability": str(stability_path),
            "walkforward_folds": str(folds_path),
            "summary": str(summary_path),
            "plots": plot_paths,
        },
        "isolation": {
            "live_dashboards": "untouched",
            "forward_return_dir": "not overwritten",
            "tbbo_matrix": "read-only",
        },
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline short-horizon V2 fair-value research")
    parser.add_argument("--input", default=str(FEATURE_MATRIX))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    summary = run_research(parse_args(argv))
    print(json.dumps({
        "ok": summary["ok"],
        "holdout_dates": len(summary["holdout_dates"]),
        "verdicts": summary["verdicts"],
        "output_dir": summary["output_dir"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
