#!/usr/bin/env python3
"""Read-only V2 resolution-type diagnostics research (Idea 1).

Guardrail: does NOT import, write to, or restart V1/V2/V3 dashboards.
Does NOT write under aggression_v2_live / aggression_v3_live.
Outputs only under workspace/analysis/aggression_v2_resolution/ and docs/.
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
GAMMA_CACHE = (
    PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_options_context" / "options_context_joined.csv"
)
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_resolution"

TRAJ_FEATURES = [
    "price",
    "fair_value",
    "residual",
    "residual_z",
    "abs_residual_z",
    "d_price_ticks",
    "d_fv_ticks",
]


def _as_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True, format="mixed")


def _safe_mean(s: pd.Series) -> float:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(s.mean()) if len(s) else float("nan")


def _safe_median(s: pd.Series) -> float:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(s.median()) if len(s) else float("nan")


def load_alerts(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["opened_ts"] = _as_utc(df["opened_ts"])
    df["closed_ts"] = _as_utc(df.get("closed_ts", pd.Series(dtype=object)))
    df["deadline_ts"] = _as_utc(df.get("deadline_ts", pd.Series(dtype=object)))
    df["et_date"] = df["et_date"].astype(str)
    share = pd.to_numeric(df["price_share_of_reversion"], errors="coerce")
    status = df["reversion_status"].astype(str).str.lower()
    bucket = pd.Series("EXPIRED", index=df.index, dtype=object)
    resolved = status.eq("resolved") & share.notna()
    bucket.loc[resolved & (share > 0.5)] = "PRICE_TO_FV"
    bucket.loc[resolved & (share <= 0.5)] = "FV_TO_PRICE"
    # unresolved / null share
    bucket.loc[~resolved] = "EXPIRED"
    df["resolution_bucket"] = bucket
    df["price_share"] = share
    return df


def load_signal_logs(live_dir: Path) -> pd.DataFrame:
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
    for col in ("price", "fair_value", "residual", "residual_z"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.set_index("ts").sort_index()
    return df


def load_gamma_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    # find a timestamp column
    ts_col = None
    for c in ("ts", "timestamp", "bucket_ts", "time"):
        if c in df.columns:
            ts_col = c
            break
    if ts_col is None:
        return pd.DataFrame()
    df["ts"] = _as_utc(df[ts_col])
    df = df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last")
    keep = [c for c in ("rr25", "rr50", "rr75", "skew_index", "backbone", "near_spot_gamma", "dv01_gamma") if c in df.columns]
    if not keep:
        return pd.DataFrame()
    out = df.set_index("ts")[keep].sort_index()
    return out


def episode_window(signals: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if signals.empty or pd.isna(start):
        return pd.DataFrame()
    if pd.isna(end):
        end = start + pd.Timedelta(minutes=30)
    # include a few minutes before open for open-time features
    pre = start - pd.Timedelta(minutes=15)
    return signals.loc[(signals.index >= pre) & (signals.index <= end)].copy()


def annotate_traj(window: pd.DataFrame) -> pd.DataFrame:
    if window.empty:
        return window
    out = window.copy()
    out["abs_residual_z"] = out["residual_z"].abs()
    out["d_price_ticks"] = out["price"].diff() / TICK_SIZE
    out["d_fv_ticks"] = out["fair_value"].diff() / TICK_SIZE
    out["d_residual"] = out["residual"].diff()
    return out


def open_time_features(window: pd.DataFrame, opened: pd.Timestamp) -> dict[str, float]:
    """Causal features available at/just before alert open."""
    if window.empty or pd.isna(opened):
        return {}
    pre = window.loc[window.index <= opened].tail(15)
    if pre.empty:
        return {}
    last = pre.iloc[-1]
    prior10 = pre.tail(10)
    price = prior10["price"].astype(float)
    fv = prior10["fair_value"].astype(float)
    resid = prior10["residual"].astype(float)
    z = prior10["residual_z"].astype(float)
    d_price = float(price.iloc[-1] - price.iloc[0]) if len(price) >= 2 else 0.0
    d_fv = float(fv.iloc[-1] - fv.iloc[0]) if len(fv) >= 2 else 0.0
    d_resid = float(resid.iloc[-1] - resid.iloc[0]) if len(resid) >= 2 else 0.0
    abs_p = abs(d_price)
    abs_f = abs(d_fv)
    if abs_p >= abs_f * 1.25 and abs_p > EPS:
        formation = 1.0  # PRICE_LED
    elif abs_f >= abs_p * 1.25 and abs_f > EPS:
        formation = -1.0  # FLOW_LED
    else:
        formation = 0.0
    rets = price.diff().dropna()
    return {
        "open_residual_z": float(last.get("residual_z", np.nan)),
        "open_abs_residual_z": float(abs(last.get("residual_z", np.nan))),
        "open_residual": float(last.get("residual", np.nan)),
        "prior10_d_price_ticks": d_price / TICK_SIZE,
        "prior10_d_fv_ticks": d_fv / TICK_SIZE,
        "prior10_d_residual": d_resid,
        "prior10_price_share_of_gap": abs_p / max(abs_p + abs_f, EPS),
        "formation_sign": formation,
        "prior10_realized_vol": float(rets.std()) if len(rets) else float("nan"),
        "prior10_abs_z_mean": float(z.abs().mean()) if len(z) else float("nan"),
        "prior10_z_change": float(z.iloc[-1] - z.iloc[0]) if len(z) >= 2 else float("nan"),
        "minutes_of_pre_history": float(len(pre)),
    }


def trajectory_summary(window: pd.DataFrame, opened: pd.Timestamp, closed: pd.Timestamp) -> dict[str, float]:
    """Summarize open / midlife / close feature levels during the episode."""
    if window.empty or pd.isna(opened):
        return {}
    life = window.loc[window.index >= opened]
    if life.empty:
        return {}
    if pd.isna(closed):
        closed = life.index.max()
    life = life.loc[life.index <= closed]
    if life.empty:
        return {}
    n = len(life)
    idx_open = 0
    idx_mid = n // 2
    idx_close = n - 1
    out: dict[str, float] = {"n_bars": float(n)}
    for stage, idx in (("open", idx_open), ("mid", idx_mid), ("close", idx_close)):
        row = life.iloc[idx]
        out[f"{stage}_price"] = float(row.get("price", np.nan))
        out[f"{stage}_fair_value"] = float(row.get("fair_value", np.nan))
        out[f"{stage}_residual"] = float(row.get("residual", np.nan))
        out[f"{stage}_residual_z"] = float(row.get("residual_z", np.nan))
        out[f"{stage}_abs_residual_z"] = float(abs(row.get("residual_z", np.nan)))
    # path stats
    out["life_price_change_ticks"] = float((life["price"].iloc[-1] - life["price"].iloc[0]) / TICK_SIZE)
    out["life_fv_change_ticks"] = float((life["fair_value"].iloc[-1] - life["fair_value"].iloc[0]) / TICK_SIZE)
    out["life_abs_price_path_ticks"] = float(life["price"].diff().abs().sum() / TICK_SIZE)
    out["life_abs_fv_path_ticks"] = float(life["fair_value"].diff().abs().sum() / TICK_SIZE)
    out["life_residual_shrink"] = float(abs(life["residual"].iloc[0]) - abs(life["residual"].iloc[-1]))
    out["life_mean_abs_z"] = float(life["residual_z"].abs().mean())
    out["life_max_abs_z"] = float(life["residual_z"].abs().max())
    # who moved more during life
    out["life_price_vs_fv_path_ratio"] = float(
        out["life_abs_price_path_ticks"] / max(out["life_abs_fv_path_ticks"], EPS)
    )
    return out


def attach_gamma_open(features: dict[str, float], gamma: pd.DataFrame, opened: pd.Timestamp) -> dict[str, float]:
    if gamma.empty or pd.isna(opened):
        return features
    # last gamma row at or before open
    prior = gamma.loc[gamma.index <= opened]
    if prior.empty:
        return features
    row = prior.iloc[-1]
    for col in gamma.columns:
        features[f"gamma_{col}"] = float(row[col]) if pd.notna(row[col]) else float("nan")
    # 30m change if available
    earlier = gamma.loc[gamma.index <= opened - pd.Timedelta(minutes=30)]
    if not earlier.empty:
        prev = earlier.iloc[-1]
        for col in ("rr50", "skew_index", "backbone"):
            if col in gamma.columns:
                features[f"gamma_{col}_chg_30m"] = float(row[col] - prev[col]) if pd.notna(row[col]) and pd.notna(prev[col]) else float("nan")
    return features


def build_episode_table(alerts: pd.DataFrame, signals: pd.DataFrame, gamma: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, ep in alerts.iterrows():
        opened = ep["opened_ts"]
        closed = ep["closed_ts"] if pd.notna(ep.get("closed_ts")) else ep.get("deadline_ts")
        win = annotate_traj(episode_window(signals, opened, closed))
        feats = open_time_features(win, opened)
        feats = attach_gamma_open(feats, gamma, opened)
        traj = trajectory_summary(win, opened, closed)
        row = {
            "id": ep.get("id"),
            "et_date": ep.get("et_date"),
            "opened_ts": opened.isoformat() if pd.notna(opened) else None,
            "closed_ts": closed.isoformat() if pd.notna(closed) else None,
            "state": ep.get("state"),
            "reversion_status": ep.get("reversion_status"),
            "resolution_bucket": ep.get("resolution_bucket"),
            "price_share": ep.get("price_share"),
            "open_residual_z_alert": ep.get("open_residual_z"),
            "direction_sign": ep.get("direction_sign"),
            "dir_pnl_15m": ep.get("dir_pnl_15m"),
            "dir_pnl_30m": ep.get("dir_pnl_30m"),
            "adverse_15m": ep.get("adverse_15m"),
            "adverse_30m": ep.get("adverse_30m"),
            "mae_to_resolve_ticks": ep.get("mae_to_resolve_ticks"),
            "minutes_to_close": ep.get("minutes_to_close"),
            **feats,
            **traj,
        }
        rows.append(row)
    return pd.DataFrame(rows)


def bucket_diagnostic_table(episodes: pd.DataFrame) -> pd.DataFrame:
    metric_cols = [
        c
        for c in episodes.columns
        if c.startswith(("open_", "mid_", "close_", "life_", "prior10_", "formation_", "gamma_"))
        or c in ("price_share", "dir_pnl_15m", "dir_pnl_30m", "adverse_15m", "adverse_30m", "mae_to_resolve_ticks")
    ]
    rows = []
    for bucket, sub in episodes.groupby("resolution_bucket", sort=False):
        base = {"bucket": bucket, "n": int(len(sub))}
        for col in metric_cols:
            base[f"{col}_median"] = _safe_median(sub[col])
            base[f"{col}_mean"] = _safe_mean(sub[col])
            q = pd.to_numeric(sub[col], errors="coerce").dropna()
            if len(q):
                base[f"{col}_p25"] = float(q.quantile(0.25))
                base[f"{col}_p75"] = float(q.quantile(0.75))
            else:
                base[f"{col}_p25"] = float("nan")
                base[f"{col}_p75"] = float("nan")
        rows.append(base)
    return pd.DataFrame(rows)


def write_trajectory_plots(episodes: pd.DataFrame, output_dir: Path) -> list[str]:
    """Mean + IQR trajectories at open / mid / close per resolution bucket."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []

    stages = ("open", "mid", "close")
    series_specs = [
        ("abs_residual_z", "Abs residual z"),
        ("residual", "Residual (price - FV)"),
        ("price", "Price"),
        ("fair_value", "Fair value"),
    ]
    buckets = ["PRICE_TO_FV", "FV_TO_PRICE", "EXPIRED"]
    colors = {"PRICE_TO_FV": "#c0392b", "FV_TO_PRICE": "#2980b9", "EXPIRED": "#7f8c8d"}
    written: list[str] = []

    fig, axes = plt.subplots(2, 2, figsize=(11, 8), sharex=True)
    axes = axes.ravel()
    x = np.arange(len(stages))
    for ax, (key, title) in zip(axes, series_specs):
        for bucket in buckets:
            sub = episodes[episodes["resolution_bucket"] == bucket]
            if sub.empty:
                continue
            medians, lows, highs = [], [], []
            for stage in stages:
                col = f"{stage}_{key}"
                if col not in sub:
                    medians.append(np.nan)
                    lows.append(np.nan)
                    highs.append(np.nan)
                    continue
                s = pd.to_numeric(sub[col], errors="coerce").dropna()
                if s.empty:
                    medians.append(np.nan)
                    lows.append(np.nan)
                    highs.append(np.nan)
                else:
                    medians.append(float(s.median()))
                    lows.append(float(s.quantile(0.25)))
                    highs.append(float(s.quantile(0.75)))
            medians_a = np.asarray(medians, dtype=float)
            lows_a = np.asarray(lows, dtype=float)
            highs_a = np.asarray(highs, dtype=float)
            ax.plot(x, medians_a, marker="o", label=f"{bucket} (n={len(sub)})", color=colors[bucket])
            ax.fill_between(x, lows_a, highs_a, alpha=0.15, color=colors[bucket])
        ax.set_xticks(x, stages)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
    axes[0].legend(fontsize=8, loc="best")
    fig.suptitle("Resolution buckets: open / midlife / close trajectories (median + IQR)")
    fig.tight_layout()
    path = output_dir / "resolution_trajectories.png"
    fig.savefig(path, dpi=140)
    plt.close(fig)
    written.append(str(path))

    # Compact life-path comparison bar chart
    life_keys = [
        ("life_price_vs_fv_path_ratio", "Price vs FV path ratio"),
        ("life_abs_price_path_ticks", "Abs price path (ticks)"),
        ("life_abs_fv_path_ticks", "Abs FV path (ticks)"),
        ("life_residual_shrink", "Residual shrink"),
    ]
    fig2, axes2 = plt.subplots(1, 4, figsize=(12, 3.5))
    for ax, (col, title) in zip(axes2, life_keys):
        vals, errs_lo, errs_hi, labels = [], [], [], []
        for bucket in buckets:
            sub = episodes[episodes["resolution_bucket"] == bucket]
            if sub.empty or col not in sub:
                continue
            s = pd.to_numeric(sub[col], errors="coerce").dropna()
            if s.empty:
                continue
            med = float(s.median())
            p25, p75 = float(s.quantile(0.25)), float(s.quantile(0.75))
            vals.append(med)
            errs_lo.append(med - p25)
            errs_hi.append(p75 - med)
            labels.append(f"{bucket}\nn={len(sub)}")
        if not vals:
            continue
        xpos = np.arange(len(vals))
        ax.bar(xpos, vals, color=[colors[b.split("\n")[0]] for b in labels], yerr=[errs_lo, errs_hi], capsize=3)
        ax.set_xticks(xpos, labels, fontsize=7)
        ax.set_title(title, fontsize=9)
        ax.grid(True, axis="y", alpha=0.3)
    fig2.suptitle("Life-path diagnostics by resolution bucket")
    fig2.tight_layout()
    path2 = output_dir / "resolution_life_path_bars.png"
    fig2.savefig(path2, dpi=140)
    plt.close(fig2)
    written.append(str(path2))
    return written


