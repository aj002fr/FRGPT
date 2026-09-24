#!/usr/bin/env python3
"""Read-only non-flow alert-quality context study for Aggression V2.

Guardrail: reads existing episode artifacts and economic_events.db in mode=ro.
It does not import/modify live dashboard apps, write *_live/, touch ports, or
build a dashboard. Outputs are confined to aggression_v2_nonflow_context/.
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

from scripts.aggression_gate_lib import benjamini_hochberg  # noqa: E402

ANALYSIS = PROJECT_ROOT / "workspace" / "analysis"
ALERT_LOG = ANALYSIS / "aggression_v2_live" / "alert_log.csv"
OPTIONS_EPISODES = ANALYSIS / "aggression_v2_options_context" / "v2_options_episode_joined.csv"
MAGNITUDE_EPISODES = ANALYSIS / "aggression_v2_magnitude" / "magnitude_episodes.csv"
MAGNITUDE_SCORES = ANALYSIS / "aggression_v2_magnitude" / "magnitude_holdout_scores.csv"
DEPTH_EPISODES = ANALYSIS / "aggression_v2_depth" / "depth_conditioned_episodes.csv"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = ANALYSIS / "aggression_v2_nonflow_context"
EASTERN = ZoneInfo("America/New_York")
TICK_SIZE = 1.0 / 64.0
COST_TICKS = 0.5
EVENT_WINDOW_MINUTES = 30
N_BOOT = 10_000
BH_Q = 0.10
MODIFIERS = ("MAGNITUDE_STATE", "GAMMA_PIN", "SKEW_AGREES_FADE", "EVENT_NEAR", "DEPTH_STRESS", "QUALITY_STACK")
OUTCOMES = ("pnl15", "pnl30", "resolution", "mae", "big_move")


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


def _bool(series: pd.Series) -> pd.Series:
    return series.map(lambda value: str(value).lower() in {"true", "1", "1.0"} if pd.notna(value) else False)


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


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


def _near_event(opened: pd.Series, event_times: np.ndarray) -> pd.Series:
    if not len(event_times):
        return pd.Series(False, index=opened.index)
    ts = pd.to_datetime(opened, errors="coerce", utc=True, format="mixed")
    values = ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    positions = np.searchsorted(event_times, values)
    out = np.zeros(len(values), dtype=bool)
    window = np.timedelta64(EVENT_WINDOW_MINUTES, "m")
    for i, value in enumerate(values):
        if np.isnat(value):
            continue
        for j in (positions[i] - 1, positions[i]):
            if 0 <= j < len(event_times) and abs(value - event_times[j]) <= window:
                out[i] = True
                break
    return pd.Series(out, index=opened.index)


def _split_dates(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    dates = sorted(frame["et_date"].dropna().astype(str).unique().tolist())
    cut = max(1, min(len(dates) - 1, int(len(dates) * 0.7))) if len(dates) > 1 else len(dates)
    return dates[:cut], dates[cut:]


def build_episode_table(
    alert_path: Path,
    options_path: Path,
    magnitude_path: Path,
    magnitude_scores_path: Path,
    depth_path: Path,
    events_db: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    alerts = pd.read_csv(alert_path)
    alerts["opened_ts"] = pd.to_datetime(alerts["opened_ts"], errors="coerce", utc=True, format="mixed")
    alerts["et_date"] = alerts["et_date"].astype(str)
    options = pd.read_csv(options_path)
    option_cols = [
        "id", "gamma_near_spot_z", "gamma_pin_zone", "gamma_pin",
        "skew_agrees_fade", "skew_against_fade", "rr50_chg_30m", "skew_index",
    ]
    options = options[[column for column in option_cols if column in options]].drop_duplicates("id", keep="last")
    magnitude = pd.read_csv(magnitude_path)
    magnitude_cols = [
        "id", "prior10_realized_vol", "prior10_range_ticks",
        "forward_max_abs_move_ticks", "y_big_move",
    ]
    magnitude = magnitude[[column for column in magnitude_cols if column in magnitude]].drop_duplicates("id", keep="last")
    scores = pd.read_csv(magnitude_scores_path)
    scores = scores[[column for column in ("id", "proba_big_move") if column in scores]].drop_duplicates("id", keep="last")
    frame = alerts.merge(options, on="id", how="left").merge(magnitude, on="id", how="left").merge(scores, on="id", how="left")
    frame["fade_after_cost_15m"] = pd.to_numeric(frame["dir_pnl_15m"], errors="coerce") / TICK_SIZE - COST_TICKS
    frame["fade_after_cost_30m"] = pd.to_numeric(frame["dir_pnl_30m"], errors="coerce") / TICK_SIZE - COST_TICKS
    frame["resolved"] = (frame["reversion_status"].astype(str).str.lower() == "resolved").astype(float)
    frame["big_move"] = (pd.to_numeric(frame["forward_max_abs_move_ticks"], errors="coerce") >= 5.0).astype(float)
    frame.loc[frame["forward_max_abs_move_ticks"].isna(), "big_move"] = np.nan
    frame["EVENT_NEAR"] = _near_event(frame["opened_ts"], _event_times(events_db))
    for column in ("gamma_pin_zone", "gamma_pin", "skew_agrees_fade", "skew_against_fade"):
        if column in frame:
            frame[column] = _bool(frame[column])

    depth_coverage = 0
    if depth_path.exists():
        depth = pd.read_csv(depth_path)
        depth["bucket_ts"] = pd.to_datetime(depth["bucket_ts"], errors="coerce", utc=True)
        depth = depth.dropna(subset=["bucket_ts"]).sort_values("bucket_ts")
        depth = depth[["bucket_ts", "depth_z", "depth_regime"]].rename(
            columns={"bucket_ts": "depth_ts", "depth_z": "depth_depth_z", "depth_regime": "depth_depth_regime"}
        )
        ordered = frame.sort_values("opened_ts")
        ordered = pd.merge_asof(
            ordered,
            depth,
            left_on="opened_ts",
            right_on="depth_ts",
            direction="nearest",
            tolerance=pd.Timedelta(minutes=1),
        )
        frame = ordered.sort_index()
        depth_coverage = int(frame["depth_ts"].notna().sum())
    return frame, {
        "alert_rows": int(len(alerts)),
        "options_coverage": int(frame["rr50_chg_30m"].notna().sum()),
        "magnitude_coverage": int(frame["prior10_realized_vol"].notna().sum()),
        "magnitude_score_coverage": int(frame["proba_big_move"].notna().sum()),
        "depth_coverage": depth_coverage,
        "event_near_rows": int(frame["EVENT_NEAR"].sum()),
    }


def add_modifiers(frame: pd.DataFrame, selection_dates: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = frame.copy()
    selection = out[out["et_date"].isin(set(selection_dates))]
    vol_threshold = float(selection["prior10_realized_vol"].quantile(0.70))
    range_threshold = float(selection["prior10_range_ticks"].quantile(0.70))
    out["MAGNITUDE_STATE"] = (
        (pd.to_numeric(out["prior10_realized_vol"], errors="coerce") >= vol_threshold)
        | (pd.to_numeric(out["prior10_range_ticks"], errors="coerce") >= range_threshold)
    )
    out["MAGNITUDE_AVAILABLE"] = out[["prior10_realized_vol", "prior10_range_ticks"]].notna().any(axis=1)
    out["GAMMA_PIN"] = out.get("gamma_pin_zone", pd.Series(False, index=out.index)).fillna(False) | out.get(
        "gamma_pin", pd.Series(False, index=out.index)
    ).fillna(False)
    out["GAMMA_AVAILABLE"] = out["gamma_near_spot_z"].notna() | out.get(
        "gamma_pin_zone", pd.Series(np.nan, index=out.index)
    ).notna()
    out["SKEW_AGREES_FADE"] = out.get("skew_agrees_fade", pd.Series(False, index=out.index)).fillna(False)
    out["SKEW_AVAILABLE"] = out["rr50_chg_30m"].notna()
    if "depth_depth_z" in out:
        depth_selection = selection["depth_depth_z"].dropna()
        depth_threshold = float(depth_selection.quantile(0.20)) if len(depth_selection) else np.nan
        out["DEPTH_STRESS"] = (
            (pd.to_numeric(out["depth_depth_z"], errors="coerce") <= depth_threshold)
            | out["depth_depth_regime"].astype(str).str.contains("thin", case=False, na=False)
        ) if np.isfinite(depth_threshold) else False
        out["DEPTH_AVAILABLE"] = out["depth_ts"].notna()
    else:
        depth_threshold = np.nan
        out["DEPTH_STRESS"] = False
        out["DEPTH_AVAILABLE"] = False
    available_pairs = [
        ("MAGNITUDE_STATE", "MAGNITUDE_AVAILABLE"),
        ("GAMMA_PIN", "GAMMA_AVAILABLE"),
        ("SKEW_AGREES_FADE", "SKEW_AVAILABLE"),
        ("EVENT_NEAR", None),
        ("DEPTH_STRESS", "DEPTH_AVAILABLE"),
    ]
    numerator = pd.Series(0.0, index=out.index)
    denominator = pd.Series(0.0, index=out.index)
    for modifier, available in available_pairs:
        availability = pd.Series(True, index=out.index) if available is None else out[available].fillna(False).astype(bool)
        numerator += out[modifier].fillna(False).astype(float) * availability.astype(float)
        denominator += availability.astype(float)
    out["quality_score"] = numerator / denominator.replace(0.0, np.nan)
    out["quality_components"] = denominator
    selection_score = out[out["et_date"].isin(set(selection_dates)) & (out["quality_components"] >= 2)]["quality_score"]
    quality_threshold = float(selection_score.quantile(2.0 / 3.0)) if len(selection_score) else np.nan
    out["QUALITY_STACK"] = (out["quality_score"] >= quality_threshold) & (out["quality_components"] >= 2)
    return out, {
        "vol_p70": vol_threshold,
        "range_p70": range_threshold,
        "depth_p20": depth_threshold,
        "quality_p67": quality_threshold,
    }


def _cluster_delta(
    frame: pd.DataFrame,
    modifier: str,
    value_col: str,
    *,
    median: bool = False,
    n_boot: int = N_BOOT,
) -> dict[str, Any]:
    data = frame[["et_date", modifier, value_col]].replace([np.inf, -np.inf], np.nan).dropna(subset=[value_col])
    data[modifier] = data[modifier].fillna(False).astype(bool)
    conditioned = data[data[modifier]]
    if data.empty or conditioned.empty:
        return {"baseline_n": int(len(data)), "conditioned_n": int(len(conditioned))}
    baseline_value = float(data[value_col].median() if median else data[value_col].mean())
    conditioned_value = float(conditioned[value_col].median() if median else conditioned[value_col].mean())
    observed = baseline_value - conditioned_value if median else conditioned_value - baseline_value
    groups = {date: block for date, block in data.groupby("et_date", sort=False)}
    dates = np.asarray(list(groups), dtype=object)
    rng = np.random.default_rng(29)
    if median:
        draws = np.empty(n_boot, dtype=float)
        for index in range(n_boot):
            sampled = rng.choice(dates, size=len(dates), replace=True)
            block = pd.concat([groups[date] for date in sampled], ignore_index=True)
            sub = block[block[modifier]]
            draws[index] = block[value_col].median() - sub[value_col].median() if not sub.empty else np.nan
    else:
        aggregates = []
        for date in dates:
            block = groups[date]
            sub = block[block[modifier]]
            aggregates.append(
                [
                    block[value_col].sum(),
                    block[value_col].notna().sum(),
                    sub[value_col].sum(),
                    sub[value_col].notna().sum(),
                ]
            )
        aggregates = np.asarray(aggregates, dtype=float)
        sampled = rng.integers(0, len(dates), size=(n_boot, len(dates)))
        totals = aggregates[sampled].sum(axis=1)
        base = totals[:, 0] / np.maximum(totals[:, 1], 1.0)
        cond = totals[:, 2] / np.maximum(totals[:, 3], 1.0)
        draws = np.where(totals[:, 3] > 0, cond - base, np.nan)
    draws = draws[np.isfinite(draws)]
    return {
        "baseline_n": int(len(data)),
        "conditioned_n": int(len(conditioned)),
        "cluster_n": int(data["et_date"].nunique()),
        "baseline_value": baseline_value,
        "conditioned_value": conditioned_value,
        "improvement": observed,
        "ci_low": float(np.quantile(draws, 0.025)) if len(draws) else np.nan,
        "ci_high": float(np.quantile(draws, 0.975)) if len(draws) else np.nan,
        "one_sided_p": float((np.sum(draws <= 0.0) + 1) / (len(draws) + 1)) if len(draws) else np.nan,
    }


def evaluate(frame: pd.DataFrame, holdout_dates: list[str]) -> pd.DataFrame:
    holdout = frame[frame["et_date"].isin(set(holdout_dates))].copy()
    outcome_map = {
        "pnl15": ("fade_after_cost_15m", False),
        "pnl30": ("fade_after_cost_30m", False),
        "resolution": ("resolved", False),
        "mae": ("mae_to_resolve_ticks", True),
        "big_move": ("big_move", False),
    }
    rows = []
    for modifier in MODIFIERS:
        if modifier == "DEPTH_STRESS" and not holdout["DEPTH_AVAILABLE"].any():
            continue
        for outcome, (column, median) in outcome_map.items():
            if outcome == "big_move" and modifier not in {"MAGNITUDE_STATE", "QUALITY_STACK", "EVENT_NEAR"}:
                continue
            result = _cluster_delta(holdout, modifier, column, median=median)
            rows.append({"modifier": modifier, "outcome": outcome, **result})
    stats = pd.DataFrame(rows)
    bh = benjamini_hochberg(stats["one_sided_p"], q=BH_Q)
    stats["bh_adjusted_p"] = bh["adjusted_p"]
    stats["bh_significant"] = bh["reject"]
    stats["adds_value"] = (
        (stats["conditioned_n"] >= 30)
        & (stats["ci_low"] > 0.0)
        & stats["bh_significant"]
    )
    stats["tier"] = np.where(
        stats["adds_value"],
        "PASS",
        np.where(stats["improvement"].fillna(-np.inf) > 0, "PROVISIONAL", "FAIL"),
    )
    return stats


def write_plot(stats: pd.DataFrame, output_dir: Path) -> str | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    data = stats[stats["outcome"] == "pnl30"].sort_values("improvement", ascending=False)
    if data.empty:
        return None
    y = np.arange(len(data))
    values = data["improvement"].to_numpy(float)
    low = values - data["ci_low"].to_numpy(float)
    high = data["ci_high"].to_numpy(float) - values
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.barh(y, values, xerr=[low, high], capsize=3)
    ax.set_yticks(y, data["modifier"])
    ax.axvline(0, color="black", linewidth=1)
    ax.set_xlabel("30m after-cost improvement vs all alerts (ticks)")
    ax.set_title("Non-flow modifier holdout ranking")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    path = output_dir / "nonflow_modifier_ranking.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frame, coverage = build_episode_table(
        Path(args.alert_log),
        Path(args.options_episodes),
        Path(args.magnitude_episodes),
        Path(args.magnitude_scores),
        Path(args.depth_episodes),
        Path(args.events_db),
    )
    selection_dates, holdout_dates = _split_dates(frame)
    frame, thresholds = add_modifiers(frame, selection_dates)
    stats = evaluate(frame, holdout_dates)
    table_path = output_dir / "nonflow_episode_table.csv"
    stats_path = output_dir / "nonflow_modifier_stats.csv"
    summary_path = output_dir / "nonflow_context_summary.json"
    frame.to_csv(table_path, index=False)
    stats.to_csv(stats_path, index=False)
    plot = write_plot(stats, output_dir)
    pass_rows = stats[stats["adds_value"]].to_dict(orient="records")
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coverage": coverage,
        "selection_dates": selection_dates,
        "holdout_dates": holdout_dates,
        "thresholds_from_selection": thresholds,
        "tests": int(len(stats)),
        "tier_counts": stats["tier"].value_counts().to_dict(),
        "value_adding_modifiers": pass_rows,
        "value_verdict": "YES" if pass_rows else "NO",
        "headline": (
            "At least one non-flow modifier adds statistically controlled holdout value."
            if pass_rows
            else "No non-flow modifier adds statistically controlled holdout value to the existing V2 alert."
        ),
        "best_rows": stats.sort_values(["adds_value", "improvement"], ascending=False).head(15).to_dict(orient="records"),
        "artifacts": {"episode_table": str(table_path), "stats": str(stats_path), "plot": plot, "summary": str(summary_path)},
        "phase2_dashboard_authorized": False,
        "isolation": {"events_db_mode": "ro", "modified_v1_v2_v3": False, "wrote_live_stores": False},
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "verdict": summary["value_verdict"], "headline": summary["headline"], "coverage": coverage, "tiers": summary["tier_counts"], "best": summary["best_rows"][:8], "artifacts": summary["artifacts"]}, indent=2, default=_json_default))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only non-flow V2 alert-quality context study.")
    parser.add_argument("--alert-log", default=str(ALERT_LOG))
    parser.add_argument("--options-episodes", default=str(OPTIONS_EPISODES))
    parser.add_argument("--magnitude-episodes", default=str(MAGNITUDE_EPISODES))
    parser.add_argument("--magnitude-scores", default=str(MAGNITUDE_SCORES))
    parser.add_argument("--depth-episodes", default=str(DEPTH_EPISODES))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
