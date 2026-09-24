#!/usr/bin/env python3
"""Research-only statistical re-evaluation of aggression experiment gates.

Guardrail: does NOT import, write to, or restart V1/V2/V3 apps. Existing
experiment artifacts are read-only. New outputs are confined to
workspace/analysis/aggression_v2_gate_reeval/.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import (  # noqa: E402
    benjamini_hochberg,
    bootstrap_ci,
    classify,
    cost_sweep,
    one_sided_p,
)

ANALYSIS = PROJECT_ROOT / "workspace" / "analysis"
OUTPUT_DIR = ANALYSIS / "aggression_v2_gate_reeval"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
EASTERN = ZoneInfo("America/New_York")
RTH_START = 8 * 60
RTH_END = 15 * 60
N_BOOT = 10_000
BH_Q = 0.10


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return str(value)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _bool_series(series: pd.Series) -> pd.Series:
    return series.map(lambda v: str(v).lower() == "true" if pd.notna(v) else False)


def _chronological_holdout_dates(df: pd.DataFrame, frac: float = 0.7) -> list[str]:
    dates = sorted(df["et_date"].dropna().astype(str).unique().tolist())
    if len(dates) <= 1:
        return dates
    cut = max(1, min(len(dates) - 1, int(len(dates) * frac)))
    return dates[cut:]


def _event_times(path: Path) -> np.ndarray:
    if not path.exists():
        return np.array([], dtype="datetime64[ns]")
    try:
        with sqlite3.connect(str(path)) as conn:
            ev = pd.read_sql_query(
                "SELECT event_date FROM economic_events WHERE country LIKE '%United States%' OR country='US' OR country IS NULL",
                conn,
            )
    except Exception:
        return np.array([], dtype="datetime64[ns]")
    times = []
    for raw in ev.get("event_date", []):
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.isna(ts):
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT")
        if pd.isna(ts):
            continue
        times.append(ts.tz_convert("UTC").tz_localize(None))
    return np.asarray(sorted(set(times)), dtype="datetime64[ns]")


def _rth_no_event_mask(ts: pd.Series, event_times: np.ndarray, window_min: int = 10) -> pd.Series:
    opened = pd.to_datetime(ts, errors="coerce", utc=True, format="mixed")
    et = opened.dt.tz_convert(EASTERN)
    minutes = et.dt.hour * 60 + et.dt.minute
    rth = (minutes >= RTH_START) & (minutes < RTH_END)
    if not len(event_times):
        return rth.fillna(False)
    vals = opened.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    idx = np.searchsorted(event_times, vals)
    blackout = np.zeros(len(vals), dtype=bool)
    window = np.timedelta64(window_min, "m")
    for i, value in enumerate(vals):
        if np.isnat(value):
            continue
        for j in (idx[i] - 1, idx[i]):
            if 0 <= j < len(event_times) and abs(value - event_times[j]) <= window:
                blackout[i] = True
                break
    return rth.fillna(False) & ~pd.Series(blackout, index=ts.index)


def _cluster_test_values(after: pd.Series, groups: pd.Series) -> np.ndarray:
    tmp = pd.DataFrame({"after": pd.to_numeric(after, errors="coerce"), "group": groups}).dropna(subset=["after", "group"])
    if tmp.empty:
        return np.array([], dtype=float)
    return tmp.groupby("group")["after"].mean().to_numpy(dtype=float)


def _add_cell(
    rows: list[dict[str, Any]],
    *,
    experiment: str,
    cell: str,
    gross: pd.Series,
    groups: pd.Series,
    adverse: pd.Series | None = None,
    stability_mask: pd.Series | None = None,
    stability_scope: str = "rth_no_event",
    original_verdict: str = "FAIL",
    pass_eligible: bool = True,
) -> None:
    frame = pd.DataFrame({"gross": pd.to_numeric(gross, errors="coerce"), "group": groups})
    if adverse is not None:
        frame["adverse"] = pd.to_numeric(adverse, errors="coerce")
    if stability_mask is not None:
        frame["stable"] = stability_mask.reindex(frame.index).fillna(False).astype(bool)
    frame = frame.dropna(subset=["gross", "group"])
    if frame.empty:
        return
    frame["after"] = frame["gross"] - COST_TICKS
    ci = bootstrap_ci(frame["after"], n_boot=N_BOOT, groups=frame["group"])
    cluster_values = _cluster_test_values(frame["after"], frame["group"])
    p_value = one_sided_p(cluster_values)
    sweep = cost_sweep(frame["gross"])

    stability_ok = False
    stability_n = 0
    stability_mean = np.nan
    stability_p_negative = np.nan
    if "stable" in frame:
        stable = frame[frame["stable"]].copy()
        stability_n = int(len(stable))
        if not stable.empty:
            stable["after"] = stable["gross"] - COST_TICKS
            stability_mean = float(stable["after"].mean())
            stable_clusters = _cluster_test_values(stable["after"], stable["group"])
            stability_p_negative = one_sided_p(-stable_clusters)
            stability_ok = not (
                stability_mean < 0.0
                and np.isfinite(stability_p_negative)
                and stability_p_negative < 0.05
            )

    row = {
        "experiment": experiment,
        "cell": cell,
        "n": int(len(frame)),
        "cluster_n": int(frame["group"].nunique()),
        "mean_gross_ticks": float(frame["gross"].mean()),
        "mean_after_cost_ticks": float(frame["after"].mean()),
        "ci_low": ci["ci_low"],
        "ci_high": ci["ci_high"],
        "one_sided_lower": ci["one_sided_lower"],
        "one_sided_p": p_value,
        **sweep,
        "median_adverse_ticks": float(frame["adverse"].median()) if "adverse" in frame and frame["adverse"].notna().any() else np.nan,
        "p10_adverse_ticks": float(frame["adverse"].quantile(0.10)) if "adverse" in frame and frame["adverse"].notna().any() else np.nan,
        "stability_scope": stability_scope,
        "stability_n": stability_n,
        "stability_mean_after_cost_ticks": stability_mean,
        "stability_p_negative": stability_p_negative,
        "stability_ok": bool(stability_ok),
        "pass_eligible": bool(pass_eligible),
        "original_verdict": original_verdict,
    }
    rows.append(row)


def _event_window_cells(rows: list[dict[str, Any]]) -> None:
    base = ANALYSIS / "aggression_v2_event_window"
    data = pd.read_csv(base / "event_window_rows.csv")
    gate = _read_json(base / "event_window_gate.json")
    hold_dates = set(map(str, gate.get("holdout_dates", [])))
    hold = data[data["et_date"].astype(str).isin(hold_dates)].copy()
    stats = pd.read_csv(base / "event_window_stats.csv")
    for horizon in (5, 15, 30):
        h = hold[hold["horizon"] == horizon].copy()
        # Event strategies cannot have a no-event stability slice; no PASS is
        # possible until a separately specified context-stability test exists.
        _add_cell(
            rows,
            experiment="event_window",
            cell=f"flow_follow_all_{horizon}m",
            gross=h["flow_follow_gross_ticks"],
            groups=h["et_date"].astype(str),
            adverse=h["adverse_ticks"],
            stability_scope="not_applicable_event_strategy",
        )
        stat = stats[(stats["horizon"] == horizon) & (stats["label"] == "flow_follow_ev_top10")]
        if not stat.empty and pd.notna(stat.iloc[0].get("selection_ev_threshold")):
            threshold = float(stat.iloc[0]["selection_ev_threshold"])
            ev = h[pd.to_numeric(h["ev_score"], errors="coerce") >= threshold]
            _add_cell(
                rows,
                experiment="event_window",
                cell=f"flow_follow_ev_top10_{horizon}m",
                gross=ev["flow_follow_gross_ticks"],
                groups=ev["et_date"].astype(str),
                adverse=ev["adverse_ticks"],
                stability_scope="not_applicable_event_strategy",
            )


def _meta_cells(rows: list[dict[str, Any]], event_times: np.ndarray) -> None:
    base = ANALYSIS / "aggression_v2_meta_label"
    data = pd.read_csv(base / "meta_label_holdout_scores.csv")
    gate = _read_json(base / "meta_label_gate.json")
    stable = _rth_no_event_mask(data["opened_ts"], event_times)
    _add_cell(
        rows,
        experiment="meta_label",
        cell="all_alerts",
        gross=data["fade_ticks_30m"],
        groups=data["et_date"].astype(str),
        adverse=data["adverse_ticks_30m"],
        stability_mask=stable,
    )
    for stat in gate.get("stats", []):
        if stat.get("label") == "all_alerts" or stat.get("threshold") is None:
            continue
        threshold = float(stat["threshold"])
        sub = data[pd.to_numeric(data["proba_take"], errors="coerce") >= threshold]
        _add_cell(
            rows,
            experiment="meta_label",
            cell=str(stat["label"]),
            gross=sub["fade_ticks_30m"],
            groups=sub["et_date"].astype(str),
            adverse=sub["adverse_ticks_30m"],
            stability_mask=stable.loc[sub.index],
        )


def _ev_cells(rows: list[dict[str, Any]]) -> None:
    base = ANALYSIS / "aggression_v2_ev_selectivity"
    data = pd.read_csv(base / "ev_selectivity_scores.csv", low_memory=False)
    stats = pd.read_csv(base / "ev_selectivity_stats.csv")
    data["is_rth"] = _bool_series(data["is_rth"])
    data["event_blackout"] = _bool_series(data["event_blackout"])
    for horizon in (1, 3, 5, 15, 30):
        h = data[data["horizon"] == horizon].copy()
        hold_dates = set(_chronological_holdout_dates(h))
        h = h[h["et_date"].astype(str).isin(hold_dates)].copy()
        target = f"fwd_ret_{horizon}m_ticks"
        gross = np.sign(pd.to_numeric(h["pred_ret_ticks"], errors="coerce")) * pd.to_numeric(h[target], errors="coerce")
        stable = h["is_rth"] & ~h["event_blackout"]
        _add_cell(
            rows,
            experiment="ev_selectivity",
            cell=f"model_all_{horizon}m",
            gross=gross,
            groups=h["et_date"].astype(str),
            stability_mask=stable,
        )
        hz_stats = stats[(stats["horizon"] == horizon) & (stats["split"] == "holdout") & (stats["label"] != "model_all")]
        for _, stat in hz_stats.iterrows():
            threshold = stat.get("selection_ev_threshold")
            if not np.isfinite(threshold):
                continue
            sub = h[pd.to_numeric(h["ev_score"], errors="coerce") >= float(threshold)]
            sub_gross = np.sign(pd.to_numeric(sub["pred_ret_ticks"], errors="coerce")) * pd.to_numeric(sub[target], errors="coerce")
            _add_cell(
                rows,
                experiment="ev_selectivity",
                cell=f"{stat['label']}_{horizon}m",
                gross=sub_gross,
                groups=sub["et_date"].astype(str),
                stability_mask=(sub["is_rth"] & ~sub["event_blackout"]),
            )


def _forward_cells(rows: list[dict[str, Any]]) -> None:
    data = pd.read_csv(ANALYSIS / "aggression_v2_forward_return" / "forward_return_predictions.csv")
    data["is_rth"] = _bool_series(data["is_rth"])
    data["event_blackout"] = _bool_series(data["event_blackout"])
    for horizon in (1, 3, 5, 15, 30):
        h = data[data["horizon"] == horizon].copy()
        hold_dates = set(_chronological_holdout_dates(h))
        h = h[h["et_date"].astype(str).isin(hold_dates)].copy()
        target = f"fwd_ret_{horizon}m_ticks"
        gross = np.sign(pd.to_numeric(h["pred_ret_ticks"], errors="coerce")) * pd.to_numeric(h[target], errors="coerce")
        _add_cell(
            rows,
            experiment="forward_return",
            cell=f"model_direction_{horizon}m",
            gross=gross,
            groups=h["et_date"].astype(str),
            stability_mask=(h["is_rth"] & ~h["event_blackout"]),
        )


def _resolution_cells(rows: list[dict[str, Any]], event_times: np.ndarray) -> None:
    base = ANALYSIS / "aggression_v2_resolution"
    data = pd.read_csv(base / "resolution_holdout_scores.csv")
    gate = _read_json(base / "resolution_gate.json")
    stable = _rth_no_event_mask(data["opened_ts"], event_times)
    gross_all = pd.to_numeric(data["dir_pnl_30m"], errors="coerce") / TICK_SIZE
    _add_cell(
        rows,
        experiment="resolution",
        cell="all_alerts",
        gross=gross_all,
        groups=data["et_date"].astype(str),
        adverse=pd.to_numeric(data["adverse_30m"], errors="coerce") / TICK_SIZE,
        stability_mask=stable,
    )
    for bucket, spec in gate.get("all_buckets", {}).items():
        threshold = spec.get("top_decile_threshold")
        col = f"proba_{bucket}"
        if threshold is None or col not in data:
            continue
        sub = data[pd.to_numeric(data[col], errors="coerce") >= float(threshold)]
        _add_cell(
            rows,
            experiment="resolution",
            cell=f"top_decile_{bucket}",
            gross=pd.to_numeric(sub["dir_pnl_30m"], errors="coerce") / TICK_SIZE,
            groups=sub["et_date"].astype(str),
            adverse=pd.to_numeric(sub["adverse_30m"], errors="coerce") / TICK_SIZE,
            stability_mask=stable.loc[sub.index],
        )


def _magnitude_cells(rows: list[dict[str, Any]], event_times: np.ndarray) -> None:
    data = pd.read_csv(ANALYSIS / "aggression_v2_magnitude" / "magnitude_holdout_scores.csv")
    threshold = float(pd.to_numeric(data["proba_big_move"], errors="coerce").quantile(0.90))
    sub = data[pd.to_numeric(data["proba_big_move"], errors="coerce") >= threshold]
    stable = _rth_no_event_mask(sub["opened_ts"], event_times)
    # Opportunity magnitude is not a directional PnL and is never PASS-eligible.
    _add_cell(
        rows,
        experiment="magnitude",
        cell="top_decile_opportunity_30m",
        gross=pd.to_numeric(sub["forward_max_abs_move_ticks"], errors="coerce"),
        groups=sub["et_date"].astype(str),
        adverse=pd.to_numeric(sub["adverse_30m"], errors="coerce") / TICK_SIZE,
        stability_mask=stable,
        pass_eligible=False,
    )


def _standaside_cells(rows: list[dict[str, Any]], event_times: np.ndarray) -> None:
    base = ANALYSIS / "aggression_v2_standaside"
    data = pd.read_csv(base / "standaside_holdout_scores.csv")
    gate = _read_json(base / "standaside_gate.json")
    threshold = float(gate.get("threshold_p70", np.nan))
    stable = _rth_no_event_mask(data["opened_ts"], event_times)
    masks = {
        "all_alerts": pd.Series(True, index=data.index),
        "keep_low_toxic_probability": pd.to_numeric(data["proba_toxic"], errors="coerce") < threshold,
        "standaside_high_toxic_probability": pd.to_numeric(data["proba_toxic"], errors="coerce") >= threshold,
    }
    for name, mask in masks.items():
        sub = data[mask].copy()
        _add_cell(
            rows,
            experiment="standaside",
            cell=name,
            gross=pd.to_numeric(sub["fade_ticks_30m"], errors="coerce"),
            groups=sub["et_date"].astype(str),
            adverse=pd.to_numeric(sub["adverse_ticks_30m"], errors="coerce"),
            stability_mask=stable.loc[sub.index],
        )


def _confluence_cells(rows: list[dict[str, Any]]) -> None:
    base = ANALYSIS / "aggression_v2xv3_confluence"
    data = pd.read_csv(base / "confluence_episodes.csv")
    gate = _read_json(base / "confluence_gate.json")
    hold_dates = set(map(str, gate.get("holdout_dates", [])))
    hold = data[data["et_date"].astype(str).isin(hold_dates)].copy()
    hold["is_rth"] = _bool_series(hold["is_rth"])
    hold["event_blackout"] = _bool_series(hold["event_blackout"])
    for formation in ("PRICE_LED", "FLOW_LED"):
        sub = hold[hold["formation"].astype(str) == formation].copy()
        stable = sub["is_rth"] & ~sub["event_blackout"]
        fade = pd.to_numeric(sub["dir_pnl_30m"], errors="coerce") / TICK_SIZE
        adverse = pd.to_numeric(sub["adverse_30m"], errors="coerce") / TICK_SIZE
        _add_cell(
            rows,
            experiment="confluence",
            cell=f"{formation}_fade_30m",
            gross=fade,
            groups=sub["et_date"].astype(str),
            adverse=adverse,
            stability_mask=stable,
        )
        _add_cell(
            rows,
            experiment="confluence",
            cell=f"{formation}_follow_30m",
            gross=-fade,
            groups=sub["et_date"].astype(str),
            adverse=adverse,
            stability_mask=stable,
        )


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    event_times = _event_times(Path(args.events_db))
    rows: list[dict[str, Any]] = []
    _event_window_cells(rows)
    _meta_cells(rows, event_times)
    _ev_cells(rows)
    _forward_cells(rows)
    _resolution_cells(rows, event_times)
    _magnitude_cells(rows, event_times)
    _standaside_cells(rows, event_times)
    _confluence_cells(rows)

    cells = pd.DataFrame(rows)
    bh = benjamini_hochberg(cells["one_sided_p"], q=BH_Q)
    cells["bh_adjusted_p"] = bh["adjusted_p"]
    cells["bh_significant"] = bh["reject"]
    cells["new_tier"] = cells.apply(classify, axis=1)
    cells.loc[(~cells["pass_eligible"]) & (cells["new_tier"] == "PASS"), "new_tier"] = "PROVISIONAL"
    cells["verdict_change"] = cells["original_verdict"].astype(str) + " -> " + cells["new_tier"].astype(str)
    cells.sort_values(
        ["new_tier", "mean_after_cost_ticks", "bh_adjusted_p"],
        ascending=[True, False, True],
        inplace=True,
    )

    cells_path = output_dir / "reeval_cells.csv"
    summary_path = output_dir / "reeval_summary.json"
    cells.to_csv(cells_path, index=False)
    counts = cells["new_tier"].value_counts().to_dict()
    changed = cells[cells["new_tier"] != cells["original_verdict"]]
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_boot": N_BOOT,
        "bh_q": BH_Q,
        "cost_ticks_primary": COST_TICKS,
        "cells_tested": int(len(cells)),
        "tier_counts": counts,
        "verdict_changes": changed[
            [
                "experiment",
                "cell",
                "n",
                "cluster_n",
                "mean_after_cost_ticks",
                "ci_low",
                "ci_high",
                "bh_adjusted_p",
                "stability_ok",
                "new_tier",
            ]
        ].to_dict(orient="records"),
        "pass_cells": cells[cells["new_tier"] == "PASS"].to_dict(orient="records"),
        "provisional_cells": cells[cells["new_tier"] == "PROVISIONAL"].to_dict(orient="records"),
        "artifacts": {"cells": str(cells_path), "summary": str(summary_path)},
        "notes": [
            "Minute/episode confidence intervals use date-clustered bootstrap.",
            "One-sided p-values use date-cluster means before BH-FDR.",
            "Event-window cells cannot PASS because RTH x no-event stability is not applicable to an event-only strategy.",
            "Magnitude opportunity cells are diagnostic-only and cannot PASS as directional trades.",
            "Existing experiment verdict files were not modified.",
        ],
        "isolation": {"modified_v1_v2_v3": False, "wrote_live_stores": False, "touched_ports_or_sod": False},
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "cells_tested": len(cells),
                "tier_counts": counts,
                "pass_cells": summary["pass_cells"],
                "top_provisional": summary["provisional_cells"][:10],
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=_json_default,
        )
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Statistically re-evaluate aggression research gates.")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
