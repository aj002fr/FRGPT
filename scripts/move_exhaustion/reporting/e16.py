"""E16 time-of-day and scheduled-event regime model comparison."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, TICK_SIZE
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.features.session_regimes import (
    PRIMARY_PRIORITY,
    load_us_auction_events,
    load_us_high_importance_events,
    session_regime_features,
    train_high_vol_threshold,
)
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.simulation.policies import _ts

BASE_FEATURES = ("abs_residual_z", "abs_move_z_5m", "abs_flow_z_5m", "signed_residual_z")
REGIME_FLAGS = (
    "is_opening_period",
    "is_late_session",
    "is_high_volatility",
    "is_scheduled_macro",
    "is_auction_window",
    "is_roll_period",
)
MIN_SEPARATE_N = 40
MIN_EXCLUDE_N = 20
AUC_INTERACTION_EDGE = 0.02
AUC_SEPARATE_EDGE = 0.03
AUC_EXCLUDE_EDGE = 0.02


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _resolved(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    work["label"] = pd.to_numeric(work["label"], errors="coerce")
    return work[work["label"].isin([0, 1])].copy()


def _regime_stats(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cat, g in df.groupby("primary_regime"):
        resolved = _resolved(g)
        n_res = len(resolved)
        out[str(cat)] = {
            "n": int(len(g)),
            "n_resolved": n_res,
            "exhaustion_rate": float((resolved["label"] == 1).mean()) if n_res else None,
            "continuation_rate": float((resolved["label"] == 0).mean()) if n_res else None,
        }
    return out


def _xy(df: pd.DataFrame, features: list[str]) -> tuple[np.ndarray, np.ndarray] | None:
    work = _resolved(df)
    if work.empty:
        return None
    x = work[features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y = work["label"].to_numpy(dtype=int)
    mask = np.isfinite(x).all(axis=1)
    x, y = x[mask], y[mask]
    if len(y) < 30 or len(np.unique(y)) < 2:
        return None
    return x, y


def _fit_eval(
    train_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    features: list[str],
) -> dict[str, Any]:
    tr = _xy(train_df, features)
    if tr is None:
        return {"ok": False, "reason": "insufficient_train", "features": features}
    x_tr, y_tr = tr
    mu, sd = x_tr.mean(axis=0), x_tr.std(axis=0)
    sd = np.where(sd < 1e-12, 1.0, sd)
    xs_tr = (x_tr - mu) / sd
    clf = LogisticRegression(C=1.0, max_iter=500)
    clf.fit(xs_tr, y_tr)
    train_proba = clf.predict_proba(xs_tr)[:, 1]
    out: dict[str, Any] = {
        "ok": True,
        "features": features,
        "n_train": int(len(y_tr)),
        "train_auc": float(roc_auc_score(y_tr, train_proba)),
        "train_brier": float(brier_score_loss(y_tr, train_proba)),
        "coefs": {f: float(c) for f, c in zip(features, clf.coef_.ravel())},
    }
    ev = _xy(eval_df, features)
    if ev is None:
        out["eval_auc"] = None
        out["eval_brier"] = None
        out["n_eval"] = 0
        return out
    x_ev, y_ev = ev
    xs_ev = (x_ev - mu) / sd
    proba = clf.predict_proba(xs_ev)[:, 1]
    out["n_eval"] = int(len(y_ev))
    out["eval_auc"] = float(roc_auc_score(y_ev, proba)) if len(np.unique(y_ev)) > 1 else None
    out["eval_brier"] = float(brier_score_loss(y_ev, proba))
    return out


def _interaction_features(base: list[str], flags: list[str]) -> list[str]:
    feats = list(base) + list(flags)
    # Interact residual magnitude with each special regime.
    for f in flags:
        feats.append(f"abs_residual_z_x_{f}")
    return feats


def _add_interactions(df: pd.DataFrame, flags: list[str]) -> pd.DataFrame:
    out = df.copy()
    az = pd.to_numeric(out["abs_residual_z"], errors="coerce")
    for f in flags:
        out[f"abs_residual_z_x_{f}"] = az * pd.to_numeric(out[f], errors="coerce").fillna(0.0)
    return out


def _separate_models(
    train_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    base: list[str],
) -> dict[str, Any]:
    by_reg: dict[str, Any] = {}
    # Fallback pooled for thin regimes
    pooled = _fit_eval(train_df, eval_df, base)
    eval_scores = []
    eval_labels = []
    for regime in PRIMARY_PRIORITY:
        tr = train_df[train_df["primary_regime"] == regime]
        ev = eval_df[eval_df["primary_regime"] == regime]
        n_tr = len(_resolved(tr))
        if n_tr >= MIN_SEPARATE_N:
            fit = _fit_eval(tr, ev, base)
            fit["mode"] = "separate"
        else:
            fit = _fit_eval(train_df, ev, base)
            fit["mode"] = "pooled_fallback"
            fit["n_train_regime"] = n_tr
        by_reg[regime] = fit
        # Reconstruct combined eval score for overall comparison
        if fit.get("ok") and len(_resolved(ev)) >= 5:
            # Re-fit to get proba on this eval slice using same path as _fit_eval
            use_tr = tr if fit.get("mode") == "separate" else train_df
            tr_xy = _xy(use_tr, base)
            ev_xy = _xy(ev, base)
            if tr_xy is not None and ev_xy is not None:
                x_tr, y_tr = tr_xy
                x_ev, y_ev = ev_xy
                mu, sd = x_tr.mean(axis=0), x_tr.std(axis=0)
                sd = np.where(sd < 1e-12, 1.0, sd)
                clf = LogisticRegression(C=1.0, max_iter=500)
                clf.fit((x_tr - mu) / sd, y_tr)
                proba = clf.predict_proba((x_ev - mu) / sd)[:, 1]
                eval_scores.append(proba)
                eval_labels.append(y_ev)
    overall: dict[str, Any] = {"ok": False}
    if eval_scores:
        y = np.concatenate(eval_labels)
        p = np.concatenate(eval_scores)
        if len(np.unique(y)) > 1:
            overall = {
                "ok": True,
                "eval_auc": float(roc_auc_score(y, p)),
                "eval_brier": float(brier_score_loss(y, p)),
                "n_eval": int(len(y)),
            }
    return {
        "by_regime": by_reg,
        "overall": overall,
        "pooled_reference": {
            "eval_auc": pooled.get("eval_auc"),
            "eval_brier": pooled.get("eval_brier"),
        },
    }


def _exclusion_tests(
    train_df: pd.DataFrame,
    eval_df: pd.DataFrame,
    base: list[str],
) -> dict[str, Any]:
    """Drop selected regimes and re-fit pooled model on the remainder."""
    pooled_all = _fit_eval(train_df, eval_df, base)
    base_auc = pooled_all.get("eval_auc")
    out: dict[str, Any] = {"pooled_all": pooled_all, "filters": {}}
    filters = {
        "exclude_scheduled_macro": "is_scheduled_macro",
        "exclude_auction_window": "is_auction_window",
        "exclude_opening_period": "is_opening_period",
        "exclude_late_session": "is_late_session",
        "exclude_roll_period": "is_roll_period",
        "exclude_high_volatility": "is_high_volatility",
        "ordinary_only": None,
    }
    for name, flag in filters.items():
        if name == "ordinary_only":
            tr = train_df[train_df["is_ordinary"] == True]  # noqa: E712
            ev = eval_df[eval_df["is_ordinary"] == True]  # noqa: E712
        else:
            tr = train_df[train_df[flag] == False]  # noqa: E712
            ev = eval_df[eval_df[flag] == False]  # noqa: E712
        n_ev = len(_resolved(ev))
        fit = _fit_eval(tr, ev, base) if n_ev >= MIN_EXCLUDE_N else {
            "ok": False,
            "reason": "eval_too_thin",
            "n_eval": n_ev,
        }
        delta = None
        if fit.get("ok") and base_auc is not None and fit.get("eval_auc") is not None:
            delta = float(fit["eval_auc"] - base_auc)
        out["filters"][name] = {
            **fit,
            "delta_eval_auc_vs_pooled_all": delta,
            "recommend_exclude": bool(delta is not None and delta >= AUC_EXCLUDE_EDGE),
        }
    return out


def _recommend(
    pooled: dict[str, Any],
    with_ind: dict[str, Any],
    with_int: dict[str, Any],
    separate: dict[str, Any],
    exclusions: dict[str, Any],
) -> dict[str, Any]:
    p_auc = pooled.get("eval_auc")
    i_auc = with_ind.get("eval_auc")
    x_auc = with_int.get("eval_auc")
    s_auc = (separate.get("overall") or {}).get("eval_auc")

    exclude_regs = [
        k.replace("exclude_", "")
        for k, v in (exclusions.get("filters") or {}).items()
        if k.startswith("exclude_") and v.get("recommend_exclude")
    ]
    # Also flag regimes where separate/pooled eval AUC is very weak
    weak = []
    for reg, fit in (separate.get("by_regime") or {}).items():
        if reg == "ordinary":
            continue
        auc = fit.get("eval_auc")
        n = fit.get("n_eval") or 0
        if auc is not None and n >= MIN_EXCLUDE_N and auc < 0.52:
            weak.append(reg)

    choice = "pooled model"
    rationale = []
    if x_auc is not None and p_auc is not None and (x_auc - p_auc) >= AUC_INTERACTION_EDGE:
        choice = "pooled model with interactions"
        rationale.append(f"interactions eval AUC {x_auc:.3f} vs pooled {p_auc:.3f}")
    elif i_auc is not None and p_auc is not None and (i_auc - p_auc) >= AUC_INTERACTION_EDGE:
        choice = "pooled model with interactions"
        rationale.append(f"regime indicators eval AUC {i_auc:.3f} vs pooled {p_auc:.3f}")
    else:
        rationale.append(
            f"pooled={p_auc}, indicators={i_auc}, interactions={x_auc} "
            f"(need +{AUC_INTERACTION_EDGE:.2f} for interaction upgrade)"
        )

    if s_auc is not None and p_auc is not None and (s_auc - p_auc) >= AUC_SEPARATE_EDGE:
        choice = "separate regime models"
        rationale.append(f"separate overall eval AUC {s_auc:.3f} beats pooled by >= {AUC_SEPARATE_EDGE}")

    if exclude_regs or weak:
        # Prefer explicit do-not-signal when exclusion helps or regime is non-informative
        regs = sorted(set(exclude_regs) | set(weak))
        choice = "do not signal in selected regimes"
        rationale.append(f"stand-aside candidates: {regs}")

    return {
        "recommendation": choice,
        "rationale": rationale,
        "stand_aside_regimes": sorted(set(exclude_regs) | set(weak)),
        "eval_aucs": {
            "pooled": p_auc,
            "pooled_with_indicators": i_auc,
            "pooled_with_interactions": x_auc,
            "separate_overall": s_auc,
        },
    }


def run_e16(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    e00 = json.loads(
        (output_root / "experiments" / "E00_data_causality_audit" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    e01 = json.loads(
        (output_root / "experiments" / "E01_event_detector_comparison" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    holdout = set(e00.get("holdout_dates") or [])
    detector = e01.get("primary_detector_candidate") or "B_price_plus_flow"
    events = pd.read_csv(
        output_root / "experiments" / "E01_event_detector_comparison" / f"events_{detector}.csv"
    )
    events = events[~events["et_date"].isin(holdout)].copy()

    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)
    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]

    macro = load_us_high_importance_events()
    auctions = load_us_auction_events()

    # First pass: features with placeholder vol thr, then set thr on train dates.
    dates = sorted(events["et_date"].dropna().unique().tolist())
    split = max(1, int(0.7 * len(dates)))
    train_dates = set(dates[:split])
    eval_dates = set(dates[split:])

    # Provisional abs_move_z from panel at event for train threshold
    tmp_rows = []
    for _, ev in events.iterrows():
        event = ev.to_dict()
        start = _ts(event["event_start_time"])
        day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
        hist = day[day["bucket_ts"] <= start]
        mz = None
        if not hist.empty:
            mz = abs(float(pd.to_numeric(hist.iloc[-1].get("move_z_5m"), errors="coerce")))
            if not np.isfinite(mz):
                mz = None
        tmp_rows.append({"et_date": event["et_date"], "abs_move_z_5m": mz})
    tmp = pd.DataFrame(tmp_rows)
    thr = train_high_vol_threshold(tmp[tmp["et_date"].isin(train_dates)]["abs_move_z_5m"])

    rows = []
    for _, ev in events.iterrows():
        event = ev.to_dict()
        feats = session_regime_features(
            panel, event, macro_events=macro, auction_events=auctions, high_vol_threshold=thr
        )
        lab = first_barrier_label(
            prices,
            start_ts=_ts(event["event_start_time"]),
            direction=int(event["event_direction"]),
            R_ticks=4,
            C_ticks=4,
            H=pd.Timedelta(minutes=15),
            tick_size=TICK_SIZE["TY"],
        )
        rows.append({**event, **feats, "label": lab.get("label")})
    df = pd.DataFrame(rows)
    for f in REGIME_FLAGS + ("is_ordinary",):
        df[f] = df[f].astype(bool)
    df = _add_interactions(df, list(REGIME_FLAGS))

    train_df = df[df["et_date"].isin(train_dates)].copy()
    eval_df = df[df["et_date"].isin(eval_dates)].copy()

    base = list(BASE_FEATURES)
    pooled = _fit_eval(train_df, eval_df, base)
    with_ind = _fit_eval(train_df, eval_df, base + list(REGIME_FLAGS))
    int_feats = _interaction_features(base, list(REGIME_FLAGS))
    with_int = _fit_eval(train_df, eval_df, int_feats)
    separate = _separate_models(train_df, eval_df, base)
    exclusions = _exclusion_tests(train_df, eval_df, base)
    recommendation = _recommend(pooled, with_ind, with_int, separate, exclusions)

    by_reg_all = _regime_stats(df)
    by_reg_eval = _regime_stats(eval_df)
    by_reg_train = _regime_stats(train_df)

    # Flag overlap counts (non-exclusive)
    overlap = {f: int(df[f].sum()) for f in REGIME_FLAGS}
    overlap["ordinary_primary"] = int((df["primary_regime"] == "ordinary").sum())

    exp_dir = output_root / "experiments" / "E16_time_of_day_and_session_regimes"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(exp_dir / "event_session_regime_features.csv", index=False)
    df.to_parquet(exp_dir / "event_session_regime_features.parquet", index=False)

    hypothesis_supported = recommendation["recommendation"] != "pooled model" or bool(
        recommendation.get("stand_aside_regimes")
    )
    # E16 purpose is model-structure recommendation; "supported" means a non-trivial
    # structure beat plain pooled OR stand-aside filters are warranted.
    structure_beats_pooled = False
    p_auc = pooled.get("eval_auc")
    for auc in (
        with_ind.get("eval_auc"),
        with_int.get("eval_auc"),
        (separate.get("overall") or {}).get("eval_auc"),
    ):
        if p_auc is not None and auc is not None and (auc - p_auc) >= AUC_INTERACTION_EDGE:
            structure_beats_pooled = True
    hypothesis_supported = bool(structure_beats_pooled or recommendation.get("stand_aside_regimes"))

    conclusion = {
        "experiment_id": "E16_time_of_day_and_session_regimes",
        "status": "completed",
        "event_universe": detector,
        "hypothesis_supported": hypothesis_supported,
        "recommendation": recommendation["recommendation"],
        "stand_aside_regimes": recommendation.get("stand_aside_regimes"),
        "eval_aucs": recommendation.get("eval_aucs"),
        "high_vol_threshold": thr,
        "roll_definition": "calendar proxy: day 1-12 of Mar/Jun/Sep/Dec (no front-month symbol on panel)",
        "conclusion": (
            f"Regime structure on {detector}. Recommendation: "
            f"{recommendation['recommendation']}. "
            f"Eval AUCs: {recommendation.get('eval_aucs')}. "
            f"Stand-aside: {recommendation.get('stand_aside_regimes') or 'none'}. "
            f"Rationale: {'; '.join(recommendation.get('rationale') or [])}. "
            "Holdout untouched."
        ),
        "next_action": "E17_interaction_only_exhaustion_rules",
    }

    manifest = {
        "experiment_id": "E16_time_of_day_and_session_regimes",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(df)),
        "n_train_dates": len(train_dates),
        "n_eval_dates": len(eval_dates),
        "high_vol_threshold": thr,
        "n_macro_anchors": int(len(macro)),
        "n_auction_anchors": int(len(auctions)),
        "overlap_counts": overlap,
        "by_regime_all": by_reg_all,
        "by_regime_train": by_reg_train,
        "by_regime_eval": by_reg_eval,
        "pooled": pooled,
        "pooled_with_indicators": with_ind,
        "pooled_with_interactions": with_int,
        "separate_models": separate,
        "exclusion_tests": exclusions,
        "recommendation": recommendation,
        "hypothesis_supported": hypothesis_supported,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {
        "pooled": pooled,
        "pooled_with_indicators": with_ind,
        "pooled_with_interactions": with_int,
        "separate_overall": separate.get("overall"),
        "exclusion_tests": exclusions,
    })
    _write_json(exp_dir / "conclusion.json", conclusion)
    _write_json(exp_dir / "recommendation.json", recommendation)
    _write_json(exp_dir / "by_regime_eval.json", by_reg_eval)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E16_time_of_day_and_session_regimes",
        "hypothesis": "One pooled model may be insufficient across TOD/macro/auction/roll regimes",
        "data_tier": "TBBO_1m_plus_ridge_plus_econ_events_ro",
        "status": "completed",
        "primary_metric": recommendation["recommendation"],
        "baseline_metric": f"pooled_eval_auc={p_auc}",
        "incremental_improvement": bool(hypothesis_supported),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = reg[reg["experiment_id"] != "E16_time_of_day_and_session_regimes"]
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
