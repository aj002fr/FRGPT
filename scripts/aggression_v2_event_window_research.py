#!/usr/bin/env python3
"""Research-only post-event flow/EV window tests for Aggression V2.

Guardrail: does NOT import, write to, or restart V1/V2/V3 apps.
Reads existing research artifacts and workspace/economic_events.db; writes only
under workspace/analysis/aggression_v2_event_window/. No dashboard or *_live/
writes are performed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_forward_return" / "forward_return_dataset.csv"
EV_SCORES = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_ev_selectivity" / "ev_selectivity_scores.csv"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_event_window"

EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
HORIZONS = (5, 15, 30)
TRAIN_FRAC = 0.7
EV_QUANTILE = 0.90
EPS = 1e-12


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


def _load_events(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    with sqlite3.connect(str(path)) as conn:
        ev = pd.read_sql_query(
            """
            SELECT event_date, event_name, country
            FROM economic_events
            WHERE country LIKE '%United States%' OR country='US' OR country IS NULL
            """,
            conn,
        )
    if ev.empty:
        return ev
    ev["event_ts"] = pd.to_datetime(ev["event_date"], errors="coerce")
    ev = ev.dropna(subset=["event_ts"]).copy()
    ev["event_ts"] = ev["event_ts"].apply(lambda t: t.tz_localize(EASTERN) if t.tzinfo is None else t.tz_convert(EASTERN))
    ev["event_ts_utc"] = ev["event_ts"].dt.tz_convert("UTC")
    ev["et_date"] = ev["event_ts"].dt.strftime("%Y-%m-%d")
    # Multiple components from the same release share a timestamp. Collapse them
    # so a release is one observation, not one row per component.
    grouped = (
        ev.groupby("event_ts_utc", as_index=False)
        .agg(
            event_ts=("event_ts", "first"),
            et_date=("et_date", "first"),
            event_name=("event_name", lambda x: " | ".join(sorted({str(v) for v in x if pd.notna(v)})[:8])),
            country=("country", "first"),
            component_count=("event_name", "count"),
        )
        .sort_values("event_ts_utc")
    )
    return grouped


def _load_dataset(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    return df.sort_values("bucket_ts")


def _load_ev(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    return df.sort_values("bucket_ts")


def _chronological_split(dates: list[str], frac: float = TRAIN_FRAC) -> tuple[list[str], list[str]]:
    ordered = sorted(d for d in dates if d)
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * frac))) if len(ordered) > 1 else len(ordered)
    return ordered[:cut], ordered[cut:]


def _ev_at(ev: pd.DataFrame, ts: pd.Timestamp, horizon: int) -> float:
    if ev.empty:
        return np.nan
    sub = ev[(ev["horizon"] == horizon) & (ev["bucket_ts"] <= ts)]
    if sub.empty:
        return np.nan
    row = sub.iloc[-1]
    if (ts - row["bucket_ts"]).total_seconds() <= 90:
        return float(row.get("ev_score", np.nan))
    return np.nan


def build_event_rows(events: pd.DataFrame, data: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    indexed = data.set_index("bucket_ts").sort_index()
    for _, event in events.iterrows():
        ts = event["event_ts_utc"]
        if pd.isna(ts):
            continue
        pre = indexed.loc[indexed.index <= ts].tail(1)
        if pre.empty:
            continue
        start_price = float(pre.iloc[-1]["price"])
        first5 = indexed.loc[(indexed.index > ts) & (indexed.index <= ts + pd.Timedelta(minutes=5))]
        if first5.empty:
            continue
        flow_sign = float(np.sign(pd.to_numeric(first5.get("agg_1m", pd.Series(dtype=float)), errors="coerce").sum()))
        if flow_sign == 0:
            flow_sign = float(np.sign(pd.to_numeric(first5.get("agg_5m", pd.Series(dtype=float)), errors="coerce").iloc[-1]))
        for horizon in HORIZONS:
            win = indexed.loc[(indexed.index > ts) & (indexed.index <= ts + pd.Timedelta(minutes=horizon))]
            if win.empty:
                continue
            end_price = float(win.iloc[-1]["price"])
            ret_ticks = (end_price - start_price) / TICK_SIZE
            gross = flow_sign * ret_ticks if flow_sign != 0 else np.nan
            path = (win["price"].astype(float) - start_price) / TICK_SIZE
            adverse = float((flow_sign * path).min()) if flow_sign != 0 and len(path) else np.nan
            rows.append({
                "event_ts": ts,
                "et_date": event["et_date"],
                "event_name": event.get("event_name"),
                "horizon": horizon,
                "first5_flow_sign": flow_sign,
                "ret_ticks": ret_ticks,
                "flow_follow_gross_ticks": gross,
                "flow_follow_after_cost_ticks": gross - COST_TICKS if np.isfinite(gross) else np.nan,
                "adverse_ticks": adverse,
                "ev_score": _ev_at(ev, ts + pd.Timedelta(minutes=5), horizon),
            })
    return pd.DataFrame(rows)


def _stats(sub: pd.DataFrame, label: str, split: str) -> dict[str, Any]:
    sub = sub.dropna(subset=["flow_follow_after_cost_ticks"]).copy()
    if sub.empty:
        return {"label": label, "split": split, "n": 0}
    neg = sub["flow_follow_after_cost_ticks"][sub["flow_follow_after_cost_ticks"] < 0]
    downside = float(neg.std(ddof=1)) if len(neg) >= 2 else np.nan
    return {
        "label": label,
        "split": split,
        "n": int(len(sub)),
        "after_cost_ticks": float(sub["flow_follow_after_cost_ticks"].mean()),
        "hit_rate": float((sub["flow_follow_after_cost_ticks"] > 0).mean()),
        "median_adverse_ticks": float(sub["adverse_ticks"].median()) if sub["adverse_ticks"].notna().any() else np.nan,
        "p10_adverse_ticks": float(sub["adverse_ticks"].quantile(0.10)) if sub["adverse_ticks"].notna().any() else np.nan,
        "sortino": float(sub["flow_follow_after_cost_ticks"].mean() / downside) if np.isfinite(downside) and downside > EPS else np.nan,
    }


def evaluate(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if rows.empty:
        return pd.DataFrame(), {"passed": False, "reason": "no event rows"}
    dates = sorted(rows["et_date"].dropna().unique().tolist())
    train_dates, hold_dates = _chronological_split(dates)
    train = rows[rows["et_date"].isin(train_dates)].copy()
    hold = rows[rows["et_date"].isin(hold_dates)].copy()
    stats = []
    for horizon in HORIZONS:
        htrain = train[train["horizon"] == horizon]
        hhold = hold[hold["horizon"] == horizon]
        stats.append({"horizon": horizon, **_stats(hhold, "flow_follow_all", "holdout")})
        if htrain["ev_score"].notna().any():
            thr = float(htrain["ev_score"].quantile(EV_QUANTILE))
            evhold = hhold[hhold["ev_score"] >= thr]
            row = {"horizon": horizon, **_stats(evhold, "flow_follow_ev_top10", "holdout")}
            row["selection_ev_threshold"] = thr
            stats.append(row)
    stats_df = pd.DataFrame(stats)
    passed = bool(
        (
            (stats_df["n"].fillna(0) >= 50)
            & (stats_df["after_cost_ticks"].fillna(-999) >= 0.5)
            & stats_df["median_adverse_ticks"].notna()
        ).any()
    )
    gate = {
        "passed": passed,
        "train_dates": train_dates,
        "holdout_dates": hold_dates,
        "stats": stats_df.to_dict(orient="records"),
        "rules": {"min_n": 50, "min_after_cost_ticks": 0.5, "cost_ticks": COST_TICKS},
        "recommendation": "PASS: research lead only; no dashboard without explicit approval" if passed else "FAIL: no dashboard; keep research offline; V1/V2/V3 untouched",
        "isolation": {"modified_v1_v2_v3": False, "wrote_live_stores": False, "touched_ports_or_sod": False},
    }
    return stats_df, gate


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events = _load_events(Path(args.events_db))
    data = _load_dataset(Path(args.dataset))
    ev = _load_ev(Path(args.ev_scores))
    rows = build_event_rows(events, data, ev)
    stats, gate = evaluate(rows)
    rows_path = output_dir / "event_window_rows.csv"
    stats_path = output_dir / "event_window_stats.csv"
    gate_path = output_dir / "event_window_gate.json"
    summary_path = output_dir / "event_window_summary.json"
    rows.to_csv(rows_path, index=False)
    stats.to_csv(stats_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=_json_default), encoding="utf-8")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": int(len(rows)),
        "events_loaded": int(len(events)),
        "gate": gate,
        "artifacts": {"rows": str(rows_path), "stats": str(stats_path), "gate": str(gate_path), "summary": str(summary_path)},
        "notes": ["Flow direction is signed first-5-minute post-release aggressor flow.", "EV gating is used where EV score overlap exists."],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "passed": gate["passed"], "rows": len(rows), "gate": gate, "artifacts": summary["artifacts"]}, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only post-event flow window tests.")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--ev-scores", default=str(EV_SCORES))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
