"""E03 existing V2 residual baseline on event-conditioned universe."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.simulation.v2_baselines import V2_ENTRY_RULES, simulate_v2_trade

EXIT_MODES = ("barrier", "fixed_30m", "abs_z_lt_1")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _summarize(df: pd.DataFrame) -> dict[str, Any]:
    entered = df[df["entered"] == True]  # noqa: E712
    if entered.empty:
        return {
            "n_events": int(len(df)),
            "n_entered": 0,
            "entry_rate": 0.0,
            "mean_net_ticks": None,
            "median_net_ticks": None,
            "hit_rate": None,
            "median_mae_ticks": None,
            "median_time_in_trade_min": None,
        }
    nets = pd.to_numeric(entered["net_ticks"], errors="coerce").dropna()
    hits = entered["hit"].dropna()
    return {
        "n_events": int(len(df)),
        "n_entered": int(len(entered)),
        "entry_rate": float(len(entered) / max(len(df), 1)),
        "mean_net_ticks": float(nets.mean()) if len(nets) else None,
        "median_net_ticks": float(nets.median()) if len(nets) else None,
        "hit_rate": float(hits.mean()) if len(hits) else None,
        "median_mae_ticks": float(pd.to_numeric(entered["mae_ticks"], errors="coerce").median()),
        "median_time_in_trade_min": float(
            pd.to_numeric(entered["time_in_trade_minutes"], errors="coerce").median()
        ),
    }


def run_e03(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
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
    e02_path = output_root / "experiments" / "E02_price_only_baselines" / "manifest.json"
    e02_best = None
    e02_mean = None
    if e02_path.exists():
        e02 = json.loads(e02_path.read_text(encoding="utf-8"))
        e02_best = e02.get("best_price_only_policy")
        if e02_best:
            e02_mean = (e02.get("summaries") or {}).get(e02_best, {}).get("mean_net_ticks")

    holdout = set(e00.get("holdout_dates") or [])
    detector = e01.get("primary_detector_candidate") or "B_price_plus_flow"
    events = pd.read_csv(
        output_root / "experiments" / "E01_event_detector_comparison" / f"events_{detector}.csv"
    )
    events = events[~events["et_date"].isin(holdout)].copy()

    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)

    exp_dir = output_root / "experiments" / "E03_v2_residual_baseline"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    summaries: dict[str, Any] = {}
    all_rows = []
    for rule_name, entry_fn in V2_ENTRY_RULES.items():
        for exit_mode in EXIT_MODES:
            key = f"{rule_name}__{exit_mode}"
            rows = []
            for _, ev in events.iterrows():
                event = ev.to_dict()
                direction = int(event["event_direction"])
                entry = entry_fn(panel, event, direction)
                if entry is None:
                    rows.append(
                        {
                            "event_id": event["event_id"],
                            "et_date": event["et_date"],
                            "rule": rule_name,
                            "exit_mode": exit_mode,
                            "entered": False,
                            "net_ticks": None,
                            "hit": None,
                            "mae_ticks": None,
                            "mfe_ticks": None,
                            "time_in_trade_minutes": None,
                        }
                    )
                    continue
                sim = simulate_v2_trade(
                    panel,
                    entry_ts=entry,
                    event_direction=direction,
                    exit_mode=exit_mode,
                    symbol=str(event.get("symbol") or "TY"),
                )
                rows.append(
                    {
                        "event_id": event["event_id"],
                        "et_date": event["et_date"],
                        "rule": rule_name,
                        "exit_mode": exit_mode,
                        **sim,
                    }
                )
            df = pd.DataFrame(rows)
            df.to_csv(exp_dir / f"results_{key}.csv", index=False)
            summaries[key] = _summarize(df)
            all_rows.append(df)

    combined = pd.concat(all_rows, ignore_index=True)
    combined.to_parquet(exp_dir / "all_v2_baseline_results.parquet", index=False)

    # Best among entered policies by mean net ticks
    ranked = sorted(
        ((k, v) for k, v in summaries.items() if (v.get("n_entered") or 0) > 0),
        key=lambda kv: (kv[1].get("mean_net_ticks") is not None, kv[1].get("mean_net_ticks") or -1e9),
        reverse=True,
    )
    best = ranked[0][0] if ranked else None
    best_mean = summaries.get(best, {}).get("mean_net_ticks") if best else None

    beats_price = (
        best_mean is not None and e02_mean is not None and best_mean > float(e02_mean)
    )

    conclusion = {
        "experiment_id": "E03_v2_residual_baseline",
        "status": "completed",
        "event_universe": detector,
        "best_v2_policy": best,
        "best_mean_net_ticks": best_mean,
        "e02_price_only_best": e02_best,
        "e02_mean_net_ticks": e02_mean,
        "beats_price_only_baseline": beats_price,
        "conclusion": (
            f"Event-conditioned V2 residual rules on {detector}. "
            f"Best: {best} (mean net ticks={best_mean}). "
            f"Price-only E02 best {e02_best} mean={e02_mean}. "
            f"{'Beats' if beats_price else 'Does not beat'} price-only baseline on this pre-cost metric. "
            "Holdout untouched. Probability-based exit deferred until classifier exists."
        ),
        "next_action": "E04_flow_dominance_categories",
    }
    manifest = {
        "experiment_id": "E03_v2_residual_baseline",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(events)),
        "summaries": summaries,
        "best_v2_policy": best,
        "comparison_to_e02": {
            "e02_best": e02_best,
            "e02_mean_net_ticks": e02_mean,
            "beats_price_only": beats_price,
        },
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", summaries)
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E03_v2_residual_baseline",
        "hypothesis": "Event-conditioned V2 residual fades vs price-only baseline",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best,
        "baseline_metric": e02_mean,
        "incremental_improvement": (
            None if best_mean is None or e02_mean is None else float(best_mean) - float(e02_mean)
        ),
        "holdout_used": False,
        "conclusion": conclusion["conclusion"],
        "next_action": conclusion["next_action"],
    }
    if registry.exists():
        reg = pd.read_csv(registry)
        reg = pd.concat([reg, pd.DataFrame([row])], ignore_index=True)
    else:
        reg = pd.DataFrame([row])
    reg.to_csv(registry, index=False)
    return manifest
