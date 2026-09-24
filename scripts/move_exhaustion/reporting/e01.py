"""E01 event-detector comparison."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.episode_builder import build_events, label_events, summarize_detector
from scripts.move_exhaustion.events.shock_detector import annotate_move_features, detect_triggers

DETECTORS = (
    "A_price_shock",
    "B_price_plus_flow",
    "C_v2_residual_shock",
    "D_breakout_velocity",
)

# Label-audit reject diagnostics from spec
MIN_RESOLVED = 100
MAX_UNRESOLVED = 0.80
MAX_ONE_CLASS = 0.90


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    assert_no_forbidden_writes(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _audit_ok(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    n_res = summary.get("n_resolved") or 0
    if n_res < MIN_RESOLVED:
        reasons.append(f"resolved_events_lt_{MIN_RESOLVED}")
    unr = summary.get("unresolved_rate")
    if unr is not None and unr > MAX_UNRESOLVED:
        reasons.append("unresolved_gt_80pct")
    ex = summary.get("exhaustion_rate")
    cont = summary.get("continuation_rate")
    if ex is not None and ex > MAX_ONE_CLASS:
        reasons.append("exhaustion_class_gt_90pct")
    if cont is not None and cont > MAX_ONE_CLASS:
        reasons.append("continuation_class_gt_90pct")
    return len(reasons) == 0, reasons


def run_e01(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    """Compare detectors A–D on development dates only (holdout locked)."""
    e00_manifest_path = output_root / "experiments" / "E00_data_causality_audit" / "manifest.json"
    if not e00_manifest_path.exists():
        raise RuntimeError("E00 manifest missing; run e00 first")
    e00 = json.loads(e00_manifest_path.read_text(encoding="utf-8"))
    if e00.get("status") != "passed":
        raise RuntimeError("E00 did not pass")
    holdout = set(e00.get("holdout_dates") or [])

    panel, meta = join_v2_panel()
    # Development only — never score holdout for selection
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)

    exp_dir = output_root / "experiments" / "E01_event_detector_comparison"
    assert_no_forbidden_writes(exp_dir)
    exp_dir.mkdir(parents=True, exist_ok=True)

    summaries = {}
    all_events = []
    pairwise_overlap = {}

    event_sets: dict[str, set[str]] = {}
    for det in DETECTORS:
        fires = detect_triggers(panel, detector=det)
        events = build_events(panel, fires)
        labeled = label_events(events, panel)
        labeled.to_csv(exp_dir / f"events_{det}.csv", index=False)
        summary = summarize_detector(labeled)
        ok, reasons = _audit_ok(summary)
        summary["label_audit_ok"] = ok
        summary["label_audit_flags"] = reasons
        summaries[det] = summary
        all_events.append(labeled)
        # Overlap by rounded start minute keys on same date+direction
        keys = {
            f"{r.et_date}|{int(r.event_direction)}|{pd.Timestamp(r.event_start_time).floor('5min')}"
            for r in labeled.itertuples()
        }
        event_sets[det] = keys

    dets = list(DETECTORS)
    for i, a in enumerate(dets):
        for b in dets[i + 1 :]:
            inter = len(event_sets[a] & event_sets[b])
            union = len(event_sets[a] | event_sets[b]) or 1
            pairwise_overlap[f"{a}__{b}"] = {
                "intersection": inter,
                "jaccard": inter / union,
                "n_a": len(event_sets[a]),
                "n_b": len(event_sets[b]),
            }

    # Selection: prefer causal, enough events, not dominated by one date, audit-ok if possible
    ranked = sorted(
        summaries.items(),
        key=lambda kv: (
            int(kv[1].get("label_audit_ok") or False),
            kv[1].get("n_events") or 0,
            -((kv[1].get("max_date_share") or 1.0)),
            -((kv[1].get("unresolved_rate") or 1.0)),
        ),
        reverse=True,
    )
    primary = ranked[0][0] if ranked else None
    retain = [d for d, s in summaries.items() if (s.get("n_events") or 0) >= 30]

    conclusion = {
        "experiment_id": "E01_event_detector_comparison",
        "status": "completed",
        "primary_detector_candidate": primary,
        "retain_as_event_type_features": retain,
        "conclusion": (
            f"Compared detectors A–D on development dates only ({panel['et_date'].nunique()} dates). "
            f"Primary candidate by count/stability/audit: {primary}. "
            "Do not pick solely on eventual hit rate. Holdout untouched."
        ),
        "next_action": "E02_price_only_baselines",
        "grid_note": "1m panel: L=5m used for primary move_z (spec 300s); 30s unavailable.",
    }

    manifest = {
        "experiment_id": "E01_event_detector_comparison",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "completed",
        "holdout_used": False,
        "holdout_dates_n": len(holdout),
        "dev_dates_n": int(panel["et_date"].nunique()),
        "rows_dev": int(len(panel)),
        "detector_summaries": summaries,
        "pairwise_overlap": pairwise_overlap,
        "primary_detector_candidate": primary,
        "retain_as_event_type_features": retain,
        "label_definition": {"R": 4, "C": 4, "H_minutes": 15},
        "join_meta_rows": meta.get("rows_merged"),
        "isolation": {"dashboards_untouched": True, "outputs_only_under": str(output_root)},
    }
    _write_json(exp_dir / "manifest.json", manifest)
    _write_json(exp_dir / "metrics.json", {"summaries": summaries, "overlap": pairwise_overlap})
    _write_json(exp_dir / "conclusion.json", conclusion)

    if all_events:
        pd.concat(all_events, ignore_index=True).to_parquet(exp_dir / "all_events.parquet", index=False)

    # Append registry
    registry = output_root / "manifests" / "experiment_registry.csv"
    assert_no_forbidden_writes(registry)
    row = {
        "experiment_id": "E01_event_detector_comparison",
        "hypothesis": "Exhaustion usefulness depends on event definition",
        "data_tier": "TBBO_1m_plus_ridge",
        "status": "completed",
        "primary_metric": primary,
        "baseline_metric": "",
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
