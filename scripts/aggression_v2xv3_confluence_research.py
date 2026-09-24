#!/usr/bin/env python3
"""Read-only V2 x V3 confluence research (Idea 2).

Guardrail: does NOT import, write to, or restart V1/V2/V3 dashboards.
Does NOT write under aggression_v2_live / aggression_v3_live.
Outputs only under workspace/analysis/aggression_v2xv3_confluence/.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
EPS = 1e-12

V2_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
V3_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v3_live"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2xv3_confluence"
RTH_START = 8 * 60
RTH_END = 15 * 60


def _as_utc(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", utc=True, format="mixed")


def _safe_mean(s: pd.Series) -> float:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(s.mean()) if len(s) else float("nan")


def load_alerts(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["opened_ts"] = _as_utc(df["opened_ts"])
    df["et_date"] = df["et_date"].astype(str)
    return df


def load_regime_logs(live_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(live_dir / "regime_log_*.csv"))):
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = _as_utc(df["ts"])
    df = df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last")
    return df.set_index("ts").sort_index()


def load_signal_logs(live_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(live_dir / "signal_log_*.csv"))):
        try:
            frames.append(pd.read_csv(path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = _as_utc(df["ts"])
    df = df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last")
    for c in ("price", "fair_value", "residual", "residual_z"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("ts").sort_index()


def formation_at(signals: pd.DataFrame, ts: pd.Timestamp, window: int = 10) -> str:
    if signals.empty or pd.isna(ts):
        return "UNKNOWN"
    pre = signals.loc[signals.index <= ts].tail(window + 1)
    if len(pre) < 3:
        return "UNKNOWN"
    d_price = float(pre["price"].iloc[-1] - pre["price"].iloc[0])
    d_fv = float(pre["fair_value"].iloc[-1] - pre["fair_value"].iloc[0])
    abs_p, abs_f = abs(d_price), abs(d_fv)
    if abs_p >= abs_f * 1.25 and abs_p > EPS:
        return "PRICE_LED"
    if abs_f >= abs_p * 1.25 and abs_f > EPS:
        return "FLOW_LED"
    return "MIXED"


def regime_at(regimes: pd.DataFrame, ts: pd.Timestamp) -> dict[str, Any]:
    if regimes.empty or pd.isna(ts):
        return {"regime": "NO_REGIME_LOG", "conviction": np.nan, "effort_per_tick": np.nan}
    prior = regimes.loc[regimes.index <= ts]
    if prior.empty:
        return {"regime": "NO_REGIME_LOG", "conviction": np.nan, "effort_per_tick": np.nan}
    row = prior.iloc[-1]
    # stale if > 5 minutes old
    age_min = (ts - prior.index[-1]).total_seconds() / 60.0
    regime = str(row.get("regime", "UNKNOWN"))
    if age_min > 5:
        regime = "STALE_REGIME"
    return {
        "regime": regime,
        "raw_regime": row.get("raw_regime"),
        "conviction": row.get("conviction"),
        "effort_per_tick": row.get("effort_per_tick"),
        "effort_ratio": row.get("effort_ratio"),
        "regime_age_min": age_min,
        "flags": row.get("flags"),
    }


def is_rth(ts: pd.Timestamp) -> bool:
    if pd.isna(ts):
        return False
    et = ts.tz_convert(EASTERN)
    mins = et.hour * 60 + et.minute
    return RTH_START <= mins < RTH_END


def load_event_blackout_mask(opened: pd.Series, db_path: Path, minutes: int = 10) -> pd.Series:
    if not db_path.exists():
        return pd.Series(False, index=opened.index)
    try:
        import sqlite3

        with sqlite3.connect(str(db_path)) as conn:
            ev = pd.read_sql_query(
                "SELECT event_date FROM economic_events WHERE country LIKE '%United States%' OR country='US' OR country IS NULL",
                conn,
            )
    except Exception:
        return pd.Series(False, index=opened.index)
    if ev.empty:
        return pd.Series(False, index=opened.index)
    times = []
    for raw in ev["event_date"]:
        t = pd.to_datetime(raw, errors="coerce")
        if pd.isna(t):
            continue
        if t.tzinfo is None:
            t = t.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT")
        if pd.isna(t):
            continue
        times.append(t.tz_convert("UTC").tz_localize(None))
    if not times:
        return pd.Series(False, index=opened.index)
    event_ns = np.array(sorted(times), dtype="datetime64[ns]")
    vals = opened.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    idx = np.searchsorted(event_ns, vals)
    out = np.zeros(len(vals), dtype=bool)
    window = np.timedelta64(minutes, "m")
    for i, v in enumerate(vals):
        if np.isnat(v):
            continue
        for j in (idx[i] - 1, idx[i]):
            if 0 <= j < len(event_ns) and not np.isnat(event_ns[j]) and abs(v - event_ns[j]) <= window:
                out[i] = True
                break
    return pd.Series(out, index=opened.index)


def cell_stats(sub: pd.DataFrame, label: str, split: str) -> dict[str, Any]:
    fade = pd.to_numeric(sub["dir_pnl_30m"], errors="coerce") / TICK_SIZE
    follow = -fade
    adv = pd.to_numeric(sub["adverse_30m"], errors="coerce") / TICK_SIZE
    n = int(fade.dropna().shape[0])
    mean_fade = float(fade.mean()) if n else float("nan")
    mean_follow = float(follow.mean()) if n else float("nan")
    return {
        "split": split,
        "label": label,
        "n": n,
        "fade_hit_30m": float((fade > 0).mean()) if n else float("nan"),
        "follow_hit_30m": float((follow > 0).mean()) if n else float("nan"),
        "fade_mean_ticks": mean_fade,
        "follow_mean_ticks": mean_follow,
        "fade_after_cost_ticks": (mean_fade - COST_TICKS) if n else float("nan"),
        "follow_after_cost_ticks": (mean_follow - COST_TICKS) if n else float("nan"),
        "median_adverse_ticks": float(adv.median()) if adv.notna().any() else float("nan"),
        "median_abs_open_z": float(pd.to_numeric(sub["open_residual_z"], errors="coerce").abs().median())
        if "open_residual_z" in sub
        else float("nan"),
    }


def chronological_split(dates: list[str], train_frac: float = 0.7) -> tuple[list[str], list[str]]:
    ordered = sorted(d for d in dates if d)
    if len(ordered) <= 1:
        return ordered, []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac)))
    return ordered[:cut], ordered[cut:]


def evaluate_gate(stats: pd.DataFrame) -> dict[str, Any]:
    hold = stats[stats["split"].isin(["holdout", "all", "rth_no_event", "regime_overlap"])].copy()
    # Prefer cells with n>=50 and after-cost >= 0.5 on fade or follow
    candidates = hold[hold["n"] >= 50].copy()
    if candidates.empty:
        return {
            "passed": False,
            "reason": "no cell with n>=50",
            "best": {},
            "recommendation": "FAIL: do not build V2.1; keep logging; leave V1/V2/V3 untouched",
        }
    candidates["best_after_cost"] = candidates[["fade_after_cost_ticks", "follow_after_cost_ticks"]].max(axis=1)
    candidates["best_side"] = np.where(
        candidates["fade_after_cost_ticks"] >= candidates["follow_after_cost_ticks"], "fade", "follow"
    )
    best = candidates.sort_values("best_after_cost", ascending=False).iloc[0].to_dict()
    passed = float(best.get("best_after_cost") or -999) >= 0.5
    # stability: same label on rth_no_event if present
    stable = False
    rth = hold[(hold["split"] == "rth_no_event") & (hold["label"] == best.get("label"))]
    if not rth.empty and int(rth.iloc[0]["n"]) >= 30:
        rth_best = max(float(rth.iloc[0]["fade_after_cost_ticks"]), float(rth.iloc[0]["follow_after_cost_ticks"]))
        stable = rth_best >= 0.0  # soft stability: non-negative after cost
    return {
        "passed": bool(passed and (stable or best.get("split") == "rth_no_event")),
        "letter_pass_expectancy": bool(passed),
        "stable_rth_no_event": bool(stable),
        "best": best,
        "recommendation": (
            "PASS: confluence cell is a research lead — still no V2.1 without explicit approval"
            if passed and stable
            else "FAIL / FRAGILE: do not build V2.1; keep logging; leave V1/V2/V3 untouched"
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    alerts = load_alerts(Path(args.alert_log))
    regimes = load_regime_logs(Path(args.v3_live_dir))
    signals = load_signal_logs(Path(args.v2_live_dir))

    rows = []
    for _, ep in alerts.iterrows():
        opened = ep["opened_ts"]
        reg = regime_at(regimes, opened)
        form = formation_at(signals, opened)
        rows.append(
            {
                "id": ep.get("id"),
                "et_date": ep.get("et_date"),
                "opened_ts": opened,
                "state": ep.get("state"),
                "open_residual_z": ep.get("open_residual_z"),
                "dir_pnl_15m": ep.get("dir_pnl_15m"),
                "dir_pnl_30m": ep.get("dir_pnl_30m"),
                "adverse_15m": ep.get("adverse_15m"),
                "adverse_30m": ep.get("adverse_30m"),
                "formation": form,
                "is_rth": is_rth(opened),
                **reg,
            }
        )
    joined = pd.DataFrame(rows)
    joined["event_blackout"] = load_event_blackout_mask(joined["opened_ts"], Path(args.events_db))
    joined["has_regime"] = ~joined["regime"].isin(["NO_REGIME_LOG", "STALE_REGIME"])

    dates = sorted(joined["et_date"].dropna().unique().tolist())
    train_dates, holdout_dates = chronological_split(dates, args.train_frac)

    stats_rows = []
    frames = {
        "all": joined,
        "holdout": joined[joined["et_date"].isin(holdout_dates)],
        "train": joined[joined["et_date"].isin(train_dates)],
        "rth_no_event": joined[joined["is_rth"] & ~joined["event_blackout"]],
        "regime_overlap": joined[joined["has_regime"]],
        "holdout_regime_overlap": joined[joined["et_date"].isin(holdout_dates) & joined["has_regime"]],
    }
    for split, frame in frames.items():
        stats_rows.append(cell_stats(frame, "ALL", split))
        for formation, sub in frame.groupby("formation"):
            stats_rows.append(cell_stats(sub, f"formation={formation}", split))
        for regime, sub in frame.groupby("regime"):
            stats_rows.append(cell_stats(sub, f"regime={regime}", split))
        # compounds
        for (formation, regime), sub in frame.groupby(["formation", "regime"]):
            stats_rows.append(cell_stats(sub, f"{formation}|{regime}", split))
        # |z|>=2 compounds
        hi = frame[pd.to_numeric(frame["open_residual_z"], errors="coerce").abs() >= 2.0]
        stats_rows.append(cell_stats(hi, "ALL_|z|>=2", split))
        for (formation, regime), sub in hi.groupby(["formation", "regime"]):
            stats_rows.append(cell_stats(sub, f"{formation}|{regime}|z>=2", split))

    stats = pd.DataFrame(stats_rows)
    gate = evaluate_gate(stats)
    gate["train_dates"] = train_dates
    gate["holdout_dates"] = holdout_dates
    gate["n_alerts"] = int(len(joined))
    gate["n_with_regime"] = int(joined["has_regime"].sum())
    gate["regime_date_coverage"] = sorted(joined.loc[joined["has_regime"], "et_date"].unique().tolist())
    gate["isolation"] = {
        "modified_v1_v2_v3": False,
        "wrote_live_stores": False,
        "touched_ports_or_sod": False,
    }

    joined_path = output_dir / "confluence_episodes.csv"
    stats_path = output_dir / "confluence_cell_stats.csv"
    gate_path = output_dir / "confluence_gate.json"
    summary_path = output_dir / "confluence_summary.json"
    joined.to_csv(joined_path, index=False)
    stats.to_csv(stats_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "idea": "v2xv3_confluence",
        "gate": gate,
        "artifacts": {
            "episodes": str(joined_path),
            "stats": str(stats_path),
            "gate": str(gate_path),
            "summary": str(summary_path),
        },
        "notes": [
            "Read-only; V1/V2/V3 untouched.",
            "V3 regime_log coverage is limited (only days with regime_log_*.csv).",
            "Formation is recomputed from V2 signal_log price/FV paths.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 x V3 confluence research (read-only).")
    parser.add_argument("--alert-log", default=str(V2_LIVE / "alert_log.csv"))
    parser.add_argument("--v2-live-dir", default=str(V2_LIVE))
    parser.add_argument("--v3-live-dir", default=str(V3_LIVE))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--train-frac", type=float, default=0.7)
    args = parser.parse_args()
    summary = run(args)
    g = summary["gate"]
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "passed": g.get("passed"),
                "letter_pass_expectancy": g.get("letter_pass_expectancy"),
                "n_with_regime": g.get("n_with_regime"),
                "regime_date_coverage": g.get("regime_date_coverage"),
                "best": g.get("best"),
                "recommendation": g.get("recommendation"),
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
