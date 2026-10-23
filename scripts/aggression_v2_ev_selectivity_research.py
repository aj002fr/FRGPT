#!/usr/bin/env python3
"""Research-only EV selectivity for Aggression V2 forward-return signals.

Guardrail: does NOT import, write to, or restart V1/V2/V3 apps.
Reads existing research artifacts only and writes under
workspace/analysis/aggression_v2_ev_selectivity/. No dashboard or *_live/
writes are performed.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, RidgeCV
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_forward_return" / "forward_return_dataset.csv"
PREDICTIONS = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_forward_return" / "forward_return_predictions.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_ev_selectivity"

HORIZONS = (1, 3, 5, 15, 30)
BIG_MOVE_TICKS = {1: 1.0, 3: 2.0, 5: 3.0, 15: 4.0, 30: 5.0}
COST_TICKS = 0.5
TRAIN_FRAC = 0.7
MIN_TRAIN_DAYS = 5
EV_QUANTILES = (0.90, 0.95, 0.98)
EPS = 1e-12

FEATURE_COLS = [
    "agg_cum_daily",
    "agg_1m",
    "agg_5m",
    "agg_30m",
    "agg_rate",
    "agg_accel",
    "signed_streak",
    "flow_decelerating",
    "buysell_ratio",
    "buysell_trend_5m",
    "trade_intensity",
    "vol_per_min",
    "large_trade_share",
    "realized_vol",
    "mom_5m",
    "FV_agg_5m",
    "TU_agg_5m",
    "US_agg_5m",
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
    "is_rth",
    "event_blackout",
]


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


def _chronological_split(dates: list[str], frac: float = TRAIN_FRAC) -> tuple[list[str], list[str]]:
    ordered = sorted(dates)
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * frac))) if len(ordered) > 1 else len(ordered)
    return ordered[:cut], ordered[cut:]


def _load(dataset_path: Path, predictions_path: Path) -> pd.DataFrame:
    ds = pd.read_csv(dataset_path, parse_dates=["bucket_ts"])
    pred = pd.read_csv(predictions_path, parse_dates=["bucket_ts"])
    ds["bucket_ts"] = pd.to_datetime(ds["bucket_ts"], utc=True)
    pred["bucket_ts"] = pd.to_datetime(pred["bucket_ts"], utc=True)
    path_cols = [c for c in ds.columns if c.startswith("path_ret_")]
    feature_cols = ["bucket_ts", *[c for c in FEATURE_COLS if c in ds.columns], *path_cols]
    out = pred.merge(ds[feature_cols], on="bucket_ts", how="left", suffixes=("", "_feat"))
    for col in FEATURE_COLS:
        if col in out and out[col].dtype == bool:
            out[col] = out[col].astype(float)
    for col in ("is_rth", "event_blackout", "flow_decelerating", "cross_tenor_aligned", "cross_tenor_disagrees", "absorption", "in_range", "range_break", "false_break"):
        if col in out:
            out[col] = out[col].map(lambda v: str(v).lower() == "true" if pd.notna(v) else False)
    return out.replace([np.inf, -np.inf], np.nan)


def _usable_features(df: pd.DataFrame) -> list[str]:
    cols = []
    for col in FEATURE_COLS:
        if col not in df:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().mean() >= 0.5 and s.nunique(dropna=True) > 1:
            cols.append(col)
    return cols


def _fit_models(data: pd.DataFrame, feature_cols: list[str], horizon: int) -> pd.DataFrame:
    target = f"fwd_ret_{horizon}m_ticks"
    threshold = BIG_MOVE_TICKS[horizon]
    dates = sorted(data["et_date"].dropna().unique().tolist())
    frames = []
    for test_idx in range(MIN_TRAIN_DAYS, len(dates)):
        train_dates = dates[:test_idx]
        test_date = dates[test_idx]
        train = data[data["et_date"].isin(train_dates)].dropna(subset=[target]).copy()
        test = data[data["et_date"] == test_date].dropna(subset=[target]).copy()
        if train.empty or test.empty:
            continue
        train["y_big"] = (train[target].abs() >= threshold).astype(int)
        x_train = train[feature_cols].astype(float)
        x_test = test[feature_cols].astype(float)
        if train["y_big"].nunique() == 2 and len(train) >= 100:
            clf = Pipeline([
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", LogisticRegression(max_iter=1000, class_weight="balanced")),
            ])
            clf.fit(x_train, train["y_big"])
            proba_big = clf.predict_proba(x_test)[:, 1]
        else:
            proba_big = np.full(len(test), float(train["y_big"].mean()) if len(train) else np.nan)
        mag = Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0])),
        ])
        mag.fit(x_train, train[target].abs().astype(float))
        pred_abs = np.clip(mag.predict(x_test), 0.0, None)
        row = test.copy()
        row["proba_big_move"] = proba_big
        row["pred_abs_move_ticks"] = pred_abs
        row["big_move_threshold_ticks"] = threshold
        row["train_end_date"] = train_dates[-1]
        frames.append(row)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _path_adverse(df: pd.DataFrame, horizon: int, direction: pd.Series) -> pd.Series:
    cols = [f"path_ret_{horizon}m_{i}" for i in range(1, horizon + 1) if f"path_ret_{horizon}m_{i}" in df]
    if not cols:
        return pd.Series(np.nan, index=df.index)
    arr = df[cols].astype(float).to_numpy()
    dirs = direction.astype(float).to_numpy().reshape(-1, 1)
    with np.errstate(all="ignore"):
        return pd.Series(np.nanmin(dirs * arr, axis=1), index=df.index)


def _stats(sub: pd.DataFrame, horizon: int, label: str, split: str) -> dict[str, Any]:
    target = f"fwd_ret_{horizon}m_ticks"
    sub = sub.dropna(subset=[target, "pred_ret_ticks"]).copy()
    direction = np.sign(pd.to_numeric(sub["pred_ret_ticks"], errors="coerce"))
    sub = sub[direction != 0].copy()
    direction = direction.loc[sub.index]
    if sub.empty:
        return {"label": label, "split": split, "horizon": horizon, "n": 0}
    gross = direction * sub[target].astype(float)
    after = gross - COST_TICKS
    adverse = _path_adverse(sub, horizon, direction)
    neg = after[after < 0]
    downside = float(neg.std(ddof=1)) if len(neg) >= 2 else np.nan
    auc = np.nan
    y_big = (sub[target].abs() >= BIG_MOVE_TICKS[horizon]).astype(int)
    if y_big.nunique() == 2 and sub["proba_big_move"].notna().any():
        auc = float(roc_auc_score(y_big, sub["proba_big_move"]))
    return {
        "label": label,
        "split": split,
        "horizon": int(horizon),
        "n": int(len(sub)),
        "hit_rate_gross": float((gross > 0).mean()),
        "mean_gross_ticks": float(gross.mean()),
        "mean_after_cost_ticks": float(after.mean()),
        "median_after_cost_ticks": float(after.median()),
        "median_ev_score": float(sub["ev_score"].median()) if "ev_score" in sub else np.nan,
        "median_adverse_ticks": float(adverse.median()) if adverse.notna().any() else np.nan,
        "p10_adverse_ticks": float(adverse.quantile(0.10)) if adverse.notna().any() else np.nan,
        "sortino_after_cost": float(after.mean() / downside) if np.isfinite(downside) and downside > EPS else np.nan,
        "big_move_auc": auc,
        "realized_big_move_rate": float(y_big.mean()),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = _load(Path(args.dataset), Path(args.predictions))
    feature_cols = _usable_features(data)
    scored_frames = []
    stats_rows = []
    for horizon in HORIZONS:
        target = f"fwd_ret_{horizon}m_ticks"
        hz = data[data["horizon"] == horizon].copy()
        scored = _fit_models(hz, feature_cols, horizon)
        if scored.empty:
            continue
        dir_conf = (pd.to_numeric(scored["pred_prob_up"], errors="coerce") - 0.5).abs().fillna(0.0) * 2.0
        fallback_conf = (pd.to_numeric(scored["pred_ret_ticks"], errors="coerce").abs() / (pd.to_numeric(scored["pred_abs_move_ticks"], errors="coerce") + EPS)).clip(0, 1)
        dir_conf = dir_conf.where(dir_conf > 0, fallback_conf)
        scored["dir_confidence"] = dir_conf
        scored["ev_score"] = scored["proba_big_move"] * scored["pred_abs_move_ticks"] * scored["dir_confidence"] - COST_TICKS
        dates = sorted(scored["et_date"].dropna().unique().tolist())
        select_dates, hold_dates = _chronological_split(dates)
        selection = scored[scored["et_date"].isin(select_dates)].copy()
        holdout = scored[scored["et_date"].isin(hold_dates)].copy()
        stats_rows.append(_stats(holdout, horizon, "model_all", "holdout"))
        for q in EV_QUANTILES:
            threshold = float(selection["ev_score"].quantile(q)) if not selection.empty else np.nan
            subset = holdout[holdout["ev_score"] >= threshold] if np.isfinite(threshold) else holdout.iloc[0:0]
            row = _stats(subset, horizon, f"ev_top_{int((1-q)*100)}pct", "holdout")
            row["selection_ev_threshold"] = threshold
            stats_rows.append(row)
            rth = subset[subset["is_rth"].fillna(False) & ~subset["event_blackout"].fillna(False)]
            rth_row = _stats(rth, horizon, f"ev_top_{int((1-q)*100)}pct", "rth_no_event")
            rth_row["selection_ev_threshold"] = threshold
            stats_rows.append(rth_row)
        scored_frames.append(scored)
    scored_all = pd.concat(scored_frames, ignore_index=True) if scored_frames else pd.DataFrame()
    stats = pd.DataFrame(stats_rows)
    passed_rows = []
    for _, r in stats[stats["split"] == "holdout"].iterrows():
        label = r.get("label")
        horizon = int(r.get("horizon", 0))
        rth = stats[(stats["split"] == "rth_no_event") & (stats["label"] == label) & (stats["horizon"] == horizon)]
        stable = not rth.empty and int(rth.iloc[0].get("n", 0) or 0) >= 30 and float(rth.iloc[0].get("mean_after_cost_ticks", np.nan)) >= 0
        if (
            label != "model_all"
            and int(r.get("n", 0) or 0) >= 50
            and ((np.isfinite(r.get("mean_after_cost_ticks", np.nan)) and float(r["mean_after_cost_ticks"]) >= 0.5)
                 or (np.isfinite(r.get("big_move_auc", np.nan)) and float(r["big_move_auc"]) >= 0.6))
            and stable
            and np.isfinite(r.get("median_adverse_ticks", np.nan))
        ):
            passed_rows.append(r.to_dict())
    gate = {
        "passed": bool(passed_rows),
        "best_candidates": sorted(passed_rows, key=lambda x: (x.get("mean_after_cost_ticks") or -999, x.get("big_move_auc") or -999), reverse=True)[:10],
        "rules": {"min_n": 50, "min_after_cost_ticks": 0.5, "min_auc": 0.6, "rth_no_event_min_n": 30, "cost_ticks": COST_TICKS},
        "recommendation": "PASS: research lead only; no dashboard without explicit approval" if passed_rows else "FAIL: no dashboard; keep research offline; V1/V2/V3 untouched",
        "isolation": {"modified_v1_v2_v3": False, "wrote_live_stores": False, "touched_ports_or_sod": False},
    }
    scored_path = output_dir / "ev_selectivity_scores.csv"
    stats_path = output_dir / "ev_selectivity_stats.csv"
    gate_path = output_dir / "ev_selectivity_gate.json"
    summary_path = output_dir / "ev_selectivity_summary.json"
    scored_all.to_csv(scored_path, index=False)
    stats.to_csv(stats_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=_json_default), encoding="utf-8")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feature_cols": feature_cols,
        "horizons": list(HORIZONS),
        "big_move_ticks": BIG_MOVE_TICKS,
        "rows_scored": int(len(scored_all)),
        "gate": gate,
        "best_stats": stats.sort_values(["mean_after_cost_ticks", "big_move_auc"], ascending=False).head(20).to_dict(orient="records") if not stats.empty else [],
        "artifacts": {"scores": str(scored_path), "stats": str(stats_path), "gate": str(gate_path), "summary": str(summary_path)},
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "passed": gate["passed"], "rows_scored": len(scored_all), "top": summary["best_stats"][:5], "artifacts": summary["artifacts"]}, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only EV selectivity for aggression V2.")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--predictions", default=str(PREDICTIONS))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
