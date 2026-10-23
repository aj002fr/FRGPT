"""E08 flow persistence / termination features (1m panel approximations).

True trade-print runs and Hawkes arrival kernels need TBBO prints; on the
frozen 1m grid we approximate with bar-level signed flow and trade_intensity.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import EPS
from scripts.move_exhaustion.simulation.policies import _ts

LOOKBACK_BARS = 15


def _signs(flow: np.ndarray) -> np.ndarray:
    s = np.sign(flow)
    s[~np.isfinite(s)] = 0.0
    return s


def _entropy_binary(signs: np.ndarray) -> float:
    """Entropy of {+1,-1} ignoring zeros; bits."""
    s = signs[signs != 0]
    if len(s) < 2:
        return 0.0
    p_pos = float((s > 0).mean())
    p_neg = 1.0 - p_pos
    h = 0.0
    for p in (p_pos, p_neg):
        if p > 0:
            h -= p * np.log2(p)
    return float(h)


def _same_side_run_length(signs: np.ndarray, direction: int) -> int:
    """Trailing run length of bars matching event direction."""
    run = 0
    for v in signs[::-1]:
        if v == direction:
            run += 1
        elif v == 0:
            continue
        else:
            break
    return int(run)


def _max_same_side_run(signs: np.ndarray, direction: int) -> int:
    best = cur = 0
    for v in signs:
        if v == direction:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return int(best)


def _flip_rate(signs: np.ndarray) -> float:
    s = signs[signs != 0]
    if len(s) < 2:
        return 0.0
    flips = np.sum(s[1:] != s[:-1])
    return float(flips / (len(s) - 1))


def _sign_autocorr(signs: np.ndarray) -> float | None:
    s = signs[signs != 0].astype(float)
    if len(s) < 4:
        return None
    x, y = s[:-1], s[1:]
    if np.std(x) < EPS or np.std(y) < EPS:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def _interarrival_bars(signs: np.ndarray, direction: int) -> float | None:
    """Mean gap (bars) between same-direction nonzero bars."""
    idx = np.where(signs == direction)[0]
    if len(idx) < 2:
        return None
    gaps = np.diff(idx).astype(float)
    return float(np.mean(gaps))


def flow_persistence_features(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    lookback_bars: int = LOOKBACK_BARS,
    observation_time: Any | None = None,
) -> dict[str, float | None | bool]:
    """Causal persistence diagnostics ending at observation_time (default event start)."""
    start = _ts(event["event_start_time"])
    obs = _ts(observation_time) if observation_time is not None else start
    direction = int(event["event_direction"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= obs].tail(lookback_bars)
    empty = {
        "same_side_run_length": None,
        "max_same_side_run": None,
        "sign_autocorr": None,
        "trade_intensity": None,
        "intensity_acceleration": None,
        "same_dir_interarrival": None,
        "sign_entropy": None,
        "sign_flip_rate": None,
        "same_dir_flow_fraction": None,
        "directional_flow_mean": None,
        "intensity_peak_passed": None,
        "same_side_run_break": None,
        "opposite_flow_emergence": None,
        "directional_flow_deceleration": None,
        "n_bars": 0.0,
        "path_window": f"pre_obs_{lookback_bars}bars",
    }
    if len(hist) < 4:
        return empty

    flow_col = "agg_1m" if "agg_1m" in hist.columns else "agg_5m"
    flow = pd.to_numeric(hist[flow_col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    intensity = pd.to_numeric(hist.get("trade_intensity"), errors="coerce").to_numpy(dtype=float)
    signs = _signs(flow)
    dir_flow = direction * flow

    run = _same_side_run_length(signs, direction)
    max_run = _max_same_side_run(signs, direction)
    flip = _flip_rate(signs)
    ac = _sign_autocorr(signs)
    entropy = _entropy_binary(signs)
    inter = _interarrival_bars(signs, direction)
    same_frac = float((signs == direction).mean())
    intens_now = float(intensity[-1]) if np.isfinite(intensity[-1]) else None
    intens_accel = None
    if len(intensity) >= 3 and np.isfinite(intensity[-1]) and np.isfinite(intensity[-3]):
        intens_accel = float(intensity[-1] - intensity[-3])

    # Pattern variables on early vs recent halves
    mid = max(2, len(hist) // 2)
    early_flow = dir_flow[:mid]
    recent_flow = dir_flow[mid:]
    early_int = intensity[:mid]
    recent_int = intensity[mid:]
    early_peak = float(np.nanmax(early_int)) if np.isfinite(early_int).any() else np.nan
    recent_mean_int = float(np.nanmean(recent_int)) if np.isfinite(recent_int).any() else np.nan
    intensity_peak_passed = bool(
        np.isfinite(early_peak) and np.isfinite(recent_mean_int) and recent_mean_int < early_peak
    )
    # Run break: had a directional run earlier but last nonzero sign is not direction
    nonzero = signs[signs != 0]
    last_sign = float(nonzero[-1]) if len(nonzero) else 0.0
    same_side_run_break = bool(max_run >= 3 and last_sign != direction and last_sign != 0)
    opposite_flow_emergence = bool(
        (early_flow > 0).sum() >= 2 and (recent_flow < 0).sum() >= 1
    )
    early_abs = float(np.mean(np.clip(early_flow, 0, None)))
    recent_abs = float(np.mean(np.clip(recent_flow, 0, None)))
    directional_flow_deceleration = bool(
        early_abs > EPS and recent_abs < 0.7 * early_abs
    )

    return {
        "same_side_run_length": float(run),
        "max_same_side_run": float(max_run),
        "sign_autocorr": ac,
        "trade_intensity": intens_now,
        "intensity_acceleration": intens_accel,
        "same_dir_interarrival": inter,
        "sign_entropy": float(entropy),
        "sign_flip_rate": float(flip),
        "same_dir_flow_fraction": same_frac,
        "directional_flow_mean": float(np.mean(dir_flow)),
        "intensity_peak_passed": intensity_peak_passed,
        "same_side_run_break": same_side_run_break,
        "opposite_flow_emergence": opposite_flow_emergence,
        "directional_flow_deceleration": directional_flow_deceleration,
        "n_bars": float(len(hist)),
        "path_window": f"pre_obs_{lookback_bars}bars",
        "hawkes_available": False,
    }


def termination_score(feats: dict[str, Any]) -> float | None:
    """Simple 0–4 count of termination pattern flags."""
    flags = [
        feats.get("intensity_peak_passed"),
        feats.get("same_side_run_break"),
        feats.get("opposite_flow_emergence"),
        feats.get("directional_flow_deceleration"),
    ]
    known = [bool(f) for f in flags if f is not None]
    if not known:
        return None
    return float(sum(known))
