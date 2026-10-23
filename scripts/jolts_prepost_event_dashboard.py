#!/usr/bin/env python3
"""
JOLTS Job Openings Pre/Post Event Dashboard.

Generates a standalone HTML dashboard for:
- Pre-event context: consensus vs prior/MA, dispersion, surprise bias.
- Driver analysis: what actually moves TY, including surprise-vs-setup R2.
- Post-event analysis: realized surprise, z-score, bucketed return distributions.
"""

import argparse
import base64
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.jolts_job_openings_postevent_dashboard import (
    HORIZONS,
    _bucket_label,
    _build_bucket_labels,
    _build_stats_lines,
    _compute_stats,
    _compute_window_return,
    _get_last_n_values,
    _percentile_rank,
    _plot_distribution,
    _prepare_ohlcv,
    _scale_if_small,
)
from src.servers.distro_analytics.db_reader import get_events_from_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)


MIN_REGRESSION_N = 8
PRIMARY_HORIZON = "t+30m"


def _finite_float(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(out):
        return None
    return out


def _scale_reference(value: object, actual_val: float, consensus_val: float) -> Optional[float]:
    """Scale previous/revised values into the same thousands-level units as actual/consensus."""
    val = _finite_float(value)
    if val is None:
        return None
    target = max(abs(actual_val), abs(consensus_val))
    if target >= 1000 and abs(val) > 100000:
        return val / 1000.0
    if target >= 1000 and 0 < abs(val) < 1000:
        return val * 1000.0
    if target < 1000 and abs(val) >= 1000:
        return val / 1000.0
    return val


def _extract_events_with_previous(events_df: pd.DataFrame) -> List[Dict[str, object]]:
    """Extract clean JOLTS level rows with previous values and historical moving averages."""
    date_col = "event_date" if "event_date" in events_df.columns else "datetime"
    if date_col not in events_df.columns:
        raise ValueError("Event data missing event_date/datetime column.")

    event_dt = pd.to_datetime(events_df[date_col], errors="coerce", utc=True)
    event_dt = event_dt.dropna().dt.tz_convert("US/Eastern")

    by_date: Dict[str, Dict[str, object]] = {}
    for idx, dt in event_dt.items():
        row = events_df.loc[idx]
        actual = _finite_float(row.get("actual"))
        consensus = _finite_float(row.get("consensus"))
        cons_std = _finite_float(row.get("consensus_std_dev"))
        if actual is None or consensus is None or cons_std is None or cons_std <= 0:
            continue

        actual_val, consensus_val = _scale_if_small(actual, consensus)
        previous_val = _scale_reference(row.get("previous"), actual_val, consensus_val)
        revised_val = _scale_reference(row.get("revised"), actual_val, consensus_val)
        event = {
            "event_dt": dt,
            "date": dt.strftime("%Y-%m-%d"),
            "actual": float(actual_val),
            "consensus": float(consensus_val),
            "consensus_std_dev": float(cons_std),
            "previous": previous_val,
            "revised": revised_val,
        }

        key = event["date"]
        current = by_date.get(key)
        if current is None or abs(float(event["actual"])) > abs(float(current["actual"])):
            by_date[key] = event

    events = sorted(by_date.values(), key=lambda x: x["event_dt"])
    actuals: List[float] = []
    for event in events:
        actual = float(event["actual"])
        if event.get("previous") is None and actuals:
            event["previous"] = actuals[-1]
        for window in (3, 6, 12):
            prior_vals = actuals[-window:]
            event[f"ma{window}"] = float(np.mean(prior_vals)) if prior_vals else None
        event["surprise"] = float(event["actual"]) - float(event["consensus"])
        event["norm_surprise"] = event["surprise"] / float(event["consensus_std_dev"])
        event["setup_vs_prior"] = (
            float(event["consensus"]) - float(event["previous"])
            if event.get("previous") is not None
            else None
        )
        event["setup_vs_ma6"] = (
            float(event["consensus"]) - float(event["ma6"])
            if event.get("ma6") is not None
            else None
        )
        actuals.append(actual)

    norm_surprises = [float(event["norm_surprise"]) for event in events]
    for event in events:
        event["surp_pct"] = _percentile_rank(norm_surprises, float(event["norm_surprise"]))
    return events


def _png_to_data_uri(path: Path) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    path.unlink(missing_ok=True)
    return f"data:image/png;base64,{payload}"


def _histogram_png(
    values: Iterable[float],
    title: str,
    xlabel: str,
    output_dir: Path,
    marker: Optional[float] = None,
) -> Optional[str]:
    arr = np.array([v for v in values if np.isfinite(v)], dtype=float)
    if len(arr) < 2:
        return None
    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=130)
    ax.hist(arr, bins=min(25, max(8, int(np.sqrt(len(arr))))), color="#60a5fa", alpha=0.78)
    if marker is not None and np.isfinite(marker):
        ax.axvline(marker, color="#fb923c", linewidth=2.0, label="Current")
        ax.legend()
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Frequency")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    path = output_dir / f"{title.lower().replace(' ', '_').replace('/', '_')}.png"
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return _png_to_data_uri(path)


