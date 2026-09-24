#!/usr/bin/env python3
"""Research-only executable event-flow study using a T+5 lock-price entry.

Guardrail: reads existing artifacts and economic_events.db only. It does not
import, write to, or restart V1/V2/V3 dashboards. Outputs are confined to
workspace/analysis/aggression_v2_event_window_lock/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from scripts.aggression_v2_event_window_research import (  # noqa: E402
    _chronological_split,
    _load_dataset,
    _load_events,
)

DATASET = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_forward_return" / "forward_return_dataset.csv"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_event_window_lock"
HORIZONS = (5, 15, 30)
TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
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


def build_rows(events: pd.DataFrame, data: pd.DataFrame) -> pd.DataFrame:
    indexed = data.set_index("bucket_ts").sort_index()
    rows: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        release_ts = pd.Timestamp(event["event_ts_utc"])
        lock_ts = release_ts + pd.Timedelta(minutes=5)
        pre = indexed.loc[indexed.index <= release_ts].tail(1)
        first5 = indexed.loc[(indexed.index > release_ts) & (indexed.index <= lock_ts)]
        lock = indexed.loc[indexed.index <= lock_ts].tail(1)
        if pre.empty or first5.empty or lock.empty or lock.index[-1] <= release_ts:
            continue
        release_price = float(pre.iloc[-1]["price"])
        lock_price = float(lock.iloc[-1]["price"])
        flow_sum = float(pd.to_numeric(first5["agg_1m"], errors="coerce").sum())
        flow_sign = float(np.sign(flow_sum))
        if flow_sign == 0.0:
            flow_sign = float(np.sign(pd.to_numeric(first5["agg_5m"], errors="coerce").iloc[-1]))
        base = {
            "release_id": pd.Timestamp(release_ts).isoformat(),
            "event_ts": release_ts,
            "lock_ts": lock_ts,
            "et_date": event["et_date"],
            "event_name": event.get("event_name"),
            "component_count": event.get("component_count"),
            "release_price": release_price,
            "lock_price": lock_price,
            "release_to_lock_ticks": (lock_price - release_price) / TICK_SIZE,
            "first5_agg_sum": flow_sum,
            "first5_buckets": int(len(first5)),
            "flow_sign": flow_sign,
            "lean": "LONG" if flow_sign > 0 else "SHORT" if flow_sign < 0 else "NO_SIGNAL",
        }
        for horizon in HORIZONS:
            target_ts = lock_ts + pd.Timedelta(minutes=horizon)
            outcome = indexed.loc[(indexed.index > lock_ts) & (indexed.index <= target_ts)]
            if outcome.empty:
                continue
            end_price = float(outcome.iloc[-1]["price"])
            ret_ticks = (end_price - lock_price) / TICK_SIZE
            gross = flow_sign * ret_ticks if flow_sign else np.nan
            path_ticks = (outcome["price"].astype(float) - lock_price) / TICK_SIZE
            adverse = float((flow_sign * path_ticks).min()) if flow_sign and len(path_ticks) else np.nan
            rows.append(
                {
                    **base,
                    "horizon": horizon,
                    "end_price": end_price,
                    "ret_ticks": ret_ticks,
                    "gross_ticks": gross,
                    "after_cost_ticks": gross - COST_TICKS if np.isfinite(gross) else np.nan,
                    "adverse_ticks": adverse,
                }
            )
    return pd.DataFrame(rows)


def evaluate(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    dates = sorted(rows["et_date"].dropna().astype(str).unique().tolist())
    train_dates, holdout_dates = _chronological_split(dates)
    hold = rows[rows["et_date"].astype(str).isin(set(holdout_dates))].copy()
    stats: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        sub = hold[(hold["horizon"] == horizon) & hold["after_cost_ticks"].notna()].copy()
        if sub.empty:
            continue
        ci = bootstrap_ci(
            sub["after_cost_ticks"],
            n_boot=N_BOOT,
            groups=sub["et_date"].astype(str),
        )
        daily = sub.groupby("et_date")["after_cost_ticks"].mean()
        row = {
            "horizon": horizon,
            "n": int(len(sub)),
            "cluster_n": int(sub["et_date"].nunique()),
            "mean_gross_ticks": float(sub["gross_ticks"].mean()),
            "mean_after_cost_ticks": float(sub["after_cost_ticks"].mean()),
            "hit_rate": float((sub["after_cost_ticks"] > 0).mean()),
            "median_adverse_ticks": float(sub["adverse_ticks"].median()),
            "p10_adverse_ticks": float(sub["adverse_ticks"].quantile(0.10)),
            "one_sided_p": one_sided_p(daily),
            "ci_low": ci["ci_low"],
            "ci_high": ci["ci_high"],
            "one_sided_lower": ci["one_sided_lower"],
            **cost_sweep(sub["gross_ticks"]),
            "stability_ok": False,
            "stability_scope": "event_context_requires_separate_preregistered_test",
            "pass_eligible": True,
        }
        stats.append(row)
    stats_df = pd.DataFrame(stats)
    bh = benjamini_hochberg(stats_df["one_sided_p"], q=BH_Q)
    stats_df["bh_adjusted_p"] = bh["adjusted_p"]
    stats_df["bh_significant"] = bh["reject"]
    stats_df["new_tier"] = stats_df.apply(classify, axis=1)
    gate = {
        "passed": bool((stats_df["new_tier"] == "PASS").any()),
        "tier_counts": stats_df["new_tier"].value_counts().to_dict(),
        "train_dates": train_dates,
        "holdout_dates": holdout_dates,
        "stats": stats_df.to_dict(orient="records"),
        "recommendation": "Paper tracking only; no automated execution or tradability claim.",
        "entry_semantics": "signal locks at release+5m; entry is observed lock price",
        "isolation": {"modified_v1_v2_v3": False, "wrote_live_stores": False, "touched_ports_or_sod": False},
    }
    return stats_df, gate


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events = _load_events(Path(args.events_db))
    data = _load_dataset(Path(args.dataset))
    rows = build_rows(events, data)
    stats, gate = evaluate(rows)
    rows_path = output_dir / "lock_entry_rows.csv"
    stats_path = output_dir / "lock_entry_stats.csv"
    gate_path = output_dir / "lock_entry_gate.json"
    summary_path = output_dir / "lock_entry_summary.json"
    rows.to_csv(rows_path, index=False)
    stats.to_csv(stats_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=_json_default), encoding="utf-8")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(rows)),
        "events": int(rows["release_id"].nunique()) if not rows.empty else 0,
        "gate": gate,
        "artifacts": {"rows": str(rows_path), "stats": str(stats_path), "gate": str(gate_path), "summary": str(summary_path)},
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "rows": len(rows), "gate": gate, "artifacts": summary["artifacts"]}, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Executable T+5 lock-entry event-flow research.")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