def chronological_split(dates: list[str], train_frac: float = 0.7) -> tuple[list[str], list[str]]:
    ordered = sorted(d for d in dates if d)
    if len(ordered) <= 1:
        return ordered, []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac)))
    return ordered[:cut], ordered[cut:]


def _one_vs_rest_auc(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Simple AUC without sklearn dependency."""
    y = y_true.astype(int)
    s = scores.astype(float)
    mask = np.isfinite(s)
    y, s = y[mask], s[mask]
    pos = s[y == 1]
    neg = s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    # Mann-Whitney
    # for each positive, fraction of negatives scored lower
    total = 0.0
    for p in pos:
        total += float(np.mean(neg < p) + 0.5 * np.mean(neg == p))
    return float(total / len(pos))


def fit_logistic_ova(X: np.ndarray, y: np.ndarray, max_iter: int = 200, lr: float = 0.1) -> tuple[np.ndarray, float]:
    """Binary logistic with L2; returns weights, bias."""
    n, d = X.shape
    w = np.zeros(d)
    b = 0.0
    # standardize
    mu = np.nanmean(X, axis=0)
    sd = np.nanstd(X, axis=0)
    sd = np.where(sd < EPS, 1.0, sd)
    Xs = np.where(np.isfinite(X), (X - mu) / sd, 0.0)
    y = y.astype(float)
    for _ in range(max_iter):
        z = Xs @ w + b
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        err = p - y
        w -= lr * ((Xs.T @ err) / n + 0.01 * w)
        b -= lr * float(err.mean())
    return np.concatenate([mu, sd, w, [b]]), float(b)


def predict_proba_logistic(pack: np.ndarray, X: np.ndarray) -> np.ndarray:
    d = (len(pack) - 1) // 3
    mu, sd, w, b = pack[:d], pack[d : 2 * d], pack[2 * d : 3 * d], pack[-1]
    Xs = np.where(np.isfinite(X), (X - mu) / np.where(sd < EPS, 1.0, sd), 0.0)
    z = Xs @ w + b
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


FEATURE_COLS = [
    "open_abs_residual_z",
    "open_residual",
    "prior10_d_price_ticks",
    "prior10_d_fv_ticks",
    "prior10_d_residual",
    "prior10_price_share_of_gap",
    "formation_sign",
    "prior10_realized_vol",
    "prior10_abs_z_mean",
    "prior10_z_change",
    "gamma_rr50",
    "gamma_skew_index",
    "gamma_rr50_chg_30m",
    "gamma_skew_index_chg_30m",
]


def run_classifier(episodes: pd.DataFrame, train_dates: set[str], holdout_dates: set[str]) -> dict[str, Any]:
    df = episodes.copy()
    # fill missing feature cols
    for c in FEATURE_COLS:
        if c not in df:
            df[c] = np.nan
    X_all = df[FEATURE_COLS].astype(float).to_numpy()
    results = {"buckets": {}, "feature_cols": FEATURE_COLS}
    for bucket in ("PRICE_TO_FV", "FV_TO_PRICE", "EXPIRED"):
        y = (df["resolution_bucket"] == bucket).astype(int).to_numpy()
        train_mask = df["et_date"].isin(train_dates).to_numpy()
        hold_mask = df["et_date"].isin(holdout_dates).to_numpy()
        if train_mask.sum() < 20 or hold_mask.sum() < 10:
            results["buckets"][bucket] = {"ok": False, "reason": "insufficient split size"}
            continue
        if y[train_mask].sum() < 5:
            results["buckets"][bucket] = {"ok": False, "reason": "too few positives in train"}
            continue
        pack, _ = fit_logistic_ova(X_all[train_mask], y[train_mask])
        proba_hold = predict_proba_logistic(pack, X_all[hold_mask])
        auc = _one_vs_rest_auc(y[hold_mask], proba_hold)
        # top-decile filter on holdout
        hold = df.loc[hold_mask].copy()
        hold["proba"] = proba_hold
        hold["y"] = y[hold_mask]
        thr = float(np.nanpercentile(proba_hold, 90)) if len(proba_hold) else float("nan")
        top = hold[hold["proba"] >= thr] if np.isfinite(thr) else hold.iloc[0:0]
        base_fade = pd.to_numeric(hold["dir_pnl_30m"], errors="coerce") / TICK_SIZE
        top_fade = pd.to_numeric(top["dir_pnl_30m"], errors="coerce") / TICK_SIZE
        base_ac = float(base_fade.mean() - COST_TICKS) if base_fade.notna().any() else float("nan")
        top_ac = float(top_fade.mean() - COST_TICKS) if top_fade.notna().any() else float("nan")
        base_hit = float((base_fade > 0).mean()) if base_fade.notna().any() else float("nan")
        top_hit = float((top_fade > 0).mean()) if top_fade.notna().any() else float("nan")
        results["buckets"][bucket] = {
            "ok": True,
            "train_n": int(train_mask.sum()),
            "holdout_n": int(hold_mask.sum()),
            "train_positives": int(y[train_mask].sum()),
            "holdout_positives": int(y[hold_mask].sum()),
            "auc": auc,
            "top_decile_n": int(len(top)),
            "top_decile_threshold": thr,
            "base_fade_after_cost_30m": base_ac,
            "top_fade_after_cost_30m": top_ac,
            "after_cost_lift_ticks": (top_ac - base_ac) if np.isfinite(top_ac) and np.isfinite(base_ac) else float("nan"),
            "base_fade_hit_30m": base_hit,
            "top_fade_hit_30m": top_hit,
            "hit_lift_pp": (top_hit - base_hit) * 100.0 if np.isfinite(top_hit) and np.isfinite(base_hit) else float("nan"),
        }
        # store holdout scores for artifact
        df.loc[hold_mask, f"proba_{bucket}"] = proba_hold
    results["episode_scores"] = df
    return results


def evaluate_gate(clf: dict[str, Any], diagnostics: pd.DataFrame) -> dict[str, Any]:
    buckets = clf.get("buckets", {})
    best = None
    for name, stats in buckets.items():
        if not stats.get("ok"):
            continue
        auc = stats.get("auc", float("nan"))
        lift = stats.get("after_cost_lift_ticks", float("nan"))
        hit_lift = stats.get("hit_lift_pp", float("nan"))
        n = stats.get("top_decile_n", 0)
        auc_ok = np.isfinite(auc) and auc >= 0.6
        trade_ok = n >= 50 and (
            (np.isfinite(lift) and lift >= 0.5) or (np.isfinite(hit_lift) and hit_lift >= 8.0)
        )
        cand = {
            "bucket": name,
            "auc": auc,
            "auc_ok": bool(auc_ok),
            "trade_ok": bool(trade_ok),
            "passed_bucket": bool(auc_ok and trade_ok),
            **stats,
        }
        if best is None or (cand["passed_bucket"] and not best.get("passed_bucket")):
            best = cand
        elif best is not None and cand["passed_bucket"] and best.get("passed_bucket"):
            if (cand.get("auc") or 0) > (best.get("auc") or 0):
                best = cand
        elif best is None or ((cand.get("auc") or 0) > (best.get("auc") or 0)):
            best = cand

    # diagnostic separation: do life_price_vs_fv_path_ratio differ by bucket?
    sep = {}
    if not diagnostics.empty and "life_price_vs_fv_path_ratio_median" in diagnostics.columns:
        for _, row in diagnostics.iterrows():
            sep[str(row["bucket"])] = {
                "n": int(row["n"]),
                "life_price_vs_fv_path_ratio_median": row.get("life_price_vs_fv_path_ratio_median"),
                "life_fv_change_ticks_median": row.get("life_fv_change_ticks_median"),
                "life_price_change_ticks_median": row.get("life_price_change_ticks_median"),
                "open_abs_residual_z_median": row.get("open_abs_residual_z_median"),
            }

    passed = bool(best and best.get("passed_bucket"))
    return {
        "passed": passed,
        "best_bucket": best,
        "all_buckets": buckets,
        "diagnostic_separation": sep,
        "gate_rules": {
            "min_auc": 0.6,
            "min_n_top_decile": 50,
            "min_after_cost_lift_ticks": 0.5,
            "min_hit_lift_pp": 8.0,
            "cost_ticks": COST_TICKS,
        },
        "recommendation": (
            "PASS: resolution open-time filter is a research lead — still no V2.1 without explicit approval"
            if passed
            else "FAIL: do not build V2.1; keep logging; leave V1/V2/V3 untouched"
        ),
        "isolation": {
            "modified_v1_v2_v3": False,
            "wrote_live_stores": False,
            "touched_ports_or_sod": False,
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    alerts = load_alerts(Path(args.alert_log))
    signals = load_signal_logs(Path(args.live_dir))
    gamma = load_gamma_cache(Path(args.gamma_cache))

    episodes = build_episode_table(alerts, signals, gamma)
    # drop rows with no signal coverage
    episodes["has_signal"] = episodes["n_bars"].fillna(0) > 0
    covered = episodes[episodes["has_signal"]].copy()

    diagnostics = bucket_diagnostic_table(covered)
    plot_paths = write_trajectory_plots(covered, output_dir)
    dates = sorted(covered["et_date"].dropna().unique().tolist())
    train_dates, holdout_dates = chronological_split(dates, train_frac=args.train_frac)
    clf = run_classifier(covered, set(train_dates), set(holdout_dates))
    scored = clf.pop("episode_scores")
    gate = evaluate_gate(clf, diagnostics)
    gate["train_dates"] = train_dates
    gate["holdout_dates"] = holdout_dates
    gate["n_alerts"] = int(len(alerts))
    gate["n_covered"] = int(len(covered))
    gate["bucket_counts"] = covered["resolution_bucket"].value_counts().to_dict()
    gate["price_share_filled"] = int(alerts["price_share"].notna().sum())

    # artifacts
    ep_path = output_dir / "resolution_episodes.csv"
    diag_path = output_dir / "resolution_bucket_diagnostics.csv"
    gate_path = output_dir / "resolution_gate.json"
    summary_path = output_dir / "resolution_summary.json"
    scored_path = output_dir / "resolution_holdout_scores.csv"

    covered.to_csv(ep_path, index=False)
    diagnostics.to_csv(diag_path, index=False)
    scored.to_csv(scored_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")

    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "idea": "resolution_type_diagnostics",
        "gate": gate,
        "classifier": clf,
        "artifacts": {
            "episodes": str(ep_path),
            "diagnostics": str(diag_path),
            "scores": str(scored_path),
            "gate": str(gate_path),
            "summary": str(summary_path),
            "trajectory_plots": plot_paths,
        },
        "notes": [
            "Read-only research; V1/V2/V3 untouched.",
            "Alert dates do not overlap TBBO feature matrix; trajectories use live signal_log price/FV/residual only.",
            "EXPIRED bucket includes unresolved episodes and null price_share.",
            "Gamma features attached when options_context_joined.csv has prior timestamps.",
            "Trajectory plots: median + IQR at open/mid/close per bucket.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 resolution-type diagnostics (read-only).")
    parser.add_argument("--alert-log", default=str(V2_LIVE / "alert_log.csv"))
    parser.add_argument("--live-dir", default=str(V2_LIVE))
    parser.add_argument("--gamma-cache", default=str(GAMMA_CACHE))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--train-frac", type=float, default=0.7)
    args = parser.parse_args()
    summary = run(args)
    g = summary["gate"]
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "passed": g["passed"],
                "bucket_counts": g["bucket_counts"],
                "n_covered": g["n_covered"],
                "best_bucket": g.get("best_bucket"),
                "diagnostic_separation": g.get("diagnostic_separation"),
                "recommendation": g["recommendation"],
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
