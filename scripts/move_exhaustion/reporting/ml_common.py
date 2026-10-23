"""Shared ML utilities for E17–E22 move-exhaustion experiments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED, TICK_SIZE
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts

LABEL_HORIZON_MINUTES = 15
R_TICKS = 4
C_TICKS = 4

FEATURE_SETS: dict[str, list[str]] = {
    "F0": [
        "move_z_5m",
        "abs_move_z_5m",
        "move_ticks_5m",
        "signed_move_z_5m",
    ],
    "F1": [
        "move_z_5m",
        "abs_move_z_5m",
        "move_ticks_5m",
        "residual_z",
        "abs_residual_z",
        "signed_residual_z",
    ],
    "F2": [
        "move_z_5m",
        "abs_move_z_5m",
        "move_ticks_5m",
        "residual_z",
        "abs_residual_z",
        "signed_residual_z",
        "flow_z_5m",
        "signed_flow_z_5m",
        "directional_flow_1m",
    ],
    "F3": [
        "move_z_5m",
        "abs_move_z_5m",
        "residual_z",
        "abs_residual_z",
        "flow_z_5m",
        "impact_efficiency_1m",
        "impact_efficiency_5m",
        "impact_efficiency_decay",
        "dv01_per_tick",
    ],
    "F4": [
        "move_z_5m",
        "abs_move_z_5m",
        "residual_z",
        "abs_residual_z",
        "flow_z_5m",
        "impact_efficiency_decay",
        "dv01_per_tick",
        "displayed_depth",
        "spread",
        "realized_vol",
        "opposing_depth",
    ],
    "F5": [
        "move_z_5m",
        "abs_move_z_5m",
        "residual_z",
        "abs_residual_z",
        "flow_z_5m",
        "impact_efficiency_decay",
        "dv01_per_tick",
        "displayed_depth",
        "spread",
        "curve_directional_flow_factor",
        "target_residual_flow",
        "curve_flow_breadth_norm",
    ],
    "F6": [
        "move_z_5m",
        "abs_move_z_5m",
        "residual_z",
        "abs_residual_z",
        "flow_z_5m",
        "impact_efficiency_decay",
        "dv01_per_tick",
        "curve_directional_flow_factor",
        "is_opening_period",
        "is_late_session",
        "is_high_volatility",
        "is_scheduled_macro",
        "is_auction_window",
        "is_roll_period",
    ],
}

# Family membership for E28 ablation (column substring / exact matches).
FEATURE_FAMILIES: dict[str, list[str]] = {
    "price": [
        "move_z_5m",
        "abs_move_z_5m",
        "move_ticks_5m",
        "signed_move_z_5m",
        "price_velocity",
        "move_velocity",
    ],
    "flow": [
        "flow_z_5m",
        "signed_flow_z_5m",
        "directional_flow_1m",
        "flow_intensity",
        "intensity_acceleration",
        "max_same_side_run",
        "sign_entropy",
        "flow_since_last_extreme",
    ],
    "trade_size": [
        "large_trade_share",
        "avg_trade_size",
        "small_trade_share",
        "large_flow",
        "composition",
    ],
    "impact_efficiency": [
        "impact_efficiency_1m",
        "impact_efficiency_5m",
        "impact_efficiency_decay",
        "dv01_per_tick",
        "contracts_per_tick",
        "impact_shortfall",
        "cum_impact_shortfall",
    ],
    "liquidity": [
        "displayed_depth",
        "opposing_depth",
        "spread",
        "realized_vol",
        "microprice",
        "size_imbalance",
        "apparent_absorption",
    ],
    "V2": [
        "residual_z",
        "abs_residual_z",
        "signed_residual_z",
        "fv_velocity",
        "residual_velocity",
        "gap_widening",
    ],
    "cross_tenor": [
        "curve_directional_flow_factor",
        "target_residual_flow",
        "curve_flow_breadth_norm",
        "n_confirming_tenors",
        "lagged_neighbouring_flow",
        "target_directional_flow",
        "target_is_last_tenor",
    ],
    "regime": [
        "is_opening_period",
        "is_late_session",
        "is_high_volatility",
        "is_scheduled_macro",
        "is_auction_window",
        "is_roll_period",
    ],
}


def family_columns(family: str, available: list[str]) -> list[str]:
    """Return columns belonging to a feature family from an available list."""
    keys = FEATURE_FAMILIES.get(family, [])
    out: list[str] = []
    for col in available:
        low = col.lower()
        for key in keys:
            if col == key or key.lower() in low:
                out.append(col)
                break
    return sorted(set(out))


def fit_resolved_logistic_proba(
    train_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    features: list[str],
    *,
    C: float = 1.0,
) -> dict[str, Any]:
    """Train logistic on resolved train rows; score eval rows. Returns metrics + proba."""
    from sklearn.linear_model import LogisticRegression

    feats = prune_features(train_df, features)
    if len(feats) < 2:
        return {"ok": False, "reason": "insufficient_features", "features": feats}

    def _xy(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
        work = frame[resolved_mask(frame)].copy()
        x = work[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        y = (work["label"].to_numpy(dtype=int) == 1).astype(int)
        mask = np.isfinite(x).all(axis=1)
        return x[mask], y[mask], work.iloc[np.where(mask)[0]]

    x_tr, y_tr, tr_rows = _xy(train_df)
    x_ev, y_ev, ev_rows = _xy(eval_df)
    if len(y_tr) < 30 or len(np.unique(y_tr)) < 2 or len(y_ev) < 5:
        return {"ok": False, "reason": "insufficient_split", "features": feats}
    xs_tr, xs_ev, mu, sd = standardize_fit_transform(x_tr, x_ev)
    clf = LogisticRegression(C=C, max_iter=1000, random_state=RANDOM_SEED)
    clf.fit(xs_tr, y_tr)
    p_tr = clf.predict_proba(xs_tr)[:, 1]
    p_ev = clf.predict_proba(xs_ev)[:, 1]
    return {
        "ok": True,
        "features": feats,
        "model": clf,
        "mu": mu,
        "sd": sd,
        "train_metrics": classification_metrics(y_tr, p_tr),
        "eval_metrics": classification_metrics(y_ev, p_ev),
        "y_train": y_tr,
        "p_train": p_tr,
        "y_eval": y_ev,
        "p_eval": p_ev,
        "train_rows": tr_rows,
        "eval_rows": ev_rows,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def load_holdout_dates(output_root: Path = OUTPUT_ROOT) -> set[str]:
    """Read holdout date list only to exclude them — never score holdout outcomes."""
    e00 = output_root / "experiments" / "E00_data_causality_audit" / "manifest.json"
    if not e00.exists():
        return set()
    manifest = json.loads(e00.read_text(encoding="utf-8"))
    return set(manifest.get("holdout_dates") or [])


def load_event_universe(output_root: Path = OUTPUT_ROOT) -> tuple[pd.DataFrame, str, set[str]]:
    holdout = load_holdout_dates(output_root)
    e01 = json.loads(
        (
            output_root / "experiments" / "E01_event_detector_comparison" / "manifest.json"
        ).read_text(encoding="utf-8")
    )
    detector = e01.get("primary_detector_candidate") or "B_price_plus_flow"
    events = pd.read_csv(
        output_root / "experiments" / "E01_event_detector_comparison" / f"events_{detector}.csv"
    )
    events = events[~events["et_date"].isin(holdout)].copy()
    return events, detector, holdout


def chronological_split(dates: list[str], train_frac: float = 0.7) -> tuple[set[str], set[str]]:
    ordered = sorted(dates)
    split = max(1, int(train_frac * len(ordered)))
    return set(ordered[:split]), set(ordered[split:])


def resolved_mask(df: pd.DataFrame) -> pd.Series:
    labels = pd.to_numeric(df["label"], errors="coerce")
    return labels.isin([0, 1])


def prune_features(
    train_df: pd.DataFrame,
    features: list[str],
    *,
    max_missing: float = 0.30,
    min_var: float = 1e-12,
) -> list[str]:
    kept: list[str] = []
    n = max(len(train_df), 1)
    for feat in features:
        if feat not in train_df.columns:
            continue
        s = pd.to_numeric(train_df[feat], errors="coerce")
        miss = float(s.isna().mean())
        if miss > max_missing:
            continue
        var = float(s.var(ddof=0))
        if not np.isfinite(var) or var < min_var:
            continue
        kept.append(feat)
    # Drop exact duplicates on train
    if kept:
        sub = train_df[kept].apply(pd.to_numeric, errors="coerce")
        sub = sub.loc[:, ~sub.T.duplicated()]
        kept = sub.columns.tolist()
    return kept


def all_eligible_features(df: pd.DataFrame) -> list[str]:
    skip = {
        "label",
        "label_reason",
        "event_id",
        "et_date",
        "event_start_time",
        "event_direction",
        "event_detector",
        "event_universe",
        "first_barrier",
        "hit_ts",
        "grid_note",
        "extension_ticks_15m",
        "max_reversal_ticks_5m",
        "max_reversal_ticks_15m",
        "max_reversal_ticks_30m",
        "primary_regime",
        "flow_regime",
        "dynamics_category",
        "composition_category",
        "efficiency_regime",
    }
    feats: list[str] = []
    for col in df.columns:
        if col in skip or col.startswith("event_"):
            continue
        if df[col].dtype == bool or pd.api.types.is_numeric_dtype(df[col]):
            feats.append(col)
    return sorted(set(feats))


def feature_set_columns(name: str, df: pd.DataFrame) -> list[str]:
    if name == "F7":
        return all_eligible_features(df)
    base = FEATURE_SETS.get(name, [])
    return [c for c in base if c in df.columns]


def precision_at_top_pct(y_true: np.ndarray, y_score: np.ndarray, pct: float = 0.10) -> float | None:
    if len(y_true) < 10:
        return None
    k = max(1, int(len(y_true) * pct))
    order = np.argsort(-y_score)
    top = y_true[order[:k]]
    return float(top.mean())


def classification_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, Any]:
    from sklearn.metrics import (
        average_precision_score,
        brier_score_loss,
        roc_auc_score,
    )

    out: dict[str, Any] = {"n": int(len(y_true))}
    if len(y_true) < 5 or len(np.unique(y_true)) < 2:
        out["ok"] = False
        return out
    out["ok"] = True
    out["brier"] = float(brier_score_loss(y_true, y_prob))
    out["roc_auc"] = float(roc_auc_score(y_true, y_prob))
    out["pr_auc"] = float(average_precision_score(y_true, y_prob))
    out["precision_at_top10pct"] = precision_at_top_pct(y_true, y_prob, 0.10)
    out["base_rate"] = float(y_true.mean())
    return out


def apply_calibration(
    y_train: np.ndarray,
    p_train: np.ndarray,
    p_eval: np.ndarray,
    method: str,
) -> np.ndarray:
    if method == "none":
        return p_eval
    if method == "platt":
        from sklearn.linear_model import LogisticRegression

        lr = LogisticRegression(C=1.0, max_iter=500)
        lr.fit(p_train.reshape(-1, 1), y_train)
        return lr.predict_proba(p_eval.reshape(-1, 1))[:, 1]
    if method == "isotonic":
        from sklearn.isotonic import IsotonicRegression

        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(p_train, y_train)
        return iso.predict(p_eval)
    raise ValueError(f"unknown calibration method: {method}")


def extract_tree_rules(clf: Any, feature_names: list[str]) -> list[dict[str, Any]]:
    """Extract human-readable rules from a fitted sklearn DecisionTreeClassifier."""
    tree = clf.tree_
    rules: list[dict[str, Any]] = []

    def recurse(node: int, conditions: list[str]) -> None:
        if tree.feature[node] < 0:
            counts = tree.value[node][0]
            n = int(tree.n_node_samples[node])
            total = float(counts.sum()) or 1.0
            exh = float(counts[1] / total) if len(counts) > 1 else 0.0
            rules.append(
                {
                    "conditions": list(conditions),
                    "n_samples": n,
                    "exhaustion_rate": exh,
                    "class_counts": {
                        "continuation": int(round(counts[0])),
                        "exhaustion": int(round(counts[1])) if len(counts) > 1 else 0,
                    },
                }
            )
            return
        feat = feature_names[tree.feature[node]]
        thr = float(tree.threshold[node])
        recurse(tree.children_left[node], conditions + [f"{feat} <= {thr:.6g}"])
        recurse(tree.children_right[node], conditions + [f"{feat} > {thr:.6g}"])

    recurse(0, [])
    return rules


def assign_tree_leaf(clf: Any, x: np.ndarray) -> np.ndarray:
    return clf.apply(x)


def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    err = y_true - y_pred
    return float(np.mean(np.maximum(quantile * err, (quantile - 1) * err)))


def quantile_coverage(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Empirical coverage: fraction of y <= prediction (for upper quantile models)."""
    if len(y_true) == 0:
        return float("nan")
    return float(np.mean(y_true <= y_pred))


