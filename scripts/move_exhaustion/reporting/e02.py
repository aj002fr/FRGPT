"""E02 simple price-only exhaustion baselines."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.simulation.policies import POLICIES, simulate_fade_from_entry


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _summarize(rows: pd.DataFrame) -> dict[str, Any]:
    entered = rows[rows["entered"] == True]  # noqa: E712
    if entered.empty:
        return {
            "n_events": int(len(rows)),
            "n_entered": 0,
            "entry_rate": 0.0,
            "hit_rate_resolved": None,
            "mean_net_ticks": None,
            "median_net_ticks": None,
            "median_mae_ticks": None,
            "median_mfe_ticks": None,
            "median_time_in_trade_min": None,
        }
    resolved = entered[entered["label"].isin([0, 1])]
    hits = resolved[resolved["hit"] == True]  # noqa: E712
    nets = pd.to_numeric(entered["net_ticks"], errors="coerce").dropna()
    return {
        "n_events": int(len(rows)),
        "n_entered": int(len(entered)),
        "entry_rate": float(len(entered) / max(len(rows), 1)),
        "n_resolved": int(len(resolved)),
        "hit_rate_resolved": float(len(hits) / len(resolved)) if len(resolved) else None,
        "mean_net_ticks": float(nets.mean()) if len(nets) else None,
        "median_net_ticks": float(nets.median()) if len(nets) else None,
        "median_mae_ticks": float(pd.to_numeric(entered["mae_ticks"], errors="coerce").median()),
        "median_mfe_ticks": float(pd.to_numeric(entered["mfe_ticks"], errors="coerce").median()),
        "median_time_in_trade_min": float(
            pd.to_numeric(entered["time_in_trade_minutes"], errors="coerce").median()
        ),
    }


def run_e02(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    e01_path = output_root / "experiments" / "E01_event_detector_comparison" / "manifest.json"
    e00_path = output_root / "experiments" / "E00_data_causality_audit" / "manifest.json"
    if not e01_path.exists() or not e00_path.exists():
        raise RuntimeError("E00/E01 manifests required")
    e01 = json.loads(e01_path.read_text(encoding="utf-8"))
    e00 = json.loads(e00_path.read_text(encoding="utf-8"))
    holdout = set(e00.get("holdout_dates") or [])
    detector = e01.get("primary_detector_candidate") or "B_price_plus_flow"
    events_path = (
        output_root / "experiments" / "E01_event_detector_comparison" / f"events_{detector}.csv"
    )
    if not events_path.exists():
        raise RuntimeError(f"Missing E01 events for {detector}")

    events = pd.read_csv(events_path)
    events = events[~events["et_date"].isin(holdout)].copy()

    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)

    exp_dir = output_root / "experiments" / "E02_price_only_baselines"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    summaries: dict[str, Any] = {}
    all_rows = []
    for name, fn in POLICIES.items():
        rows = []
        for _, ev in events.iterrows():
            event = ev.to_dict()
            direction = int(event["event_direction"])
            entry = fn(panel, event, direction)
            if entry is None:
                rows.append(
                    {
                        "event_id": event["event_id"],
                        "et_date": event["et_date"],
                        "policy": name,
                        "entered": False,
                        "label": None,
                        "hit": None,
                        "net_ticks": None,
                        "mae_ticks": None,
                        "mfe_ticks": None,
                        "time_in_trade_minutes": None,
                    }
                )
                continue
            sim = simulate_fade_from_entry(
                panel,
                entry_ts=entry,
                event_direction=direction,
                symbol=str(event.get("symbol") or "TY"),
            )
            rows.append(
                {
                    "event_id": event["event_id"],
                    "et_date": event["et_date"],
                    "policy": name,
                    **sim,
                }
            )
        df = pd.DataFrame(rows)
        df.to_csv(exp_dir / f"results_{name}.csv", index=False)
        summaries[name] = _summarize(df)
        all_rows.append(df)

    combined = pd.concat(all_rows, ignore_index=True)
    combined.to_parquet(exp_dir / "all_policy_results.parquet", index=False)

    # Rank by mean net ticks among policies with entries
    ranked = sorted(
        ((k, v) for k, v in summaries.items() if v.get("n_entered")),
        key=lambda kv: (
            kv[1].get("mean_net_ticks") is not None,
            kv[1].get("mean_net_ticks") or -1e9,
            kv[1].get("hit_rate_resolved") or 0.0,
        ),
        reverse=True,
    )
    best = ranked[0][0] if ranked else None

    conclusion = {
        "experiment_id": "E02_price_only_baselines",
        "status": "completed",
        "event_universe": detector,
        "best_price_only_policy": best,
        "conclusion": (
            f"Price-only fade baselines on {detector} development events. "
            f"Best by mean net ticks: {best}. "
            "These establish how much predictability exists without flow features. "
            "Holdout untouched. No costs applied yet (pre-E26)."
        ),
        "next_action": "E03_v2_residual_baseline",
        "grid_note": "B2 uses 1 bar (~30–60s); B3 uses 2 bars (~60–120s) on 1m panel.",
    }
    manifest = {
        "experiment_id": "E02_price_only_baselines",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "event_universe": detector,
        "n_events": int(len(events)),
        "summaries": summaries,
        "best_price_only_policy": best,
        "label_definition": {"R": 4, "C": 4, "H_minutes": 15},
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", summaries)
    _write_json(exp_dir / "conclusion.json", conclusion)

    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E02_price_only_baselines",
        "hypothesis": "Price-only rules establish baseline predictability without flow",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": best,
        "baseline_metric": summaries.get(best, {}).get("mean_net_ticks") if best else "",
        "incremental_improvement": "",
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