def _line_png(events: List[Dict[str, object]], output_dir: Path) -> Optional[str]:
    if len(events) < 3:
        return None
    dates = [event["date"] for event in events]
    xs = np.arange(len(events))
    actual = np.array([event["actual"] for event in events], dtype=float) / 1000.0
    ma3 = np.array([event["ma3"] if event["ma3"] is not None else np.nan for event in events], dtype=float) / 1000.0
    ma6 = np.array([event["ma6"] if event["ma6"] is not None else np.nan for event in events], dtype=float) / 1000.0
    ma12 = np.array([event["ma12"] if event["ma12"] is not None else np.nan for event in events], dtype=float) / 1000.0

    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=130)
    ax.plot(xs, actual, color="#60a5fa", linewidth=1.8, label="Actual")
    ax.plot(xs, ma3, color="#34d399", linewidth=1.2, label="MA3")
    ax.plot(xs, ma6, color="#fb923c", linewidth=1.2, label="MA6")
    ax.plot(xs, ma12, color="#c084fc", linewidth=1.2, label="MA12")
    tick_idx = np.linspace(0, len(xs) - 1, min(8, len(xs))).astype(int)
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([dates[i] for i in tick_idx], rotation=30, ha="right")
    ax.set_title("JOLTS Job Openings Level and Moving Averages")
    ax.set_ylabel("Millions")
    ax.grid(alpha=0.2)
    ax.legend(loc="best")
    fig.tight_layout()
    path = output_dir / "jolts_level_ma.png"
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return _png_to_data_uri(path)


def _scatter_png(
    points: List[Dict[str, float]],
    x_key: str,
    y_key: str,
    regression: Dict[str, object],
    title: str,
    xlabel: str,
    output_dir: Path,
) -> Optional[str]:
    xs = np.array([p[x_key] for p in points if p.get(x_key) is not None and p.get(y_key) is not None], dtype=float)
    ys = np.array([p[y_key] for p in points if p.get(x_key) is not None and p.get(y_key) is not None], dtype=float)
    if len(xs) < 3:
        return None
    fig, ax = plt.subplots(figsize=(7.5, 4.5), dpi=130)
    ax.scatter(xs, ys, color="#60a5fa", alpha=0.78, edgecolor="white", linewidth=0.4)
    if regression.get("ok"):
        x_line = np.linspace(float(xs.min()), float(xs.max()), 100)
        y_line = float(regression["intercept"]) + float(regression["beta"]) * x_line
        ax.plot(x_line, y_line, color="#fb923c", linewidth=2.0)
        ax.set_title(f"{title} | beta={float(regression['beta']):+.2f}, R2={float(regression['r2']):.2f}")
    else:
        ax.set_title(title)
    ax.axhline(0, color="#111827", linewidth=0.8, alpha=0.45)
    ax.axvline(0, color="#111827", linewidth=0.8, alpha=0.45)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("TY return (bps)")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    path = output_dir / f"{title.lower().replace(' ', '_').replace('+', 'plus').replace('/', '_')}.png"
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return _png_to_data_uri(path)


def _ols(xs: Iterable[object], ys: Iterable[object]) -> Dict[str, object]:
    pairs: List[Tuple[float, float]] = []
    for x_val, y_val in zip(xs, ys):
        x = _finite_float(x_val)
        y = _finite_float(y_val)
        if x is not None and y is not None:
            pairs.append((x, y))
    if len(pairs) < MIN_REGRESSION_N:
        return {"ok": False, "n": len(pairs), "reason": "insufficient sample"}

    x_arr = np.array([p[0] for p in pairs], dtype=float)
    y_arr = np.array([p[1] for p in pairs], dtype=float)
    x_var = float(np.var(x_arr, ddof=0))
    y_var = float(np.var(y_arr, ddof=0))
    if x_var <= 0 or y_var <= 0:
        return {"ok": False, "n": len(pairs), "reason": "flat series"}

    beta = float(np.cov(x_arr, y_arr, ddof=0)[0, 1] / x_var)
    intercept = float(np.mean(y_arr) - beta * np.mean(x_arr))
    pred = intercept + beta * x_arr
    resid = y_arr - pred
    sse = float(np.sum(resid * resid))
    sst = float(np.sum((y_arr - np.mean(y_arr)) ** 2))
    corr = float(np.corrcoef(x_arr, y_arr)[0, 1])
    return {
        "ok": True,
        "n": len(pairs),
        "beta": beta,
        "intercept": intercept,
        "r2": float(1.0 - sse / sst) if sst > 0 else 0.0,
        "corr": corr,
        "resid_std": float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.0,
    }


def _standardized(values: List[Optional[float]]) -> List[Optional[float]]:
    clean = np.array([v for v in values if v is not None and np.isfinite(v)], dtype=float)
    if len(clean) < 2:
        return [None for _ in values]
    mean = float(np.mean(clean))
    std = float(np.std(clean, ddof=1))
    if std <= 0:
        return [None for _ in values]
    out: List[Optional[float]] = []
    for value in values:
        if value is None or not np.isfinite(value):
            out.append(None)
        else:
            out.append((float(value) - mean) / std)
    return out


def _compute_return_points(
    events: List[Dict[str, object]],
    ohlcv_df: pd.DataFrame,
    bucket_width: int,
) -> List[Dict[str, object]]:
    norm_surprises = [float(event["norm_surprise"]) for event in events]
    for event in events:
        event["surp_pct"] = _percentile_rank(norm_surprises, float(event["norm_surprise"]))
        event["bucket"] = _bucket_label(float(event["surp_pct"]), bucket_width)
        for label, delta in HORIZONS:
            event[f"ret_{label}"] = _compute_window_return(
                ohlcv_df,
                event["event_dt"],
                event["event_dt"] + delta,
            )
    return events


