#!/usr/bin/env python3
"""Research-only generic forward-return signal for Aggression V2.

Guardrail: this script does NOT import, write to, or restart V1/V2/V3 apps.
It reads existing TBBO/model/event artifacts and writes only under
workspace/analysis/aggression_v2_forward_return/.
No dashboard, SOD, Tailscale, port, or *_live/ changes are performed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, RidgeCV
from sklearn.metrics import mean_squared_error, r2_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_flow_effort import (  # noqa: E402
    EASTERN,
    TICK_SIZE,
    FlowEffortConfig,
    _breakout_tier,
    _event_blackout,
    _exhaustion_tier,
    _tier_down,
    annotate_flow_effort,
)

FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_forward_return"
MODEL = "ridge"
HORIZONS = (1, 3, 5, 15, 30)
COST_TICKS = 0.5
DEADZONE_TICKS = 0.5
MIN_ROWS_PER_DAY = 500
MIN_TRAIN_DAYS = 5
PRIOR_OBSERVATIONS = 60.0
MIN_POINTS_FOR_Z = 30
RTH_START = 8 * 60
RTH_END = 15 * 60
EPS = 1e-12

BASE_FEATURE_SET = [
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

RESEARCH_FEATURES = [
    *BASE_FEATURE_SET,
    "agg_1m",
    "agg_rate",
    "agg_accel",
    "signed_streak",
    "flow_decelerating",
    "buysell_trend_5m",
    "cross_tenor_vote",
    "cross_tenor_aligned",
    "cross_tenor_disagrees",
    "effort_per_tick",
    "flow_price_gap",
    "absorption",
    "in_range",
    "range_break",
    "false_break",
    "effort_ratio",
    "residual",
    "residual_z",
    "abs_residual_z",
    "event_blackout",
    "is_rth",
]


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        if not np.isfinite(float(value)):
            return None
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return str(value)


def _load_inputs(feature_path: Path, prediction_path: Path, model: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    features = pd.read_csv(feature_path, parse_dates=["bucket_ts"])
    preds = pd.read_csv(prediction_path, parse_dates=["bucket_ts"])
    features["bucket_ts"] = pd.to_datetime(features["bucket_ts"], utc=True)
    preds["bucket_ts"] = pd.to_datetime(preds["bucket_ts"], utc=True)
    preds = preds[preds["model"] == model].copy()

    by_day = features.groupby("et_date").agg(
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

    keep = ["bucket_ts", "et_date", "price", "day_open_price", "target_price_delta", *BASE_FEATURE_SET]
    extras = [
        "agg_1m",
        "agg_rate",
        "agg_accel",
        "vol_per_min",
        "TY_agg_5m",
        "session_sin",
        "session_cos",
    ]
    keep.extend([c for c in extras if c in features.columns])

    merged = preds[
        ["bucket_ts", "et_date", "fair_value", "residual", "predicted_delta", "train_end_date"]
    ].merge(features[keep], on=["bucket_ts", "et_date"], how="inner")
    merged = merged[merged["et_date"].isin(clean_dates)].copy()
    merged.sort_values(["et_date", "bucket_ts"], inplace=True)
    merged.replace([np.inf, -np.inf], np.nan, inplace=True)
    return merged, {
        "rows_features_raw": int(len(features)),
        "rows_predictions_raw": int(len(preds)),
        "rows_merged": int(len(merged)),
        "clean_dates": clean_dates,
        "excluded_dates": [d for d in by_day.index.tolist() if d not in clean_dates],
        "day_quality": by_day.reset_index().to_dict(orient="records"),
    }


def _signed_streak(signs: pd.Series) -> pd.Series:
    out: list[float] = []
    current = 0.0
    prev = 0.0
    for raw in signs.fillna(0.0):
        sign = float(np.sign(raw))
        if sign == 0.0:
            current = 0.0
        elif sign == prev:
            current += sign
        else:
            current = sign
        out.append(current)
        prev = sign
    return pd.Series(out, index=signs.index)


def _add_causal_residual_z(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    parts = []
    historical_by_day: list[pd.Series] = []
    for et_date in sorted(out["et_date"].dropna().unique().tolist()):
        day = out[out["et_date"] == et_date].sort_values("bucket_ts").copy()
        resid = pd.to_numeric(day["residual"], errors="coerce")
        if historical_by_day:
            hist = pd.concat(historical_by_day).dropna()
            mu_hist = float(hist.mean()) if len(hist) else 0.0
            day_stds = [float(s.dropna().std(ddof=1)) for s in historical_by_day if s.dropna().shape[0] >= 2]
            day_stds = [v for v in day_stds if np.isfinite(v) and v > EPS]
            sigma_hist = float(np.median(day_stds)) if day_stds else float(hist.std(ddof=1) or 1.0)
        else:
            mu_hist = 0.0
            sigma_hist = float(resid.std(ddof=1) or 1.0)
        if not np.isfinite(sigma_hist) or sigma_hist <= EPS:
            sigma_hist = 1.0

        expanding_mean = resid.expanding(min_periods=MIN_POINTS_FOR_Z).mean()
        expanding_std = resid.expanding(min_periods=MIN_POINTS_FOR_Z).std(ddof=1)
        count = resid.expanding(min_periods=1).count()
        weight = PRIOR_OBSERVATIONS / (PRIOR_OBSERVATIONS + count)
        today_mean = expanding_mean.where(expanding_mean.notna(), mu_hist)
        today_std = expanding_std.where(expanding_std.notna(), sigma_hist).clip(lower=EPS)
        blended_mean = weight * mu_hist + (1.0 - weight) * today_mean
        blended_var = weight * (sigma_hist**2) + (1.0 - weight) * (today_std**2)
        day["resid_mean"] = blended_mean
        day["resid_std"] = np.sqrt(blended_var).replace(0.0, np.nan)
        day["residual_z"] = (resid - day["resid_mean"]) / day["resid_std"]
        parts.append(day)
        historical_by_day.append(resid)
    return pd.concat(parts, ignore_index=True).sort_values(["et_date", "bucket_ts"])


def _is_rth(ts: pd.Timestamp) -> bool:
    if pd.isna(ts):
        return False
    et = ts.tz_convert(EASTERN)
    mins = et.hour * 60 + et.minute
    return RTH_START <= mins < RTH_END


def _label_regimes(annotated: pd.DataFrame, config: FlowEffortConfig) -> pd.DataFrame:
    out = annotated.copy()
    if out.empty:
        return out
    out["et_date"] = pd.DatetimeIndex(out.index).tz_convert(EASTERN).strftime("%Y-%m-%d")
    out["session_count"] = out.groupby("et_date").cumcount() + 1
    regimes: list[str] = []
    exhaustion_tiers: list[str] = []
    breakout_tiers: list[str] = []
    blackouts: list[bool] = []
    for _, row in out.iterrows():
        ts = pd.Timestamp(row.name)
        blackout = _event_blackout(ts, config.event_blackout_minutes)
        blackouts.append(bool(blackout))
        if int(row.get("session_count", 0)) < config.min_warmup_minutes:
            regimes.append("WARMUP")
            exhaustion_tiers.append("NONE")
            breakout_tiers.append("NONE")
            continue
        if bool(row.get("false_break")):
            regimes.append("FALSE_BREAK")
            exhaustion_tiers.append("NONE")
            breakout_tiers.append("NONE")
            continue

        exhaustion_tier = _exhaustion_tier(row)
        breakout_tier = _breakout_tier(row, config)
        trend_follow = bool(row.get("flow_price_aligned")) and bool(row.get("cross_tenor_aligned"))
        if trend_follow and exhaustion_tier in {"C", "B"}:
            exhaustion_tier = "NONE"
        if blackout:
            exhaustion_tier = _tier_down(exhaustion_tier)
            breakout_tier = _tier_down(breakout_tier)
        if breakout_tier != "NONE":
            regime = "BREAKOUT"
        elif exhaustion_tier != "NONE":
            regime = "EXHAUSTION"
        elif trend_follow:
            regime = "TREND_FOLLOW"
        else:
            regime = "QUIET"
        regimes.append(regime)
        exhaustion_tiers.append(exhaustion_tier)
        breakout_tiers.append(breakout_tier)
    out["regime"] = regimes
    out["exhaustion_tier"] = exhaustion_tiers
    out["breakout_tier"] = breakout_tiers
    out["event_blackout"] = blackouts
    return out


def _add_forward_targets(df: pd.DataFrame, horizons: Iterable[int]) -> pd.DataFrame:
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_values("bucket_ts").copy()
        for horizon in horizons:
            future = d["price"].shift(-horizon)
            d[f"fwd_ret_{horizon}m_ticks"] = (future - d["price"]) / TICK_SIZE
            for step in range(1, horizon + 1):
                d[f"path_ret_{horizon}m_{step}"] = (d["price"].shift(-step) - d["price"]) / TICK_SIZE
        parts.append(d)
    return pd.concat(parts, ignore_index=True)


def build_dataset(feature_path: Path, prediction_path: Path, model: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    df, quality = _load_inputs(feature_path, prediction_path, model)
    df = _add_causal_residual_z(df)
    df["abs_residual_z"] = df["residual_z"].abs()
    df["signed_streak"] = df.groupby("et_date", group_keys=False)["agg_1m"].apply(_signed_streak)
    df["flow_decelerating"] = (
        (df["agg_rate"].abs() > EPS)
        & (df["agg_accel"].abs() > EPS)
        & (np.sign(df["agg_rate"]) != np.sign(df["agg_accel"]))
    )
    df["buysell_trend_5m"] = df.groupby("et_date")["buysell_ratio"].diff(5)
    ty_sign = np.sign(df["agg_5m"]).fillna(0.0)
    curve_vote = sum(np.sign(df.get(f"{s}_agg_5m", pd.Series(0.0, index=df.index))).fillna(0.0) for s in ("FV", "TU", "US"))
    df["cross_tenor_vote"] = curve_vote
    df["cross_tenor_aligned"] = (ty_sign != 0) & (np.sign(curve_vote) == ty_sign) & (pd.Series(curve_vote).abs() >= 2)
    df["cross_tenor_disagrees"] = (ty_sign != 0) & (curve_vote != 0) & (np.sign(curve_vote) != ty_sign)
    df["is_rth"] = df["bucket_ts"].apply(_is_rth)

    features_idx = df.set_index("bucket_ts").sort_index()
    model_idx = features_idx[["fair_value", "residual", "residual_z"]].copy()
    flow_features = features_idx.drop(columns=["fair_value", "residual", "residual_z"], errors="ignore")
    annotated = annotate_flow_effort(flow_features, model_idx, FlowEffortConfig())
    annotated = _label_regimes(annotated, FlowEffortConfig())
    join_cols = [
        "effort_per_tick",
        "flow_price_gap",
        "absorption",
        "in_range",
        "range_break",
        "false_break",
        "effort_ratio",
        "regime",
        "exhaustion_tier",
        "breakout_tier",
        "event_blackout",
    ]
    df = df.merge(annotated[join_cols].reset_index().rename(columns={"index": "bucket_ts"}), on="bucket_ts", how="left")
    df["regime"] = df["regime"].fillna("UNKNOWN")
    bool_cols = [
        "flow_decelerating",
        "cross_tenor_aligned",
        "cross_tenor_disagrees",
        "is_rth",
        "event_blackout",
        "absorption",
        "in_range",
        "range_break",
        "false_break",
    ]
    for col in bool_cols:
        if col in df:
            df[col] = df[col].fillna(False).astype(bool)
    df = _add_forward_targets(df, HORIZONS)
    return df, quality


def _usable_feature_cols(df: pd.DataFrame) -> list[str]:
    out = []
    for col in RESEARCH_FEATURES:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().mean() >= 0.5 and s.nunique(dropna=True) > 1:
            out.append(col)
    return out


def _chronological_split(dates: list[str], train_frac: float) -> tuple[list[str], list[str]]:
    ordered = sorted(dates)
    if len(ordered) <= 1:
        return ordered, []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac)))
    return ordered[:cut], ordered[cut:]


def _rank_ic(pred: pd.Series, actual: pd.Series) -> float:
    tmp = pd.DataFrame({"pred": pred, "actual": actual}).replace([np.inf, -np.inf], np.nan).dropna()
    if len(tmp) < 3 or tmp["pred"].nunique() < 2 or tmp["actual"].nunique() < 2:
        return float("nan")
    return float(tmp["pred"].corr(tmp["actual"], method="spearman"))


def run_walkforward_models(df: pd.DataFrame, train_frac: float) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    dates = sorted(df["et_date"].dropna().unique().tolist())
    feature_cols = _usable_feature_cols(df)
    predictions: list[pd.DataFrame] = []
    metrics: list[dict[str, Any]] = []
    fold_rows: list[dict[str, Any]] = []

    for horizon in HORIZONS:
        target = f"fwd_ret_{horizon}m_ticks"
        for test_idx in range(MIN_TRAIN_DAYS, len(dates)):
            train_dates = dates[:test_idx]
            test_date = dates[test_idx]
            train = df[df["et_date"].isin(train_dates)].dropna(subset=[target]).copy()
            test = df[df["et_date"] == test_date].dropna(subset=[target]).copy()
            if train.empty or test.empty:
                continue

            x_train = train[feature_cols].astype(float)
            y_train = train[target].astype(float)
            x_test = test[feature_cols].astype(float)

            reg = Pipeline([
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0])),
            ])
            reg.fit(x_train, y_train)
            pred_ret = reg.predict(x_test)

            p_up = np.full(len(test), np.nan)
            dir_train = train[train[target].abs() >= DEADZONE_TICKS].copy()
            if not dir_train.empty:
                y_dir = (dir_train[target] > 0).astype(int)
                if y_dir.nunique() == 2 and len(dir_train) >= 50:
                    clf = Pipeline([
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                        ("model", LogisticRegression(max_iter=1000, class_weight="balanced")),
                    ])
                    clf.fit(dir_train[feature_cols].astype(float), y_dir)
                    p_up = clf.predict_proba(x_test)[:, 1]

            pred = test[
                [
                    "bucket_ts",
                    "et_date",
                    "price",
                    "residual",
                    "residual_z",
                    "agg_5m",
                    "regime",
                    "is_rth",
                    "event_blackout",
                    target,
                ]
            ].copy()
            pred["horizon"] = horizon
            pred["pred_ret_ticks"] = pred_ret
            pred["pred_prob_up"] = p_up
            pred["train_end_date"] = train_dates[-1]
            predictions.append(pred)
            fold_rows.append({
                "horizon": horizon,
                "test_date": test_date,
                "train_rows": int(len(train)),
                "test_rows": int(len(test)),
                "feature_count": int(len(feature_cols)),
                "ridge_alpha": float(reg.named_steps["model"].alpha_),
            })

        all_pred = [p for p in predictions if not p.empty and int(p["horizon"].iloc[0]) == horizon]
        if all_pred:
            hp = pd.concat(all_pred, ignore_index=True)
            actual = hp[target].astype(float)
            pred_ret = hp["pred_ret_ticks"].astype(float)
            pred_dir = np.sign(pred_ret)
            realized_dir = np.sign(actual)
            hit_mask = (pred_dir != 0) & (realized_dir != 0)
            auc = float("nan")
            auc_data = hp[hp[target].abs() >= DEADZONE_TICKS].dropna(subset=["pred_prob_up"])
            if not auc_data.empty and (auc_data[target] > 0).nunique() == 2:
                auc = float(roc_auc_score((auc_data[target] > 0).astype(int), auc_data["pred_prob_up"]))
            metrics.append({
                "horizon": horizon,
                "n": int(len(hp)),
                "oos_r2": float(r2_score(actual, pred_ret)) if actual.nunique() > 1 else float("nan"),
                "oos_rmse_ticks": float(mean_squared_error(actual, pred_ret) ** 0.5),
                "rank_ic": _rank_ic(pred_ret, actual),
                "direction_hit_rate": float((pred_dir[hit_mask] == realized_dir[hit_mask]).mean()) if hit_mask.any() else float("nan"),
                "auc_up_vs_down_deadzone": auc,
                "avg_abs_pred_ticks": float(pred_ret.abs().mean()),
                "avg_abs_realized_ticks": float(actual.abs().mean()),
            })

    pred_df = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
    fold_df = pd.DataFrame(fold_rows)
    meta = {
        "feature_cols": feature_cols,
        "dates": dates,
        "walkforward_min_train_days": MIN_TRAIN_DAYS,
        "model_train_frac_for_trade_translation": train_frac,
    }
    return pred_df, pd.DataFrame(metrics), meta


def _direction_for(df: pd.DataFrame, thesis: str) -> pd.Series:
    if thesis == "FADE":
        return -np.sign(pd.to_numeric(df["residual"], errors="coerce"))
    if thesis == "FOLLOW":
        return np.sign(pd.to_numeric(df["residual"], errors="coerce"))
    if thesis == "FLOW_FOLLOW":
        return np.sign(pd.to_numeric(df["agg_5m"], errors="coerce"))
    if thesis == "MODEL":
        return np.sign(pd.to_numeric(df["pred_ret_ticks"], errors="coerce"))
    return pd.Series(0.0, index=df.index)


def _path_adverse(df: pd.DataFrame, horizon: int, direction: pd.Series) -> pd.Series:
    cols = [f"path_ret_{horizon}m_{step}" for step in range(1, horizon + 1) if f"path_ret_{horizon}m_{step}" in df]
    if not cols:
        return pd.Series(np.nan, index=df.index)
    path = df[cols].astype(float).to_numpy()
    dirs = direction.astype(float).to_numpy().reshape(-1, 1)
    directional_path = dirs * path
    with np.errstate(all="ignore"):
        return pd.Series(np.nanmin(directional_path, axis=1), index=df.index)


def _trade_stats(df: pd.DataFrame, horizon: int, thesis: str, label: str, split: str) -> dict[str, Any]:
    target = f"fwd_ret_{horizon}m_ticks"
    sub = df.dropna(subset=[target]).copy()
    direction = _direction_for(sub, thesis)
    valid = direction.replace(0.0, np.nan).notna()
    sub = sub[valid].copy()
    direction = direction[valid].astype(float)
    if sub.empty:
        return {"label": label, "split": split, "horizon": horizon, "thesis": thesis, "n": 0}
    gross = direction * sub[target].astype(float)
    after = gross - COST_TICKS
    adverse = _path_adverse(sub, horizon, direction)
    neg = after[after < 0.0]
    downside = float(neg.std(ddof=1)) if len(neg) >= 2 else float("nan")
    return {
        "label": label,
        "split": split,
        "horizon": int(horizon),
        "thesis": thesis,
        "n": int(len(sub)),
        "hit_rate_gross": float((gross > 0.0).mean()),
        "mean_gross_ticks": float(gross.mean()),
        "mean_after_cost_ticks": float(after.mean()),
        "median_after_cost_ticks": float(after.median()),
        "median_adverse_ticks": float(adverse.median()) if adverse.notna().any() else float("nan"),
        "p10_adverse_ticks": float(adverse.quantile(0.10)) if adverse.notna().any() else float("nan"),
        "sortino_after_cost": float(after.mean() / downside) if np.isfinite(downside) and downside > EPS else float("nan"),
        "avg_abs_pred_ticks": float(pd.to_numeric(sub["pred_ret_ticks"], errors="coerce").abs().mean()),
    }


def _auc_for_subset(df: pd.DataFrame, horizon: int) -> float:
    target = f"fwd_ret_{horizon}m_ticks"
    sub = df[df[target].abs() >= DEADZONE_TICKS].dropna(subset=[target, "pred_prob_up"])
    if sub.empty or (sub[target] > 0).nunique() != 2:
        return float("nan")
    return float(roc_auc_score((sub[target] > 0).astype(int), sub["pred_prob_up"]))


def regime_conditioning(df: pd.DataFrame, pred_df: pd.DataFrame, train_frac: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if pred_df.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), {"passed": False, "reason": "no model predictions"}
    path_cols = [c for c in df.columns if c.startswith("path_ret_")]
    source_cols = ["bucket_ts", *path_cols]
    merged = pred_df.merge(df[source_cols], on="bucket_ts", how="left")
    dates = sorted(merged["et_date"].dropna().unique().tolist())
    selection_dates, holdout_dates = _chronological_split(dates, train_frac)
    selection = merged[merged["et_date"].isin(selection_dates)].copy()
    holdout = merged[merged["et_date"].isin(holdout_dates)].copy()

    assignment_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []

    for horizon in HORIZONS:
        for thesis in ("FADE", "FOLLOW", "FLOW_FOLLOW", "MODEL"):
            baseline_rows.append(_trade_stats(holdout[holdout["horizon"] == horizon], horizon, thesis, thesis.lower(), "holdout"))
        for regime, sel_regime in selection[selection["horizon"] == horizon].groupby("regime", dropna=False):
            regime_name = str(regime)
            fade = _trade_stats(sel_regime, horizon, "FADE", regime_name, "selection")
            follow = _trade_stats(sel_regime, horizon, "FOLLOW", regime_name, "selection")
            candidates = [fade, follow]
            eligible = [c for c in candidates if int(c.get("n", 0)) >= 50]
            if not eligible:
                thesis = "STAND_ASIDE"
                selection_after = float("nan")
            else:
                best = max(eligible, key=lambda r: float(r.get("mean_after_cost_ticks", -999.0)))
                thesis = str(best["thesis"]) if float(best.get("mean_after_cost_ticks", float("nan"))) > 0.0 else "STAND_ASIDE"
                selection_after = float(best.get("mean_after_cost_ticks", float("nan")))
            assignment_rows.append({
                "horizon": horizon,
                "regime": regime_name,
                "selected_thesis": thesis,
                "selection_fade_after_cost_ticks": fade.get("mean_after_cost_ticks"),
                "selection_follow_after_cost_ticks": follow.get("mean_after_cost_ticks"),
                "selection_fade_n": fade.get("n"),
                "selection_follow_n": follow.get("n"),
                "selection_best_after_cost_ticks": selection_after,
                "selection_auc": _auc_for_subset(sel_regime, horizon),
            })

            ho = holdout[(holdout["horizon"] == horizon) & (holdout["regime"].astype(str) == regime_name)].copy()
            if thesis == "STAND_ASIDE":
                row = {"label": regime_name, "split": "holdout", "horizon": horizon, "thesis": thesis, "n": int(len(ho))}
            else:
                row = _trade_stats(ho, horizon, thesis, regime_name, "holdout")
            row["holdout_auc"] = _auc_for_subset(ho, horizon)
            rth = ho[ho["is_rth"].fillna(False) & ~ho["event_blackout"].fillna(False)]
            if thesis != "STAND_ASIDE":
                rth_stats = _trade_stats(rth, horizon, thesis, regime_name, "rth_no_event")
                row["rth_no_event_n"] = rth_stats.get("n", 0)
                row["rth_no_event_after_cost_ticks"] = rth_stats.get("mean_after_cost_ticks")
            else:
                row["rth_no_event_n"] = int(len(rth))
                row["rth_no_event_after_cost_ticks"] = float("nan")
            holdout_rows.append(row)

    assignments = pd.DataFrame(assignment_rows)
    holdout_stats = pd.DataFrame(holdout_rows)
    baseline_stats = pd.DataFrame(baseline_rows)

    pass_candidates = []
    for _, row in holdout_stats.iterrows():
        n = int(row.get("n", 0) or 0)
        after = row.get("mean_after_cost_ticks", np.nan)
        auc = row.get("holdout_auc", np.nan)
        rth_n = int(row.get("rth_no_event_n", 0) or 0)
        rth_after = row.get("rth_no_event_after_cost_ticks", np.nan)
        adverse = row.get("median_adverse_ticks", np.nan)
        stable = rth_n >= 30 and np.isfinite(rth_after) and float(rth_after) >= 0.0
        if (
            str(row.get("thesis")) != "STAND_ASIDE"
            and n >= 50
            and ((np.isfinite(after) and float(after) >= 0.5) or (np.isfinite(auc) and float(auc) >= 0.6))
            and stable
            and np.isfinite(adverse)
        ):
            pass_candidates.append(row.to_dict())

    gate = {
        "passed": bool(pass_candidates),
        "best_candidates": sorted(
            pass_candidates,
            key=lambda r: (
                float(r.get("mean_after_cost_ticks") or -999.0),
                float(r.get("holdout_auc") or -999.0),
                int(r.get("n") or 0),
            ),
            reverse=True,
        )[:10],
        "rules": {
            "min_conditioned_n": 50,
            "min_after_cost_ticks": 0.5,
            "min_auc": 0.6,
            "rth_no_event_min_n": 30,
            "rth_no_event_min_after_cost_ticks": 0.0,
            "cost_ticks": COST_TICKS,
        },
        "selection_dates": selection_dates,
        "holdout_dates": holdout_dates,
        "recommendation": (
            "PASS: research lead only; do not build a dashboard without explicit approval"
            if pass_candidates
            else "FAIL: no dashboard; keep logging/research; V1/V2/V3 untouched"
        ),
        "isolation": {"modified_v1_v2_v3": False, "wrote_live_stores": False, "touched_ports_or_sod": False},
    }
    return assignments, holdout_stats, baseline_stats, gate


def write_plots(metrics: pd.DataFrame, holdout_stats: pd.DataFrame, baseline_stats: pd.DataFrame, output_dir: Path) -> list[str]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []
    paths: list[str] = []
    if not metrics.empty:
        fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
        axes[0].plot(metrics["horizon"], metrics["oos_r2"], marker="o")
        axes[0].set_title("OOS R2 by Horizon")
        axes[1].plot(metrics["horizon"], metrics["rank_ic"], marker="o")
        axes[1].set_title("Rank IC by Horizon")
        axes[2].plot(metrics["horizon"], metrics["auc_up_vs_down_deadzone"], marker="o")
        axes[2].set_title("Direction AUC")
        for ax in axes:
            ax.set_xlabel("Minutes")
            ax.grid(True, alpha=0.3)
        fig.tight_layout()
        path = output_dir / "forward_model_metrics.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        paths.append(str(path))
    eligible = holdout_stats[holdout_stats.get("thesis", pd.Series(dtype=str)) != "STAND_ASIDE"].copy()
    if not eligible.empty:
        top = eligible.sort_values("mean_after_cost_ticks", ascending=False).head(20).copy()
        fig, ax = plt.subplots(figsize=(12, 5))
        labels = [f"{r.regime if hasattr(r, 'regime') else r.label}\\n{int(r.horizon)}m {r.thesis}" for r in top.itertuples()]
        ax.bar(range(len(top)), top["mean_after_cost_ticks"])
        ax.axhline(0.5, color="black", linestyle="--", linewidth=1)
        ax.set_xticks(range(len(top)), labels, rotation=60, ha="right", fontsize=7)
        ax.set_ylabel("After-cost ticks")
        ax.set_title("Top Regime Thesis Holdout Cells")
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()
        path = output_dir / "regime_thesis_holdout.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        paths.append(str(path))
    if not baseline_stats.empty:
        piv = baseline_stats.pivot_table(index="horizon", columns="thesis", values="mean_after_cost_ticks", aggfunc="mean")
        fig, ax = plt.subplots(figsize=(8, 4))
        piv.plot(marker="o", ax=ax)
        ax.axhline(0.0, color="black", linewidth=1)
        ax.set_title("Holdout Baselines")
        ax.set_ylabel("After-cost ticks")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        path = output_dir / "baseline_after_cost.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        paths.append(str(path))
    return paths


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset, quality = build_dataset(Path(args.features), Path(args.predictions), args.model)
    pred_df, model_metrics, model_meta = run_walkforward_models(dataset, float(args.train_frac))
    assignments, holdout_stats, baseline_stats, gate = regime_conditioning(dataset, pred_df, float(args.train_frac))

    dataset_path = output_dir / "forward_return_dataset.csv"
    pred_path = output_dir / "forward_return_predictions.csv"
    metrics_path = output_dir / "forward_model_metrics.csv"
    assignments_path = output_dir / "regime_thesis_assignments.csv"
    holdout_path = output_dir / "regime_thesis_holdout_stats.csv"
    baseline_path = output_dir / "baseline_holdout_stats.csv"
    gate_path = output_dir / "forward_return_gate.json"
    summary_path = output_dir / "forward_return_summary.json"

    dataset.to_csv(dataset_path, index=False)
    pred_df.to_csv(pred_path, index=False)
    model_metrics.to_csv(metrics_path, index=False)
    assignments.to_csv(assignments_path, index=False)
    holdout_stats.to_csv(holdout_path, index=False)
    baseline_stats.to_csv(baseline_path, index=False)
    plot_paths = write_plots(model_metrics, holdout_stats, baseline_stats, output_dir)
    gate_path.write_text(json.dumps(gate, indent=2, default=_json_default), encoding="utf-8")

    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "model": args.model,
        "horizons": list(HORIZONS),
        "cost_ticks": COST_TICKS,
        "deadzone_ticks": DEADZONE_TICKS,
        "quality": quality,
        "rows_dataset": int(len(dataset)),
        "rows_predictions": int(len(pred_df)),
        "regime_counts": dataset["regime"].value_counts(dropna=False).to_dict(),
        "model_meta": model_meta,
        "model_metrics": model_metrics.to_dict(orient="records"),
        "gate": gate,
        "artifacts": {
            "dataset": str(dataset_path),
            "predictions": str(pred_path),
            "model_metrics": str(metrics_path),
            "assignments": str(assignments_path),
            "holdout_stats": str(holdout_path),
            "baseline_stats": str(baseline_path),
            "gate": str(gate_path),
            "summary": str(summary_path),
            "plots": plot_paths,
        },
        "notes": [
            "Forward targets are within ET date only; no cross-day leakage.",
            "Flow-effort regimes reuse V3 annotate_flow_effort as a pure read-only function.",
            "No dashboard or live alert code is modified.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only generic forward-return signal for aggression V2.")
    parser.add_argument("--features", default=str(FEATURE_MATRIX))
    parser.add_argument("--predictions", default=str(PREDICTIONS))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--train-frac", type=float, default=0.7)
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps({
        "ok": summary["ok"],
        "rows_dataset": summary["rows_dataset"],
        "rows_predictions": summary["rows_predictions"],
        "regime_counts": summary["regime_counts"],
        "passed": summary["gate"]["passed"],
        "recommendation": summary["gate"]["recommendation"],
        "artifacts": summary["artifacts"],
    }, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
