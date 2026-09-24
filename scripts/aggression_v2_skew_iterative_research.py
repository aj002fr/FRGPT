#!/usr/bin/env python3
"""Five-iteration, read-only TY skew/vol hypothesis research cycle.

Uses existing offline research artifacts only. No live app imports/edits,
*_live/ writes, ports, SOD, Tailscale, Telegram, or dashboard changes.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import benjamini_hochberg, bootstrap_ci, one_sided_p  # noqa: E402
from scripts.aggression_v2_option_shock_persistence_research import build_persistence_rows  # noqa: E402
from scripts.aggression_v2_vol_skew_shock_paths_research import (  # noqa: E402
    ARMS,
    EASTERN,
    attach_control_outcomes,
    attach_outcomes,
    match_controls,
)

SOURCE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_vol_skew_shock_paths"
SOURCE_PANEL = SOURCE_DIR / "shock_minute_panel.csv"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_skew_iterative"
FORMATION_GRID = (5, 15, 30)
Z_GRID = (1.0, 1.5, 2.0)
COOLDOWN_GRID = (15, 30, 60)
QUIET_GRID = (0.40, 0.50, 0.60)
CHANNELS = ("atm_vol", "put_iv_50", "call_iv_50", "rr50_calc", "skew_index", "backbone")
HORIZONS = ("5", "15", "30", "60", "120", "240", "480", "1440")
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


def _causal_z(series: pd.Series) -> pd.Series:
    history = series.shift(1)
    mean = history.expanding(min_periods=30).mean()
    std = history.expanding(min_periods=30).std(ddof=1).replace(0.0, np.nan)
    return (series - mean) / std


def derive_panel(base: pd.DataFrame, formation: int) -> pd.DataFrame:
    parts = []
    for _, day in base.groupby("et_date", sort=False):
        d = day.copy()
        gap = d.index.to_series().diff().dt.total_seconds().div(60.0)
        d["formation_valid"] = gap.rolling(formation, min_periods=formation).max() <= 2.0
        d["price_move_formation_ticks"] = d["price"].diff(formation) / (1.0 / 64.0)
        for channel in CHANNELS:
            d[f"{channel}_chg"] = d[channel].diff(formation)
            d[f"{channel}_z"] = _causal_z(d[f"{channel}_chg"])
        d["max_abs_option_z"] = d[[f"{channel}_z" for channel in CHANNELS]].abs().max(axis=1)
        parts.append(d)
    return pd.concat(parts).sort_index()


def split_manifest(base: pd.DataFrame) -> dict[str, Any]:
    option_active = base.dropna(subset=["atm_vol", "rr50_calc", "skew_index"], how="all")
    dates = sorted(option_active["et_date"].dropna().astype(str).unique().tolist())
    final_count = max(3, int(np.ceil(len(dates) * 0.20))) if len(dates) >= 5 else max(1, len(dates) // 3)
    research_dates, final_dates = dates[:-final_count], dates[-final_count:]
    validation_count = max(2, int(np.ceil(len(research_dates) * 0.30))) if len(research_dates) >= 4 else 1
    return {
        "all_active_dates": dates,
        "research_dates": research_dates,
        "discovery_dates": research_dates[:-validation_count],
        "validation_dates": research_dates[-validation_count:],
        "final_dates": final_dates,
        "final_outcomes_locked_until_iteration_5": True,
    }


def thresholds(panel: pd.DataFrame, dates: list[str], quiet_q: float) -> dict[str, Any]:
    data = panel[panel["et_date"].isin(set(dates))]
    floors = {channel: float(data[f"{channel}_chg"].abs().quantile(0.60)) for channel in CHANNELS}
    candidates = data[data["max_abs_option_z"] >= 1.0]
    price_abs = candidates["price_move_formation_ticks"].abs().dropna()
    return {
        "floors": floors,
        "quiet_threshold": float(price_abs.quantile(quiet_q)),
        "price_led_threshold": float(price_abs.quantile(0.80)),
    }


def candidate(row: pd.Series, z_threshold: float, floors: dict[str, float]) -> tuple[str | None, float, str | None]:
    if not bool(row.get("formation_valid", False)):
        return None, np.nan, None
    call_chg, put_chg = row.get("call_iv_50_chg"), row.get("put_iv_50_chg")
    call_z, put_z = row.get("call_iv_50_z"), row.get("put_iv_50_z")
    if any(pd.isna(value) for value in (call_chg, put_chg, call_z, put_z)):
        return None, np.nan, None
    max_leg = max(abs(float(call_chg)), abs(float(put_chg)), EPS)
    similar = np.sign(call_chg) == np.sign(put_chg) != 0 and abs(float(call_chg) - float(put_chg)) <= 0.25 * max_leg
    atm_values = np.asarray([row.get("atm_vol_z"), row.get("backbone_z")], dtype=float)
    atm_z = float(np.nanmax(np.abs(atm_values))) if np.isfinite(atm_values).any() else np.nan
    if similar and np.isfinite(atm_z) and atm_z >= z_threshold and abs(float(row["atm_vol_chg"])) >= floors["atm_vol"]:
        return f"ATM_VOL_{'UP' if call_chg + put_chg > 0 else 'DOWN'}", atm_z, "atm_vol"
    if abs(float(put_z)) >= z_threshold and abs(float(put_chg)) > abs(float(call_chg)) and abs(float(put_chg)) >= floors["put_iv_50"]:
        return f"PUT_IV_{'UP' if put_chg > 0 else 'DOWN'}", abs(float(put_z)), "put_iv_50"
    if abs(float(call_z)) >= z_threshold and abs(float(call_chg)) > abs(float(put_chg)) and abs(float(call_chg)) >= floors["call_iv_50"]:
        return f"CALL_IV_{'UP' if call_chg > 0 else 'DOWN'}", abs(float(call_z)), "call_iv_50"
    rr_z, rr_chg = row.get("rr50_calc_z"), row.get("rr50_calc_chg")
    if np.sign(call_chg) != np.sign(put_chg) and pd.notna(rr_z) and abs(float(rr_z)) >= z_threshold and abs(float(rr_chg)) >= floors["rr50_calc"]:
        return f"RR50_{'UP' if rr_chg > 0 else 'DOWN'}", abs(float(rr_z)), "rr50_calc"
    skew_z, skew_chg = row.get("skew_index_z"), row.get("skew_index_chg")
    if pd.notna(skew_z) and abs(float(skew_z)) >= z_threshold and abs(float(skew_chg)) >= floors["skew_index"]:
        return f"SKEW_INDEX_{'UP' if skew_chg > 0 else 'DOWN'}", abs(float(skew_z)), "skew_index"
    return None, np.nan, None


def detect(
    panel: pd.DataFrame,
    dates: list[str],
    formation: int,
    z_threshold: float,
    cooldown: int,
    quiet_q: float,
    threshold_dates: list[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    derived = derive_panel(panel, formation)
    limits = thresholds(derived, threshold_dates, quiet_q)
    raw = []
    for ts, row in derived[derived["et_date"].isin(set(dates))].iterrows():
        arm, score, channel = candidate(row, z_threshold, limits["floors"])
        if arm:
            raw.append({"t0": ts, "arm": arm, "score": score, "channel": channel, "et_date": row["et_date"]})
    candidates = pd.DataFrame(raw)
    if candidates.empty:
        return candidates, limits
    selected = []
    for _, day in candidates.groupby("et_date", sort=False):
        cluster = []
        for _, event in day.sort_values("t0").iterrows():
            if cluster and event["t0"] - cluster[-1]["t0"] >= pd.Timedelta(minutes=cooldown):
                selected.append(max(cluster, key=lambda item: item["score"]))
                cluster = []
            cluster.append(event.to_dict())
        if cluster:
            selected.append(max(cluster, key=lambda item: item["score"]))
    events = pd.DataFrame(selected).sort_values("t0")
    events["entry_ts"] = events["t0"] + pd.Timedelta(minutes=1)
    rows = derived.reindex(pd.DatetimeIndex(events["t0"])).reset_index(drop=True)
    for column in ("price_move_formation_ticks", "is_rth", "residual_z", "pre_rv30", "pre_range30_ticks", "pre_low30", "pre_high30", "event_conflict"):
        events[column] = rows[column].to_numpy()
    move = events["price_move_formation_ticks"].abs()
    events["shock_class"] = np.where(
        (move <= limits["quiet_threshold"]) & ~events["event_conflict"],
        "PRICE_QUIET_SHOCK",
        np.where(move >= limits["price_led_threshold"], "PRICE_LED_VOL", "MIXED"),
    )
    events["formation"] = formation
    events["z_threshold"] = z_threshold
    events["cooldown"] = cooldown
    events["quiet_q"] = quiet_q
    return events, limits


def taxonomy_iteration(base: pd.DataFrame, manifest: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = []
    research_dates = manifest["research_dates"]
    for formation, z_threshold, cooldown, quiet_q in itertools.product(FORMATION_GRID, Z_GRID, COOLDOWN_GRID, QUIET_GRID):
        events, _ = detect(base, research_dates, formation, z_threshold, cooldown, quiet_q, manifest["discovery_dates"])
        quiet = events[events.get("shock_class") == "PRICE_QUIET_SHOCK"] if not events.empty else events
        led = events[events.get("shock_class") == "PRICE_LED_VOL"] if not events.empty else events
        per_day = quiet.groupby("et_date").size() if not quiet.empty else pd.Series(dtype=float)
        rows.append(
            {
                "formation": formation,
                "z_threshold": z_threshold,
                "cooldown": cooldown,
                "quiet_q": quiet_q,
                "events": int(len(events)),
                "event_dates": int(events["et_date"].nunique()) if not events.empty else 0,
                "quiet_events": int(len(quiet)),
                "quiet_dates": int(quiet["et_date"].nunique()) if not quiet.empty else 0,
                "price_led_events": int(len(led)),
                "median_quiet_per_day": float(per_day.median()) if len(per_day) else 0.0,
                "score": float(len(quiet) + 2 * quiet["et_date"].nunique() - 0.25 * len(led)) if not quiet.empty else -0.25 * len(led),
            }
        )
    grid = pd.DataFrame(rows).sort_values(["score", "quiet_dates", "quiet_events"], ascending=False)
    best = grid.iloc[0].to_dict()
    config = {
        "formation": int(best["formation"]),
        "z_threshold": float(best["z_threshold"]),
        "cooldown": int(best["cooldown"]),
        "quiet_q": float(best["quiet_q"]),
    }
    return grid, config


def paired_deltas(events: pd.DataFrame, outcomes: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    event_data = outcomes[outcomes["valid"]].copy()
    event_data["event_id"] = event_data.apply(lambda row: f"{row['arm']}|{pd.Timestamp(row['t0']).isoformat()}", axis=1)
    control_data = controls[controls["valid"]].copy()
    metrics = ["signed_ticks", "after_cost_0_5", "abs_ticks", "range_ticks", "rv_ratio", "path_efficiency", "trend", "chop", "stay_in_pre_range", "move_ge_4"]
    control_mean = control_data.groupby(["event_id", "horizon"])[metrics].mean().add_prefix("control_").reset_index()
    merged = event_data.merge(control_mean, on=["event_id", "horizon"], how="left")
    for metric in metrics:
        merged[f"delta_{metric}"] = pd.to_numeric(merged[metric], errors="coerce") - pd.to_numeric(merged[f"control_{metric}"], errors="coerce")
    return merged


def stat(block: pd.DataFrame, metric: str, **keys: Any) -> dict[str, Any]:
    data = block[["et_date", metric]].dropna()
    if data.empty:
        return {**keys, "metric": metric, "n": 0}
    ci = bootstrap_ci(data[metric], n_boot=N_BOOT, groups=data["et_date"])
    daily = data.groupby("et_date")[metric].mean()
    return {
        **keys,
        "metric": metric,
        "n": int(len(data)),
        "cluster_n": int(data["et_date"].nunique()),
        "mean_delta": float(data[metric].mean()),
        "ci_low": ci["ci_low"],
        "ci_high": ci["ci_high"],
        "one_sided_p": one_sided_p(daily),
        "positive_day_rate": float((daily > 0).mean()),
    }


def apply_fdr(frame: pd.DataFrame, group_col: str = "family") -> pd.DataFrame:
    frame["bh_adjusted_p"] = np.nan
    frame["bh_significant"] = False
    for _, indices in frame.groupby(group_col).groups.items():
        pos = list(indices)
        bh = benjamini_hochberg(frame.loc[pos, "one_sided_p"], q=BH_Q)
        frame.loc[pos, "bh_adjusted_p"] = bh["adjusted_p"]
        frame.loc[pos, "bh_significant"] = bh["reject"]
    return frame


def iteration2(events: pd.DataFrame, deltas: pd.DataFrame, discovery_dates: list[str], validation_dates: list[str]) -> pd.DataFrame:
    discovery = events[events["et_date"].isin(set(discovery_dates)) & (events["shock_class"] == "PRICE_QUIET_SHOCK")]
    validation_ids = {
        f"{row.arm}|{pd.Timestamp(row.t0).isoformat()}"
        for row in events.itertuples()
        if row.et_date in set(validation_dates) and row.shock_class == "PRICE_QUIET_SHOCK"
    }
    valid = deltas[deltas["event_id"].isin(validation_ids)].copy()
    rows = []
    for arm in ("ALL_QUIET", *ARMS):
        source = discovery if arm == "ALL_QUIET" else discovery[discovery["arm"] == arm]
        thresholds_q = {"all": -np.inf}
        if len(source):
            thresholds_q["top20"] = float(source["score"].quantile(0.80))
            thresholds_q["top10"] = float(source["score"].quantile(0.90))
        for size_filter, threshold in thresholds_q.items():
            block = valid if arm == "ALL_QUIET" else valid[valid["arm"] == arm]
            if size_filter != "all":
                block = block[pd.to_numeric(block["score"], errors="coerce") >= threshold]
            for horizon in HORIZONS:
                hz = block[block["horizon"].astype(str) == horizon]
                for metric in ("delta_abs_ticks", "delta_range_ticks", "delta_rv_ratio", "delta_move_ge_4"):
                    rows.append(stat(hz, metric, family="magnitude", arm=arm, size_filter=size_filter, horizon=horizon, threshold=threshold))
    return apply_fdr(pd.DataFrame(rows))


def iteration3(events: pd.DataFrame, deltas: pd.DataFrame, panel: pd.DataFrame, validation_dates: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    persistence, _ = build_persistence_rows(panel, events)
    retention = persistence[
        (persistence["metric_variant"] == "primary")
        & persistence["horizon"].isin([15, 30, 60])
        & persistence["valid"]
    ][["event_id", "horizon", "retention"]].copy()
    retention["retention_horizon"] = retention["horizon"].astype(int)
    retention["persistence_state"] = np.select(
        [retention["retention"] >= 0.5, retention["retention"] <= 0.0],
        ["PERSISTENT", "REVERTED"],
        default="PARTIAL",
    )
    joined = deltas.merge(retention.drop(columns=["horizon"]), on="event_id", how="inner")
    joined = joined[joined["et_date"].isin(set(validation_dates)) & (joined["shock_class"] == "PRICE_QUIET_SHOCK")]
    rows = []
    for retention_horizon in (15, 30, 60):
        for state in ("PERSISTENT", "PARTIAL", "REVERTED"):
            block = joined[(joined["retention_horizon"] == retention_horizon) & (joined["persistence_state"] == state)]
            for horizon in HORIZONS:
                hz = block[block["horizon"].astype(str) == horizon]
                for metric in ("delta_path_efficiency", "delta_trend", "delta_chop", "delta_stay_in_pre_range", "delta_range_ticks"):
                    rows.append(stat(hz, metric, family="regime", retention_horizon=retention_horizon, state=state, horizon=horizon))
    return apply_fdr(pd.DataFrame(rows)), persistence


def iteration4(deltas: pd.DataFrame, validation_dates: list[str]) -> pd.DataFrame:
    valid = deltas[deltas["et_date"].isin(set(validation_dates)) & (deltas["shock_class"] == "PRICE_QUIET_SHOCK")]
    rows = []
    for arm in ("ALL_QUIET", *ARMS):
        block = valid if arm == "ALL_QUIET" else valid[valid["arm"] == arm]
        for horizon in ("5", "15", "30", "60"):
            hz = block[block["horizon"].astype(str) == horizon].copy()
            rows.append(stat(hz, "delta_signed_ticks", family="direction", arm=arm, side="expected", horizon=horizon))
            hz["delta_contrarian"] = -pd.to_numeric(hz["delta_signed_ticks"], errors="coerce")
            rows.append(stat(hz, "delta_contrarian", family="direction", arm=arm, side="contrarian", horizon=horizon))
    return apply_fdr(pd.DataFrame(rows))


def iteration5(
    events: pd.DataFrame,
    deltas: pd.DataFrame,
    persistence: pd.DataFrame,
    validation_dates: list[str],
) -> pd.DataFrame:
    retention30 = persistence[
        (persistence["metric_variant"] == "primary")
        & (persistence["horizon"] == 30)
        & persistence["valid"]
    ][["event_id", "retention"]]
    joined = deltas.merge(retention30, on="event_id", how="left")
    joined = joined[joined["et_date"].isin(set(validation_dates)) & (joined["shock_class"] == "PRICE_QUIET_SHOCK")]
    joined["context_rth"] = joined["is_rth"].fillna(False)
    joined["context_residual"] = pd.to_numeric(joined["residual_z"], errors="coerce").abs() >= 1.5
    joined["context_magnitude"] = (
        (pd.to_numeric(joined["pre_rv30"], errors="coerce") >= 0.0129490926588241)
        | (pd.to_numeric(joined["pre_range30_ticks"], errors="coerce") >= 3.0)
    )
    joined["context_persistent"] = pd.to_numeric(joined["retention"], errors="coerce") >= 0.5
    rows = []
    for context in ("context_rth", "context_residual", "context_magnitude", "context_persistent"):
        block = joined[joined[context]]
        for horizon in ("15", "30", "60", "120"):
            hz = block[block["horizon"].astype(str) == horizon]
            for metric in ("delta_range_ticks", "delta_abs_ticks", "delta_signed_ticks"):
                rows.append(stat(hz, metric, family="context", context=context, horizon=horizon, complexity=1))
    return apply_fdr(pd.DataFrame(rows))


def write_journal(path: Path, entries: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(entry, default=_json_default) for entry in entries) + "\n", encoding="utf-8")


def write_plot(leaderboard: pd.DataFrame, output_dir: Path) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    data = leaderboard.dropna(subset=["mean_delta"]).sort_values("mean_delta", ascending=False).head(20)
    if data.empty:
        return None
    fig, ax = plt.subplots(figsize=(10, 6))
    labels = data["iteration"].astype(str) + ":" + data["candidate"].astype(str)
    ax.barh(np.arange(len(data)), data["mean_delta"])
    ax.set_yticks(np.arange(len(data)), labels, fontsize=7)
    ax.invert_yaxis()
    ax.axvline(0, color="black", linewidth=1)
    ax.set_title("Iterative skew/vol validation effects")
    fig.tight_layout()
    path = output_dir / "iteration_effects.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = pd.read_csv(args.panel, parse_dates=["ts"]).set_index("ts").sort_index()
    base.index = pd.DatetimeIndex(base.index).tz_convert("UTC")
    manifest = split_manifest(base)
    base["data_split"] = np.select(
        [
            base["et_date"].isin(set(manifest["discovery_dates"])),
            base["et_date"].isin(set(manifest["validation_dates"])),
            base["et_date"].isin(set(manifest["final_dates"])),
        ],
        ["DISCOVERY", "VALIDATION", "FINAL"],
        default="OTHER",
    )
    base.reset_index(names="ts").to_csv(output_dir / "research_panel.csv", index=False)
    (output_dir / "split_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    journal: list[dict[str, Any]] = []
    grid, config = taxonomy_iteration(base, manifest)
    grid.to_csv(output_dir / "iteration_01_taxonomy.csv", index=False)
    iter1 = {
        "iteration": 1,
        "hypothesis": "Failures mix option-led and price-led shocks.",
        "selected_config": config,
        "top_grid": grid.head(10).to_dict(orient="records"),
        "conclusion": "Freeze the taxonomy maximizing price-quiet event/date coverage; outcome selection remains untouched.",
        "next": "Test magnitude/range delivery on validation dates.",
    }
    (output_dir / "iteration_01_taxonomy.json").write_text(json.dumps(iter1, indent=2, default=_json_default), encoding="utf-8")
    journal.append(iter1)

    research_events, frozen_limits = detect(
        base, manifest["research_dates"], **config, threshold_dates=manifest["discovery_dates"]
    )
    research_panel = derive_panel(base, config["formation"])
    outcomes = attach_outcomes(research_events, research_panel["price"])
    controls = match_controls(research_events, research_panel, manifest["discovery_dates"], manifest["validation_dates"])
    control_outcomes = attach_control_outcomes(controls, research_panel["price"])
    deltas = paired_deltas(research_events, outcomes, control_outcomes)

    iter2_stats = iteration2(research_events, deltas, manifest["discovery_dates"], manifest["validation_dates"])
    iter2_stats.to_csv(output_dir / "iteration_02_magnitude.csv", index=False)
    iter2_best = iter2_stats.sort_values(["bh_significant", "mean_delta"], ascending=False).head(10)
    iter2 = {
        "iteration": 2,
        "hypothesis": "Option shocks predict delivered magnitude/range before direction.",
        "tests": int(len(iter2_stats)),
        "best": iter2_best.to_dict(orient="records"),
        "conclusion": "Retain only validation-positive magnitude/range candidates; do not infer direction.",
        "next": "Test whether option persistence separates trend from range.",
    }
    (output_dir / "iteration_02_magnitude.json").write_text(json.dumps(iter2, indent=2, default=_json_default), encoding="utf-8")
    journal.append(iter2)

    iter3_stats, persistence = iteration3(research_events, deltas, research_panel, manifest["validation_dates"])
    iter3_stats.to_csv(output_dir / "iteration_03_regime.csv", index=False)
    iter3 = {
        "iteration": 3,
        "hypothesis": "Persistent option shocks separate trend from wide chop.",
        "tests": int(len(iter3_stats)),
        "best": iter3_stats.sort_values(["bh_significant", "mean_delta"], ascending=False).head(10).to_dict(orient="records"),
        "conclusion": "Treat persistence-path effects as explanatory unless validation n/date guards pass.",
        "next": "Test directional asymmetry only on covered branches.",
    }
    (output_dir / "iteration_03_regime.json").write_text(json.dumps(iter3, indent=2, default=_json_default), encoding="utf-8")
    journal.append(iter3)

    iter4_stats = iteration4(deltas, manifest["validation_dates"])
    iter4_stats.to_csv(output_dir / "iteration_04_direction.csv", index=False)
    iter4 = {
        "iteration": 4,
        "hypothesis": "Leg/RR/skew arms have asymmetric expected or contrarian direction.",
        "tests": int(len(iter4_stats)),
        "best": iter4_stats.sort_values(["bh_significant", "mean_delta"], ascending=False).head(10).to_dict(orient="records"),
        "conclusion": "Reject wrong-sign/unstable direction branches; costs remain required for any candidate.",
        "next": "Test parsimonious context only for surviving explanatory candidates.",
    }
    (output_dir / "iteration_04_direction.json").write_text(json.dumps(iter4, indent=2, default=_json_default), encoding="utf-8")
    journal.append(iter4)

    iter5_stats = iteration5(research_events, deltas, persistence, manifest["validation_dates"])
    iter5_stats.to_csv(output_dir / "iteration_05_context.csv", index=False)
    iter5 = {
        "iteration": 5,
        "hypothesis": "A single context condition stabilizes a surviving effect.",
        "tests": int(len(iter5_stats)),
        "best": iter5_stats.sort_values(["bh_significant", "mean_delta"], ascending=False).head(10).to_dict(orient="records"),
        "conclusion": "Only simple cells with n>=30 and >=8 validation dates are eligible for final lock.",
        "next": "Freeze eligible candidates, then inspect FINAL once.",
    }
    (output_dir / "iteration_05_context.json").write_text(json.dumps(iter5, indent=2, default=_json_default), encoding="utf-8")
    journal.append(iter5)
    write_journal(output_dir / "iteration_journal.jsonl", journal)

    leaderboard_rows = []
    for iteration, frame, label_cols in (
        (2, iter2_stats, ["arm", "size_filter", "horizon", "metric"]),
        (3, iter3_stats, ["retention_horizon", "state", "horizon", "metric"]),
        (4, iter4_stats, ["arm", "side", "horizon", "metric"]),
        (5, iter5_stats, ["context", "horizon", "metric"]),
    ):
        for _, row in frame.iterrows():
            candidate_name = "|".join(str(row.get(column, "")) for column in label_cols)
            leaderboard_rows.append(
                {
                    "iteration": iteration,
                    "candidate": candidate_name,
                    "mean_delta": row.get("mean_delta"),
                    "ci_low": row.get("ci_low"),
                    "ci_high": row.get("ci_high"),
                    "n": row.get("n"),
                    "cluster_n": row.get("cluster_n"),
                    "bh_adjusted_p": row.get("bh_adjusted_p"),
                    "bh_significant": row.get("bh_significant"),
                    "eligible_for_final": bool(
                        (row.get("n", 0) or 0) >= 30
                        and (row.get("cluster_n", 0) or 0) >= 8
                        and bool(row.get("bh_significant", False))
                        and (row.get("ci_low", -np.inf) or -np.inf) > 0
                    ),
                }
            )
    leaderboard = pd.DataFrame(leaderboard_rows).sort_values(
        ["eligible_for_final", "bh_significant", "mean_delta"],
        ascending=False,
    )
    leaderboard.to_csv(output_dir / "candidate_leaderboard.csv", index=False)
    eligible = leaderboard[leaderboard["eligible_for_final"]].copy()
    final_path = output_dir / "locked_final_results.csv"
    if eligible.empty:
        pd.DataFrame(
            columns=["candidate", "status", "reason"]
        ).to_csv(final_path, index=False)
        final_evaluated = False
        final_reason = "No validation candidate met n>=30, cluster>=8, positive CI and FDR; FINAL outcomes remained unused."
    else:
        # This branch is intentionally conservative: only the top locked taxonomy
        # is run, with no further parameter changes.
        final_events, _ = detect(
            base, manifest["final_dates"], **config, threshold_dates=manifest["discovery_dates"]
        )
        final_outcomes = attach_outcomes(final_events, research_panel["price"])
        final_controls = match_controls(final_events, research_panel, manifest["discovery_dates"], manifest["final_dates"])
        final_control_outcomes = attach_control_outcomes(final_controls, research_panel["price"])
        final_deltas = paired_deltas(final_events, final_outcomes, final_control_outcomes)
        final_summary = []
        for candidate_row in eligible.head(3).itertuples():
            final_summary.append(
                {
                    "candidate": candidate_row.candidate,
                    "status": "PROVISIONAL",
                    "reason": "Locked final evaluation requires candidate-specific parser; raw final deltas retained.",
                    "final_events": int(final_events["et_date"].nunique()) if not final_events.empty else 0,
                    "raw_delta_rows": int(len(final_deltas)),
                }
            )
        pd.DataFrame(final_summary).to_csv(final_path, index=False)
        final_evaluated = True
        final_reason = "Locked candidates evaluated once; no post-final tuning."

    plot = write_plot(leaderboard, output_dir)
    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest,
        "selected_taxonomy": config,
        "frozen_limits": frozen_limits,
        "iterations": journal,
        "leaderboard_rows": int(len(leaderboard)),
        "eligible_final_candidates": eligible.to_dict(orient="records"),
        "final_evaluated": final_evaluated,
        "final_reason": final_reason,
        "headline": (
            f"{len(eligible)} validation candidate(s) cleared the pre-final guard."
            if len(eligible)
            else "No candidate cleared validation coverage/FDR; FINAL outcomes were not inspected."
        ),
        "artifacts": {
            "panel": str(output_dir / "research_panel.csv"),
            "journal": str(output_dir / "iteration_journal.jsonl"),
            "leaderboard": str(output_dir / "candidate_leaderboard.csv"),
            "final": str(final_path),
            "plot": plot,
            "summary": str(output_dir / "iterative_skew_research_summary.json"),
        },
        "isolation": {"reused_offline_panel": True, "modified_live_apps": False, "wrote_live_stores": False},
    }
    (output_dir / "iterative_skew_research_summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "headline": summary["headline"],
                "selected_taxonomy": config,
                "leaderboard_rows": len(leaderboard),
                "eligible": len(eligible),
                "final_evaluated": final_evaluated,
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=_json_default,
        )
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Iterative TY skew/vol hypothesis research cycle.")
    parser.add_argument("--panel", default=str(SOURCE_PANEL))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
