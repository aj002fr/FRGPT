"""E26 trading-policy simulation (Phase 5; NO holdout scoring)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, RANDOM_SEED
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.features.event_feature_matrix import load_event_feature_matrix
from scripts.move_exhaustion.reporting.ml_common import (
    append_registry,
    chronological_split,
    feature_set_columns,
    load_event_universe,
    prune_features,
    resolved_mask,
    standardize_fit_transform,
    write_json,
)
from scripts.move_exhaustion.simulation.policies import entry_first_1tick_retrace, entry_immediate, _ts
from scripts.move_exhaustion.simulation.trading import (
    PROB_THRESHOLDS,
    date_pnl_concentration,
    simulate_costed_fade,
)

EXIT_MODES = ("fixed_5m", "fixed_15m", "fixed_30m", "barrier", "abs_z_lt_1")
LATENCY_PRIMARY = 1
LATENCY_SENS = (0, 1)


def _train_val_split(train_dates: list[str]) -> tuple[set[str], set[str]]:
    ordered = sorted(train_dates)
    cut = max(1, int(0.75 * len(ordered)))
    return set(ordered[:cut]), set(ordered[cut:])


def _score_events(
    train_df: pd.DataFrame,
    score_df: pd.DataFrame,
    feats: list[str],
) -> pd.DataFrame:
    feats = prune_features(train_df, feats)
    tr = train_df[resolved_mask(train_df)].copy()
    x_tr = tr[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_tr = (tr["label"].to_numpy(dtype=int) == 1).astype(int)
    m_tr = np.isfinite(x_tr).all(axis=1)
    x_tr, y_tr = x_tr[m_tr], y_tr[m_tr]
    xs_tr, _, mu, sd = standardize_fit_transform(x_tr, None)
    clf = LogisticRegression(C=1.0, max_iter=1000, random_state=RANDOM_SEED)
    clf.fit(xs_tr, y_tr)

    out = score_df.copy()
    x = out[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    mask = np.isfinite(x).all(axis=1)
    proba = np.full(len(out), np.nan)
    xs = (x[mask] - mu) / sd
    proba[mask] = clf.predict_proba(xs)[:, 1]
    out["p_exh"] = proba
    return out


def _extension_bound_model(train_df: pd.DataFrame, score_df: pd.DataFrame) -> pd.Series:
    """Fit E21-style q=0.8 extension model on train; predict on score rows."""
    target = "extension_ticks_15m"
    feat_cols = [
        c
        for c in (
            "abs_move_z_5m",
            "abs_residual_z",
            "flow_z_5m",
            "impact_efficiency_decay",
            "dv01_per_tick",
            "curve_directional_flow_factor",
            "displayed_depth",
            "spread",
        )
        if c in train_df.columns
    ]
    feats = prune_features(train_df.dropna(subset=[target]), feat_cols)
    tr = train_df.dropna(subset=[target]).copy()
    if len(feats) < 2 or len(tr) < 30:
        return pd.Series(np.nan, index=score_df.index)
    x_tr = tr[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    y_tr = tr[target].to_numpy(dtype=float)
    m = np.isfinite(x_tr).all(axis=1) & np.isfinite(y_tr)
    x_tr, y_tr = x_tr[m], y_tr[m]
    reg = HistGradientBoostingRegressor(
        loss="quantile",
        quantile=0.8,
        max_depth=3,
        min_samples_leaf=max(5, int(0.05 * len(y_tr))),
        random_state=RANDOM_SEED,
    )
    reg.fit(x_tr, y_tr)
    x_sc = score_df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    pred = np.full(len(score_df), np.nan)
    ok = np.isfinite(x_sc).all(axis=1)
    pred[ok] = reg.predict(x_sc[ok])
    return pd.Series(pred, index=score_df.index)


def _summarize_trades(rows: pd.DataFrame) -> dict[str, Any]:
    entered = rows[rows["entered"] == True]  # noqa: E712
    if entered.empty:
        return {"n_entered": 0, "mean_net_ticks": None, "hit_rate": None, "median_mae": None}
    nets = pd.to_numeric(entered["net_ticks"], errors="coerce").dropna()
    by_date = entered.groupby("et_date")["net_ticks"].sum()
    conc = date_pnl_concentration(by_date)
    hits = entered["hit"].dropna()
    return {
        "n_events": int(len(rows)),
        "n_entered": int(len(entered)),
        "entry_rate": float(len(entered) / max(len(rows), 1)),
        "mean_net_ticks": float(nets.mean()) if len(nets) else None,
        "median_net_ticks": float(nets.median()) if len(nets) else None,
        "hit_rate": float(hits.mean()) if len(hits) else None,
        "median_mae": float(pd.to_numeric(entered["mae_ticks"], errors="coerce").median()),
        "total_net_ticks": float(nets.sum()) if len(nets) else None,
        "date_concentration": conc,
    }


def run_e26(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    _, detector, holdout = load_event_universe(output_root)
    df = load_event_feature_matrix(output_root)
    df = df[~df["et_date"].isin(holdout)].copy()
    dates = sorted(df["et_date"].dropna().unique().tolist())
    train_dates, eval_dates = chronological_split(dates, 0.7)
    # Nested threshold selection: train / val inside train_dates only
    fit_dates, val_dates = _train_val_split(sorted(train_dates))

    train_df = df[df["et_date"].isin(fit_dates)]
    val_df = df[df["et_date"].isin(val_dates)]
    eval_df = df[df["et_date"].isin(eval_dates)]

    exp_dir = output_root / "experiments" / "E26_trading_policy_simulation"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    feats = feature_set_columns("F7", df)
    val_scored = _score_events(train_df, val_df, feats)
    eval_scored = _score_events(train_df, eval_df, feats)
    val_scored["ext_q80"] = _extension_bound_model(train_df, val_scored)
    eval_scored["ext_q80"] = _extension_bound_model(train_df, eval_scored)

    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)

    # Choose threshold + policy on val only (primary exit fixed_15m, latency=1)
    best: dict[str, Any] | None = None
    val_grid: list[dict[str, Any]] = []
    for thr in PROB_THRESHOLDS:
        for policy in ("P1", "P2", "P3", "P4", "P5"):
            rows = _run_policy(
                panel,
                val_scored,
                policy=policy,
                thr=thr,
                exit_mode="fixed_15m",
                latency_bars=LATENCY_PRIMARY,
                ext_bound_max=3.0,
                abs_z_min=1.0,
            )
            summ = _summarize_trades(pd.DataFrame(rows))
            cell = {"policy": policy, "threshold": thr, **summ}
            val_grid.append(cell)
            score = summ.get("mean_net_ticks")
            if score is None:
                continue
            if best is None or score > (best.get("mean_net_ticks") or -1e9):
                best = cell

    # Eval freeze of best + report all policies at best thr (or 0.65 default)
    thr_star = float(best["threshold"]) if best else 0.65
    policy_star = str(best["policy"]) if best else "P2"
    eval_summaries: dict[str, Any] = {}
    all_eval_rows: list[pd.DataFrame] = []
    for policy in ("P1", "P2", "P3", "P4", "P5"):
        for exit_mode in EXIT_MODES:
            for lat in LATENCY_SENS:
                rows = _run_policy(
                    panel,
                    eval_scored,
                    policy=policy,
                    thr=thr_star if policy != "P1" else 0.0,
                    exit_mode=exit_mode,
                    latency_bars=lat,
                    ext_bound_max=3.0,
                    abs_z_min=1.0,
                )
                key = f"{policy}__{exit_mode}__lat{lat}"
                rdf = pd.DataFrame(rows)
                rdf["policy"] = policy
                rdf["exit_mode"] = exit_mode
                rdf["latency_bars"] = lat
                rdf["threshold"] = thr_star if policy != "P1" else None
                all_eval_rows.append(rdf)
                eval_summaries[key] = _summarize_trades(rdf)

    primary_key = f"{policy_star}__fixed_15m__lat{LATENCY_PRIMARY}"
    primary = eval_summaries.get(primary_key) or {}
    combined = pd.concat(all_eval_rows, ignore_index=True) if all_eval_rows else pd.DataFrame()
    if not combined.empty:
        combined.to_parquet(exp_dir / "eval_trades.parquet", index=False)

    flag = (primary.get("date_concentration") or {}).get("flag_gt_20pct")
    mean_net = primary.get("mean_net_ticks")
    survives_costs = bool(mean_net is not None and mean_net > 0)
    verdict = "research-promising" if survives_costs and not flag else (
        "provisional_cost_edge" if survives_costs else "not_supported_after_costs"
    )

    assumptions = {
        "entry": "fade short at bid / long at ask via mid ± half-spread (spread_mean)",
        "costs": "executable bid/ask entry+exit plus 0.5 tick fee/slippage proxy",
        "latency_primary_bars": LATENCY_PRIMARY,
        "latency_note": "1m grid; 5s/15s latency unavailable — sensitivity uses 0/1 bar",
        "thresholds_selected_on": "train/val nested split only",
        "holdout_used": False,
    }

    conclusion_text = (
        f"Trading simulation on {detector}. Val-selected {policy_star} thr={thr_star}. "
        f"Primary eval ({primary_key}) mean_net={mean_net}, hit={primary.get('hit_rate')}, "
        f"n_entered={primary.get('n_entered')}, date_conc_flag={flag}. "
        f"Verdict={verdict}. Holdout NOT opened."
    )
    metrics = {
        "assumptions": assumptions,
        "val_best": best,
        "val_grid_top": sorted(
            [c for c in val_grid if c.get("mean_net_ticks") is not None],
            key=lambda c: c["mean_net_ticks"],
            reverse=True,
        )[:15],
        "eval_primary": primary,
        "eval_summaries": eval_summaries,
    }
    write_json(exp_dir / "metrics.json", metrics)
    conclusion = {
        "experiment_id": "E26_trading_policy_simulation",
        "status": "completed",
        "verdict": verdict,
        "hypothesis_supported": survives_costs,
        "selected_policy": policy_star,
        "selected_threshold": thr_star,
        "eval_mean_net_ticks": mean_net,
        "date_concentration_flag": flag,
        "conclusion": conclusion_text,
        "next_action": "E29_placebo_falsification",
    }
    manifest = {
        "experiment_id": "E26_trading_policy_simulation",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "verdict": verdict,
        "hypothesis_supported": survives_costs,
        "metrics": metrics,
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    write_json(exp_dir / "manifest.json", manifest)
    write_json(exp_dir / "conclusion.json", conclusion)
    append_registry(
        output_root,
        {
            "experiment_id": "E26_trading_policy_simulation",
            "hypothesis": "Costed fade policies with E18 probs are tradable",
            "data_tier": "TBBO_1m_plus_ridge",
            "status": "completed",
            "primary_metric": f"mean_net={mean_net}",
            "baseline_metric": f"policy={policy_star} thr={thr_star}",
            "incremental_improvement": survives_costs,
            "holdout_used": False,
            "conclusion": conclusion_text,
            "next_action": "E29_placebo_falsification",
        },
        experiment_id="E26_trading_policy_simulation",
    )
    return manifest


def _run_policy(
    panel: pd.DataFrame,
    events: pd.DataFrame,
    *,
    policy: str,
    thr: float,
    exit_mode: str,
    latency_bars: int,
    ext_bound_max: float,
    abs_z_min: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, ev in events.iterrows():
        event = ev.to_dict()
        direction = int(event["event_direction"])
        p = float(pd.to_numeric(event.get("p_exh"), errors="coerce"))
        az = float(pd.to_numeric(event.get("abs_residual_z"), errors="coerce"))
        ext = float(pd.to_numeric(event.get("ext_q80"), errors="coerce"))
        enter = False
        entry_ts = None
        if policy == "P1":
            entry_ts = entry_immediate(panel, event, direction)
            enter = entry_ts is not None
        elif policy == "P2":
            if np.isfinite(p) and p > thr:
                entry_ts = entry_immediate(panel, event, direction)
                enter = entry_ts is not None
        elif policy == "P3":
            if np.isfinite(p) and p > thr and (not np.isfinite(ext) or ext <= ext_bound_max):
                entry_ts = entry_immediate(panel, event, direction)
                enter = entry_ts is not None
        elif policy == "P4":
            if np.isfinite(p) and p > thr and np.isfinite(az) and az >= abs_z_min:
                entry_ts = entry_immediate(panel, event, direction)
                enter = entry_ts is not None
        elif policy == "P5":
            if np.isfinite(p) and p > thr:
                entry_ts = entry_first_1tick_retrace(panel, event, direction)
                enter = entry_ts is not None

        base = {
            "event_id": event.get("event_id"),
            "et_date": event.get("et_date"),
            "p_exh": p if np.isfinite(p) else None,
        }
        if not enter or entry_ts is None:
            rows.append({**base, "entered": False, "net_ticks": None, "hit": None, "mae_ticks": None})
            continue
        sim = simulate_costed_fade(
            panel,
            entry_ts=_ts(entry_ts),
            event_direction=direction,
            et_date=str(event["et_date"]),
            exit_mode=exit_mode,
            latency_bars=latency_bars,
            symbol=str(event.get("symbol") or "TY"),
        )
        rows.append({**base, **sim})
    return rows