def _compute_bias(events: List[Dict[str, object]]) -> Dict[str, float]:
    surprises = np.array([event["surprise"] for event in events], dtype=float)
    zscores = np.array([event["norm_surprise"] for event in events], dtype=float)
    beats = surprises > 0
    misses = surprises < 0
    return {
        "count": int(len(surprises)),
        "mean_surprise": float(np.mean(surprises)),
        "median_surprise": float(np.median(surprises)),
        "mean_z": float(np.mean(zscores)),
        "median_z": float(np.median(zscores)),
        "beat_pct": float(np.mean(beats) * 100.0),
        "miss_pct": float(np.mean(misses) * 100.0),
    }


def _horizon_magnitude_stats(events: List[Dict[str, object]], label: str) -> Dict[str, object]:
    returns = [event[f"ret_{label}"] for event in events if event.get(f"ret_{label}") is not None]
    if not returns:
        return {"count": 0}
    arr = np.array(returns, dtype=float)
    abs_arr = np.abs(arr)
    return {
        "count": int(len(arr)),
        "mean_abs": float(np.mean(abs_arr)),
        "median_abs": float(np.median(abs_arr)),
        "p75_abs": float(np.percentile(abs_arr, 75)),
        "p95_abs": float(np.percentile(abs_arr, 95)),
        "p99_abs": float(np.percentile(abs_arr, 99)),
        "p1": float(np.percentile(arr, 1)),
        "p99": float(np.percentile(arr, 99)),
    }


def _directional_hit_rate(events: List[Dict[str, object]], label: str) -> Optional[float]:
    hits = []
    for event in events:
        ret = event.get(f"ret_{label}")
        z = event.get("norm_surprise")
        if ret is None or z is None or float(z) == 0 or float(ret) == 0:
            continue
        expected_sign = -1.0 if float(z) > 0 else 1.0
        hits.append(math.copysign(1.0, float(ret)) == expected_sign)
    if not hits:
        return None
    return float(np.mean(hits) * 100.0)


def _compute_driver_analysis(events: List[Dict[str, object]]) -> Dict[str, object]:
    setup_prior_z = _standardized([event.get("setup_vs_prior") for event in events])
    setup_ma6_z = _standardized([event.get("setup_vs_ma6") for event in events])
    for event, prior_z, ma6_z in zip(events, setup_prior_z, setup_ma6_z):
        event["setup_vs_prior_z"] = prior_z
        event["setup_vs_ma6_z"] = ma6_z

    by_horizon: Dict[str, object] = {}
    for label, _ in HORIZONS:
        returns = [event.get(f"ret_{label}") for event in events]
        regs = {
            "normalized_surprise": _ols([event.get("norm_surprise") for event in events], returns),
            "consensus_vs_prior": _ols([event.get("setup_vs_prior_z") for event in events], returns),
            "consensus_vs_ma6": _ols([event.get("setup_vs_ma6_z") for event in events], returns),
        }
        ranked = sorted(
            [
                {"signal": signal, **reg}
                for signal, reg in regs.items()
                if reg.get("ok")
            ],
            key=lambda item: float(item.get("r2", 0.0)),
            reverse=True,
        )
        by_horizon[label] = {
            "regressions": regs,
            "ranked": ranked,
            "magnitude": _horizon_magnitude_stats(events, label),
            "directional_hit_rate": _directional_hit_rate(events, label),
            "interaction": _compute_interactions(events, label),
        }
    return {"by_horizon": by_horizon}


def _compute_interactions(events: List[Dict[str, object]], label: str) -> Dict[str, object]:
    def setup_ma6(event: Dict[str, object]) -> float:
        value = event.get("setup_vs_ma6")
        return float(value) if value is not None and np.isfinite(value) else 0.0

    def surprise_z(event: Dict[str, object]) -> float:
        value = event.get("norm_surprise")
        return float(value) if value is not None and np.isfinite(value) else 0.0

    groups = {
        "beat_hawkish_setup": lambda e: surprise_z(e) > 0 and setup_ma6(e) > 0,
        "beat_non_hawkish_setup": lambda e: surprise_z(e) > 0 and setup_ma6(e) <= 0,
        "miss_hawkish_setup": lambda e: surprise_z(e) < 0 and setup_ma6(e) > 0,
        "miss_non_hawkish_setup": lambda e: surprise_z(e) < 0 and setup_ma6(e) <= 0,
    }
    out: Dict[str, object] = {}
    for name, predicate in groups.items():
        subset = [event for event in events if event.get(f"ret_{label}") is not None and predicate(event)]
        returns = np.array([event[f"ret_{label}"] for event in subset], dtype=float)
        out[name] = {
            "n": int(len(subset)),
            "mean": float(np.mean(returns)) if len(returns) else None,
            "median": float(np.median(returns)) if len(returns) else None,
            "mean_abs": float(np.mean(np.abs(returns))) if len(returns) else None,
            "beta": _ols([event.get("norm_surprise") for event in subset], [event.get(f"ret_{label}") for event in subset]),
        }
    return out


