#!/usr/bin/env python3
"""Read-only TY call/put skew-leg shock event study.

Guardrail: gamma and economic-event SQLite files are opened with mode=ro.
No V1/V2/V3 app imports, live-store writes, port changes, or dashboard changes.
Outputs are confined to workspace/analysis/aggression_v2_skew_leg_events/.
"""

from __future__ import annotations

import argparse
import json
import re
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

from scripts.aggression_gate_lib import benjamini_hochberg, bootstrap_ci, one_sided_p  # noqa: E402

GAMMA_DB = Path(r"C:\uikitxv2_cache\gamma_tracker\gamma_tracker_v3.db")
V2_MINUTE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_options_context" / "v2_options_minute_joined.csv"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_skew_leg_events"
EASTERN = ZoneInfo("America/New_York")
MONEYNESS = (25, 50, 75)
PRIMARY_MONEYNESS = 50
HORIZONS = (5, 15, 30, 60)
ARMS = ("PUT_BID_DOWN", "CALL_BID_UP", "PUT_SOFTEN_UP", "CALL_SOFTEN_DOWN")
EXPECTED_SIGN = {"PUT_BID_DOWN": -1.0, "CALL_BID_UP": 1.0, "PUT_SOFTEN_UP": 1.0, "CALL_SOFTEN_DOWN": -1.0}
Z_THRESHOLD = 1.5
COOLDOWN_MINUTES = 30
TICK_SIZE = 1.0 / 64.0
N_BOOT = 10_000
BH_Q = 0.10
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


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _ordinal(expiry: pd.Series) -> pd.Series:
    return pd.to_numeric(expiry.astype(str).str.extract(r"(\d+)", expand=False), errors="coerce")


def _front_weight(expiry: pd.Series) -> pd.Series:
    ordinal = _ordinal(expiry)
    return 1.0 / (ordinal - 1.0).clip(lower=1.0)