def max_reversal_ticks(
    prices: pd.Series,
    event: dict[str, Any],
    *,
    horizon_minutes: int,
    tick_size: float = TICK_SIZE["TY"],
) -> float | None:
    """Max adverse move in reversal direction within horizon after event start."""
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    series = prices.sort_index()
    idx = pd.DatetimeIndex(series.index)
    series.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    if start not in series.index:
        prior = series.index[series.index <= start]
        if len(prior) == 0:
            return None
        start = prior[-1]
    p0 = float(series.loc[start])
    window = series[(series.index > start) & (series.index <= start + pd.Timedelta(minutes=horizon_minutes))]
    if window.empty:
        return None
    px = window.astype(float)
    if direction > 0:
        return float((p0 - px.min()) / tick_size)
    return float((px.max() - p0) / tick_size)


def barrier_hit_times(
    prices: pd.Series,
    event: dict[str, Any],
    *,
    horizon_minutes: int = LABEL_HORIZON_MINUTES,
    tick_size: float = TICK_SIZE["TY"],
) -> dict[str, Any]:
    lab = first_barrier_label(
        prices,
        start_ts=_ts(event["event_start_time"]),
        direction=int(event["event_direction"]),
        R_ticks=R_TICKS,
        C_ticks=C_TICKS,
        H=pd.Timedelta(minutes=horizon_minutes),
        tick_size=tick_size,
    )
    hit_ts = lab.get("hit_ts")
    return {
        "label": lab.get("label"),
        "first_barrier": lab.get("first_barrier"),
        "hit_ts": pd.Timestamp(hit_ts) if hit_ts else None,
        "reason": lab.get("reason"),
    }