def _build_postevent_plots(
    events: List[Dict[str, object]],
    output_dir: Path,
    symbol: str,
    bucket_width: int,
) -> Dict[str, object]:
    bucket_labels = _build_bucket_labels(bucket_width)
    plot_payload: Dict[str, object] = {}
    for label, _ in HORIZONS:
        all_returns = [event[f"ret_{label}"] for event in events if event.get(f"ret_{label}") is not None]
        all_times = [event["event_dt"] for event in events if event.get(f"ret_{label}") is not None]
        if not all_returns:
            plot_payload[label] = {"all": {"img": None, "stats": {}, "stats_lines": [], "count": 0}, "buckets": {}}
            continue
        all_stats = _compute_stats(all_returns)
        all_img = _plot_distribution(
            values=all_returns,
            title=f"{symbol} JOLTS Job Openings {label} (All Events)",
            output_dir=output_dir,
            stats_lines=_build_stats_lines(all_stats),
            vertical_lines=_get_last_n_values(all_returns, all_times, n=2),
        )
        buckets: Dict[str, object] = {}
        for bucket in bucket_labels:
            bucket_returns = [
                event[f"ret_{label}"]
                for event in events
                if event.get(f"ret_{label}") is not None and event.get("bucket") == bucket
            ]
            bucket_times = [
                event["event_dt"]
                for event in events
                if event.get(f"ret_{label}") is not None and event.get("bucket") == bucket
            ]
            if not bucket_returns:
                buckets[bucket] = {"img": None, "stats": {}, "stats_lines": [], "count": 0}
                continue
            bucket_stats = _compute_stats(bucket_returns)
            bucket_img = _plot_distribution(
                values=bucket_returns,
                title=f"{symbol} JOLTS Job Openings {label} ({bucket})",
                output_dir=output_dir,
                stats_lines=_build_stats_lines(bucket_stats),
                vertical_lines=_get_last_n_values(bucket_returns, bucket_times, n=2),
            )
            buckets[bucket] = {
                "img": bucket_img,
                "stats": bucket_stats,
                "stats_lines": _build_stats_lines(bucket_stats),
                "count": int(len(bucket_returns)),
            }
        plot_payload[label] = {
            "all": {
                "img": all_img,
                "stats": all_stats,
                "stats_lines": _build_stats_lines(all_stats),
                "count": int(len(all_returns)),
            },
            "buckets": buckets,
        }
    return plot_payload


def _build_regression_plots(
    events: List[Dict[str, object]],
    driver_analysis: Dict[str, object],
    output_dir: Path,
) -> Dict[str, Optional[str]]:
    images: Dict[str, Optional[str]] = {}
    points = []
    for event in events:
        point = {"norm_surprise": event["norm_surprise"]}
        for label, _ in HORIZONS:
            point[f"ret_{label}"] = event.get(f"ret_{label}")
        points.append(point)
    for label, _ in HORIZONS:
        reg = driver_analysis["by_horizon"][label]["regressions"]["normalized_surprise"]
        images[label] = _scatter_png(
            points=points,
            x_key="norm_surprise",
            y_key=f"ret_{label}",
            regression=reg,
            title=f"JOLTS Surprise vs TY {label}",
            xlabel="Normalized surprise (actual - consensus) / dispersion",
            output_dir=output_dir,
        )
    return images


def _build_pre_event_payload(events: List[Dict[str, object]], output_dir: Path) -> Dict[str, object]:
    latest = events[-1]
    actuals = np.array([event["actual"] for event in events[-6:]], dtype=float)
    trend_std6 = float(np.std(actuals, ddof=1)) if len(actuals) > 1 else 0.0
    cons_std_values = [event["consensus_std_dev"] for event in events]
    surprise_values = [event["surprise"] for event in events]
    z_values = [event["norm_surprise"] for event in events]

    return {
        "latest": {
            "date": latest["date"],
            "actual": latest["actual"],
            "consensus": latest["consensus"],
            "consensus_std_dev": latest["consensus_std_dev"],
            "previous": latest.get("previous"),
            "ma3": latest.get("ma3"),
            "ma6": latest.get("ma6"),
            "ma12": latest.get("ma12"),
        },
        "trend_std6": trend_std6,
        "cons_std_values": cons_std_values,
        "surprise_values": surprise_values,
        "z_values": z_values,
        "bias": _compute_bias(events),
        "images": {
            "level_ma": _line_png(events, output_dir),
            "cons_std_hist": _histogram_png(cons_std_values, "JOLTS Consensus Std Dev", "Thousands", output_dir),
            "surprise_hist": _histogram_png(surprise_values, "JOLTS Raw Surprise", "Thousands", output_dir),
            "z_hist": _histogram_png(z_values, "JOLTS Normalized Surprise", "Z-score", output_dir),
        },
    }


def _json_ready(obj: object) -> object:
    if isinstance(obj, dict):
        return {str(k): _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, float):
        return None if not np.isfinite(obj) else obj
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    return obj


def _build_dashboard_data(
    events: List[Dict[str, object]],
    symbol: str,
    event_name: str,
    start_date: str,
    bucket_width: int,
    output_dir: Path,
) -> Dict[str, object]:
    pre_event = _build_pre_event_payload(events, output_dir)
    driver_analysis = _compute_driver_analysis(events)
    post_plots = _build_postevent_plots(events, output_dir, symbol, bucket_width)
    regression_plots = _build_regression_plots(events, driver_analysis, output_dir)
    norm_surprises = [float(event["norm_surprise"]) for event in events]

    return {
        "meta": {
            "event_name": event_name,
            "symbol": symbol,
            "start_date": start_date,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event_count": len(events),
            "primary_horizon": PRIMARY_HORIZON,
        },
        "bucket_width": bucket_width,
        "bucket_labels": _build_bucket_labels(bucket_width),
        "horizons": [label for label, _ in HORIZONS],
        "norm_surprises": norm_surprises,
        "pre_event": pre_event,
        "driver_analysis": driver_analysis,
        "post_plots": post_plots,
        "regression_plots": regression_plots,
        "events": [
            {
                "date": event["date"],
                "actual": event["actual"],
                "consensus": event["consensus"],
                "previous": event.get("previous"),
                "ma3": event.get("ma3"),
                "ma6": event.get("ma6"),
                "ma12": event.get("ma12"),
                "consensus_std_dev": event["consensus_std_dev"],
                "surprise": event["surprise"],
                "norm_surprise": event["norm_surprise"],
                "surp_pct": event["surp_pct"],
                "bucket": event["bucket"],
                "setup_vs_prior": event.get("setup_vs_prior"),
                "setup_vs_ma6": event.get("setup_vs_ma6"),
                "returns": {label: event.get(f"ret_{label}") for label, _ in HORIZONS},
            }
            for event in events
        ],
    }


