"""E05 DV01 / contracts-per-tick exhaustion features."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS, TICK_SIZE
from scripts.move_exhaustion.simulation.policies import _day_slice, _ts

PROGRESS_FLOOR_TICKS = 0.25


def per_tick_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    symbol: str = "TY",
    lookback_bars: int = 5,
) -> dict[str, float | None]:
    """Causal flow-per-tick diagnostics at event start."""
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= start].tail(lookback_bars + 1)
    if len(hist) < 2:
        return {
            "directional_progress_ticks": None,
            "directional_dv01": None,
            "dv01_per_tick": None,
            "contracts_per_tick": None,
            "trades_per_tick": None,
        }
    direction = int(event["event_direction"])
    px = pd.to_numeric(hist["price"], errors="coerce")
    progress = max(direction * (float(px.iloc[-1]) - float(px.iloc[0])) / tick, 0.0)
    denom = max(progress, PROGRESS_FLOOR_TICKS)

    flow_col = "agg_5m" if "agg_5m" in hist.columns else "agg_1m"
    # Panel agg is already DV01-weighted in V2 features
    dir_dv01 = max(direction * float(pd.to_numeric(hist[flow_col], errors="coerce").iloc[-1]), 0.0)
    intensity = pd.to_numeric(hist.get("trade_intensity"), errors="coerce")
    trades = float(intensity.iloc[-1]) if intensity is not None and np.isfinite(intensity.iloc[-1]) else None

    # Approximate contracts from DV01 / unit DV01
    from scripts.move_exhaustion.data.loader import load_dv01

    unit = float(load_dv01().get(symbol, 85.0))
    contracts = dir_dv01 / max(unit, EPS)

    return {
        "directional_progress_ticks": float(progress),
        "directional_dv01": float(dir_dv01),
        "dv01_per_tick": float(dir_dv01 / denom),
        "contracts_per_tick": float(contracts / denom),
        "trades_per_tick": float(trades / denom) if trades is not None else None,
        "progress_floor_applied": bool(progress < PROGRESS_FLOOR_TICKS),
    }


def event_path_per_tick(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    symbol: str = "TY",
    observation_time: Any | None = None,
    max_lookback_minutes: int = 10,
) -> dict[str, float | None]:
    """Path effort features using only information <= observation_time.

    Default observation_time = event start → uses pre-event lookback only
    (no forward label-horizon leakage).
    """
    tick = TICK_SIZE[symbol]
    start = _ts(event["event_start_time"])
    obs = _ts(observation_time) if observation_time is not None else start
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    # Causal window: (obs - lookback, obs]
    lo = obs - pd.Timedelta(minutes=max_lookback_minutes)
    window = day[(day["bucket_ts"] > lo) & (day["bucket_ts"] <= obs)]
    if len(window) < 2:
        return {
            "event_dv01_per_tick": None,
            "flow_since_last_new_extreme": None,
            "n_new_extremes": None,
            "event_progress_ticks": None,
        }

    direction = int(event["event_direction"])
    px = pd.to_numeric(window["price"], errors="coerce").to_numpy(dtype=float)
    flow_1m = "agg_1m" if "agg_1m" in window.columns else ("agg_5m" if "agg_5m" in window.columns else None)
    if flow_1m is None:
        flows = np.zeros(len(window))
    else:
        flows = direction * pd.to_numeric(window[flow_1m], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    pos_flow = np.clip(flows, 0.0, None)

    extreme = px[0]
    n_ext = 0
    last_ext_flow = 0.0
    cum_flow = 0.0
    for i in range(1, len(px)):
        cum_flow += pos_flow[i]
        moved = False
        if direction > 0 and px[i] > extreme + 0.5 * tick:
            extreme = px[i]
            n_ext += 1
            moved = True
        elif direction < 0 and px[i] < extreme - 0.5 * tick:
            extreme = px[i]
            n_ext += 1
            moved = True
        if moved:
            last_ext_flow = 0.0
        else:
            last_ext_flow += pos_flow[i]

    progress_ticks = abs(extreme - px[0]) / tick
    denom = max(progress_ticks, PROGRESS_FLOOR_TICKS)
    return {
        "event_dv01_per_tick": float(cum_flow / denom),
        "flow_since_last_new_extreme": float(last_ext_flow),
        "n_new_extremes": float(n_ext),
        "event_progress_ticks": float(progress_ticks),
        "path_window": "pre_event_lookback_10m",
    }


def decile_exhaustion_curve(df: pd.DataFrame, feature: str) -> pd.DataFrame:
    """Resolved-only exhaustion rate by feature decile."""
    work = df.dropna(subset=[feature, "label"]).copy()
    work = work[work["label"].isin([0, 1])]
    if work.empty:
        return pd.DataFrame()
    try:
        work["decile"] = pd.qcut(pd.to_numeric(work[feature], errors="coerce"), 10, labels=False, duplicates="drop")
    except ValueError:
        work["decile"] = 0
    rows = []
    for d, g in work.groupby("decile"):
        rows.append(
            {
                "decile": int(d),
                "n": int(len(g)),
                "exhaustion_rate": float((g["label"] == 1).mean()),
                "feature_median": float(pd.to_numeric(g[feature], errors="coerce").median()),
            }
        )
    return pd.DataFrame(rows)


def univariate_logistic_auc(df: pd.DataFrame, feature: str) -> dict[str, Any]:
    """Fit logistic on resolved labels; report coef, AUC if sklearn available."""
    work = df.dropna(subset=[feature, "label"]).copy()
    work = work[work["label"].isin([0, 1])]
    if len(work) < 40 or work["label"].nunique() < 2:
        return {"ok": False, "reason": "insufficient_resolved", "n": int(len(work))}
    x = pd.to_numeric(work[feature], errors="coerce").to_numpy(dtype=float).reshape(-1, 1)
    y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
    # Standardize
    mu, sd = float(np.nanmean(x)), float(np.nanstd(x))
    if not np.isfinite(sd) or sd < EPS:
        return {"ok": False, "reason": "zero_variance"}
    xs = (x - mu) / sd
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
    except ImportError:
        # Fallback: point-biserial correlation
        corr = float(np.corrcoef(xs.ravel(), y)[0, 1])
        return {"ok": True, "model": "corr_fallback", "corr": corr, "n": int(len(y)), "coef": corr}
    clf = LogisticRegression(C=1.0, max_iter=500)
    clf.fit(xs, y)
    proba = clf.predict_proba(xs)[:, 1]
    auc = float(roc_auc_score(y, proba)) if len(np.unique(y)) > 1 else None
    return {
        "ok": True,
        "model": "logistic",
        "coef_standardized": float(clf.coef_.ravel()[0]),
        "intercept": float(clf.intercept_.ravel()[0]),
        "auc": auc,
        "n": int(len(y)),
        "positive_rate": float(y.mean()),
        "feature_mean": mu,
        "feature_std": sd,
    }
