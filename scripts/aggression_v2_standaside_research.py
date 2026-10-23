#!/usr/bin/env python3
"""Read-only V2 stand-aside filter research (Idea 4).

Guardrail: does NOT import, write to, or restart V1/V2/V3 dashboards.
Does NOT change V2 alert emission. Outputs only under workspace/analysis/aggression_v2_standaside/.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
EPS = 1e-12

V2_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
V3_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_live"
RESOLUTION_EP = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_resolution" / "resolution_holdout_scores.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_standaside"


def _as_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True, format="mixed")


def chronological_split(dates: list[str], train_frac: float = 0.7) -> tuple[list[str], list[str]]:
    ordered = sorted(d for d in dates if d)
    if len(ordered) <= 1:
        return ordered, []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac)))
    return ordered[:cut], ordered[cut:]


def _auc(y: np.ndarray, s: np.ndarray) -> float:
    y = y.astype(int)
    mask = np.isfinite(s)
    y, s = y[mask], s[mask]
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    total = 0.0
    for p in pos:
        total += float(np.mean(neg < p) + 0.5 * np.mean(neg == p))
    return float(total / len(pos))


def fit_logistic(X: np.ndarray, y: np.ndarray, max_iter: int = 250, lr: float = 0.1) -> np.ndarray:
    n, d = X.shape
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0)
    sd = np.where(sd < EPS, 1.0, sd)
    Xs = np.where(np.isfinite(X), (X - mu) / sd, 0.0)
    w = np.zeros(d)
    b = 0.0
    y = y.astype(float)
    for _ in range(max_iter):
        z = Xs @ w + b
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        err = p - y
        w -= lr * ((Xs.T @ err) / n + 0.01 * w)
        b -= lr * float(err.mean())
    return np.concatenate([mu, sd, w, [b]])


def predict_proba(pack: np.ndarray, X: np.ndarray) -> np.ndarray:
    d = (len(pack) - 1) // 3
    mu, sd, w, b = pack[:d], pack[d : 2 * d], pack[2 * d : 3 * d], pack[-1]
    Xs = np.where(np.isfinite(X), (X - mu) / np.where(sd < EPS, 1.0, sd), 0.0)
    z = Xs @ w + b
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


def load_alerts(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["opened_ts"] = _as_utc(df["opened_ts"])
    df["et_date"] = df["et_date"].astype(str)
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


FEATURE_COLS = [
    "open_abs_z",
    "prior10_realized_vol",
    "prior10_range_ticks",
    "prior10_abs_z_mean",
    "prior10_d_price_ticks",
    "prior10_d_fv_ticks",
    "formation_sign",
]


def _usable_feature_cols(df: pd.DataFrame, cols: list[str]) -> list[str]:
    out = []
    for c in cols:
        if c not in df:
            continue
        s = pd.to_numeric(df[c], errors="coerce")
        if s.notna().mean() >= 0.3:
            out.append(c)
    return out


def build_rows(alerts: pd.DataFrame, signals: pd.DataFrame, resolution: pd.DataFrame) -> pd.DataFrame:
    res_map = {}
    if not resolution.empty and "id" in resolution.columns:
        for _, r in resolution.iterrows():
            res_map[r["id"]] = r
    rows = []
    for _, ep in alerts.iterrows():
        opened = ep["opened_ts"]
        if pd.isna(opened) or signals.empty:
            continue
        pre = signals.loc[signals.index <= opened].tail(15)
        if pre.empty:
            continue
        price_pre = pre["price"].astype(float)
        fv_pre = pre["fair_value"].astype(float)
        z_pre = pre["residual_z"].astype(float)
        d_price = float(price_pre.iloc[-1] - price_pre.iloc[0])
        d_fv = float(fv_pre.iloc[-1] - fv_pre.iloc[0])
        abs_p, abs_f = abs(d_price), abs(d_fv)
        if abs_p >= abs_f * 1.25 and abs_p > EPS:
            formation = 1.0
        elif abs_f >= abs_p * 1.25 and abs_f > EPS:
            formation = -1.0
        else:
            formation = 0.0
        rets = price_pre.diff().dropna()
        fade_ticks = float(pd.to_numeric(ep.get("dir_pnl_30m"), errors="coerce") / TICK_SIZE)
        adverse_ticks = float(pd.to_numeric(ep.get("adverse_30m"), errors="coerce") / TICK_SIZE)
        # toxic = fade after cost <= -1 tick OR adverse dominates favorable
        toxic = 0
        if np.isfinite(fade_ticks) and (fade_ticks - COST_TICKS) <= -1.0:
            toxic = 1
        if np.isfinite(adverse_ticks) and np.isfinite(fade_ticks) and adverse_ticks >= max(abs(fade_ticks), EPS):
            toxic = 1
        rid = ep.get("id")
        rr = res_map.get(rid, {})
        rows.append(
            {
                "id": rid,
                "et_date": ep.get("et_date"),
                "opened_ts": opened,
                "open_abs_z": float(abs(pd.to_numeric(ep.get("open_residual_z"), errors="coerce"))),
                "prior10_realized_vol": float(rets.std()) if len(rets) else float("nan"),
                "prior10_range_ticks": float((price_pre.max() - price_pre.min()) / TICK_SIZE),
                "prior10_abs_z_mean": float(z_pre.abs().mean()),
                "prior10_d_price_ticks": d_price / TICK_SIZE,
                "prior10_d_fv_ticks": d_fv / TICK_SIZE,
                "formation_sign": formation,
                "fade_ticks_30m": fade_ticks,
                "fade_after_cost_30m": fade_ticks - COST_TICKS if np.isfinite(fade_ticks) else float("nan"),
                "adverse_ticks_30m": adverse_ticks,
                "y_toxic": toxic,
            }
        )
    return pd.DataFrame(rows)


def evaluate_gate(hold: pd.DataFrame, proba: np.ndarray) -> dict[str, Any]:
    hold = hold.copy()
    hold["proba_toxic"] = proba
    # bottom/worst 30% by toxic probability = stand-aside set
    thr = float(np.nanpercentile(proba, 70)) if len(proba) else float("nan")
    aside = hold[hold["proba_toxic"] >= thr] if np.isfinite(thr) else hold.iloc[0:0]
    keep = hold[hold["proba_toxic"] < thr] if np.isfinite(thr) else hold
    aside_ac = float(aside["fade_after_cost_30m"].mean()) if len(aside) else float("nan")
    keep_ac = float(keep["fade_after_cost_30m"].mean()) if len(keep) else float("nan")
    base_ac = float(hold["fade_after_cost_30m"].mean()) if len(hold) else float("nan")
    auc = _auc(hold["y_toxic"].to_numpy(), proba)
    passed = bool(
        np.isfinite(auc)
        and auc >= 0.6
        and len(aside) >= 30
        and len(keep) >= 50
        and np.isfinite(aside_ac)
        and aside_ac <= -0.5
        and np.isfinite(keep_ac)
        and keep_ac > 0.0
    )
    return {
        "passed": passed,
        "auc": auc,
        "standaside_n": int(len(aside)),
        "keep_n": int(len(keep)),
        "standaside_after_cost_30m": aside_ac,
        "keep_after_cost_30m": keep_ac,
        "base_after_cost_30m": base_ac,
        "keep_minus_base_ticks": (keep_ac - base_ac) if np.isfinite(keep_ac) and np.isfinite(base_ac) else float("nan"),
        "threshold_p70": thr,
        "gate_rules": {
            "min_auc": 0.6,
            "aside_max_after_cost": -0.5,
            "keep_min_after_cost": 0.0,
            "min_keep_n": 50,
            "min_aside_n": 30,
        },
        "recommendation": (
            "PASS: stand-aside filter is a research lead — still no V2.1 without explicit approval"
            if passed
            else "FAIL: do not build V2.1; keep logging; leave V1/V2/V3 untouched"
        ),
        "isolation": {"modified_v1_v2_v3": False, "wrote_live_stores": False, "touched_ports_or_sod": False},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    alerts = load_alerts(Path(args.alert_log))
    signals = load_signals(Path(args.v2_live_dir))
    resolution = pd.read_csv(args.resolution_episodes) if Path(args.resolution_episodes).exists() else pd.DataFrame()
    data = build_rows(alerts, signals, resolution)
    dates = sorted(data["et_date"].dropna().unique().tolist())
    train_dates, holdout_dates = chronological_split(dates, args.train_frac)
    train = data[data["et_date"].isin(train_dates)]
    hold = data[data["et_date"].isin(holdout_dates)]
    for c in FEATURE_COLS:
        if c not in data:
            data[c] = np.nan
    feat_cols = _usable_feature_cols(train, FEATURE_COLS)
    if not feat_cols:
        feat_cols = [c for c in FEATURE_COLS if c in train.columns]
    X_train = train[feat_cols].astype(float).to_numpy()
    y_train = train["y_toxic"].astype(int).to_numpy()
    X_hold = hold[feat_cols].astype(float).to_numpy()
    pack = fit_logistic(X_train, y_train) if len(train) >= 30 and y_train.sum() >= 5 else None
    if pack is None:
        proba = np.full(len(hold), np.nan)
        gate = evaluate_gate(hold, proba)
        gate["reason"] = "insufficient train sample"
    else:
        proba = predict_proba(pack, X_hold)
        gate = evaluate_gate(hold, proba)
        gate["feature_cols_used"] = feat_cols
    hold = hold.copy()
    hold["proba_toxic"] = proba
    gate["train_dates"] = train_dates
    gate["holdout_dates"] = holdout_dates
    gate["n_train"] = int(len(train))
    gate["n_holdout"] = int(len(hold))
    gate["train_toxic_rate"] = float(train["y_toxic"].mean()) if len(train) else float("nan")
    gate["holdout_toxic_rate"] = float(hold["y_toxic"].mean()) if len(hold) else float("nan")

    data_path = output_dir / "standaside_episodes.csv"
    hold_path = output_dir / "standaside_holdout_scores.csv"
    gate_path = output_dir / "standaside_gate.json"
    summary_path = output_dir / "standaside_summary.json"
    data.to_csv(data_path, index=False)
    hold.to_csv(hold_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "idea": "standaside_filter",
        "feature_cols": FEATURE_COLS,
        "gate": gate,
        "artifacts": {
            "episodes": str(data_path),
            "holdout_scores": str(hold_path),
            "gate": str(gate_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Read-only; V1/V2/V3 untouched; V2 alert emission unchanged.",
            "Toxic label: fade after-cost <= -1 tick OR adverse >= |fade|.",
            "Stand-aside = top 30% toxic probability; keep = remaining 70%.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 stand-aside filter research (read-only).")
    parser.add_argument("--alert-log", default=str(V2_LIVE / "alert_log.csv"))
    parser.add_argument("--v2-live-dir", default=str(V2_LIVE))
    parser.add_argument("--resolution-episodes", default=str(RESOLUTION_EP))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--train-frac", type=float, default=0.7)
    args = parser.parse_args()
    summary = run(args)
    g = summary["gate"]
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "passed": g.get("passed"),
                "auc": g.get("auc"),
                "standaside_n": g.get("standaside_n"),
                "keep_n": g.get("keep_n"),
                "standaside_after_cost_30m": g.get("standaside_after_cost_30m"),
                "keep_after_cost_30m": g.get("keep_after_cost_30m"),
                "base_after_cost_30m": g.get("base_after_cost_30m"),
                "recommendation": g.get("recommendation"),
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
