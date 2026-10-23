#!/usr/bin/env python3
"""Read-only V2 magnitude prediction research (Idea 3).

Guardrail: does NOT import, write to, or restart V1/V2/V3 dashboards.
Outputs only under workspace/analysis/aggression_v2_magnitude/.
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
EPS = 1e-12
MOVE_TICKS = 5.0

V2_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
V3_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_live"
RESOLUTION_EP = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_resolution" / "resolution_holdout_scores.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_magnitude"


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


def load_regimes(live_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(live_dir / "regime_log_*.csv"))):
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = _as_utc(df["ts"])
    return df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")


FEATURE_COLS = [
    "open_abs_z",
    "prior10_realized_vol",
    "prior10_range_ticks",
    "prior10_abs_z_mean",
    "prior10_d_price_ticks",
    "prior10_d_fv_ticks",
    "formation_sign",
    "regime_conviction",
    "has_regime",
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


def build_rows(alerts: pd.DataFrame, signals: pd.DataFrame, regimes: pd.DataFrame, resolution: pd.DataFrame) -> pd.DataFrame:
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
        fut = signals.loc[(signals.index > opened) & (signals.index <= opened + pd.Timedelta(minutes=30))]
        if pre.empty or fut.empty:
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
        range_ticks = float((price_pre.max() - price_pre.min()) / TICK_SIZE)
        # forward magnitude
        move_ticks = float(abs(fut["price"].iloc[-1] - price_pre.iloc[-1]) / TICK_SIZE)
        max_abs_move = float((fut["price"] - price_pre.iloc[-1]).abs().max() / TICK_SIZE)
        y = 1 if max_abs_move >= MOVE_TICKS else 0
        # regime
        conviction = np.nan
        has_regime = 0.0
        if not regimes.empty:
            prior = regimes.loc[regimes.index <= opened]
            if not prior.empty and (opened - prior.index[-1]).total_seconds() <= 300:
                conviction = float(pd.to_numeric(prior.iloc[-1].get("conviction"), errors="coerce"))
                has_regime = 1.0
        # resolution probs if available
        proba_expired = proba_fv = proba_price = np.nan
        rid = ep.get("id")
        if rid in res_map:
            rr = res_map[rid]
            proba_expired = rr.get("proba_EXPIRED", np.nan)
            proba_fv = rr.get("proba_FV_TO_PRICE", np.nan)
            proba_price = rr.get("proba_PRICE_TO_FV", np.nan)
        rows.append(
            {
                "id": rid,
                "et_date": ep.get("et_date"),
                "opened_ts": opened,
                "open_abs_z": float(abs(pd.to_numeric(ep.get("open_residual_z"), errors="coerce"))),
                "prior10_realized_vol": float(rets.std()) if len(rets) else float("nan"),
                "prior10_range_ticks": range_ticks,
                "prior10_abs_z_mean": float(z_pre.abs().mean()),
                "prior10_d_price_ticks": d_price / TICK_SIZE,
                "prior10_d_fv_ticks": d_fv / TICK_SIZE,
                "formation_sign": formation,
                "regime_conviction": conviction,
                "has_regime": has_regime,
                "proba_EXPIRED": proba_expired,
                "proba_FV_TO_PRICE": proba_fv,
                "proba_PRICE_TO_FV": proba_price,
                "forward_abs_move_ticks": move_ticks,
                "forward_max_abs_move_ticks": max_abs_move,
                "y_big_move": y,
                "dir_pnl_30m": ep.get("dir_pnl_30m"),
                "adverse_30m": ep.get("adverse_30m"),
            }
        )
    return pd.DataFrame(rows)


def evaluate_gate(auc: float, base_rate: float, top_rate: float, top_n: int) -> dict[str, Any]:
    lift_pp = (top_rate - base_rate) * 100.0 if np.isfinite(top_rate) and np.isfinite(base_rate) else float("nan")
    passed = bool(np.isfinite(auc) and auc >= 0.6 and top_n >= 50 and np.isfinite(lift_pp) and lift_pp >= 5.0)
    return {
        "passed": passed,
        "auc": auc,
        "base_big_move_rate": base_rate,
        "top_decile_big_move_rate": top_rate,
        "lift_pp": lift_pp,
        "top_decile_n": top_n,
        "move_ticks_threshold": MOVE_TICKS,
        "gate_rules": {"min_auc": 0.6, "min_lift_pp": 5.0, "min_n": 50},
        "recommendation": (
            "PASS: magnitude classifier is a research lead — still no V2.1 without explicit approval"
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
    regimes = load_regimes(Path(args.v3_live_dir))
    resolution = pd.read_csv(args.resolution_episodes) if Path(args.resolution_episodes).exists() else pd.DataFrame()

    data = build_rows(alerts, signals, regimes, resolution)
    dates = sorted(data["et_date"].dropna().unique().tolist())
    train_dates, holdout_dates = chronological_split(dates, args.train_frac)
    train = data[data["et_date"].isin(train_dates)]
    hold = data[data["et_date"].isin(holdout_dates)]

    for c in FEATURE_COLS:
        if c not in data:
            data[c] = np.nan
    feat_cols = _usable_feature_cols(train, FEATURE_COLS)
    if not feat_cols:
        feat_cols = [c for c in FEATURE_COLS if c in train.columns][:5]
    X_train = train[feat_cols].astype(float).to_numpy()
    y_train = train["y_big_move"].astype(int).to_numpy()
    X_hold = hold[feat_cols].astype(float).to_numpy()
    y_hold = hold["y_big_move"].astype(int).to_numpy()

    pack = fit_logistic(X_train, y_train) if len(train) >= 30 and y_train.sum() >= 5 else None
    if pack is None:
        gate = evaluate_gate(float("nan"), float("nan"), float("nan"), 0)
        gate["reason"] = "insufficient train sample"
        proba = np.full(len(hold), np.nan)
    else:
        proba = predict_proba(pack, X_hold)
        auc = _auc(y_hold, proba)
        base_rate = float(y_hold.mean()) if len(y_hold) else float("nan")
        thr = float(np.nanpercentile(proba, 90)) if np.isfinite(proba).any() else float("nan")
        top_mask = (proba >= thr) & np.isfinite(proba) if np.isfinite(thr) else np.zeros(len(proba), dtype=bool)
        top_rate = float(y_hold[top_mask].mean()) if top_mask.any() else float("nan")
        gate = evaluate_gate(auc, base_rate, top_rate, int(top_mask.sum()))
        gate["feature_cols_used"] = feat_cols

    hold = hold.copy()
    hold["proba_big_move"] = proba
    gate["train_dates"] = train_dates
    gate["holdout_dates"] = holdout_dates
    gate["n_train"] = int(len(train))
    gate["n_holdout"] = int(len(hold))
    gate["train_positive_rate"] = float(train["y_big_move"].mean()) if len(train) else float("nan")

    data_path = output_dir / "magnitude_episodes.csv"
    hold_path = output_dir / "magnitude_holdout_scores.csv"
    gate_path = output_dir / "magnitude_gate.json"
    summary_path = output_dir / "magnitude_summary.json"
    data.to_csv(data_path, index=False)
    hold.to_csv(hold_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "idea": "magnitude_classifier",
        "feature_cols": FEATURE_COLS,
        "gate": gate,
        "artifacts": {
            "episodes": str(data_path),
            "holdout_scores": str(hold_path),
            "gate": str(gate_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Read-only; V1/V2/V3 untouched.",
            f"Target: max |price move| over next 30m >= {MOVE_TICKS} ticks.",
            "Uses resolution-episode probabilities when available from Idea 1 artifacts.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 magnitude classifier research (read-only).")
    parser.add_argument("--alert-log", default=str(V2_LIVE / "alert_log.csv"))
    parser.add_argument("--v2-live-dir", default=str(V2_LIVE))
    parser.add_argument("--v3-live-dir", default=str(V3_LIVE))
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
                "lift_pp": g.get("lift_pp"),
                "base_big_move_rate": g.get("base_big_move_rate"),
                "top_decile_big_move_rate": g.get("top_decile_big_move_rate"),
                "top_decile_n": g.get("top_decile_n"),
                "recommendation": g.get("recommendation"),
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
