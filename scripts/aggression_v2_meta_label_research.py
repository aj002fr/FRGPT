#!/usr/bin/env python3
"""Research-only meta-labeling for V2 alert take/skip decisions.

Guardrail: does NOT import, write to, or restart V1/V2/V3 apps.
Reads V2 alert/signal logs and optional offline EV scores; writes only under
workspace/analysis/aggression_v2_meta_label/. No dashboard or *_live/ writes.
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
V2_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
EV_SCORES = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_ev_selectivity" / "ev_selectivity_scores.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_meta_label"

TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
TRAIN_FRAC = 0.7
TAKE_QUANTILES = (0.70, 0.80, 0.90)
EPS = 1e-12

FEATURE_COLS = [
    "open_abs_z",
    "open_z",
    "prior10_realized_vol",
    "prior10_range_ticks",
    "prior10_abs_z_mean",
    "prior10_d_price_ticks",
    "prior10_d_fv_ticks",
    "formation_sign",
    "max_abs_z_open",
    "state_dislocation",
    "ev_score_1m",
    "ev_score_3m",
    "ev_score_5m",
    "ev_score_15m",
    "ev_score_30m",
    "proba_big_30m",
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


def _as_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True, format="mixed")


def _chronological_split(dates: list[str], frac: float = TRAIN_FRAC) -> tuple[list[str], list[str]]:
    ordered = sorted(d for d in dates if d)
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * frac))) if len(ordered) > 1 else len(ordered)
    return ordered[:cut], ordered[cut:]


def load_alerts(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["opened_ts"] = _as_utc(df["opened_ts"])
    df["et_date"] = df["et_date"].astype(str)
    for col in ("dir_pnl_30m", "adverse_30m", "open_residual_z", "max_abs_z"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_signals(live_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(live_dir / "signal_log_*.csv"))):
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = _as_utc(df["ts"])
    df = df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last")
    for c in ("price", "fair_value", "residual", "residual_z"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("ts").sort_index()


def load_ev_scores(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    keep = ["bucket_ts", "horizon", "ev_score", "proba_big_move"]
    return df[[c for c in keep if c in df]].dropna(subset=["bucket_ts"]).sort_values("bucket_ts")


def _nearest_ev_features(ev: pd.DataFrame, ts: pd.Timestamp) -> dict[str, float]:
    out = {f"ev_score_{h}m": np.nan for h in (1, 3, 5, 15, 30)}
    out["proba_big_30m"] = np.nan
    if ev.empty or pd.isna(ts):
        return out
    for horizon in (1, 3, 5, 15, 30):
        sub = ev[(ev["horizon"] == horizon) & (ev["bucket_ts"] <= ts)]
        if sub.empty:
            continue
        row = sub.iloc[-1]
        if (ts - row["bucket_ts"]).total_seconds() <= 90:
            out[f"ev_score_{horizon}m"] = float(row.get("ev_score", np.nan))
            if horizon == 30:
                out["proba_big_30m"] = float(row.get("proba_big_move", np.nan))
    return out


def build_rows(alerts: pd.DataFrame, signals: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, ep in alerts.iterrows():
        opened = ep["opened_ts"]
        if pd.isna(opened) or signals.empty:
            continue
        pre = signals.loc[signals.index <= opened].tail(15)
        if pre.empty:
            continue
        price = pre["price"].astype(float)
        fv = pre["fair_value"].astype(float)
        z = pre["residual_z"].astype(float)
        d_price = float(price.iloc[-1] - price.iloc[0])
        d_fv = float(fv.iloc[-1] - fv.iloc[0])
        abs_p, abs_f = abs(d_price), abs(d_fv)
        formation = 1.0 if abs_p >= abs_f * 1.25 and abs_p > EPS else -1.0 if abs_f >= abs_p * 1.25 and abs_f > EPS else 0.0
        fade_ticks = float(ep.get("dir_pnl_30m", np.nan) / TICK_SIZE)
        adverse_ticks = float(ep.get("adverse_30m", np.nan) / TICK_SIZE)
        fade_after = fade_ticks - COST_TICKS if np.isfinite(fade_ticks) else np.nan
        ev_feats = _nearest_ev_features(ev, opened)
        rows.append({
            "id": ep.get("id"),
            "et_date": ep.get("et_date"),
            "opened_ts": opened,
            "state": ep.get("state"),
            "open_z": float(ep.get("open_residual_z", np.nan)),
            "open_abs_z": abs(float(ep.get("open_residual_z", np.nan))),
            "max_abs_z_open": float(ep.get("max_abs_z", np.nan)),
            "state_dislocation": 1.0 if str(ep.get("state")) == "DISLOCATION" else 0.0,
            "prior10_realized_vol": float(price.diff().dropna().std()) if len(price) > 1 else np.nan,
            "prior10_range_ticks": float((price.max() - price.min()) / TICK_SIZE),
            "prior10_abs_z_mean": float(z.abs().mean()),
            "prior10_d_price_ticks": d_price / TICK_SIZE,
            "prior10_d_fv_ticks": d_fv / TICK_SIZE,
            "formation_sign": formation,
            "fade_ticks_30m": fade_ticks,
            "fade_after_cost_30m": fade_after,
            "adverse_ticks_30m": adverse_ticks,
            "y_profitable": 1 if np.isfinite(fade_after) and fade_after > 0 else 0,
            **ev_feats,
        })
    return pd.DataFrame(rows)


def _usable_features(df: pd.DataFrame) -> list[str]:
    out = []
    for c in FEATURE_COLS:
        if c not in df:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().mean() >= 0.3 and s.nunique(dropna=True) > 1:
            out.append(c)
    return out


def evaluate(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    dates = sorted(data["et_date"].dropna().unique().tolist())
    train_dates, hold_dates = _chronological_split(dates)
    train = data[data["et_date"].isin(train_dates)].dropna(subset=["y_profitable"]).copy()
    hold = data[data["et_date"].isin(hold_dates)].dropna(subset=["fade_after_cost_30m"]).copy()
    features = _usable_features(train)
    if len(train) < 50 or train["y_profitable"].nunique() < 2 or hold.empty or not features:
        hold["proba_take"] = np.nan
        return hold, {"passed": False, "reason": "insufficient train/holdout sample", "feature_cols": features}
    clf = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(max_iter=1000, class_weight="balanced")),
    ])
    clf.fit(train[features].astype(float), train["y_profitable"].astype(int))
    hold = hold.copy()
    hold["proba_take"] = clf.predict_proba(hold[features].astype(float))[:, 1]
    auc = np.nan
    if hold["y_profitable"].nunique() == 2:
        auc = float(roc_auc_score(hold["y_profitable"].astype(int), hold["proba_take"]))
    rows = []
    base = {
        "label": "all_alerts",
        "n": int(len(hold)),
        "auc": auc,
        "after_cost_ticks": float(hold["fade_after_cost_30m"].mean()),
        "hit_rate": float((hold["fade_after_cost_30m"] > 0).mean()),
        "median_adverse_ticks": float(hold["adverse_ticks_30m"].median()),
    }
    rows.append(base)
    passed = False
    for q in TAKE_QUANTILES:
        thr = float(train.assign(proba_take=clf.predict_proba(train[features].astype(float))[:, 1])["proba_take"].quantile(q))
        sub = hold[hold["proba_take"] >= thr]
        row = {
            "label": f"take_top_{int((1-q)*100)}pct",
            "n": int(len(sub)),
            "threshold": thr,
            "auc": auc,
            "after_cost_ticks": float(sub["fade_after_cost_30m"].mean()) if len(sub) else np.nan,
            "hit_rate": float((sub["fade_after_cost_30m"] > 0).mean()) if len(sub) else np.nan,
            "median_adverse_ticks": float(sub["adverse_ticks_30m"].median()) if len(sub) else np.nan,
            "lift_vs_all_ticks": (float(sub["fade_after_cost_30m"].mean()) - base["after_cost_ticks"]) if len(sub) else np.nan,
        }
        rows.append(row)
        if row["n"] >= 50 and ((np.isfinite(row["after_cost_ticks"]) and row["after_cost_ticks"] >= 0.5) or (np.isfinite(auc) and auc >= 0.6)) and np.isfinite(row["median_adverse_ticks"]):
            passed = True
    gate = {
        "passed": passed,
        "auc": auc,
        "train_dates": train_dates,
        "holdout_dates": hold_dates,
        "n_train": int(len(train)),
        "n_holdout": int(len(hold)),
        "feature_cols": features,
        "stats": rows,
        "ev_feature_overlap_rows": int(hold[[c for c in hold.columns if c.startswith("ev_score_")]].notna().any(axis=1).sum()) if any(c.startswith("ev_score_") for c in hold.columns) else 0,
        "recommendation": "PASS: research lead only; no dashboard without explicit approval" if passed else "FAIL: no dashboard; keep research offline; V1/V2/V3 untouched",
        "isolation": {"modified_v1_v2_v3": False, "wrote_live_stores": False, "touched_ports_or_sod": False},
    }
    return hold, gate


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    alerts = load_alerts(Path(args.alert_log))
    signals = load_signals(Path(args.v2_live_dir))
    ev = load_ev_scores(Path(args.ev_scores))
    data = build_rows(alerts, signals, ev)
    hold, gate = evaluate(data)
    data_path = output_dir / "meta_label_dataset.csv"
    hold_path = output_dir / "meta_label_holdout_scores.csv"
    gate_path = output_dir / "meta_label_gate.json"
    summary_path = output_dir / "meta_label_summary.json"
    data.to_csv(data_path, index=False)
    hold.to_csv(hold_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=_json_default), encoding="utf-8")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(data)),
        "gate": gate,
        "artifacts": {"dataset": str(data_path), "holdout_scores": str(hold_path), "gate": str(gate_path), "summary": str(summary_path)},
        "notes": ["Primary signal remains V2 fade; classifier only decides take/skip.", "EV features are used only where timestamp overlap exists."],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "passed": gate["passed"], "rows": len(data), "gate": gate, "artifacts": summary["artifacts"]}, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only V2 alert meta-labeling.")
    parser.add_argument("--alert-log", default=str(V2_LIVE / "alert_log.csv"))
    parser.add_argument("--v2-live-dir", default=str(V2_LIVE))
    parser.add_argument("--ev-scores", default=str(EV_SCORES))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
