#!/usr/bin/env python3
"""Offline path-follow-up: trail meat, range persistence, pieces, fade, clock, run-end.

Six diagnostics on the published TBBO panel. Primary exam is still Branch-0
lifo_zone_15m. No letter authorizes a live banner.

1. trail_meat alone, then flow flags *inside* that state
2. Last-15m zone/two-way/meat → next-15m zone (range persistence)
3. Every environment flag scored on zone / meat / two-way separately
4. Fade the last 3-tick push without H5
5. Session buckets + hour table + event shoulder 30–90m
6. First bars after a same-sign run of length ≥10 ends

Guardrail: does not import, write to, or restart V1/V1.2/V2/V2.1/V2.2/V2.3/V2.6/V2.7/V3.
Reads the published TBBO matrix and events DB only.
Writes only under workspace/analysis/aggression_v2_lifo_path_followup/.
Does not overwrite prior LIFO study dirs or tbbo_feature_matrix.csv.
Does not retune H5. ship_gate is always false.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_v1_dispersion_foundations_research import (  # noqa: E402
    fast_decluster,
)
from scripts.aggression_v1_flow_price_dispersion_research import (  # noqa: E402
    EPISODE_STRIDE_MIN,
    EVENTS_DB,
    FEATURE_MATRIX,
    TICK_SIZE,
    _event_times,
    _near_event,
    add_dispersion_dynamics,
    chronological_splits,
    fit_causal_v1_residual,
    load_features,
)
from scripts.aggression_gate_lib import bootstrap_ci  # noqa: E402
from scripts.aggression_v2_lifo_literature_research import (  # noqa: E402
    add_literature_flags,
    sign_run_length,
)
from scripts.aggression_v2_lifo_zone_research import (  # noqa: E402
    ADVERSE_TICKS,
    EASTERN,
    MEAT_TICKS,
    MIN_DATES,
    MIN_LIFT_PP,
    MIN_N,
    N_BOOT,
    PATH_CLOCK_MIN,
    PRIMARY_H,
    RTH_END,
    RTH_START,
    _gate,
    _json_default,
    _score_binary,
    add_edge_clock,
    add_flow_flags,
    add_lifo_labels,
    label_forward_window,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_lifo_path_followup"
TRAIL_H = 15
RUN_CUT = 10
SHIP_GATE = False

ENV_FLAGS = [
    ("trail_meat_15m", "E1"),
    ("trail_meat_win", "E2"),
    ("trail_twoway_15m", "E2"),
    ("trail_zone_15m", "E2"),
    ("rth", "E5"),
    ("overnight", "E5"),
    ("globex_evening", "E5"),
    ("london_hours", "E5"),
    ("us_morning", "E5"),
    ("us_afternoon", "E5"),
    ("event_shoulder_30_90", "E5"),
    ("post_event_30_90", "E5"),
    ("run_just_ended", "E6"),
    ("after_run_end_5m", "E6"),
    ("after_run_end_15m", "E6"),
    ("h5", "H5"),
    ("twosided", "F"),
    ("absorb_bar", "F"),
    ("intensity_hi", "F"),
    ("large_z", "F"),
    ("mod_twosided", "F"),
    ("flippy_active", "F"),
]

INSIDE_TRAIL_FLAGS = [
    "twosided",
    "absorb_bar",
    "h5",
    "intensity_hi",
    "large_z",
    "mod_twosided",
    "flippy_active",
]

FADE_FLAGS = [
    "last_push_3t",
    "push_and_trail_meat",
    "push_and_active",
    "push_and_run_end",
]

OUTCOMES = (
    (f"lifo_zone_{PRIMARY_H}m", "zone"),
    (f"lifo_meat_{PRIMARY_H}m", "meat"),
    (f"lifo_twoway_{PRIMARY_H}m", "twoway"),
)


def load_event_times(path: Path) -> np.ndarray:
    """US event timestamps. Falls back if the shared file: URI reader returns none."""
    times = _event_times(path)
    if len(times):
        return times
    if not path.exists():
        return np.array([], dtype="datetime64[ns]")
    try:
        conn = sqlite3.connect(str(path))
        try:
            events = pd.read_sql_query(
                "SELECT event_date FROM economic_events "
                "WHERE country LIKE '%United States%' OR country='US' OR country IS NULL",
                conn,
            )
        finally:
            conn.close()
    except Exception:
        return np.array([], dtype="datetime64[ns]")
    values = []
    for raw in events.get("event_date", []):
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.isna(ts):
            continue
        if ts.tzinfo is None:
            ts = ts.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT")
        else:
            ts = ts.tz_convert(EASTERN)
        if pd.notna(ts):
            values.append(ts.tz_convert("UTC").tz_localize(None))
    return np.asarray(sorted(set(values)), dtype="datetime64[ns]")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def add_trail_path_labels(df: pd.DataFrame, horizon: int = TRAIL_H) -> pd.DataFrame:
    """Completed last-H path using the same label as the forward zone (H+1 prices)."""
    out = df.copy()
    meat_c = pd.Series(np.nan, index=out.index)
    contained = pd.Series(np.nan, index=out.index)
    two_way = pd.Series(np.nan, index=out.index)
    zone = pd.Series(np.nan, index=out.index)
    for _, day in out.groupby("et_date", sort=False):
        price = day["price"].to_numpy(dtype=float)
        n = len(day)
        m = np.full(n, np.nan)
        c = np.full(n, np.nan)
        tw = np.full(n, np.nan)
        z = np.full(n, np.nan)
        for i in range(horizon, n):
            lab = label_forward_window(price[i - horizon : i + 1], MEAT_TICKS, 8.0)
            m[i] = lab["meat"]
            c[i] = lab["contained"]
            tw[i] = lab["two_way"]
            z[i] = lab["zone"]
        idx = day.index
        meat_c.loc[idx] = m
        contained.loc[idx] = c
        two_way.loc[idx] = tw
        zone.loc[idx] = z
    out["trail_meat_win"] = (meat_c >= 0.5) & meat_c.notna()
    out["trail_contained_15m"] = (contained >= 0.5) & contained.notna()
    out["trail_twoway_15m"] = (two_way >= 0.5) & two_way.notna()
    out["trail_zone_15m"] = (zone >= 0.5) & zone.notna()
    return out


def add_session_flags(df: pd.DataFrame, event_times: np.ndarray) -> pd.DataFrame:
    out = df.copy()
    et = pd.DatetimeIndex(out.index).tz_convert(EASTERN)
    minutes = et.hour * 60 + et.minute
    out["et_hour"] = et.hour
    out["rth"] = (minutes >= RTH_START) & (minutes < RTH_END)
    out["overnight"] = ~out["rth"]
    out["globex_evening"] = et.hour >= 18
    out["london_hours"] = (et.hour >= 2) & (et.hour < 8)
    out["us_morning"] = (et.hour >= 8) & (et.hour < 12)
    out["us_afternoon"] = (et.hour >= 12) & (et.hour < 15)
    near, post, shoulder = event_distance_flags(out.index, event_times)
    out["event_near"] = near if "event_near" not in out.columns else out["event_near"]
    out["post_event_30_90"] = post
    out["event_shoulder_30_90"] = shoulder
    return out


def event_distance_flags(
    index: pd.DatetimeIndex,
    event_times: np.ndarray,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """±30 near; 30–90 after last event; 30–90 from nearest event either side."""
    near = _near_event(index, event_times, minutes=30)
    post = pd.Series(False, index=index)
    shoulder = pd.Series(False, index=index)
    if not len(event_times):
        return near, post, shoulder
    values = pd.DatetimeIndex(index).tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    positions = np.searchsorted(event_times, values)
    one_min = np.timedelta64(1, "m")
    post_v = np.zeros(len(values), dtype=bool)
    shoulder_v = np.zeros(len(values), dtype=bool)
    for i, value in enumerate(values):
        if np.isnat(value):
            continue
        pos = int(positions[i])
        if pos > 0:
            after = (value - event_times[pos - 1]) / one_min
            if 30.0 < float(after) <= 90.0:
                post_v[i] = True
        nearest = np.inf
        for j in (pos - 1, pos):
            if 0 <= j < len(event_times):
                nearest = min(nearest, abs(float((value - event_times[j]) / one_min)))
        if 30.0 < nearest <= 90.0:
            shoulder_v[i] = True
    post.loc[index] = post_v
    shoulder.loc[index] = shoulder_v
    return near, post, shoulder


def add_run_end_flags(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "sign_run" not in out.columns:
        parts = [sign_run_length(day["agg_1m"]) for _, day in out.groupby("et_date", sort=False)]
        out["sign_run"] = pd.concat(parts).sort_index()
    prev = out.groupby("et_date", sort=False)["sign_run"].shift(1)
    out["run_just_ended"] = (prev >= RUN_CUT) & (out["sign_run"] < RUN_CUT)
    out["after_run_end_5m"] = (
        out.groupby("et_date", sort=False)["run_just_ended"]
        .transform(lambda s: s.rolling(6, min_periods=1).max())
        .fillna(0)
        .astype(bool)
    )
    out["after_run_end_15m"] = (
        out.groupby("et_date", sort=False)["run_just_ended"]
        .transform(lambda s: s.rolling(16, min_periods=1).max())
        .fillna(0)
        .astype(bool)
    )
    out["push_and_trail_meat"] = out["last_push_3t"].fillna(False) & out["trail_meat_15m"].fillna(False)
    out["push_and_active"] = out["last_push_3t"].fillna(False) & ~out["intensity_lo"].fillna(False)
    out["push_and_run_end"] = out["last_push_3t"].fillna(False) & out["run_just_ended"].fillna(False)
    return out


def score_env(
    panel: pd.DataFrame,
    splits: dict[str, list[str]],
    flags: list[tuple[str, str]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, branch in flags:
        if name not in panel.columns:
            continue
        mask = panel[name].fillna(False)
        for outcome, role in OUTCOMES:
            for split, dates in splits.items():
                if split == "all":
                    continue
                stats = _score_binary(panel, dates, mask, outcome, decluster=True)
                stats.update({
                    "flag": name,
                    "branch": branch,
                    "split": split,
                    "outcome": outcome,
                    "role": role,
                })
                stats["gate"] = _gate(stats)
                rows.append(stats)
    return pd.DataFrame(rows)


def score_inside_host(
    panel: pd.DataFrame,
    splits: dict[str, list[str]],
    host: str,
    guests: list[str],
) -> pd.DataFrame:
    """Lift of guest flags inside host=True only (baseline is P(outcome | host))."""
    rows: list[dict[str, Any]] = []
    host_on = panel[host].fillna(False)
    restricted = panel.loc[host_on].copy()
    if restricted.empty:
        return pd.DataFrame()
    for guest in guests:
        if guest not in restricted.columns:
            continue
        mask = restricted[guest].fillna(False)
        for outcome, role in OUTCOMES:
            for split, dates in splits.items():
                if split == "all":
                    continue
                stats = _score_binary(restricted, dates, mask, outcome, decluster=True)
                stats.update({
                    "flag": guest,
                    "host": host,
                    "branch": "E1in",
                    "split": split,
                    "outcome": outcome,
                    "role": role,
                })
                stats["gate"] = _gate(stats)
                rows.append(stats)
    return pd.DataFrame(rows)


def score_fade(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    work = panel.copy()
    for flag in FADE_FLAGS:
        if flag not in work.columns:
            continue
        col = f"edge_{flag}"
        work[col] = add_edge_clock(work, flag)
        for split, dates in splits.items():
            if split == "all":
                continue
            sub = work.loc[work["et_date"].isin(set(dates)) & ~work["event_near"].fillna(False)]
            fired = sub.loc[sub[col].notna()]
            if not fired.empty:
                fired = fast_decluster(fired, pd.Series(True, index=fired.index), stride_min=EPISODE_STRIDE_MIN)
            y = pd.to_numeric(fired[col], errors="coerce").dropna() if not fired.empty else pd.Series(dtype=float)
            n = int(len(y))
            hit = float(y.mean()) if n else np.nan
            ticks = y.map(lambda v: MEAT_TICKS if v >= 0.5 else -ADVERSE_TICKS) if n else pd.Series(dtype=float)
            if n < MIN_N:
                gate = "FAIL_N"
            elif np.isfinite(hit) and hit >= 0.60:
                groups = fired.loc[y.index, "et_date"].astype(str).tolist()
                ci = bootstrap_ci((y - 0.50).to_numpy(), n_boot=N_BOOT, seed=7, groups=groups or None)
                gate = "PASS" if ci.get("one_sided_lower", -1) > 0 else "PROVISIONAL"
            else:
                gate = "FAIL"
            rows.append({
                "flag": flag,
                "branch": "E4",
                "split": split,
                "n": n,
                "n_dates": int(fired["et_date"].nunique()) if n else 0,
                "hit": hit,
                "avg_ticks": float(ticks.mean()) if n else np.nan,
                "clock_min": PATH_CLOCK_MIN,
                "gate": gate,
            })
    return pd.DataFrame(rows)


def score_hours(panel: pd.DataFrame, splits: dict[str, list[str]]) -> pd.DataFrame:
    """Descriptive nonevent zone rate by ET hour. Not a letter."""
    rows: list[dict[str, Any]] = []
    outcome = f"lifo_zone_{PRIMARY_H}m"
    nonevent = panel.loc[~panel["event_near"].fillna(False) & panel[outcome].notna()]
    for split, dates in splits.items():
        if split == "all":
            continue
        sub = nonevent.loc[nonevent["et_date"].isin(set(dates))]
        baseline = float(sub[outcome].mean()) if len(sub) else np.nan
        for hour in range(24):
            cell = sub.loc[sub["et_hour"] == hour]
            rows.append({
                "split": split,
                "et_hour": hour,
                "n": int(len(cell)),
                "n_dates": int(cell["et_date"].nunique()) if len(cell) else 0,
                "zone_rate": float(cell[outcome].mean()) if len(cell) else np.nan,
                "meat_rate": float(cell[f"lifo_meat_{PRIMARY_H}m"].mean()) if len(cell) else np.nan,
                "twoway_rate": float(cell[f"lifo_twoway_{PRIMARY_H}m"].mean()) if len(cell) else np.nan,
                "baseline": baseline,
                "lift_pp": float((cell[outcome].mean() - baseline) * 100.0) if len(cell) and np.isfinite(baseline) else np.nan,
            })
    return pd.DataFrame(rows)


def _fmt(value: Any, spec: str = ".3f") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not np.isfinite(number):
        return "n/a"
    return format(number, spec)


def _validation_zone_letter(journal: pd.DataFrame) -> bool:
    if journal.empty:
        return False
    val = journal.loc[
        (journal["split"] == "validation")
        & (journal["role"] == "zone")
        & (journal["gate"] == "PASS")
    ]
    if val.empty:
        return False
    disc = journal.loc[
        (journal["split"] == "discovery")
        & (journal["role"] == "zone")
        & journal["flag"].isin(val["flag"])
    ]
    return bool((disc["lift_pp"] > 0).any()) if not disc.empty else False


def write_report(
    journal: pd.DataFrame,
    inside: pd.DataFrame,
    edge: pd.DataFrame,
    hours: pd.DataFrame,
    splits: dict[str, list[str]],
    show_final: bool,
    path: Path,
) -> None:
    shown = {"discovery", "validation"}
    if show_final:
        shown.add("final")
    lines = [
        "# Aggression V2 LIFO path follow-up",
        "",
        "Research-only. Live V1 / V2 / V2.2 / V2.7 were not modified, restarted, or written.",
        f"Output: `{path.parent}`",
        f"Run: {datetime.now(timezone.utc).isoformat()}",
        "`ship_gate`: **false**",
        "",
        "FINAL is folded unless a validation zone letter exists (still not a ship).",
        "",
        "## Environment flags (declustered)",
        "",
        "| Flag | Branch | split | outcome | n | rate | baseline | lift pp | gate |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    view = journal.loc[journal["split"].isin(shown)].sort_values(["branch", "flag", "split", "role"])
    for row in view.itertuples(index=False):
        lines.append(
            f"| `{row.flag}` | {row.branch} | {row.split} | {row.role} | {int(row.n)} | "
            f"{_fmt(row.rate)} | {_fmt(row.baseline)} | {_fmt(row.lift_pp, '.2f')} | {row.gate} |"
        )
    lines.extend([
        "",
        "## Flow inside trail_meat_15m",
        "",
        "| Flag | split | outcome | n | rate | baseline | lift pp | gate |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ])
    if not inside.empty:
        iv = inside.loc[inside["split"].isin(shown)].sort_values(["flag", "split", "role"])
        for row in iv.itertuples(index=False):
            lines.append(
                f"| `{row.flag}` | {row.split} | {row.role} | {int(row.n)} | "
                f"{_fmt(row.rate)} | {_fmt(row.baseline)} | {_fmt(row.lift_pp, '.2f')} | {row.gate} |"
            )
    lines.extend([
        "",
        "## Fade last 3-tick push (10m clock, no H5 required)",
        "",
        "| Flag | split | n | hit | avg ticks | gate |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ])
    ev = edge.loc[edge["split"].isin(shown)].sort_values(["flag", "split"])
    for row in ev.itertuples(index=False):
        lines.append(
            f"| `{row.flag}` | {row.split} | {int(row.n)} | {_fmt(row.hit)} | {_fmt(row.avg_ticks, '.2f')} | {row.gate} |"
        )
    lines.extend([
        "",
        "## Zone rate by ET hour (descriptive, no letter)",
        "",
        "Validation only.",
        "",
        "| Hour ET | n | zone | meat | two-way | lift pp |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ])
    hv = hours.loc[hours["split"] == "validation"].sort_values("et_hour")
    for row in hv.itertuples(index=False):
        if int(row.n) == 0:
            continue
        lines.append(
            f"| {int(row.et_hour):02d} | {int(row.n)} | {_fmt(row.zone_rate)} | "
            f"{_fmt(row.meat_rate)} | {_fmt(row.twoway_rate)} | {_fmt(row.lift_pp, '.2f')} |"
        )
    lines.extend([
        "",
        "## Splits",
        "",
        f"- Discovery: {splits['discovery'][0]} … {splits['discovery'][-1]} ({len(splits['discovery'])} dates)",
        f"- Validation: {splits['validation'][0]} … {splits['validation'][-1]} ({len(splits['validation'])} dates)",
        f"- FINAL: {splits['final'][0]} … {splits['final'][-1]} ({len(splits['final'])} dates)",
        "",
        "`ship_gate` is false. This run does not authorize a live banner, clock, or H5 change.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading features...", flush=True)
    features, meta = load_features(FEATURE_MATRIX)
    event_times = load_event_times(EVENTS_DB)
    print(f"Event timestamps: {len(event_times)}", flush=True)
    features["event_near"] = _near_event(features.index, event_times)
    print("Causal V1 residual (H5 / leftover controls)...", flush=True)
    panel = fit_causal_v1_residual(features)
    panel = add_dispersion_dynamics(panel)
    print("Labels + flags...", flush=True)
    panel = add_lifo_labels(panel)
    panel = add_trail_path_labels(panel)
    panel = add_flow_flags(panel)
    panel = add_literature_flags(panel)
    panel = add_session_flags(panel, event_times)
    panel = add_run_end_flags(panel)
    splits = chronological_splits(meta["clean_dates"])

    print("Scoring...", flush=True)
    journal = score_env(panel, splits, ENV_FLAGS)
    inside = score_inside_host(panel, splits, "trail_meat_15m", INSIDE_TRAIL_FLAGS)
    edge = score_fade(panel, splits)
    hours = score_hours(panel, splits)
    show_final = _validation_zone_letter(journal)

    journal.to_csv(OUTPUT_DIR / "flag_journal.csv", index=False)
    inside.to_csv(OUTPUT_DIR / "inside_trail_meat.csv", index=False)
    edge.to_csv(OUTPUT_DIR / "edge_clock.csv", index=False)
    hours.to_csv(OUTPUT_DIR / "hour_table.csv", index=False)
    _write_json(
        OUTPUT_DIR / "summary.json",
        {
            "ship_gate": SHIP_GATE,
            "show_final": show_final,
            "rows_clean": meta["rows_clean"],
            "dates_clean": meta["dates_clean"],
            "splits": {k: v for k, v in splits.items() if k != "all"},
        },
    )
    write_report(journal, inside, edge, hours, splits, show_final, OUTPUT_DIR / "readout.md")
    val_zone = journal.loc[
        (journal["split"] == "validation") & (journal["role"] == "zone"),
        ["flag", "n", "lift_pp", "gate"],
    ]
    print(f"Wrote {OUTPUT_DIR}", flush=True)
    print(json.dumps({"show_final": show_final, "ship_gate": SHIP_GATE, "val_zone": val_zone.to_dict(orient="records")}, default=_json_default), flush=True)


if __name__ == "__main__":
    main()