def _build_html(data: Dict[str, object]) -> str:
    payload = json.dumps(_json_ready(data))
    template = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>JOLTS Pre/Post Event Analytics</title>
  <style>
    :root {
      --bg: #0b111b;
      --card: #111827;
      --card2: #1f2937;
      --text: #f9fafb;
      --muted: #9ca3af;
      --border: #374151;
      --blue: #60a5fa;
      --green: #34d399;
      --orange: #fb923c;
      --red: #f87171;
      --purple: #c084fc;
    }
    * { box-sizing: border-box; }
    body { margin: 0; padding: 24px; font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    .container { max-width: 1800px; margin: 0 auto; }
    header { border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 18px; }
    h1 { margin: 0 0 6px; font-size: 1.9rem; }
    h2 { font-size: 1.18rem; margin: 0 0 12px; }
    h3 { font-size: 1rem; margin: 0 0 8px; }
    .subtitle, .muted { color: var(--muted); }
    .grid { display: grid; gap: 14px; }
    .grid-2 { grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); }
    .grid-3 { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .grid-4 { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
    section, .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
    .card { margin-bottom: 0; }
    label { display: block; color: var(--muted); font-size: 0.84rem; margin-bottom: 5px; }
    input, textarea, select { width: 100%; background: var(--card2); color: var(--text); border: 1px solid var(--border); border-radius: 7px; padding: 8px 10px; font: inherit; }
    textarea { min-height: 150px; resize: vertical; }
    .metric { background: var(--card2); border: 1px solid var(--border); border-radius: 9px; padding: 10px 12px; }
    .metric .label { color: var(--muted); font-size: 0.78rem; }
    .metric .value { color: var(--blue); font-size: 1.08rem; margin-top: 4px; white-space: pre-wrap; }
    .value.green { color: var(--green); }
    .value.orange { color: var(--orange); }
    .value.red { color: var(--red); }
    table { width: 100%; border-collapse: collapse; font-size: 0.86rem; }
    th, td { text-align: left; border-bottom: 1px solid var(--border); padding: 7px 6px; }
    th { color: var(--muted); font-weight: 600; }
    img { width: 100%; border-radius: 8px; border: 1px solid var(--border); background: white; }
    .note { color: var(--muted); font-size: 0.86rem; line-height: 1.45; }
    .pill { display: inline-block; padding: 3px 8px; border-radius: 999px; background: var(--card2); border: 1px solid var(--border); color: var(--muted); font-size: 0.8rem; margin-right: 5px; }
    .warn { color: var(--orange); }
    .hidden { display: none; }
  </style>
</head>
<body>
<div class="container">
  <header>
    <h1>JOLTS Job Openings Pre/Post Analytics</h1>
    <div class="subtitle" id="metaLine"></div>
  </header>

  <section>
    <h2>Inputs</h2>
    <div class="grid grid-4">
      <div><label for="consensusInput">Consensus (K or M)</label><input id="consensusInput" type="number" step="0.001"></div>
      <div><label for="consStdInput">Consensus Std Dev / Dispersion (K or M)</label><input id="consStdInput" type="number" step="0.001"></div>
      <div><label for="previousInput">Previous (K or M)</label><input id="previousInput" type="number" step="0.001"></div>
      <div><label for="actualInput">Actual after release (optional, K or M)</label><input id="actualInput" type="number" step="0.001"></div>
    </div>
    <div style="margin-top: 12px;">
      <label for="bankEstimatesInput">Bank estimates (manual, comma/space separated, K or M; consolidated only)</label>
      <input id="bankEstimatesInput" type="text" placeholder="Example: 6.790, 6.700, 6.800">
    </div>
    <p class="note" style="margin-bottom:0;">Values below 1000 are treated as millions and converted to thousands. Bank estimates are only used to compute consolidated dispersion; no per-bank display.</p>
  </section>

  <section>
    <h2>Automated Message</h2>
    <textarea id="autoMessage" readonly></textarea>
  </section>

  <section>
    <h2>Pre-Event Setup</h2>
    <div class="grid grid-4">
      <div class="metric"><div class="label">Narrative Lean</div><div id="impulseVal" class="value"></div></div>
      <div class="metric"><div class="label">Consensus vs Prior</div><div id="vsPriorVal" class="value"></div></div>
      <div class="metric"><div class="label">Consensus vs MA6</div><div id="vsMaVal" class="value"></div></div>
      <div class="metric"><div class="label">Dispersion Percentile</div><div id="dispersionVal" class="value"></div></div>
    </div>
    <div class="grid grid-3" style="margin-top: 14px;">
      <div class="metric"><div class="label">Bank Estimates Consolidated</div><div id="bankStatsVal" class="value"></div></div>
      <div class="metric"><div class="label">Historical Surprise Bias</div><div id="biasVal" class="value"></div></div>
      <div class="metric"><div class="label">Effective Dispersion</div><div id="effectiveDispVal" class="value"></div></div>
    </div>
    <p class="note">Consensus vs prior/MA is shown as a priced-in economic narrative, not as a release-move predictor. The historical JOLTS sample shows near-zero link between that setup and the actual surprise; the release move is modeled from the surprise z-score.</p>
  </section>

  <section>
    <h2>Pre-Event Charts</h2>
    <div class="grid grid-2">
      <div id="levelChart"></div>
      <div id="dispChart"></div>
      <div id="surpriseChart"></div>
      <div id="zChart"></div>
    </div>
  </section>

  <section>
    <h2>Driver Analysis: What Actually Matters</h2>
    <div id="driverTables"></div>
  </section>

  <section>
    <h2>Implied Move Scenario Fan</h2>
    <div id="scenarioTables"></div>
  </section>

  <section>
    <h2>Post-Event Metrics</h2>
    <div class="grid grid-4">
      <div class="metric"><div class="label">Surprise</div><div id="surpriseVal" class="value"></div></div>
      <div class="metric"><div class="label">Normalized Surprise (z)</div><div id="zVal" class="value"></div></div>
      <div class="metric"><div class="label">Percentile / Bucket</div><div id="bucketVal" class="value"></div></div>
      <div class="metric"><div class="label">Predicted TY Move</div><div id="predictedVal" class="value"></div></div>
    </div>
  </section>

  <section>
    <h2>Post-Event Return Distributions</h2>
    <div id="postEventPlots"></div>
  </section>

  <section>
    <h2>Regression Scatters</h2>
    <div id="regressionPlots" class="grid grid-2"></div>
  </section>
</div>

<script>
const DATA = __DATA__;

function finite(v) { return Number.isFinite(v); }
function fmt(v, d=1) { return finite(v) ? v.toFixed(d) : "--"; }
function fmtSign(v, d=1) { return finite(v) ? (v >= 0 ? "+" : "") + v.toFixed(d) : "--"; }
function toK(v) {
  if (!finite(v)) return NaN;
  return Math.abs(v) < 1000 ? v * 1000 : v;
}
function dispToK(v) {
  if (!finite(v)) return NaN;
  return Math.abs(v) < 10 ? v * 1000 : v;
}
function toM(v) { return finite(v) ? (v / 1000).toFixed(3) + "m" : "--"; }
function parseNums(text) {
  return (text || "").split(/[\s,;]+/).map(Number).filter(Number.isFinite).map(toK);
}
function mean(arr) { return arr.length ? arr.reduce((a,b)=>a+b,0)/arr.length : NaN; }
function std(arr) {
  if (arr.length < 2) return NaN;
  const m = mean(arr);
  return Math.sqrt(arr.reduce((s,v)=>s+(v-m)*(v-m),0)/(arr.length-1));
}
function percentileRank(values, target) {
  if (!values.length || !finite(target)) return NaN;
  let below = 0, equal = 0;
  for (const v of values) {
    if (v < target) below++;
    if (v === target) equal++;
  }
  return (below + 0.5 * equal) / values.length * 100;
}
function bucketLabel(percentile, width) {
  if (!finite(percentile)) return "--";
  const clamped = Math.max(0, Math.min(99.999, percentile));
  const idx = Math.floor(clamped / width);
  const low = idx * width;
  return `${low}-${low + width}%`;
}
function classifyDispersion(pct) {
  if (!finite(pct)) return "--";
  if (pct < 25) return "tight";
  if (pct > 75) return "wide";
  return "normal";
}
function imageCard(title, img) {
  if (!img) return `<div class="card"><h3>${title}</h3><p class="note">No chart available.</p></div>`;
  return `<div class="card"><h3>${title}</h3><img src="${img}" /></div>`;
}
function renderStatic() {
  document.getElementById("metaLine").textContent =
    `${DATA.meta.symbol} | ${DATA.meta.event_name} | Events: ${DATA.meta.event_count} | Start: ${DATA.meta.start_date} | Generated: ${DATA.meta.generated_at}`;
  const latest = DATA.pre_event.latest;
  document.getElementById("consensusInput").value = latest.consensus ?? "";
  document.getElementById("consStdInput").value = latest.consensus_std_dev ?? "";
  document.getElementById("previousInput").value = latest.previous ?? "";
  document.getElementById("levelChart").innerHTML = imageCard("Level and Moving Averages", DATA.pre_event.images.level_ma);
  document.getElementById("dispChart").innerHTML = imageCard("Historical Consensus Std Dev", DATA.pre_event.images.cons_std_hist);
  document.getElementById("surpriseChart").innerHTML = imageCard("Historical Raw Surprise", DATA.pre_event.images.surprise_hist);
  document.getElementById("zChart").innerHTML = imageCard("Historical Normalized Surprise", DATA.pre_event.images.z_hist);
  renderDrivers();
  renderScenarios();
  renderPostPlots();
  renderRegressionPlots();
}
function renderDrivers() {
  const labels = DATA.horizons;
  let html = "";
  for (const h of labels) {
    const hData = DATA.driver_analysis.by_horizon[h];
    html += `<div class="card" style="margin-bottom:12px;"><h3>${h}</h3>`;
    html += `<table><thead><tr><th>Signal</th><th>N</th><th>Beta</th><th>R2</th><th>Corr</th></tr></thead><tbody>`;
    for (const row of hData.ranked) {
      html += `<tr><td>${row.signal}</td><td>${row.n}</td><td>${fmtSign(row.beta,2)}</td><td>${fmt(row.r2,3)}</td><td>${fmtSign(row.corr,2)}</td></tr>`;
    }
    const mag = hData.magnitude;
    html += `</tbody></table><p class="note">Magnitude: median |move| ${fmt(mag.median_abs,2)} bps, p75 ${fmt(mag.p75_abs,2)}, p95 ${fmt(mag.p95_abs,2)}, p99 ${fmt(mag.p99_abs,2)}. Directional hit-rate vs surprise sign: ${fmt(hData.directional_hit_rate,1)}%.</p>`;
    html += `<table><thead><tr><th>Interaction bucket</th><th>N</th><th>Mean</th><th>Median</th><th>Mean |move|</th></tr></thead><tbody>`;
    for (const [name, val] of Object.entries(hData.interaction)) {
      html += `<tr><td>${name}</td><td>${val.n}</td><td>${fmtSign(val.mean,2)}</td><td>${fmtSign(val.median,2)}</td><td>${fmt(val.mean_abs,2)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }
  document.getElementById("driverTables").innerHTML = html;
}
function renderScenarios() {
  let html = "";
  for (const h of DATA.horizons) {
    const reg = DATA.driver_analysis.by_horizon[h].regressions.normalized_surprise;
    html += `<div class="card" style="margin-bottom:12px;"><h3>${h}</h3>`;
    if (!reg.ok) {
      html += `<p class="note">Insufficient regression sample.</p></div>`;
      continue;
    }
    html += `<table><thead><tr><th>Surprise scenario</th><th>Predicted TY bps</th><th>Approx band (+/- residual std)</th></tr></thead><tbody>`;
    for (const z of [-2,-1,0,1,2]) {
      const pred = reg.intercept + reg.beta * z;
      html += `<tr><td>${fmtSign(z,0)} sigma</td><td>${fmtSign(pred,2)}</td><td>${fmtSign(pred - reg.resid_std,2)} to ${fmtSign(pred + reg.resid_std,2)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }
  document.getElementById("scenarioTables").innerHTML = html;
}
function renderPostPlots(bucketOverride) {
  let html = "";
  const activeBucket = bucketOverride || DATA.bucket_labels[0];
  for (const h of DATA.horizons) {
    const allData = DATA.post_plots[h].all;
    const bucketData = DATA.post_plots[h].buckets[activeBucket] || {img:null, stats_lines:[], count:0};
    html += `<div class="card" style="margin-bottom:14px;"><h3>${h} | Similar bucket: ${activeBucket} (N=${bucketData.count || 0})</h3><div class="grid grid-2">`;
    html += `<div><h3>All Events</h3>${allData.img ? `<img src="${allData.img}">` : `<p class="note">No all-event plot.</p>`}</div>`;
    html += `<div><h3>Similar Bucket</h3>${bucketData.img ? `<img src="${bucketData.img}">` : `<p class="note">No bucket plot.</p>`}</div>`;
    html += `</div></div>`;
  }
  document.getElementById("postEventPlots").innerHTML = html;
}
function renderRegressionPlots() {
  let html = "";
  for (const h of DATA.horizons) {
    html += imageCard(`Surprise vs TY ${h}`, DATA.regression_plots[h]);
  }
  document.getElementById("regressionPlots").innerHTML = html;
}
function impulseLabel(consensus, previous, ma6) {
  const trendStd = DATA.pre_event.trend_std6 || 1;
  const vsPrior = finite(previous) ? (consensus - previous) / trendStd : 0;
  const vsMa = finite(ma6) ? (consensus - ma6) / trendStd : 0;
  const score = (vsPrior + vsMa) / 2;
  const absScore = Math.abs(score);
  let degree = "neutral";
  if (absScore >= 1.0) degree = "strongly";
  else if (absScore >= 0.5) degree = "moderately";
  else if (absScore >= 0.15) degree = "slightly";
  if (degree === "neutral") return "Consensus near prior/MA6 -> neutral expected economic lean (priced in)";
  const side = score > 0 ? "hawkish" : "dovish";
  const dir = score > 0 ? "data expected to firm" : "data expected to soften";
  return `Consensus vs prior/MA6 -> ${dir} (${degree} ${side} lean, largely priced in)`;
}
function update() {
  const consensus = toK(Number(document.getElementById("consensusInput").value));
  const rawConsStd = dispToK(Number(document.getElementById("consStdInput").value));
  const previous = toK(Number(document.getElementById("previousInput").value));
  const actual = toK(Number(document.getElementById("actualInput").value));
  const bankVals = parseNums(document.getElementById("bankEstimatesInput").value);
  const bankMean = mean(bankVals);
  const bankStd = std(bankVals);
  const effectiveStd = finite(bankStd) ? bankStd : rawConsStd;
  const latest = DATA.pre_event.latest;
  const ma6 = latest.ma6;
  const ma3 = latest.ma3;
  const ma12 = latest.ma12;
  const dispPct = percentileRank(DATA.pre_event.cons_std_values, effectiveStd);
  const narrative = impulseLabel(consensus, previous, ma6);
  const bias = DATA.pre_event.bias;
  const primary = DATA.meta.primary_horizon;
  const primaryData = DATA.driver_analysis.by_horizon[primary];
  const reg = primaryData.regressions.normalized_surprise;
  const mag = primaryData.magnitude;

  document.getElementById("impulseVal").textContent = narrative;
  document.getElementById("vsPriorVal").textContent = `${fmtSign(consensus - previous,0)}k (${toM(consensus)} vs ${toM(previous)})`;
  document.getElementById("vsMaVal").textContent = `${fmtSign(consensus - ma6,0)}k vs MA6 ${toM(ma6)} | MA3 ${toM(ma3)} | MA12 ${toM(ma12)}`;
  document.getElementById("dispersionVal").textContent = `${fmt(dispPct,1)}th pctile (${classifyDispersion(dispPct)})`;
  document.getElementById("bankStatsVal").textContent = bankVals.length
    ? `N=${bankVals.length}, mean=${toM(bankMean)}, std=${fmt(bankStd,1)}k, range=${fmt(Math.min(...bankVals),0)}-${fmt(Math.max(...bankVals),0)}k`
    : "No manual estimates; using Consensus Std Dev input.";
  document.getElementById("biasVal").textContent = `Beat rate ${fmt(bias.beat_pct,1)}%, mean surprise ${fmtSign(bias.mean_surprise,0)}k, mean z ${fmtSign(bias.mean_z,2)}`;
  document.getElementById("effectiveDispVal").textContent = `${fmt(effectiveStd,1)}k (${finite(bankStd) ? "bank-estimate std" : "input std dev"})`;

  let surprise = NaN, z = NaN, pct = NaN, bucket = "--", pred = NaN, bucketMedian = NaN, bucketN = 0;
  if (finite(actual) && finite(consensus) && finite(effectiveStd) && effectiveStd > 0) {
    surprise = actual - consensus;
    z = surprise / effectiveStd;
    pct = percentileRank(DATA.norm_surprises, z);
    bucket = bucketLabel(pct, DATA.bucket_width);
    if (reg.ok) pred = reg.intercept + reg.beta * z;
    const bucketData = DATA.post_plots[primary].buckets[bucket];
    if (bucketData && bucketData.stats) {
      bucketMedian = bucketData.stats.median;
      bucketN = bucketData.count || 0;
    }
    renderPostPlots(bucket);
  } else {
    renderPostPlots();
  }
  document.getElementById("surpriseVal").textContent = finite(surprise) ? `${fmtSign(surprise,0)}k` : "Enter Actual after release";
  document.getElementById("zVal").textContent = finite(z) ? fmtSign(z,2) + " sigma" : "--";
  document.getElementById("bucketVal").textContent = finite(pct) ? `${fmt(pct,1)}th pctile | ${bucket}` : "--";
  document.getElementById("predictedVal").textContent = finite(pred) ? `${fmtSign(pred,2)} bps (${primary})` : "--";

  const sensitivity = reg.ok ? `Each +1 sigma JOLTS surprise has mapped to ${fmtSign(reg.beta,2)} bps in TY at ${primary} (R2=${fmt(reg.r2,3)}, N=${reg.n}).` : "Insufficient sample for surprise sensitivity.";
  const typical = `JOLTS typically moves TY +/-${fmt(mag.median_abs,2)} bps at ${primary} (p95 +/-${fmt(mag.p95_abs,2)}, p99 +/-${fmt(mag.p99_abs,2)}).`;
  const biasLine = `Actual beats consensus ${fmt(bias.beat_pct,1)}% of the time (mean surprise ${fmtSign(bias.mean_surprise,0)}k), so historical surprise risk skews ${bias.beat_pct >= 50 ? "hawkish" : "dovish"}.`;
  let postLine = "Post-release: enter Actual to compute realized surprise, z-score, percentile bucket, and predicted TY move.";
  if (finite(actual) && finite(z)) {
    postLine = `Post-release: Actual ${toM(actual)} vs cons ${toM(consensus)} = ${fmtSign(surprise,0)}k (${fmtSign(z,2)} sigma, ${fmt(pct,1)}th pctile) -> predicted ${fmtSign(pred,2)} bps; similar-bucket median ${fmtSign(bucketMedian,2)} bps (N=${bucketN}).`;
  }
  document.getElementById("autoMessage").value =
    `JOLTS Job Openings: ${narrative}.\n${sensitivity} Higher openings / beats are hawkish and historically imply TY price down; misses imply TY price up.\n${typical}\n${biasLine}\n${postLine}`;
}
renderStatic();
update();
for (const id of ["consensusInput", "consStdInput", "previousInput", "actualInput", "bankEstimatesInput"]) {
  document.getElementById(id).addEventListener("input", update);
}
</script>
</body>
</html>
"""
    return template.replace("__DATA__", payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="JOLTS Job Openings pre/post analytics dashboard")
    parser.add_argument("--symbol", default="TY", help="Symbol prefix (e.g., TY, ZN)")
    parser.add_argument("--event-name", default="JOLTs Job Openings")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--bucket-width", type=int, default=20)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    output_dir = PROJECT_ROOT / "workspace" / "analysis" / "jolts_prepost_event"
    output_dir.mkdir(parents=True, exist_ok=True)

    events_df = get_events_from_db(
        event_name_pattern=args.event_name,
        country="US",
        start_date=args.start_date,
        limit=50000,
    )
    if events_df.empty:
        raise SystemExit(f"No events found for {args.event_name} since {args.start_date}.")

    events = _extract_events_with_previous(events_df)
    if len(events) < 10:
        raise SystemExit("Insufficient JOLTS events with actual/consensus/std-dev history.")

    symbol_pattern = f"{args.symbol}%"
    logger.info("Loading OHLCV for %s from %s", symbol_pattern, args.start_date)
    ohlcv_df = _prepare_ohlcv(symbol_pattern, args.start_date)
    events = _compute_return_points(events, ohlcv_df, args.bucket_width)

    data = _build_dashboard_data(
        events=events,
        symbol=args.symbol,
        event_name=args.event_name,
        start_date=args.start_date,
        bucket_width=args.bucket_width,
        output_dir=output_dir,
    )
    html = _build_html(data)
    output_path = (
        Path(args.output)
        if args.output
        else PROJECT_ROOT
        / "workspace"
        / "scenarios"
        / f"jolts_prepost_event_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    summary_path = output_dir / "jolts_prepost_event_summary.json"
    summary_path.write_text(json.dumps(_json_ready(data["meta"]), indent=2), encoding="utf-8")
    logger.info("Saved dashboard: %s", output_path)
    logger.info("Saved summary: %s", summary_path)


if __name__ == "__main__":
    main()
