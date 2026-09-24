#!/usr/bin/env python3
"""Read-only TY option shock persistence and half-life study.

Uses existing shock artifacts only. No gamma/live DB writes, live app imports,
ports, SOD, Tailscale, Telegram, dashboard edits, or *_live/ writes.
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
from scipy.stats import spearmanr, ttest_1samp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_gate_lib import benjamini_hochberg, bootstrap_ci, one_sided_p  # noqa: E402

SHOCK_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_vol_skew_shock_paths"
PANEL_PATH = SHOCK_DIR / "shock_minute_panel.csv"
EVENTS_PATH = SHOCK_DIR / "shock_events.csv"
OUTCOMES_PATH = SHOCK_DIR / "shock_outcomes.csv"
SHOCK_SUMMARY = SHOCK_DIR / "shock_paths_summary.json"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_option_shock_persistence"
HORIZONS = (1, 5, 15, 30, 60, 120, 240, 480, 1440)
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


def metric_variants(arm: str) -> list[tuple[str, str]]:
    if arm.startswith("ATM_VOL_"):
        return [("atm_vol", "primary"), ("backbone", "backbone_robustness")]
    if arm.startswith("PUT_IV_"):
        return [("put_iv_50", "primary")]
    if arm.startswith("CALL_IV_"):
        return [("call_iv_50", "primary")]
    if arm.startswith("RR50_"):
        return [("rr50_calc", "primary")]
    if arm.startswith("SKEW_INDEX_"):
        return [("skew_index", "primary")]
    return []


def shock_sign(arm: str) -> float:
    return 1.0 if arm.endswith("_UP") else -1.0


def _nearest_metric(series: pd.Series, target: pd.Timestamp, tolerance: pd.Timedelta) -> tuple[pd.Timestamp | None, float | None]:
    data = series.dropna()
    if data.empty:
        return None, None
    pos = data.index.searchsorted(target)
    choices = []
    for index in (pos - 1, pos):
        if 0 <= index < len(data):
            ts = data.index[index]
            choices.append((abs(ts - target), ts, float(data.iloc[index])))
    if not choices:
        return None, None
    distance, ts, value = min(choices, key=lambda item: item[0])
    return (ts, value) if distance <= tolerance else (None, None)


def build_persistence_rows(
    panel: pd.DataFrame,
    events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, half_life = [], []
    for _, event in events.iterrows():
        t0 = pd.Timestamp(event["t0"])
        sign = shock_sign(str(event["arm"]))
        for metric, variant in metric_variants(str(event["arm"])):
            if metric not in panel:
                continue
            baseline_ts, baseline = _nearest_metric(panel[metric], t0 - pd.Timedelta(minutes=15), pd.Timedelta(seconds=90))
            shock_ts, endpoint = _nearest_metric(panel[metric], t0, pd.Timedelta(seconds=90))
            if baseline_ts is None or shock_ts is None:
                continue
            initial = float(endpoint - baseline)
            aligned_initial = sign * initial
            if aligned_initial <= EPS:
                continue
            base = {
                "event_id": f"{event['arm']}|{t0.isoformat()}",
                "t0": t0,
                "et_date": str(event["et_date"]),
                "arm": str(event["arm"]),
                "shock_class": str(event["shock_class"]),
                "metric": metric,
                "metric_variant": variant,
                "baseline_ts": baseline_ts,
                "baseline": baseline,
                "shock_endpoint": endpoint,
                "shock_size": initial,
                "aligned_initial": aligned_initial,
            }
            for horizon in HORIZONS:
                target = t0 + pd.Timedelta(minutes=horizon)
                tolerance = pd.Timedelta(seconds=90) if horizon <= 240 else pd.Timedelta(minutes=5)
                observed_ts, observed = _nearest_metric(panel[metric], target, tolerance)
                valid = observed_ts is not None
                aligned_excess = sign * (float(observed) - baseline) if valid else np.nan
                retention = aligned_excess / aligned_initial if valid else np.nan
                rows.append(
                    {
                        **base,
                        "horizon": horizon,
                        "target_ts": target,
                        "observed_ts": observed_ts,
                        "valid": valid,
                        "metric_value": observed,
                        "aligned_excess": aligned_excess,
                        "retention": retention,
                        "still_shocked": float(retention > 0) if valid else np.nan,
                        "retains_half": float(retention >= 0.5) if valid else np.nan,
                        "overshoot": float(retention > 1.0) if valid else np.nan,
                        "fully_reverted": float(retention <= 0) if valid else np.nan,
                    }
                )
            path = panel.loc[(panel.index > t0) & (panel.index <= t0 + pd.Timedelta(hours=24)), metric].dropna()
            retention_path = sign * (path - baseline) / aligned_initial
            half_candidates = retention_path[retention_path <= 0.5]
            full_candidates = retention_path[retention_path <= 0.0]
            end_ts, _ = _nearest_metric(panel[metric], t0 + pd.Timedelta(hours=24), pd.Timedelta(minutes=5))
            half_life.append(
                {
                    **base,
                    "half_life_minutes": (
                        (half_candidates.index[0] - t0).total_seconds() / 60.0 if len(half_candidates) else np.nan
                    ),
                    "full_reversion_minutes": (
                        (full_candidates.index[0] - t0).total_seconds() / 60.0 if len(full_candidates) else np.nan
                    ),
                    "half_life_reached": bool(len(half_candidates)),
                    "full_reversion_reached": bool(len(full_candidates)),
                    "coverage_to_24h": bool(end_ts is not None),
                    "censored": bool(end_ts is None or not len(full_candidates)),
                    "observed_option_rows_24h": int(len(path)),
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(half_life)


def persistence_stats(rows: pd.DataFrame, holdout_dates: list[str]) -> pd.DataFrame:
    holdout = rows[
        rows["et_date"].isin(set(holdout_dates))
        & (rows["metric_variant"] == "primary")
        & (rows["shock_class"] == "PRICE_QUIET_SHOCK")
        & rows["valid"]
    ].copy()
    stats = []
    for (arm, horizon), block in holdout.groupby(["arm", "horizon"], sort=False):
        ci = bootstrap_ci(block["retention"], n_boot=N_BOOT, groups=block["et_date"])
        daily = block.groupby("et_date")["retention"].mean()
        stats.append(
            {
                "arm": arm,
                "horizon": int(horizon),
                "n": int(len(block)),
                "cluster_n": int(block["et_date"].nunique()),
                "mean_retention": float(block["retention"].mean()),
                "median_retention": float(block["retention"].median()),
                "still_shocked_rate": float(block["still_shocked"].mean()),
                "retains_half_rate": float(block["retains_half"].mean()),
                "overshoot_rate": float(block["overshoot"].mean()),
                "full_reversion_rate": float(block["fully_reverted"].mean()),
                "ci_low": ci["ci_low"],
                "ci_high": ci["ci_high"],
                "one_sided_p": one_sided_p(daily),
            }
        )
    frame = pd.DataFrame(stats)
    if frame.empty:
        return frame
    bh = benjamini_hochberg(frame["one_sided_p"], q=BH_Q)
    frame["bh_adjusted_p"] = bh["adjusted_p"]
    frame["bh_significant"] = bh["reject"]
    frame["pass"] = (
        (frame["n"] >= 30)
        & (frame["cluster_n"] >= 8)
        & (frame["ci_low"] > 0)
        & frame["bh_significant"]
    )
    return frame


def half_life_stats(rows: pd.DataFrame, holdout_dates: list[str]) -> pd.DataFrame:
    records = []
    slices = {
        "pooled": rows[rows["metric_variant"] == "primary"],
        "holdout": rows[
            rows["et_date"].isin(set(holdout_dates))
            & (rows["metric_variant"] == "primary")
            & (rows["shock_class"] == "PRICE_QUIET_SHOCK")
        ],
        "backbone_robustness": rows[rows["metric_variant"] == "backbone_robustness"],
    }
    for slice_name, frame in slices.items():
        for arm, block in frame.groupby("arm", sort=False):
            records.append(
                {
                    "slice": slice_name,
                    "arm": arm,
                    "n": int(len(block)),
                    "cluster_n": int(block["et_date"].nunique()),
                    "half_life_reached_rate": float(block["half_life_reached"].mean()),
                    "median_half_life_minutes": float(block["half_life_minutes"].median()) if block["half_life_minutes"].notna().any() else np.nan,
                    "full_reversion_reached_rate": float(block["full_reversion_reached"].mean()),
                    "median_full_reversion_minutes": float(block["full_reversion_minutes"].median()) if block["full_reversion_minutes"].notna().any() else np.nan,
                    "coverage_24h_rate": float(block["coverage_to_24h"].mean()),
                    "median_observed_rows_24h": float(block["observed_option_rows_24h"].median()),
                }
            )
    return pd.DataFrame(records)


def _retention_state(value: float) -> str:
    if not np.isfinite(value):
        return "UNKNOWN"
    if value >= 0.5:
        return "PERSISTENT"
    if value <= 0.0:
        return "REVERTED"
    return "PARTIAL"


def join_ty_paths(
    persistence: pd.DataFrame,
    outcomes: pd.DataFrame,
    holdout_dates: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    primary = persistence[
        (persistence["metric_variant"] == "primary")
        & persistence["horizon"].isin([30, 60])
        & persistence["valid"]
    ][["event_id", "horizon", "retention"]].copy()
    primary["retention_horizon"] = primary["horizon"].astype(int)
    primary["persistence_state"] = primary["retention"].map(_retention_state)
    outcomes = outcomes[outcomes["valid"]].copy()
    outcomes["event_id"] = outcomes.apply(
        lambda row: f"{row['arm']}|{pd.Timestamp(row['t0']).isoformat()}",
        axis=1,
    )
    joined = outcomes.merge(primary.drop(columns=["horizon"]), on="event_id", how="inner")
    joined = joined[
        joined["et_date"].isin(set(holdout_dates))
        & (joined["shock_class"] == "PRICE_QUIET_SHOCK")
    ].copy()
    metrics = ("abs_ticks", "range_ticks", "rv_ratio", "signed_ticks", "path_efficiency", "trend", "chop")
    grouped = []
    for keys, block in joined.groupby(
        ["retention_horizon", "persistence_state", "horizon"],
        sort=False,
    ):
        retention_horizon, state, ty_horizon = keys
        row: dict[str, Any] = {
            "retention_horizon": int(retention_horizon),
            "persistence_state": state,
            "ty_horizon": str(ty_horizon),
            "n": int(len(block)),
            "cluster_n": int(block["et_date"].nunique()),
        }
        for metric in metrics:
            row[f"mean_{metric}"] = float(pd.to_numeric(block[metric], errors="coerce").mean())
        grouped.append(row)
    comparison_rows = []
    for (retention_horizon, ty_horizon), block in joined.groupby(
        ["retention_horizon", "horizon"],
        sort=False,
    ):
        for metric in metrics:
            data = block[["et_date", "persistence_state", metric]].dropna()
            persistent = data[data["persistence_state"] == "PERSISTENT"]
            reverted = data[data["persistence_state"] == "REVERTED"]
            if persistent.empty or reverted.empty:
                continue
            observed = float(persistent[metric].mean() - reverted[metric].mean())
            groups = {date: day for date, day in data.groupby("et_date", sort=False)}
            dates = np.asarray(list(groups), dtype=object)
            rng = np.random.default_rng(47)
            draws = []
            for _ in range(3000):
                sampled = rng.choice(dates, size=len(dates), replace=True)
                draw = pd.concat([groups[date] for date in sampled], ignore_index=True)
                p = draw[draw["persistence_state"] == "PERSISTENT"]
                r = draw[draw["persistence_state"] == "REVERTED"]
                if not p.empty and not r.empty:
                    draws.append(float(p[metric].mean() - r[metric].mean()))
            comparison_rows.append(
                {
                    "row_type": "PERSISTENT_MINUS_REVERTED",
                    "retention_horizon": int(retention_horizon),
                    "persistence_state": "PERSISTENT_MINUS_REVERTED",
                    "ty_horizon": str(ty_horizon),
                    "metric": metric,
                    "n": int(len(data)),
                    "persistent_n": int(len(persistent)),
                    "reverted_n": int(len(reverted)),
                    "cluster_n": int(data["et_date"].nunique()),
                    "mean_delta": observed,
                    "ci_low": float(np.quantile(draws, 0.025)) if draws else np.nan,
                    "ci_high": float(np.quantile(draws, 0.975)) if draws else np.nan,
                    "one_sided_p": float((np.sum(np.asarray(draws) <= 0) + 1) / (len(draws) + 1)) if draws else np.nan,
                }
            )
    grouped_frame = pd.DataFrame(grouped)
    if not grouped_frame.empty:
        grouped_frame["row_type"] = "GROUP_MEAN"
    comparisons = pd.DataFrame(comparison_rows)
    if not comparisons.empty:
        bh = benjamini_hochberg(comparisons["one_sided_p"], q=BH_Q)
        comparisons["bh_adjusted_p"] = bh["adjusted_p"]
        comparisons["bh_significant"] = bh["reject"]
        comparisons["pass"] = (
            (comparisons["n"] >= 30)
            & (comparisons["cluster_n"] >= 8)
            & (comparisons["ci_low"] > 0)
            & comparisons["bh_significant"]
        )
    return joined, pd.concat([grouped_frame, comparisons], ignore_index=True, sort=False)


def _cluster_corr(frame: pd.DataFrame, x: str, y: str) -> dict[str, Any]:
    data = frame[["et_date", x, y]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(data) < 3:
        return {"n": int(len(data)), "cluster_n": int(data["et_date"].nunique())}
    correlation = float(spearmanr(data[x], data[y]).statistic)
    daily = []
    for _, day in data.groupby("et_date", sort=False):
        if len(day) >= 3 and day[x].nunique() > 1 and day[y].nunique() > 1:
            daily.append(float(spearmanr(day[x], day[y]).statistic))
    p_value = float(ttest_1samp(daily, 0.0).pvalue) if len(daily) >= 3 else np.nan
    rng = np.random.default_rng(43)
    groups = {date: block for date, block in data.groupby("et_date", sort=False)}
    dates = np.asarray(list(groups), dtype=object)
    draws = []
    for _ in range(3000):
        sampled = rng.choice(dates, size=len(dates), replace=True)
        block = pd.concat([groups[date] for date in sampled], ignore_index=True)
        if block[x].nunique() > 1 and block[y].nunique() > 1:
            draws.append(float(spearmanr(block[x], block[y]).statistic))
    return {
        "n": int(len(data)),
        "cluster_n": int(data["et_date"].nunique()),
        "spearman": correlation,
        "ci_low": float(np.quantile(draws, 0.025)) if draws else np.nan,
        "ci_high": float(np.quantile(draws, 0.975)) if draws else np.nan,
        "p_value": p_value,
    }


def retention_correlations(joined: pd.DataFrame) -> pd.DataFrame:
    rows = []
    ty_horizons = ("30", "60", "120", "240", "480", "1440")
    metrics = ("abs_ticks", "range_ticks", "rv_ratio", "path_efficiency", "signed_ticks")
    for retention_horizon in (30, 60):
        subset = joined[joined["retention_horizon"] == retention_horizon]
        for ty_horizon in ty_horizons:
            block = subset[subset["horizon"].astype(str) == ty_horizon]
            for metric in metrics:
                rows.append(
                    {
                        "retention_horizon": retention_horizon,
                        "ty_horizon": ty_horizon,
                        "metric": metric,
                        **_cluster_corr(block, "retention", metric),
                    }
                )
    frame = pd.DataFrame(rows)
    bh = benjamini_hochberg(frame["p_value"], q=BH_Q)
    frame["bh_adjusted_p"] = bh["adjusted_p"]
    frame["bh_significant"] = bh["reject"]
    frame["pass"] = (
        (frame["n"] >= 30)
        & (frame["cluster_n"] >= 8)
        & frame["bh_significant"]
        & ((frame["ci_low"] > 0) | (frame["ci_high"] < 0))
    )
    return frame


def write_plot(rows: pd.DataFrame, output_dir: Path) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    primary = rows[(rows["metric_variant"] == "primary") & rows["valid"]]
    data = primary.groupby(["arm", "horizon"])["retention"].mean().unstack("horizon")
    if data.empty:
        return None
    fig, ax = plt.subplots(figsize=(11, 5))
    for arm, values in data.iterrows():
        ax.plot(values.index, values.values, marker="o", label=arm)
    ax.axhline(1.0, color="black", linewidth=1, linestyle=":")
    ax.axhline(0.5, color="gray", linewidth=1, linestyle="--")
    ax.axhline(0.0, color="black", linewidth=1)
    ax.set_xscale("log")
    ax.set_xlabel("Minutes after shock")
    ax.set_ylabel("Mean shock retention ratio")
    ax.set_title("Option shock persistence curves (pooled diagnostics)")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    path = output_dir / "persistence_curves.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    panel = pd.read_csv(args.panel, parse_dates=["ts"]).set_index("ts").sort_index()
    panel.index = pd.DatetimeIndex(panel.index).tz_convert("UTC")
    events = pd.read_csv(args.events, parse_dates=["t0", "entry_ts"])
    outcomes = pd.read_csv(args.outcomes, parse_dates=["t0", "entry_ts", "end_ts", "target_ts"])
    prior_summary = json.loads(Path(args.shock_summary).read_text(encoding="utf-8"))
    holdout_dates = list(map(str, prior_summary["holdout_dates"]))
    persistence, half_life = build_persistence_rows(panel, events)
    stats = persistence_stats(persistence, holdout_dates)
    half_stats = half_life_stats(half_life, holdout_dates)
    joined, path_groups = join_ty_paths(persistence, outcomes, holdout_dates)
    correlations = retention_correlations(joined)
    paths = {
        "rows": output_dir / "shock_persistence_rows.csv",
        "stats": output_dir / "persistence_stats.csv",
        "half_life": output_dir / "half_life_stats.csv",
        "ty_paths": output_dir / "ty_path_by_persistence.csv",
        "correlations": output_dir / "retention_ty_correlations.csv",
        "summary": output_dir / "option_shock_persistence_summary.json",
    }
    persistence.to_csv(paths["rows"], index=False)
    stats.to_csv(paths["stats"], index=False)
    half_stats.to_csv(paths["half_life"], index=False)
    path_groups.to_csv(paths["ty_paths"], index=False)
    correlations.to_csv(paths["correlations"], index=False)
    plot = write_plot(persistence, output_dir)
    pass_persistence = stats[stats.get("pass", False)].to_dict(orient="records") if not stats.empty else []
    pass_correlations = correlations[correlations["pass"]].to_dict(orient="records") if not correlations.empty else []
    result = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events": int(events.shape[0]),
        "persistence_rows": int(len(persistence)),
        "holdout_dates": holdout_dates,
        "valid_by_horizon": persistence.groupby("horizon")["valid"].sum().to_dict(),
        "pass_persistence": pass_persistence,
        "pass_correlations": pass_correlations,
        "headline": (
            "Option shocks show validated persistence and/or TY-path linkage."
            if pass_persistence or pass_correlations
            else "No option-shock persistence or persistence-to-TY-path relationship passes holdout coverage and FDR."
        ),
        "pooled_half_life": half_stats[half_stats["slice"] == "pooled"].to_dict(orient="records"),
        "artifacts": {key: str(path) for key, path in paths.items()} | {"plot": plot},
        "isolation": {"reused_existing_artifacts": True, "modified_live_apps": False, "wrote_live_stores": False},
    }
    paths["summary"].write_text(json.dumps(result, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps({"ok": True, "headline": result["headline"], "valid_by_horizon": result["valid_by_horizon"], "passes": len(pass_persistence) + len(pass_correlations), "half_life": result["pooled_half_life"], "artifacts": result["artifacts"]}, indent=2, default=_json_default))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="TY option shock persistence and half-life study.")
    parser.add_argument("--panel", default=str(PANEL_PATH))
    parser.add_argument("--events", default=str(EVENTS_PATH))
    parser.add_argument("--outcomes", default=str(OUTCOMES_PATH))
    parser.add_argument("--shock-summary", default=str(SHOCK_SUMMARY))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    run(parser.parse_args())


if __name__ == "__main__":
    main()