def expand_hazard_intervals(
    event: dict[str, Any],
    prices: pd.Series,
    *,
    horizon_minutes: int = LABEL_HORIZON_MINUTES,
    bin_minutes: int = 1,
    tick_size: float = TICK_SIZE["TY"],
) -> list[dict[str, Any]]:
    """Expand one event into discrete-time hazard rows on a 1m grid.

    Spec bins 5/15/30s are unavailable on the frozen 1m TBBO panel; primary bin=1m.
    Each row is an at-risk minute with interval indicators for exhaustion / continuation.
    """
    start = _ts(event["event_start_time"])
    direction = int(event["event_direction"])
    series = prices.sort_index()
    idx = pd.DatetimeIndex(series.index)
    series.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    if start not in series.index:
        prior = series.index[series.index <= start]
        if len(prior) == 0:
            return []
        start = prior[-1]
    p0 = float(series.loc[start])
    cont_px = p0 + direction * C_TICKS * tick_size
    rev_px = p0 - direction * R_TICKS * tick_size

    end_horizon = start + pd.Timedelta(minutes=horizon_minutes)
    path = series[(series.index > start) & (series.index <= end_horizon)]
    hit_c: pd.Timestamp | None = None
    hit_r: pd.Timestamp | None = None
    for ts, px in path.items():
        px = float(px)
        if hit_c is None and ((direction > 0 and px >= cont_px) or (direction < 0 and px <= cont_px)):
            hit_c = ts
        if hit_r is None and ((direction > 0 and px <= rev_px) or (direction < 0 and px >= rev_px)):
            hit_r = ts
        if hit_c is not None and hit_r is not None:
            break

    rows: list[dict[str, Any]] = []
    n_bins = int(horizon_minutes / bin_minutes)
    for b in range(n_bins):
        t0 = start + pd.Timedelta(minutes=b * bin_minutes)
        t1 = start + pd.Timedelta(minutes=(b + 1) * bin_minutes)
        if hit_c is not None and hit_c <= t0:
            break
        if hit_r is not None and hit_r <= t0:
            break
        y_exh = int(hit_r is not None and t0 < hit_r <= t1)
        y_cont = int(hit_c is not None and t0 < hit_c <= t1)
        if y_exh and y_cont:
            # Ambiguous same bar — stop and exclude this interval
            break
        censored = int(
            hit_r is None
            and hit_c is None
            and b == n_bins - 1
        )
        rows.append(
            {
                "event_id": event.get("event_id"),
                "et_date": event.get("et_date"),
                "event_age_min": float((b + 1) * bin_minutes),
                "interval_start": t0.isoformat(),
                "interval_end": t1.isoformat(),
                "y_exhaustion": y_exh,
                "y_continuation": y_cont,
                "censored_horizon": censored,
                "at_risk": 1,
            }
        )
        if y_exh or y_cont:
            break
    return rows


def append_registry(
    output_root: Path,
    row: dict[str, Any],
    *,
    experiment_id: str,
) -> None:
    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != experiment_id]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)


def standardize_fit_transform(
    x_train: np.ndarray, x_eval: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray, np.ndarray]:
    mu = x_train.mean(axis=0)
    sd = x_train.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    xs_tr = (x_train - mu) / sd
    xs_ev = None
    if x_eval is not None:
        xs_ev = (x_eval - mu) / sd
    return xs_tr, xs_ev, mu, sd