def _to_utc_from_et(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce")
    return parsed.dt.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


def load_leg_panel(db_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    with _connect_ro(db_path) as conn:
        legs = pd.read_sql_query(
            """
            SELECT s.timestamp, o.expiry, o.option_type, o.moneyness,
                   o.implied_vol_daily_bp
            FROM option_surface_v2 o
            JOIN snapshot_v2 s ON s.id=o.snapshot_id
            WHERE o.moneyness IN (25,50,75)
              AND o.option_type IN ('Call','Put')
              AND o.implied_vol_daily_bp IS NOT NULL
            """,
            conn,
        )
        rr = pd.read_sql_query(
            """
            SELECT s.timestamp, m.expiry, m.rr25, m.rr50, m.rr75
            FROM summary_metrics_v2 m
            JOIN snapshot_v2 s ON s.id=m.snapshot_id
            """,
            conn,
        )
    legs["ts"] = _to_utc_from_et(legs["timestamp"])
    legs["weight"] = _front_weight(legs["expiry"])
    legs["iv"] = pd.to_numeric(legs["implied_vol_daily_bp"], errors="coerce")
    paired = legs.dropna(subset=["ts", "expiry", "moneyness", "iv", "weight"]).pivot_table(
        index=["ts", "expiry", "weight"],
        columns=["option_type", "moneyness"],
        values="iv",
        aggfunc="last",
    )
    leg_frames = []
    for moneyness in MONEYNESS:
        call_key, put_key = ("Call", float(moneyness)), ("Put", float(moneyness))
        if call_key not in paired or put_key not in paired:
            continue
        pair = pd.DataFrame(
            {
                "ts": paired.index.get_level_values("ts"),
                "expiry": paired.index.get_level_values("expiry"),
                "weight": paired.index.get_level_values("weight").astype(float),
                "call_iv": paired[call_key].to_numpy(float),
                "put_iv": paired[put_key].to_numpy(float),
            }
        ).dropna()
        pair["call_weighted"] = pair["call_iv"] * pair["weight"]
        pair["put_weighted"] = pair["put_iv"] * pair["weight"]
        agg = pair.groupby("ts", as_index=True).agg(
            call_weighted=("call_weighted", "sum"),
            put_weighted=("put_weighted", "sum"),
            total_weight=("weight", "sum"),
        )
        agg[f"call_iv_{moneyness}"] = agg["call_weighted"] / agg["total_weight"].replace(0.0, np.nan)
        agg[f"put_iv_{moneyness}"] = agg["put_weighted"] / agg["total_weight"].replace(0.0, np.nan)
        leg_frames.append(agg[[f"call_iv_{moneyness}", f"put_iv_{moneyness}"]])
    pivot = pd.concat(leg_frames, axis=1).sort_index()

    rr["ts"] = _to_utc_from_et(rr["timestamp"])
    rr["weight"] = _front_weight(rr["expiry"])
    rr_rows = []
    for ts, block in rr.dropna(subset=["ts", "weight"]).groupby("ts", sort=False):
        row: dict[str, Any] = {"ts": ts}
        for moneyness in MONEYNESS:
            col = f"rr{moneyness}"
            valid = block[[col, "weight"]].dropna()
            row[f"stored_rr{moneyness}"] = float(np.average(valid[col], weights=valid["weight"])) if len(valid) else np.nan
        rr_rows.append(row)
    rr_frame = pd.DataFrame(rr_rows).set_index("ts").sort_index()
    raw = pivot.join(rr_frame, how="outer").sort_index()
    minute = raw.resample("1min").last().ffill(limit=2)
    return minute, raw


def load_price(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    frame["ts"] = pd.to_datetime(frame["ts"], errors="coerce", utc=True)
    keep = ["ts", "v2_price", "v2_residual_z", "opt_gamma_pin_zone"]
    frame = frame[[col for col in keep if col in frame]].dropna(subset=["ts", "v2_price"])
    return frame.sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")


def _causal_z(series: pd.Series) -> pd.Series:
    history = series.shift(1)
    mean = history.expanding(min_periods=30).mean()
    std = history.expanding(min_periods=30).std(ddof=1).replace(0.0, np.nan)
    return (series - mean) / std


def _event_times(path: Path) -> np.ndarray:
    if not path.exists():
        return np.array([], dtype="datetime64[ns]")
    try:
        with _connect_ro(path) as conn:
            events = pd.read_sql_query(
                "SELECT event_date FROM economic_events WHERE country LIKE '%United States%' OR country='US' OR country IS NULL",
                conn,
            )
    except Exception:
        return np.array([], dtype="datetime64[ns]")
    values = []
    for raw in events.get("event_date", []):
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.isna(ts):
            continue
        ts = ts.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT") if ts.tzinfo is None else ts.tz_convert(EASTERN)
        if pd.notna(ts):
            values.append(ts.tz_convert("UTC").tz_localize(None))
    return np.asarray(sorted(set(values)), dtype="datetime64[ns]")


def _near_event(index: pd.DatetimeIndex, event_times: np.ndarray, minutes: int = 120) -> np.ndarray:
    if not len(event_times):
        return np.zeros(len(index), dtype=bool)
    values = index.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    positions = np.searchsorted(event_times, values)
    out = np.zeros(len(values), dtype=bool)
    window = np.timedelta64(minutes, "m")
    for i, value in enumerate(values):
        for j in (positions[i] - 1, positions[i]):
            if 0 <= j < len(event_times) and abs(value - event_times[j]) <= window:
                out[i] = True
                break
    return out


def build_minute_panel(legs: pd.DataFrame, price: pd.DataFrame, events_db: Path) -> pd.DataFrame:
    panel = price.join(legs, how="inner").sort_index()
    panel = panel.rename(columns={"v2_price": "price", "v2_residual_z": "residual_z", "opt_gamma_pin_zone": "gamma_pin_zone"})
    panel["et_date"] = panel.index.tz_convert(EASTERN).strftime("%Y-%m-%d")
    et = panel.index.tz_convert(EASTERN)
    panel["is_rth"] = ((et.hour * 60 + et.minute) >= 8 * 60) & ((et.hour * 60 + et.minute) < 17 * 60)
    panel["event_near_2h"] = _near_event(panel.index, _event_times(events_db))
    for moneyness in MONEYNESS:
        call_col, put_col = f"call_iv_{moneyness}", f"put_iv_{moneyness}"
        panel[f"rr{moneyness}_calc"] = panel[call_col] - panel[put_col]
        parts = []
        for _, day in panel.groupby("et_date", sort=False):
            d = day[[call_col, put_col]].copy()
            d[f"call_chg_{moneyness}_30m"] = d[call_col].diff(30)
            d[f"put_chg_{moneyness}_30m"] = d[put_col].diff(30)
            d[f"call_z_{moneyness}"] = _causal_z(d[f"call_chg_{moneyness}_30m"])
            d[f"put_z_{moneyness}"] = _causal_z(d[f"put_chg_{moneyness}_30m"])
            parts.append(d.drop(columns=[call_col, put_col]))
        derived = pd.concat(parts).sort_index()
        panel = panel.join(derived)
    return panel


def _arm_candidates(row: pd.Series, moneyness: int) -> tuple[list[str], str | None]:
    call_chg = row.get(f"call_chg_{moneyness}_30m")
    put_chg = row.get(f"put_chg_{moneyness}_30m")
    call_z = row.get(f"call_z_{moneyness}")
    put_z = row.get(f"put_z_{moneyness}")
    if any(pd.isna(value) for value in (call_chg, put_chg, call_z, put_z)):
        return [], None
    max_abs = max(abs(float(call_chg)), abs(float(put_chg)), EPS)
    similar = (
        np.sign(call_chg) == np.sign(put_chg)
        and np.sign(call_chg) != 0
        and abs(float(call_chg) - float(put_chg)) <= 0.25 * max_abs
    )
    if similar and call_chg > 0:
        return [], "GENERAL_VOL_UP"
    if similar and call_chg < 0:
        return [], "GENERAL_VOL_DOWN"
    candidates = []
    if put_z >= Z_THRESHOLD and put_chg > 0 and put_chg > call_chg:
        candidates.append("PUT_BID_DOWN")
    if call_z >= Z_THRESHOLD and call_chg > 0 and call_chg > put_chg:
        candidates.append("CALL_BID_UP")
    if put_z <= -Z_THRESHOLD and put_chg < 0 and put_chg < call_chg:
        candidates.append("PUT_SOFTEN_UP")
    if call_z <= -Z_THRESHOLD and call_chg < 0 and call_chg < put_chg:
        candidates.append("CALL_SOFTEN_DOWN")
    return candidates, "OVERLAP" if len(candidates) > 1 else None


def detect_events(
    panel: pd.DataFrame,
    moneyness: int,
    cooldown_minutes: int = COOLDOWN_MINUTES,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates: dict[str, list[pd.Timestamp]] = {arm: [] for arm in ARMS}
    diagnostics: list[dict[str, Any]] = []
    for ts, row in panel.iterrows():
        arms, diagnostic = _arm_candidates(row, moneyness)
        if diagnostic:
            diagnostics.append({"ts": ts, "arm": diagnostic, "moneyness": moneyness})
            continue
        if len(arms) == 1:
            candidates[arms[0]].append(ts)
    selected = []
    for arm, timestamps in candidates.items():
        last_by_date: dict[str, pd.Timestamp] = {}
        for ts in timestamps:
            et_date = ts.tz_convert(EASTERN).strftime("%Y-%m-%d")
            last = last_by_date.get(et_date)
            if last is not None and ts - last < pd.Timedelta(minutes=cooldown_minutes):
                continue
            selected.append({"ts": ts, "arm": arm, "moneyness": moneyness})
            last_by_date[et_date] = ts
    events = pd.DataFrame(selected)
    if not events.empty:
        events.sort_values("ts", inplace=True)
    diagnostic_frame = pd.DataFrame(diagnostics)
    if not diagnostic_frame.empty:
        kept = []
        for diagnostic, block in diagnostic_frame.groupby("arm", sort=False):
            last_by_date: dict[str, pd.Timestamp] = {}
            for _, row in block.sort_values("ts").iterrows():
                ts = pd.Timestamp(row["ts"])
                et_date = ts.tz_convert(EASTERN).strftime("%Y-%m-%d")
                last = last_by_date.get(et_date)
                if last is None or ts - last >= pd.Timedelta(minutes=cooldown_minutes):
                    kept.append(row.to_dict())
                    last_by_date[et_date] = ts
        diagnostic_frame = pd.DataFrame(kept)
    return events, diagnostic_frame


def attach_outcomes(events: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events
    rows = []
    by_date = {date: day.sort_index() for date, day in panel.groupby("et_date", sort=False)}
    for _, event in events.iterrows():
        ts = pd.Timestamp(event["ts"])
        et_date = ts.tz_convert(EASTERN).strftime("%Y-%m-%d")
        day = by_date.get(et_date)
        if day is None or ts not in day.index:
            continue
        source = day.loc[ts]
        price0 = float(source["price"])
        row = {
            **event.to_dict(),
            "et_date": et_date,
            "price": price0,
            "expected_sign": EXPECTED_SIGN[str(event["arm"])],
            "is_rth": bool(source.get("is_rth")),
            "event_near_2h": bool(source.get("event_near_2h")),
            "gamma_pin_zone": bool(source.get("gamma_pin_zone")) if pd.notna(source.get("gamma_pin_zone")) else False,
            "residual_z": source.get("residual_z"),
            "call_chg_30m": source.get(f"call_chg_{int(event['moneyness'])}_30m"),
            "put_chg_30m": source.get(f"put_chg_{int(event['moneyness'])}_30m"),
            "call_z": source.get(f"call_z_{int(event['moneyness'])}"),
            "put_z": source.get(f"put_z_{int(event['moneyness'])}"),
        }
        for horizon in HORIZONS:
            target = ts + pd.Timedelta(minutes=horizon)
            path = day.loc[(day.index > ts) & (day.index <= target)]
            if path.empty or path.index[-1] < target - pd.Timedelta(seconds=90):
                row[f"signed_move_{horizon}m"] = np.nan
                row[f"adverse_{horizon}m"] = np.nan
                continue
            signed_path = float(row["expected_sign"]) * (path["price"].astype(float) - price0) / TICK_SIZE
            row[f"signed_move_{horizon}m"] = float(signed_path.iloc[-1])
            row[f"adverse_{horizon}m"] = float(signed_path.min())
        rows.append(row)
    return pd.DataFrame(rows)


def _split_dates(events: pd.DataFrame) -> tuple[list[str], list[str]]:
    dates = sorted(events["et_date"].dropna().astype(str).unique().tolist())
    if len(dates) <= 1:
        return dates, []
    cut = max(1, min(len(dates) - 1, int(len(dates) * 0.7)))
    return dates[:cut], dates[cut:]


def _stats(sub: pd.DataFrame, arm: str, horizon: int, slice_name: str, moneyness: int) -> dict[str, Any]:
    move_col, adverse_col = f"signed_move_{horizon}m", f"adverse_{horizon}m"
    data = sub[(sub["arm"] == arm) & sub[move_col].notna()].copy()
    if data.empty:
        return {"slice": slice_name, "moneyness": moneyness, "arm": arm, "horizon": horizon, "n": 0}
    ci = bootstrap_ci(data[move_col], n_boot=N_BOOT, groups=data["et_date"])
    daily = data.groupby("et_date")[move_col].mean()
    return {
        "slice": slice_name,
        "moneyness": moneyness,
        "arm": arm,
        "horizon": horizon,
        "n": int(len(data)),
        "cluster_n": int(data["et_date"].nunique()),
        "hit_rate": float((data[move_col] > 0).mean()),
        "mean_signed_ticks": float(data[move_col].mean()),
        "median_signed_ticks": float(data[move_col].median()),
        "move_ge_4_ticks_rate": float((data[move_col] >= 4.0).mean()),
        "median_adverse_ticks": float(data[adverse_col].median()),
        "p10_adverse_ticks": float(data[adverse_col].quantile(0.10)),
        "ci_low": ci["ci_low"],
        "ci_high": ci["ci_high"],
        "one_sided_lower": ci["one_sided_lower"],
        "one_sided_p": one_sided_p(daily),
    }


def primary_stats(events: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    train_dates, holdout_dates = _split_dates(events)
    holdout = events[events["et_date"].isin(set(holdout_dates))]
    rows = [_stats(holdout, arm, horizon, "holdout_primary", PRIMARY_MONEYNESS) for arm in ARMS for horizon in HORIZONS]
    stats = pd.DataFrame(rows)
    bh = benjamini_hochberg(stats["one_sided_p"], q=BH_Q)
    stats["bh_adjusted_p"] = bh["adjusted_p"]
    stats["bh_significant"] = bh["reject"]
    stats["candidate"] = (
        (stats["n"] >= 30)
        & (stats["one_sided_lower"] > 0)
        & stats["bh_significant"]
    )
    return stats, train_dates, holdout_dates


def robustness_stats(all_events: dict[int, pd.DataFrame], holdout_dates: list[str]) -> pd.DataFrame:
    rows = []
    for moneyness in MONEYNESS:
        events = all_events[moneyness]
        holdout = events[events["et_date"].isin(set(holdout_dates))]
        for arm in ARMS:
            for horizon in HORIZONS:
                rows.append(_stats(holdout, arm, horizon, f"moneyness_{moneyness}", moneyness))
    primary = all_events[PRIMARY_MONEYNESS]
    hold = primary[primary["et_date"].isin(set(holdout_dates))].copy()
    one_day = hold.sort_values("ts").drop_duplicates(["et_date", "arm"], keep="first")
    slices = {
        "pooled_all_dates": primary,
        "one_per_day": one_day,
        "rth": hold[hold["is_rth"]],
        "non_rth": hold[~hold["is_rth"]],
        "event_near_2h": hold[hold["event_near_2h"]],
        "non_event": hold[~hold["event_near_2h"]],
        "gamma_pin": hold[hold["gamma_pin_zone"]],
        "no_gamma_pin": hold[~hold["gamma_pin_zone"]],
        "v2_stretched": hold[pd.to_numeric(hold["residual_z"], errors="coerce").abs() >= 2.0],
        "v2_not_stretched": hold[pd.to_numeric(hold["residual_z"], errors="coerce").abs() < 2.0],
    }
    for name, subset in slices.items():
        for arm in ARMS:
            for horizon in HORIZONS:
                rows.append(_stats(subset, arm, horizon, name, PRIMARY_MONEYNESS))
    return pd.DataFrame(rows)


def reconstruction_stats(panel: pd.DataFrame) -> dict[str, Any]:
    out = {}
    for moneyness in MONEYNESS:
        calc = panel[f"rr{moneyness}_calc"]
        stored = panel[f"stored_rr{moneyness}"]
        valid = pd.DataFrame({"calc": calc, "stored": stored}).dropna()
        out[f"rr{moneyness}"] = {
            "n": int(len(valid)),
            "corr": float(valid["calc"].corr(valid["stored"])) if len(valid) else np.nan,
            "mae": float((valid["calc"] - valid["stored"]).abs().mean()) if len(valid) else np.nan,
        }
    return out


def write_plot(stats: pd.DataFrame, output_dir: Path) -> str | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(ARMS))
    width = 0.18
    for i, horizon in enumerate(HORIZONS):
        block = stats[stats["horizon"] == horizon].set_index("arm").reindex(ARMS)
        means = block["mean_signed_ticks"].to_numpy(float)
        low = means - block["ci_low"].to_numpy(float)
        high = block["ci_high"].to_numpy(float) - means
        ax.bar(x + (i - 1.5) * width, means, width, yerr=[low, high], capsize=2, label=f"{horizon}m")
    ax.axhline(0.0, color="black", linewidth=1)
    ax.set_xticks(x, ARMS, rotation=20, ha="right")
    ax.set_ylabel("Expected-direction TY move (ticks)")
    ax.set_title("Holdout skew-leg shock event outcomes")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    path = output_dir / "skew_leg_event_moves.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    legs, _ = load_leg_panel(Path(args.gamma_db))
    price = load_price(Path(args.v2_minute))
    panel = build_minute_panel(legs, price, Path(args.events_db))
    all_events: dict[int, pd.DataFrame] = {}
    all_diagnostics = []
    for moneyness in MONEYNESS:
        detected, diagnostics = detect_events(panel, moneyness)
        all_events[moneyness] = attach_outcomes(detected, panel)
        if not diagnostics.empty:
            all_diagnostics.append(diagnostics)
    primary = all_events[PRIMARY_MONEYNESS]
    primary_table, train_dates, holdout_dates = primary_stats(primary)
    robust = robustness_stats(all_events, holdout_dates)
    panel_path = output_dir / "leg_minute_panel.csv"
    events_path = output_dir / "skew_leg_events.csv"
    primary_path = output_dir / "primary_holdout_stats.csv"
    robust_path = output_dir / "robustness_stats.csv"
    diagnostic_path = output_dir / "skew_leg_diagnostics.csv"
    summary_path = output_dir / "skew_leg_events_summary.json"
    panel.reset_index(names="ts").to_csv(panel_path, index=False)
    primary.to_csv(events_path, index=False)
    primary_table.to_csv(primary_path, index=False)
    robust.to_csv(robust_path, index=False)
    diagnostics = pd.concat(all_diagnostics, ignore_index=True) if all_diagnostics else pd.DataFrame()
    diagnostics.to_csv(diagnostic_path, index=False)
    plot_path = write_plot(primary_table, output_dir)
    adjacent_pairs = ((5, 15), (15, 30), (30, 60))
    leads = []
    for arm in ARMS:
        candidate_h = set(primary_table[(primary_table["arm"] == arm) & primary_table["candidate"]]["horizon"].astype(int))
        if any(left in candidate_h and right in candidate_h for left, right in adjacent_pairs):
            leads.append(arm)
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gamma_db_mode": "ro",
        "rows_panel": int(len(panel)),
        "panel_start": panel.index.min(),
        "panel_end": panel.index.max(),
        "reconstruction": reconstruction_stats(panel),
        "event_counts": {m: all_events[m]["arm"].value_counts().to_dict() if not all_events[m].empty else {} for m in MONEYNESS},
        "diagnostic_counts": {
            f"{int(moneyness)}:{arm}": int(count)
            for (moneyness, arm), count in diagnostics.groupby(["moneyness", "arm"]).size().items()
        } if not diagnostics.empty else {},
        "train_dates": train_dates,
        "holdout_dates": holdout_dates,
        "research_leads": leads,
        "headline": (
            f"Holdout evidence supports adjacent-horizon directional consistency for: {', '.join(leads)}."
            if leads
            else "No skew-leg event arm passes sample, CI, FDR, and adjacent-horizon consistency requirements."
        ),
        "artifacts": {
            "panel": str(panel_path),
            "events": str(events_path),
            "primary_stats": str(primary_path),
            "robustness": str(robust_path),
            "diagnostics": str(diagnostic_path),
            "plot": plot_path,
            "summary": str(summary_path),
        },
        "isolation": {"gamma_db_read_only": True, "modified_v1_v2_v3": False, "wrote_live_stores": False},
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "headline": summary["headline"], "event_counts": summary["event_counts"], "reconstruction": summary["reconstruction"], "artifacts": summary["artifacts"]}, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="TY put/call skew-leg shock event study.")
    parser.add_argument("--gamma-db", default=str(GAMMA_DB))
    parser.add_argument("--v2-minute", default=str(V2_MINUTE))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
