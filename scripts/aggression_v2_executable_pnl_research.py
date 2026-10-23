#!/usr/bin/env python3
"""Offline session-gap fade with agree + V1 aggress veto and price +N/−X exits.

Entry and exit ARE these conditions (not flags on the live 10m B1_W10 z-fade).

Guardrail: does not import, write to, or restart V1/V1.2/V2/V2.1/V2.2/V2.3/V2.4/V2.6/V2.7/V3
app/runtime surfaces. No *_live writes, SOD, Tailscale, or Telegram.
Reads live logs and research artifacts read-only. Writes only under
workspace/analysis/aggression_v2_executable_pnl/.
Not a live V2.7 retune and not an after-cost ship gate unless holdout
after-cost >= +0.5 ticks and n>=50.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.ipc as pa_ipc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard.core import classify_aggressor, load_dv01_table
from scripts.aggression_dashboard.flow_dominance_context import (
    RAW_TO_DISPLAY,
    _annotate_contributions,
    classify_strength_change,
)
from scripts.aggression_dashboard.hf_model import _ols_params, fit_session_model
from scripts.aggression_v2_alert_clock_research import locked_holdout_dates

EASTERN = ZoneInfo("America/New_York")
API_BASE = "http://100.79.30.21:8001"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_executable_pnl"
OOS_PATH = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_short_horizon_fv" / "oos_predictions.csv"
FEATURE_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
V2_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
V27_LIVE = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v27_live"

TICK_SIZE = 1.0 / 64.0
TODAY_G = 2.0
TODAY_N = 2.0
TODAY_X = 2.0
HORIZON_MINUTES = 10
RT_COST_TICKS = 0.5
SESSION_OPEN_HOUR_ET = 18
RTH_START_HOUR_ET = 8
RTH_END_HOUR_ET = 17
MIN_HOLD_MINUTES_TP = 0
SHIP_N = 50
SHIP_EXPECTANCY = 0.5
CURVE_SYMBOLS = ("TY", "FV", "TU", "US")
G_GRID = (1.0, 2.0, 3.0, 4.0)
N_GRID = (1.0, 2.0, 3.0)
X_GRID = (1.0, 2.0, 3.0)
PRINT_TARGETS = (50, 150, 400, 800, 1500)
MIDCHG_TARGETS = (3, 5, 8, 10)
HOLDOUT_START = "2026-05-13"
HOLDOUT_END = "2026-06-05"
DISCOVERY_START = "2026-02-03"
DISCOVERY_END = "2026-05-12"
TODAY_SESSION_START_ET = "2026-08-31 18:00:00"
CLOCK_LABEL = "NOT_MBP1_L1"


@dataclass(frozen=True)
class ReplayConfig:
    g_ticks: float = TODAY_G
    n_ticks: float = TODAY_N
    x_ticks: float = TODAY_X
    horizon_minutes: int = HORIZON_MINUTES
    require_agree: bool = True
    apply_v1_veto: bool = True
    require_session_z: float | None = None
    rth_only: bool = False
    min_hold_minutes_tp: int = MIN_HOLD_MINUTES_TP
    min_hold_minutes_sl: int = 0
    block_same_sign_reentry: bool = False
    cooldown_minutes: float | None = None
    g_max_ticks: float | None = None
    require_peak_shrink: bool = False
    apply_c2_veto: bool = False
    apply_passive_increasing_veto: bool = False
    apply_balanced_two_leg_veto: bool = False
    exit_space: str = "bidask"
    fill_mode: str = "first_print"


def _utc(ts: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def session_start_et(ts: Any) -> pd.Timestamp:
    et = _utc(ts).tz_convert(EASTERN)
    open_today = et.normalize() + pd.Timedelta(hours=SESSION_OPEN_HOUR_ET)
    if et >= open_today:
        return open_today
    return open_today - pd.Timedelta(days=1)


def residual_ticks(residual: float) -> float:
    return float(residual) / TICK_SIZE


def signs_agree(session_residual: float, local_residual: float) -> bool:
    s = float(np.sign(session_residual))
    loc = float(np.sign(local_residual))
    return s != 0.0 and s == loc


def session_gap_ok(session_residual: float, g_ticks: float) -> bool:
    return abs(residual_ticks(session_residual)) >= float(g_ticks)


def fade_side(session_residual: float) -> int:
    """+1 long cheap, -1 short rich."""
    sign = float(np.sign(session_residual))
    if sign > 0:
        return -1
    if sign < 0:
        return 1
    return 0


def v1_aggress_with_fade(v1_state: str | None, z_aggress: float | None, session_residual: float) -> bool:
    if v1_state != "AGGRESS_DOMINANT":
        return False
    if z_aggress is None or not np.isfinite(float(z_aggress)):
        return False
    s = float(np.sign(session_residual))
    return s != 0.0 and float(np.sign(z_aggress)) == s


def v1_passive_increasing_veto(
    z_passive: float | None,
    z_passive_trend: str | None,
    session_residual: float,
) -> bool:
    """I7: skip fade if implied-passive is INCREASING in the residual's sign."""
    if str(z_passive_trend or "") != "INCREASING":
        return False
    if z_passive is None or not np.isfinite(float(z_passive)):
        return False
    s = float(np.sign(session_residual))
    return s != 0.0 and float(np.sign(z_passive)) == s


def v1_balanced_two_leg_veto(
    v1_state: str | None,
    z_aggress: float | None,
    z_passive: float | None,
    session_residual: float,
) -> bool:
    """I7 diagnostic: BALANCED two-leg sell when both |z|>=1 and both match fade risk."""
    if v1_state != "BALANCED":
        return False
    if z_aggress is None or z_passive is None:
        return False
    if not np.isfinite(float(z_aggress)) or not np.isfinite(float(z_passive)):
        return False
    if abs(float(z_aggress)) < 1.0 or abs(float(z_passive)) < 1.0:
        return False
    s = float(np.sign(session_residual))
    if s == 0.0:
        return False
    return float(np.sign(z_aggress)) == s and float(np.sign(z_passive)) == s


def c2_same_sign_increasing_veto(same_sign_trend: Any, z_passive_trend: str | None) -> bool:
    return bool(same_sign_trend) and str(z_passive_trend or "") == "INCREASING"


def quote_fillable(bid: float, ask: float) -> bool:
    return bool(np.isfinite(bid) and np.isfinite(ask) and ask >= bid)


def fill_price(side: int, bid: float, ask: float) -> float | None:
    if not quote_fillable(bid, ask):
        return None
    if side > 0:
        return float(ask)
    if side < 0:
        return float(bid)
    return None


def mark_price(side: int, bid: float, ask: float) -> float | None:
    """Exit mark: long sells bid, short covers ask."""
    if not quote_fillable(bid, ask):
        return None
    if side > 0:
        return float(bid)
    if side < 0:
        return float(ask)
    return None


def signed_ticks(side: int, entry_px: float, exit_px: float) -> float:
    return side * (float(exit_px) - float(entry_px)) / TICK_SIZE


def barrier_hit(side: int, entry_px: float, mark_px: float, n_ticks: float, x_ticks: float) -> str | None:
    pnl = signed_ticks(side, entry_px, mark_px)
    if pnl <= -float(x_ticks):
        return "SL"
    if pnl >= float(n_ticks):
        return "TP"
    return None


def _window_prints(prints: pd.DataFrame, start: Any, end: Any) -> pd.DataFrame:
    if prints is None or prints.empty:
        return pd.DataFrame()
    ts = prints["ts_event"]
    left = int(ts.searchsorted(_utc(start), side="left"))
    right = int(ts.searchsorted(_utc(end), side="right"))
    return prints.iloc[left:right]


def first_fill_row(prints: pd.DataFrame, decision_ts: Any) -> pd.Series | None:
    if prints.empty:
        return None
    later = prints.loc[prints["ts_event"] >= _utc(decision_ts)]
    if later.empty:
        return None
    return later.iloc[0]


def walk_exits(
    prints: pd.DataFrame,
    *,
    side: int,
    entry_px: float,
    fill_ts: Any,
    n_ticks: float,
    x_ticks: float,
    deadline_ts: Any,
    min_hold_minutes_tp: int = 0,
    min_hold_minutes_sl: int = 0,
    exit_space: str = "bidask",
    print_target: float | None = None,
    midchg_target: float | None = None,
) -> dict[str, Any]:
    """First of SL / TP / print-clock / midchg-clock / wall clock.

    Official PnL is always bid/ask at the chosen print. Barriers may use mid space.
    """
    empty = {
        "exit_reason": "NO_PRINTS",
        "exit_ts": None,
        "exit_px": None,
        "pnl_bidask_ticks": None,
        "pnl_mid_ticks": None,
        "spread_ticks_exit": None,
        "n_prints": 0,
        "n_midchg": 0,
        "mae_hold_ticks": None,
        "clock_family": CLOCK_LABEL,
    }
    if prints.empty:
        return empty
    work = prints.loc[prints["ts_event"] >= _utc(fill_ts)]
    if work.empty:
        return empty
    deadline = _utc(deadline_ts)
    work = work.loc[work["ts_event"] <= deadline]
    bid = pd.to_numeric(work["bid_price"], errors="coerce").to_numpy()
    ask = pd.to_numeric(work["ask_price"], errors="coerce").to_numpy()
    ts = pd.to_datetime(work["ts_event"], utc=True)
    valid = np.isfinite(bid) & np.isfinite(ask) & (ask >= bid)
    if not valid.any():
        return empty
    bid, ask = bid[valid], ask[valid]
    ts_valid = pd.Series(pd.to_datetime(ts[valid], utc=True)).reset_index(drop=True)
    ts_ns = np.array([int(_utc(x).value) for x in ts_valid], dtype=np.int64)
    mark = bid if side > 0 else ask
    pnl = side * (mark - float(entry_px)) / TICK_SIZE
    spread = (ask - bid) / TICK_SIZE
    mid = 0.5 * (bid + ask)
    mid_pnl = side * (mid - float(entry_px)) / TICK_SIZE
    barrier = mid_pnl if str(exit_space) == "mid" else pnl
    mid_chg = np.zeros(len(mid), dtype=int)
    if len(mid) > 1:
        mid_chg[1:] = (np.abs(np.diff(mid)) >= 0.5 * TICK_SIZE).astype(int)
    n_prints_arr = np.arange(1, len(mark) + 1)
    n_midchg_arr = np.cumsum(mid_chg)
    fill_ns = int(_utc(fill_ts).value)
    sl_ok_ns = int((_utc(fill_ts) + pd.Timedelta(minutes=int(min_hold_minutes_sl))).value)
    tp_ok_ns = int((_utc(fill_ts) + pd.Timedelta(minutes=int(min_hold_minutes_tp))).value)
    sl_idx = np.flatnonzero((barrier <= -float(x_ticks)) & (ts_ns >= sl_ok_ns))
    tp_idx = np.flatnonzero((barrier >= float(n_ticks)) & (ts_ns >= tp_ok_ns))
    print_idx = np.flatnonzero(n_prints_arr >= float(print_target)) if print_target is not None else np.array([], dtype=int)
    mid_idx = np.flatnonzero(n_midchg_arr >= float(midchg_target)) if midchg_target is not None else np.array([], dtype=int)
    candidates = []
    if len(sl_idx):
        candidates.append((int(sl_idx[0]), "SL"))
    if len(tp_idx):
        candidates.append((int(tp_idx[0]), "TP"))
    if len(print_idx):
        candidates.append((int(print_idx[0]), f"PRINTS_{int(print_target)}"))
    if len(mid_idx):
        candidates.append((int(mid_idx[0]), f"MIDCHG_{int(midchg_target)}"))
    if candidates:
        i, reason = min(candidates, key=lambda item: item[0])
    else:
        i, reason = len(mark) - 1, "CLOCK_10M"
    hold_end = max(sl_ok_ns, tp_ok_ns, fill_ns)
    hold_mask = ts_ns <= hold_end
    mae_hold = float(pnl[hold_mask].min()) if hold_mask.any() else float(pnl[0])
    return {
        "exit_reason": reason,
        "exit_ts": _utc(ts_valid.iloc[i]),
        "exit_px": float(mark[i]),
        "pnl_bidask_ticks": float(pnl[i]),
        "pnl_mid_ticks": float(mid_pnl[i]),
        "spread_ticks_exit": float(spread[i]),
        "n_prints": int(n_prints_arr[i]),
        "n_midchg": int(n_midchg_arr[i]),
        "mae_hold_ticks": mae_hold,
        "clock_family": CLOCK_LABEL,
    }


def entry_gate(
    row: pd.Series,
    cfg: ReplayConfig,
) -> str | None:
    """Return skip reason, or None if the bar may open a fade."""
    sess = float(row["session_residual"])
    local = float(row["local_residual"])
    if not np.isfinite(sess) or not np.isfinite(local):
        return "MISSING_RESIDUAL"
    if cfg.rth_only and not bool(row.get("is_rth", False)):
        return "NOT_RTH"
    if cfg.require_session_z is not None:
        z = row.get("session_residual_z")
        if z is None or not np.isfinite(float(z)) or abs(float(z)) < float(cfg.require_session_z):
            return "SESSION_Z"
    if not session_gap_ok(sess, cfg.g_ticks):
        return "BELOW_G"
    if cfg.g_max_ticks is not None and abs(residual_ticks(sess)) > float(cfg.g_max_ticks):
        return "ABOVE_G_MAX"
    if cfg.require_peak_shrink and not bool(row.get("peak_shrink_ok", False)):
        return "NO_PEAK_SHRINK"
    if cfg.require_agree and not signs_agree(sess, local):
        return "DISAGREE"
    if cfg.apply_v1_veto and v1_aggress_with_fade(
        row.get("v1_state"),
        row.get("z_aggress"),
        sess,
    ):
        return "V1_AGGRESS_WITH_FADE"
    if cfg.apply_c2_veto and c2_same_sign_increasing_veto(
        row.get("same_sign_trend"),
        row.get("z_passive_trend"),
    ):
        return "C2_SAME_SIGN_INCREASING"
    if cfg.apply_passive_increasing_veto and v1_passive_increasing_veto(
        row.get("z_passive"),
        row.get("z_passive_trend"),
        sess,
    ):
        return "V1_PASSIVE_INCREASING"
    if cfg.apply_balanced_two_leg_veto and v1_balanced_two_leg_veto(
        row.get("v1_state"),
        row.get("z_aggress"),
        row.get("z_passive"),
        sess,
    ):
        return "V1_BALANCED_TWO_LEG"
    if fade_side(sess) == 0:
        return "FLAT_SESSION"
    return None


def annotate_correction_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Stamp residual-change, peak-shrink, and V1 5m strength trends for correction cells."""
    if bars is None or bars.empty:
        return bars
    out = bars.sort_values("bucket_ts").copy()
    out["bucket_ts"] = pd.to_datetime(out["bucket_ts"], utc=True)
    sess = pd.to_numeric(out["session_residual"], errors="coerce")
    ticks = sess / TICK_SIZE
    out["d_resid_15m"] = sess - sess.shift(15)
    out["sign_level"] = np.sign(sess.to_numpy())
    out["sign_change"] = np.sign(out["d_resid_15m"].to_numpy())
    out["same_sign_trend"] = (out["sign_level"] != 0) & (out["sign_level"] == out["sign_change"])
    roll_max = ticks.rolling(15, min_periods=2).max()
    roll_min = ticks.rolling(15, min_periods=2).min()
    extreme = np.where(ticks > 0, roll_max, np.where(ticks < 0, roll_min, np.nan))
    shrink = np.abs(extreme) - np.abs(ticks)
    out["resid_peak_ticks"] = extreme
    out["resid_shrink_ticks"] = shrink
    out["peak_shrink_ok"] = (
        (np.sign(ticks.to_numpy()) == np.sign(extreme))
        & (shrink >= 1.0)
        & np.isfinite(shrink)
    )
    zp = pd.to_numeric(out["z_passive"], errors="coerce") if "z_passive" in out.columns else pd.Series(np.nan, index=out.index)
    za = pd.to_numeric(out["z_aggress"], errors="coerce") if "z_aggress" in out.columns else pd.Series(np.nan, index=out.index)
    out["z_passive_trend"] = [
        classify_strength_change(cur, prv)[0] for cur, prv in zip(zp, zp.shift(5))
    ]
    out["z_aggress_trend"] = [
        classify_strength_change(cur, prv)[0] for cur, prv in zip(za, za.shift(5))
    ]
    return out


def replay_bars(
    bars: pd.DataFrame,
    prints: pd.DataFrame,
    cfg: ReplayConfig,
) -> pd.DataFrame:
    """One-trade-at-a-time session-gap replay. prints may be empty (last-print path)."""
    rows: list[dict[str, Any]] = []
    busy_until: pd.Timestamp | None = None
    last_exit_sign: float | None = None
    work = annotate_correction_features(bars.sort_values("bucket_ts").copy())
    if prints is None or prints.empty:
        print_index = pd.DataFrame()
    elif bool(prints["ts_event"].is_monotonic_increasing):
        print_index = prints
    else:
        print_index = prints.sort_values("ts_event").reset_index(drop=True)
    for row in work.itertuples(index=False):
        rec = row._asdict() if hasattr(row, "_asdict") else dict(zip(work.columns, row))
        series = pd.Series(rec)
        ts = _utc(series["bucket_ts"])
        if busy_until is not None and ts < busy_until:
            continue
        sess_sign = float(np.sign(float(series["session_residual"]))) if np.isfinite(float(series["session_residual"])) else 0.0
        if cfg.block_same_sign_reentry and last_exit_sign is not None and sess_sign != 0.0 and sess_sign == last_exit_sign:
            continue
        skip = entry_gate(series, cfg)
        if skip is not None:
            continue
        side = fade_side(float(series["session_residual"]))
        decision_ts = ts + pd.Timedelta(minutes=1)
        deadline_ts = decision_ts + pd.Timedelta(minutes=int(cfg.horizon_minutes))
        trade = {
            "decision_bar_ts": ts,
            "decision_ts": decision_ts,
            "deadline_ts": deadline_ts,
            "side": side,
            "side_name": "LONG_CHEAP" if side > 0 else "SHORT_RICH",
            "session_residual": float(series["session_residual"]),
            "session_residual_z": _finite(series.get("session_residual_z")),
            "local_residual": float(series["local_residual"]),
            "local_residual_z": _finite(series.get("local_residual_z")),
            "session_ticks": residual_ticks(float(series["session_residual"])),
            "v1_state": series.get("v1_state"),
            "z_aggress": _finite(series.get("z_aggress")),
            "z_passive": _finite(series.get("z_passive")),
            "is_rth": bool(series.get("is_rth", False)),
            "et_date": str(series.get("et_date", "")),
            "bar_last_print": _finite(series.get("price")),
            "g_ticks": cfg.g_ticks,
            "n_ticks": cfg.n_ticks,
            "x_ticks": cfg.x_ticks,
            "d_resid_15m": _finite(series.get("d_resid_15m")),
            "sign_level": _finite(series.get("sign_level")),
            "sign_change": _finite(series.get("sign_change")),
            "same_sign_trend": bool(series.get("same_sign_trend", False)),
            "z_passive_trend": series.get("z_passive_trend"),
            "z_aggress_trend": series.get("z_aggress_trend"),
            "peak_shrink_ok": bool(series.get("peak_shrink_ok", False)),
            "exit_space": cfg.exit_space,
            "fill_mode": cfg.fill_mode,
        }
        fill_px = None
        fill_ts = None
        fill_bid = None
        fill_ask = None
        fill_vs_5s_mid = None
        window = _window_prints(print_index, decision_ts, deadline_ts + pd.Timedelta(minutes=1))
        if not window.empty:
            fill_px, fill_ts, fill_bid, fill_ask, fill_vs_5s_mid = _choose_fill(
                window, work, series, side, decision_ts, cfg.fill_mode,
            )
        if fill_px is None:
            last = _finite(series.get("price"))
            last_print_exit = _last_print_exit(work, ts, side, last if last is not None else float("nan"), cfg)
            trade.update({
                "fill_status": "unfillable" if not print_index.empty else "last_print_only",
                "fill_ts": None,
                "fill_px": last,
                "delay_sec": None,
                "spread_ticks_fill": None,
                "pnl_last_print_ticks": last_print_exit.get("pnl_ticks"),
                "pnl_mid_ticks": None,
                "pnl_bidask_ticks": None,
                "pnl_bidask_after_cost_ticks": None,
                "fill_vs_5s_mid_ticks": fill_vs_5s_mid,
                "exit_reason": "UNFILLABLE" if not print_index.empty else last_print_exit.get("reason") or "NO_TBBO",
            })
            rows.append(trade)
            busy_until = deadline_ts
            last_exit_sign = sess_sign if cfg.block_same_sign_reentry else last_exit_sign
            continue
        exit_info = walk_exits(
            window,
            side=side,
            entry_px=fill_px,
            fill_ts=fill_ts,
            n_ticks=cfg.n_ticks,
            x_ticks=cfg.x_ticks,
            deadline_ts=deadline_ts,
            min_hold_minutes_tp=cfg.min_hold_minutes_tp,
            min_hold_minutes_sl=cfg.min_hold_minutes_sl,
            exit_space=cfg.exit_space,
        )
        last_print_exit = _last_print_exit(work, ts, side, float(series["price"]), cfg)
        delay = (fill_ts - decision_ts).total_seconds() if fill_ts is not None else None
        pnl = exit_info.get("pnl_bidask_ticks")
        pnl_mid = exit_info.get("pnl_mid_ticks")
        exit_ts = _utc(exit_info["exit_ts"]) if exit_info.get("exit_ts") is not None else deadline_ts
        hold_sec = (exit_ts - fill_ts).total_seconds() if fill_ts is not None else None
        trade.update({
            "fill_status": "filled",
            "fill_ts": fill_ts,
            "fill_px": fill_px,
            "delay_sec": delay,
            "time_in_market_sec": hold_sec,
            "spread_ticks_fill": None if fill_bid is None or fill_ask is None else (fill_ask - fill_bid) / TICK_SIZE,
            "pnl_last_print_ticks": last_print_exit.get("pnl_ticks"),
            "pnl_mid_ticks": pnl_mid,
            "pnl_bidask_ticks": pnl,
            "pnl_bidask_after_cost_ticks": None if pnl is None else float(pnl) - RT_COST_TICKS,
            "fill_vs_5s_mid_ticks": fill_vs_5s_mid,
            **exit_info,
        })
        rows.append(trade)
        busy_until = exit_ts
        if cfg.cooldown_minutes is not None:
            cool = exit_ts + pd.Timedelta(minutes=float(cfg.cooldown_minutes))
            if busy_until is None or cool > busy_until:
                busy_until = cool
        if cfg.block_same_sign_reentry:
            last_exit_sign = sess_sign
    return pd.DataFrame(rows)


def _choose_fill(
    window: pd.DataFrame,
    bars: pd.DataFrame,
    series: pd.Series,
    side: int,
    decision_ts: Any,
    fill_mode: str,
) -> tuple[float | None, Any, float | None, float | None, float | None]:
    """Return fill_px, fill_ts, fill_bid, fill_ask, fill_vs_5s_mid_ticks."""
    first = first_fill_row(window, decision_ts)
    first_bid = _finite(first["bid_price"]) if first is not None else None
    first_ask = _finite(first["ask_price"]) if first is not None else None
    first_ts = _utc(first["ts_event"]) if first is not None else None
    first_px = fill_price(side, first_bid, first_ask) if first_bid is not None and first_ask is not None else None
    mid_5s = _five_second_twap_mid(window, decision_ts)
    vs_5s = None
    if first_px is not None and mid_5s is not None:
        vs_5s = (float(first_px) - float(mid_5s)) / TICK_SIZE
    mode = str(fill_mode or "first_print")
    if mode == "next_bar":
        nxt = _next_bar_last_print(bars, series.get("bucket_ts"))
        if nxt is None:
            return first_px, first_ts, first_bid, first_ask, vs_5s
        px, ts = nxt
        return px, ts, px, px, vs_5s
    if mode == "twap_5s":
        end = _utc(decision_ts) + pd.Timedelta(seconds=5)
        slice_5s = window.loc[(window["ts_event"] >= _utc(decision_ts)) & (window["ts_event"] <= end)]
        row = slice_5s.iloc[-1] if not slice_5s.empty else first
        if row is None:
            return None, None, None, None, vs_5s
        bid = _finite(row["bid_price"])
        ask = _finite(row["ask_price"])
        ts = _utc(row["ts_event"])
        px = fill_price(side, bid, ask) if bid is not None and ask is not None else None
        if px is not None and mid_5s is not None:
            vs_5s = (float(px) - float(mid_5s)) / TICK_SIZE
        return px, ts, bid, ask, vs_5s
    return first_px, first_ts, first_bid, first_ask, vs_5s


def _five_second_twap_mid(prints: pd.DataFrame, decision_ts: Any) -> float | None:
    if prints is None or prints.empty:
        return None
    start = _utc(decision_ts)
    end = start + pd.Timedelta(seconds=5)
    work = prints.loc[(prints["ts_event"] >= start) & (prints["ts_event"] <= end)]
    if work.empty:
        return None
    bid = pd.to_numeric(work["bid_price"], errors="coerce")
    ask = pd.to_numeric(work["ask_price"], errors="coerce")
    ok = bid.notna() & ask.notna() & (ask >= bid)
    if not ok.any():
        return None
    return float((0.5 * (bid[ok] + ask[ok])).mean())


def _next_bar_last_print(bars: pd.DataFrame, bucket_ts: Any) -> tuple[float, pd.Timestamp] | None:
    ts = _utc(bucket_ts)
    later = bars.loc[pd.to_datetime(bars["bucket_ts"], utc=True) > ts].sort_values("bucket_ts")
    if later.empty:
        return None
    px = _finite(later.iloc[0]["price"])
    if px is None:
        return None
    nxt = _utc(later.iloc[0]["bucket_ts"])
    return px, nxt + pd.Timedelta(minutes=1)


def _last_print_exit(
    bars: pd.DataFrame,
    open_ts: pd.Timestamp,
    side: int,
    entry_px: float,
    cfg: ReplayConfig,
) -> dict[str, Any]:
    later = bars.loc[pd.to_datetime(bars["bucket_ts"], utc=True) > open_ts].sort_values("bucket_ts")
    if later.empty or not np.isfinite(entry_px):
        return {"pnl_ticks": None, "reason": "NO_BAR"}
    deadline = open_ts + pd.Timedelta(minutes=int(cfg.horizon_minutes))
    window = later.loc[pd.to_datetime(later["bucket_ts"], utc=True) <= deadline]
    if window.empty:
        return {"pnl_ticks": None, "reason": "NO_BAR"}
    for row in window.itertuples(index=False):
        px = _finite(row.price)
        if px is None:
            continue
        hit = barrier_hit(side, entry_px, px, cfg.n_ticks, cfg.x_ticks)
        if hit:
            return {"pnl_ticks": signed_ticks(side, entry_px, px), "reason": hit}
    last = _finite(window.iloc[-1]["price"])
    if last is None:
        return {"pnl_ticks": None, "reason": "NO_BAR"}
    return {"pnl_ticks": signed_ticks(side, entry_px, last), "reason": "CLOCK_10M"}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _is_rth(ts: Any) -> bool:
    et = _utc(ts).tz_convert(EASTERN)
    minutes = et.hour * 60 + et.minute
    return (RTH_START_HOUR_ET * 60) <= minutes < (RTH_END_HOUR_ET * 60)


def annotate_session_flags(bars: pd.DataFrame) -> pd.DataFrame:
    out = bars.copy()
    out["bucket_ts"] = pd.to_datetime(out["bucket_ts"], utc=True)
    out["is_rth"] = out["bucket_ts"].map(_is_rth)
    et = out["bucket_ts"].dt.tz_convert(EASTERN)
    out["et_date"] = et.dt.strftime("%Y-%m-%d")
    out["session_start_et"] = out["bucket_ts"].map(session_start_et)
    return out


def summarize_trades(trades: pd.DataFrame, slice_name: str) -> dict[str, Any]:
    empty = {
        "slice": slice_name,
        "n_trades": 0,
        "n_filled": 0,
        "n_unfillable": 0,
        "n_tp_ticks": None,
        "n_sl_ticks": None,
        "sum_bidask": None,
        "mean_bidask": None,
        "mean_after_cost": None,
        "sum_last_print": None,
        "mean_last_print": None,
        "sum_mid": None,
        "mean_mid": None,
        "n_tp": 0,
        "n_sl": 0,
        "n_clock": 0,
        "mean_time_in_market_sec": None,
        "p_tp_before_sl": None,
        "ship_gate": False,
    }
    if trades is None or trades.empty:
        empty["n"] = 0
        return empty
    filled = trades[trades.get("fill_status", "filled") == "filled"] if "fill_status" in trades else trades
    bidask = pd.to_numeric(filled.get("pnl_bidask_ticks"), errors="coerce")
    after = pd.to_numeric(filled.get("pnl_bidask_after_cost_ticks"), errors="coerce")
    last = pd.to_numeric(trades.get("pnl_last_print_ticks"), errors="coerce")
    mid = pd.to_numeric(filled.get("pnl_mid_ticks"), errors="coerce") if "pnl_mid_ticks" in filled else pd.Series(dtype=float)
    reasons = filled["exit_reason"].astype(str) if "exit_reason" in filled else pd.Series(dtype=str)
    n_filled = int(len(filled))
    mean_after = float(after.mean()) if after.notna().any() else None
    n_tp = int(reasons.eq("TP").sum())
    n_sl = int(reasons.eq("SL").sum())
    n_tp_ticks = float(pd.to_numeric(trades.get("n_ticks"), errors="coerce").dropna().iloc[0]) if "n_ticks" in trades and trades["n_ticks"].notna().any() else None
    n_sl_ticks = float(pd.to_numeric(trades.get("x_ticks"), errors="coerce").dropna().iloc[0]) if "x_ticks" in trades and trades["x_ticks"].notna().any() else None
    hold = pd.to_numeric(filled.get("time_in_market_sec"), errors="coerce") if "time_in_market_sec" in filled else pd.Series(dtype=float)
    resolved = n_tp + n_sl
    return {
        "slice": slice_name,
        "n": n_filled,
        "n_trades": n_filled,
        "n_filled": n_filled,
        "n_unfillable": int((trades.get("fill_status") == "unfillable").sum()) if "fill_status" in trades else 0,
        "n_tp_ticks": n_tp_ticks,
        "n_sl_ticks": n_sl_ticks,
        "sum_bidask": float(bidask.sum()) if bidask.notna().any() else None,
        "mean_bidask": float(bidask.mean()) if bidask.notna().any() else None,
        "mean_after_cost": mean_after,
        "sum_last_print": float(last.sum()) if last.notna().any() else None,
        "mean_last_print": float(last.mean()) if last.notna().any() else None,
        "sum_mid": float(mid.sum()) if mid.notna().any() else None,
        "mean_mid": float(mid.mean()) if mid.notna().any() else None,
        "n_tp": n_tp,
        "n_sl": n_sl,
        "n_clock": int(reasons.str.startswith("CLOCK").sum() + reasons.str.startswith("PRINTS").sum() + reasons.str.startswith("MIDCHG").sum()),
        "mean_time_in_market_sec": float(hold.mean()) if hold.notna().any() else None,
        "p_tp_before_sl": (n_tp / resolved) if resolved else None,
        "ship_gate": bool(n_filled >= SHIP_N and mean_after is not None and mean_after >= SHIP_EXPECTANCY),
    }


def load_signal_logs(v2_dir: Path, v27_dir: Path, dates: list[str]) -> pd.DataFrame:
    frames = []
    for date_text in dates:
        v2_path = v2_dir / f"signal_log_{date_text.replace('-', '')}.csv"
        v27_path = v27_dir / f"signal_log_{date_text.replace('-', '')}.csv"
        if not v2_path.exists() or not v27_path.exists():
            continue
        v2 = pd.read_csv(v2_path)
        v27 = pd.read_csv(v27_path)
        v2 = v2.rename(columns={
            "ts": "bucket_ts",
            "residual": "session_residual",
            "residual_z": "session_residual_z",
            "fair_value": "session_fv",
        })
        v27 = v27.rename(columns={
            "ts": "bucket_ts",
            "residual": "local_residual",
            "residual_z": "local_residual_z",
            "fair_value": "local_fv",
            "price": "local_price",
        })
        v2["bucket_ts"] = pd.to_datetime(v2["bucket_ts"], utc=True)
        v27["bucket_ts"] = pd.to_datetime(v27["bucket_ts"], utc=True)
        keep_v27 = [c for c in ("bucket_ts", "local_residual", "local_residual_z", "local_fv", "local_price") if c in v27]
        merged = v2.merge(v27[keep_v27], on="bucket_ts", how="inner")
        frames.append(merged)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True).sort_values("bucket_ts")
    if "price" not in out.columns and "local_price" in out.columns:
        out["price"] = out["local_price"]
    return annotate_session_flags(out)


def filter_session_day(bars: pd.DataFrame, session_start: str) -> pd.DataFrame:
    start = pd.Timestamp(session_start, tz=EASTERN)
    end = start + pd.Timedelta(days=1)
    ts = pd.to_datetime(bars["bucket_ts"], utc=True)
    start_utc = start.tz_convert("UTC")
    end_utc = end.tz_convert("UTC")
    return bars.loc[(ts >= start_utc) & (ts < end_utc)].copy()


def load_v27_episodes(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    raw = json.loads(path.read_text(encoding="utf-8"))
    episodes = raw.get("episodes", raw) if isinstance(raw, dict) else raw
    if not episodes:
        return pd.DataFrame()
    return pd.DataFrame(episodes)


def compare_to_live_v27(trades: pd.DataFrame, episodes: pd.DataFrame, session_start: str) -> pd.DataFrame:
    if episodes.empty:
        return pd.DataFrame()
    start = pd.Timestamp(session_start, tz=EASTERN).tz_convert("UTC")
    end = start + pd.Timedelta(days=1)
    work = episodes.copy()
    for col in ("opened_ts", "entry_ts", "opened_at", "ts", "start_ts"):
        if col in work.columns:
            work["_entry"] = pd.to_datetime(work[col], utc=True, errors="coerce")
            break
    if "_entry" not in work.columns:
        return pd.DataFrame()
    day = work.loc[(work["_entry"] >= start) & (work["_entry"] < end)].copy()
    if day.empty:
        return pd.DataFrame()
    paper_ts = set()
    if not trades.empty:
        paper_ts = set(pd.to_datetime(trades["decision_bar_ts"], utc=True).dt.floor("min"))
    rows = []
    for rec in day.to_dict("records"):
        entry = _utc(rec.get("_entry"))
        matched = entry.floor("min") in paper_ts or (entry - pd.Timedelta(minutes=1)).floor("min") in paper_ts
        rows.append({
            "live_entry_ts": entry.isoformat(),
            "live_side": rec.get("side") or rec.get("direction") or rec.get("signal"),
            "live_pnl_ticks": rec.get("dir_pnl_trade_ticks") or rec.get("dir_pnl_ticks") or rec.get("pnl_ticks") or rec.get("dir_pnl_trade"),
            "paper_opened": bool(matched),
            "skip_or_match": "OPENED" if matched else "LIVE_ONLY",
        })
    return pd.DataFrame(rows)


def probe_ty_date_range(api_base: str = API_BASE, timeout: int = 20) -> dict[str, Any]:
    url = f"{api_base.rstrip('/')}/db/date-range?{urllib.parse.urlencode({'symbol': 'TY'})}"
    last_error = None
    for attempt in range(1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            payload["probed_at"] = datetime.now(timezone.utc).isoformat()
            payload["api_base"] = api_base
            payload["endpoint"] = "/db/date-range"
            payload["attempts"] = attempt + 1
            return payload
        except Exception as exc:
            last_error = exc
    raise TimeoutError(f"TY date-range failed after retries: {last_error}")


def fetch_tbbo_window(
    start: Any,
    end: Any,
    *,
    symbol: str = "TY",
    api_base: str = API_BASE,
    timeout: int = 180,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    start_ts = _utc(start)
    end_ts = _utc(end)
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        name = f"{symbol}_{start_ts.strftime('%Y%m%dT%H%M%S')}_{end_ts.strftime('%Y%m%dT%H%M%S')}.parquet"
        cache_path = cache_dir / name
        if cache_path.exists():
            frame = pd.read_parquet(cache_path)
            frame["ts_event"] = pd.to_datetime(frame["ts_event"], utc=True)
            return frame
    start_txt = start_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_txt = end_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    frame = pd.DataFrame()
    last_error = None
    for fmt in ("arrow", "json"):
        query = urllib.parse.urlencode({
            "source": "database",
            "schema": "tbbo",
            "symbols": symbol,
            "start": start_txt,
            "end": end_txt,
            "format": fmt,
        })
        url = f"{api_base.rstrip('/')}/historical/trades?{query}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                raw = response.read()
            if fmt == "arrow":
                table = pa_ipc.open_stream(io.BytesIO(raw)).read_all()
                frame = table.to_pandas()
            else:
                payload = json.loads(raw.decode("utf-8"))
                rows = payload.get("data", payload) if isinstance(payload, dict) else payload
                frame = pd.DataFrame(rows)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            frame = pd.DataFrame()
    if last_error is not None and frame.empty:
        raise last_error
    if frame.empty:
        empty = pd.DataFrame(columns=["ts_event", "ts_recv", "price", "size", "side", "bid_price", "ask_price", "symbol"])
        if cache_path is not None:
            empty.to_parquet(cache_path, index=False)
        return empty
    aliases = {"bid_price_00": "bid_price", "ask_price_00": "ask_price", "ts": "ts_event"}
    for source, target in aliases.items():
        if target not in frame.columns and source in frame.columns:
            frame[target] = frame[source]
    frame["ts_event"] = pd.to_datetime(frame.get("ts_event"), utc=True, errors="coerce")
    if "ts_recv" in frame.columns:
        frame["ts_recv"] = pd.to_datetime(frame["ts_recv"], utc=True, errors="coerce")
    for col in ("price", "size", "bid_price", "ask_price"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        else:
            frame[col] = np.nan
    if "side" not in frame.columns:
        frame["side"] = ""
    frame["symbol"] = symbol
    keep = [c for c in ("ts_event", "ts_recv", "price", "size", "side", "bid_price", "ask_price", "symbol") if c in frame.columns]
    frame = frame[keep].dropna(subset=["ts_event"]).sort_values("ts_event")
    if cache_path is not None:
        frame.to_parquet(cache_path, index=False)
    return frame.reset_index(drop=True)


def fetch_tbbo_chunks(
    start: Any,
    end: Any,
    *,
    symbol: str = "TY",
    hours: int = 2,
    api_base: str = API_BASE,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    cursor = _utc(start)
    stop = _utc(end)
    parts = []
    while cursor < stop:
        nxt = min(cursor + pd.Timedelta(hours=hours), stop)
        try:
            part = fetch_tbbo_window(cursor, nxt, symbol=symbol, api_base=api_base, cache_dir=cache_dir)
            if not part.empty:
                parts.append(part)
            print(f"TBBO {symbol} {cursor} -> {nxt} rows={len(part)}", flush=True)
        except Exception as exc:
            print(f"TBBO chunk failed {symbol} {cursor} {nxt}: {exc}", flush=True)
        cursor = nxt
    if not parts:
        return pd.DataFrame(columns=["ts_event", "ts_recv", "price", "size", "side", "bid_price", "ask_price", "symbol"])
    return pd.concat(parts, ignore_index=True).drop_duplicates(["ts_event", "price", "size", "side"]).sort_values("ts_event")


def fetch_curve_chunks(start: Any, end: Any, *, cache_dir: Path, api_base: str = API_BASE) -> dict[str, pd.DataFrame]:
    out = {}
    for symbol in CURVE_SYMBOLS:
        frame = fetch_tbbo_chunks(start, end, symbol=symbol, cache_dir=cache_dir / symbol, api_base=api_base)
        if not frame.empty:
            frame = frame.rename(columns={"ts_event": "ts"})
        out[symbol] = frame
    return out


def reconstruct_v1_from_tbbo(
    trades_by_symbol: dict[str, pd.DataFrame],
    bars: pd.DataFrame,
) -> pd.DataFrame:
    from scripts.aggression_dashboard.core import aggregate_curve_aggression

    priced = {}
    for symbol, frame in trades_by_symbol.items():
        if frame is None or frame.empty:
            continue
        work = frame.copy()
        if "ts" not in work.columns and "ts_event" in work.columns:
            work = work.rename(columns={"ts_event": "ts"})
        priced[symbol] = work
    if not priced:
        out = bars.copy()
        out["v1_state"] = "UNAVAILABLE"
        out["z_aggress"] = np.nan
        out["z_passive"] = np.nan
        out["v1_source"] = "UNAVAILABLE"
        return out
    flow_df, _meta = aggregate_curve_aggression(priced, dv01_table=load_dv01_table(), bucket="1min", reset="daily")
    price_df = pd.DataFrame({"price": flow_df["trade_price"]}) if "trade_price" in flow_df else pd.DataFrame()
    if "TY" in priced and not priced["TY"].empty:
        ty = priced["TY"].copy()
        ty["ts"] = pd.to_datetime(ty["ts"], utc=True)
        ty = ty.set_index("ts").sort_index()
        price_df = pd.DataFrame({"price": ty["price"].resample("1min").last().ffill()})
    model_df, model_meta = fit_session_model(flow_df, price_df)
    out = bars.copy()
    out["v1_state"] = "UNAVAILABLE"
    out["z_aggress"] = np.nan
    out["z_passive"] = np.nan
    out["v1_source"] = "CURVE" if len(priced) > 1 else "TY_ONLY"
    if model_df.empty or not model_meta.get("ok"):
        return out
    return _stamp_v1_from_model(out, model_df, float(model_meta["beta"]), out["v1_source"].iloc[0])


def reconstruct_v1_from_feature_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    out = panel.copy()
    out["v1_state"] = "UNAVAILABLE"
    out["z_aggress"] = np.nan
    out["z_passive"] = np.nan
    out["v1_source"] = "FEATURE_MATRIX"
    if "agg_1m" not in out.columns and "agg_cum_daily" not in out.columns:
        return out
    work = out.sort_values(["et_date", "bucket_ts"]).copy()
    if "agg_1m" in work.columns:
        work["total_aggress"] = work.groupby("et_date", sort=False)["agg_1m"].cumsum()
    else:
        work["total_aggress"] = work["agg_cum_daily"]
    for date, day in work.groupby("et_date", sort=False):
        alpha, beta, reason = _ols_params(day["total_aggress"], day["price"])
        if reason or not np.isfinite(beta):
            continue
        model_df = pd.DataFrame(
            {
                "total_aggress": day["total_aggress"].to_numpy(),
                "residual": (day["price"] - (alpha + beta * day["total_aggress"])).to_numpy(),
                "price": day["price"].to_numpy(),
            },
            index=pd.DatetimeIndex(pd.to_datetime(day["bucket_ts"], utc=True)),
        )
        stamped = _stamp_v1_from_model(day.copy(), model_df, float(beta), "FEATURE_MATRIX")
        out.loc[day.index, ["v1_state", "z_aggress", "z_passive", "v1_source"]] = stamped[
            ["v1_state", "z_aggress", "z_passive", "v1_source"]
        ]
    return out


def _stamp_v1_from_model(bars: pd.DataFrame, model_df: pd.DataFrame, beta: float, source: str) -> pd.DataFrame:
    """Stamp the prior completed bar's V1 state (as_of = bucket_ts excludes the decision bar)."""
    out = bars.copy()
    annotated = _annotate_contributions(model_df, float(beta))
    annotated = annotated.copy()
    annotated["v1_state"] = annotated["research_state"].map(RAW_TO_DISPLAY).fillna("WARMING")
    lookup = annotated[["v1_state", "z_aggress", "z_passive"]].copy()
    lookup = lookup.shift(1)
    ts = pd.to_datetime(out["bucket_ts"], utc=True)
    aligned = lookup.reindex(ts)
    out["v1_state"] = aligned["v1_state"].to_numpy()
    out["z_aggress"] = pd.to_numeric(aligned["z_aggress"], errors="coerce").to_numpy()
    out["z_passive"] = pd.to_numeric(aligned["z_passive"], errors="coerce").to_numpy()
    out["v1_source"] = source
    out["v1_state"] = out["v1_state"].fillna("WARMING")
    return out


def load_oos_panel() -> pd.DataFrame:
    oos = pd.read_csv(OOS_PATH, parse_dates=["bucket_ts"])
    oos["bucket_ts"] = pd.to_datetime(oos["bucket_ts"], utc=True)
    session = oos[oos["construction"] == "session"].copy()
    local = oos[oos["construction"] == "B1_W10"].copy()
    session = session.rename(columns={
        "residual": "session_residual",
        "residual_z": "session_residual_z",
        "fair_value": "session_fv",
        "price": "price",
    })
    local = local.rename(columns={
        "residual": "local_residual",
        "residual_z": "local_residual_z",
        "fair_value": "local_fv",
    })
    keep_local = ["bucket_ts", "et_date", "local_residual", "local_residual_z", "local_fv"]
    panel = session.merge(local[keep_local], on=["bucket_ts", "et_date"], how="inner")
    feat_cols = pd.read_csv(FEATURE_MATRIX, nrows=0).columns.tolist()
    want = [c for c in (
        "bucket_ts", "et_date", "agg_1m", "agg_cum_daily", "trade_intensity",
        "avg_trade_size", "vol_per_min", "TY_contracts", "price",
    ) if c in feat_cols]
    feat = pd.read_csv(FEATURE_MATRIX, usecols=want, parse_dates=["bucket_ts"])
    feat["bucket_ts"] = pd.to_datetime(feat["bucket_ts"], utc=True)
    panel = panel.merge(feat, on=["bucket_ts", "et_date"], how="left", suffixes=("", "_feat"))
    if "price" not in panel.columns and "price_feat" in panel.columns:
        panel["price"] = panel["price_feat"]
    return annotate_session_flags(panel)


def coverage_holes(probe: dict[str, Any], dates: list[str]) -> list[str]:
    min_date = str(probe.get("min_date") or probe.get("start") or "")[:10]
    max_date = str(probe.get("max_date") or probe.get("end") or "")[:10]
    holes = []
    for date in dates:
        if min_date and date < min_date:
            holes.append(date)
        elif max_date and date > max_date:
            holes.append(date)
    return holes


def fetch_alert_windows(
    trades: pd.DataFrame,
    *,
    cache_dir: Path,
    api_base: str,
    holes: set[str],
) -> pd.DataFrame:
    """Cache TY TBBO for each UTC hour that contains an alert path (decision through deadline)."""
    if trades.empty:
        return pd.DataFrame(columns=["ts_event", "bid_price", "ask_price", "price", "size", "side"])
    hours: set[pd.Timestamp] = set()
    for row in trades.itertuples(index=False):
        rec = row._asdict() if hasattr(row, "_asdict") else {}
        et_date = str(rec.get("et_date") or "")[:10]
        if et_date in holes:
            continue
        start = _utc(rec["decision_ts"]).floor("h")
        end = (_utc(rec["deadline_ts"]) + pd.Timedelta(minutes=1)).floor("h")
        cursor = start
        while cursor <= end:
            hours.add(cursor)
            cursor += pd.Timedelta(hours=1)
    parts = []
    for hour in sorted(hours):
        try:
            part = fetch_tbbo_window(
                hour,
                hour + pd.Timedelta(hours=1),
                symbol="TY",
                api_base=api_base,
                cache_dir=cache_dir,
            )
        except Exception as exc:
            print(f"alert hour failed {hour}: {exc}", flush=True)
            continue
        if not part.empty:
            parts.append(part)
        print(f"cached TY hour {hour.isoformat()} rows={0 if part is None else len(part)}", flush=True)
    if not parts:
        return pd.DataFrame(columns=["ts_event", "bid_price", "ask_price", "price", "size", "side"])
    return pd.concat(parts, ignore_index=True).drop_duplicates(["ts_event", "price", "size"]).sort_values("ts_event")


def score_clocks_on_trades(
    trades: pd.DataFrame,
    prints: pd.DataFrame,
    cfg: ReplayConfig,
) -> pd.DataFrame:
    rows = []
    if trades.empty or prints.empty:
        return pd.DataFrame()
    filled = trades[trades["fill_status"] == "filled"]
    for row in filled.itertuples(index=False):
        rec = row._asdict() if hasattr(row, "_asdict") else {}
        side = int(rec["side"])
        entry_px = float(rec["fill_px"])
        fill_ts = rec["fill_ts"]
        deadline = rec["deadline_ts"]
        window = _window_prints(prints, fill_ts, _utc(deadline) + pd.Timedelta(minutes=1))
        for target in PRINT_TARGETS:
            info = walk_exits(
                window, side=side, entry_px=entry_px, fill_ts=fill_ts,
                n_ticks=cfg.n_ticks, x_ticks=cfg.x_ticks, deadline_ts=deadline,
                print_target=float(target),
            )
            rows.append({**rec, "clock_id": f"prints_{target}", "clock_kind": CLOCK_LABEL, **info})
        for target in MIDCHG_TARGETS:
            info = walk_exits(
                window, side=side, entry_px=entry_px, fill_ts=fill_ts,
                n_ticks=cfg.n_ticks, x_ticks=cfg.x_ticks, deadline_ts=deadline,
                midchg_target=float(target),
            )
            rows.append({**rec, "clock_id": f"midchg_{target}", "clock_kind": CLOCK_LABEL, **info})
    return pd.DataFrame(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(_fmt(row.get(col)) for col in columns) + " |")
    return "\n".join([header, sep, *body])


def _fmt(value: Any) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def run_today(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "tbbo_cache"
    output_dir.mkdir(parents=True, exist_ok=True)
    dates = ["2026-08-31", "2026-09-01"]
    bars = load_signal_logs(Path(args.v2_live), Path(args.v27_live), dates)
    bars = filter_session_day(bars, TODAY_SESSION_START_ET)
    if bars.empty:
        raise SystemExit("No joined V2/V2.7 bars for the 2026-08-31 18:00 ET session.")
    probe = {"ok": False}
    try:
        probe = probe_ty_date_range(args.api_base)
        probe["ok"] = True
    except Exception as exc:
        probe = {"ok": False, "error": str(exc)}
    write_json(output_dir / "ty_date_range.json", probe)

    session_start = pd.Timestamp(TODAY_SESSION_START_ET, tz=EASTERN).tz_convert("UTC")
    session_end = min(
        session_start + pd.Timedelta(days=1),
        pd.Timestamp.now(tz="UTC"),
    )
    prints = pd.DataFrame()
    curve: dict[str, pd.DataFrame] = {}
    print("Fetching today TY TBBO in chunks...", flush=True)
    try:
        prints = fetch_tbbo_chunks(session_start, session_end, symbol="TY", cache_dir=cache_dir / "TY", api_base=args.api_base)
    except Exception as exc:
        print(f"today TY fetch failed: {exc}", flush=True)
    if args.fetch_curve:
        print("Fetching curve TBBO for V1 reconstruct...", flush=True)
        try:
            curve = fetch_curve_chunks(session_start, session_end, cache_dir=cache_dir, api_base=args.api_base)
        except Exception as exc:
            print(f"curve fetch failed: {exc}", flush=True)
    elif not prints.empty:
        curve = {"TY": prints.rename(columns={"ts_event": "ts"})}
    if curve:
        bars = reconstruct_v1_from_tbbo(curve, bars)
    else:
        bars["v1_state"] = "UNAVAILABLE"
        bars["z_aggress"] = np.nan
        bars["z_passive"] = np.nan
        bars["v1_source"] = "UNAVAILABLE"

    cfg = ReplayConfig(
        g_ticks=float(args.g),
        n_ticks=float(args.n),
        x_ticks=float(args.x),
        require_agree=True,
        apply_v1_veto=True,
    )
    official = replay_bars(bars, prints, cfg)
    rth_bars = bars[bars["is_rth"]].copy()
    rth = replay_bars(rth_bars, prints, ReplayConfig(g_ticks=cfg.g_ticks, n_ticks=cfg.n_ticks, x_ticks=cfg.x_ticks, rth_only=True))
    z_cfg = ReplayConfig(g_ticks=cfg.g_ticks, n_ticks=cfg.n_ticks, x_ticks=cfg.x_ticks, require_session_z=1.5)
    z_and_g = replay_bars(bars, prints, z_cfg)

    slices = [
        summarize_trades(official, "full_session"),
        summarize_trades(rth, "rth_0800_1700_et"),
        summarize_trades(z_and_g, "session_z_ge_1_5_and_g"),
    ]
    episodes = load_v27_episodes(Path(args.v27_live) / "alert_episodes.json")
    compare = compare_to_live_v27(official, episodes, TODAY_SESSION_START_ET)
    skipped_live = _live_skip_reasons(bars, episodes, cfg)

    official_path = output_dir / "today_replay_20260901.csv"
    official.to_csv(official_path, index=False)
    rth.to_csv(output_dir / "today_replay_20260901_rth.csv", index=False)
    z_and_g.to_csv(output_dir / "today_replay_20260901_z_and_g.csv", index=False)
    if not compare.empty:
        compare.to_csv(output_dir / "today_vs_live_v27.csv", index=False)
    if not skipped_live.empty:
        skipped_live.to_csv(output_dir / "today_live_skip_reasons.csv", index=False)

    report = {
        "ok": True,
        "mode": "today",
        "session_start_et": TODAY_SESSION_START_ET,
        "n_bars": int(len(bars)),
        "n_prints": int(len(prints)),
        "v1_source": str(bars["v1_source"].iloc[0]) if "v1_source" in bars and len(bars) else "UNAVAILABLE",
        "probe": probe,
        "config": {"G": cfg.g_ticks, "N": cfg.n_ticks, "X": cfg.x_ticks, "horizon_min": HORIZON_MINUTES, "rt_cost_ticks": RT_COST_TICKS},
        "slices": slices,
        "live_v27_compared": int(len(compare)),
        "ship_gate": False,
        "note": "Research paper only. Live V2.7 on 5063 was not changed.",
    }
    write_json(output_dir / "today_replay_20260901_summary.json", report)
    _write_today_md(output_dir / "today_replay_20260901.md", report, official, rth, z_and_g, compare, skipped_live)
    return report


def _live_skip_reasons(bars: pd.DataFrame, episodes: pd.DataFrame, cfg: ReplayConfig) -> pd.DataFrame:
    if episodes.empty:
        return pd.DataFrame()
    work = episodes.copy()
    for col in ("opened_ts", "entry_ts", "opened_at", "ts", "start_ts"):
        if col in work.columns:
            work["_entry"] = pd.to_datetime(work[col], utc=True, errors="coerce")
            break
    if "_entry" not in work.columns:
        return pd.DataFrame()
    start = pd.Timestamp(TODAY_SESSION_START_ET, tz=EASTERN).tz_convert("UTC")
    day = work.loc[(work["_entry"] >= start) & (work["_entry"] < start + pd.Timedelta(days=1))]
    indexed = bars.set_index(pd.to_datetime(bars["bucket_ts"], utc=True))
    rows = []
    for rec in day.to_dict("records"):
        entry = _utc(rec.get("_entry")).floor("min")
        bar = None
        if entry in indexed.index:
            bar = indexed.loc[entry]
        elif (entry - pd.Timedelta(minutes=1)) in indexed.index:
            bar = indexed.loc[entry - pd.Timedelta(minutes=1)]
        if bar is None:
            rows.append({"live_entry_ts": entry.isoformat(), "skip_reason": "BAR_MISSING"})
            continue
        if isinstance(bar, pd.DataFrame):
            bar = bar.iloc[-1]
        reason = entry_gate(bar, cfg)
        rows.append({
            "live_entry_ts": entry.isoformat(),
            "skip_reason": reason or "WOULD_OPEN",
            "session_ticks": residual_ticks(float(bar["session_residual"])) if pd.notna(bar.get("session_residual")) else None,
            "local_residual": _finite(bar.get("local_residual")),
            "v1_state": bar.get("v1_state"),
        })
    return pd.DataFrame(rows)


def _write_today_md(
    path: Path,
    report: dict[str, Any],
    official: pd.DataFrame,
    rth: pd.DataFrame,
    z_and_g: pd.DataFrame,
    compare: pd.DataFrame,
    skipped: pd.DataFrame,
) -> None:
    lines = [
        "# Today session replay 2026-09-01",
        "",
        "Research paper only. Entry/exit ARE session G + agree + V1 aggress-with-fade veto + price +N/−X.",
        "Live V2.7 on port 5063 was not changed.",
        "",
        f"Session start: `{report['session_start_et']}` ET. V1 source: `{report['v1_source']}`. Prints cached: {report['n_prints']}.",
        "",
        "## Slice totals",
        "",
        _md_table(report["slices"], ["slice", "n", "n_filled", "sum_bidask", "mean_after_cost", "sum_last_print", "n_tp", "n_sl", "n_clock"]),
        "",
        "## Official full-session trades",
        "",
    ]
    if official.empty:
        lines.append("No official trades.")
    else:
        show = official.copy()
        cols = [c for c in (
            "decision_bar_ts", "side_name", "session_ticks", "v1_state",
            "fill_status", "delay_sec", "pnl_bidask_ticks", "pnl_bidask_after_cost_ticks",
            "pnl_last_print_ticks", "exit_reason",
        ) if c in show.columns]
        lines.append(_md_table(show[cols].to_dict("records"), cols))
    lines += ["", "## Live V2.7 episodes vs this trigger", ""]
    if skipped.empty:
        lines.append("No live episodes in the session window, or episode timestamps were not found.")
    else:
        cols = [c for c in skipped.columns]
        lines.append(_md_table(skipped.to_dict("records"), cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_holdout(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "tbbo_cache" / "holdout"
    output_dir.mkdir(parents=True, exist_ok=True)
    probe = {"ok": False}
    try:
        probe = probe_ty_date_range(args.api_base)
        probe["ok"] = True
    except Exception as exc:
        probe = {"ok": False, "error": str(exc)}
    write_json(output_dir / "ty_date_range.json", probe)

    print("Loading OOS panel and reconstructing V1 from the feature matrix...", flush=True)
    panel = load_oos_panel()
    panel = reconstruct_v1_from_feature_matrix(panel)
    print(f"panel rows={len(panel)} dates={panel['et_date'].nunique()}", flush=True)
    dates = sorted(panel["et_date"].astype(str).unique())
    holdout_dates = [d for d in dates if HOLDOUT_START <= d <= HOLDOUT_END]
    if not holdout_dates:
        holdout_dates = locked_holdout_dates(dates)
    discovery_dates = [d for d in dates if DISCOVERY_START <= d <= DISCOVERY_END]
    holes = set(coverage_holes(probe, holdout_dates + discovery_dates)) if probe.get("ok") else set()
    write_json(output_dir / "pg_holes.json", {"holes": sorted(holes), "probe": probe})

    grid_rows = []
    clock_rows = []
    primary_cfg = ReplayConfig(g_ticks=TODAY_G, n_ticks=TODAY_N, x_ticks=TODAY_X)
    holdout_panel = panel[panel["et_date"].astype(str).isin(holdout_dates)].copy()
    discovery_panel = panel[panel["et_date"].astype(str).isin(discovery_dates)].copy()
    seed = replay_bars(holdout_panel, pd.DataFrame(), ReplayConfig(g_ticks=TODAY_G, n_ticks=TODAY_N, x_ticks=TODAY_X))
    session_alerts = pd.concat(
        [
            replay_bars(discovery_panel, pd.DataFrame(), ReplayConfig(g_ticks=TODAY_G, n_ticks=TODAY_N, x_ticks=TODAY_X)).assign(split="discovery"),
            seed.assign(split="holdout") if not seed.empty else pd.DataFrame(),
        ],
        ignore_index=True,
    )
    if not session_alerts.empty:
        session_alerts.to_csv(output_dir / "session_alerts_g2.csv", index=False)
    prints = pd.DataFrame()
    if not args.skip_fetch and not seed.empty:
        unique = seed.drop_duplicates(["decision_ts", "deadline_ts"])
        print(f"Fetching {len(unique)} G=2 holdout alert hours (cached if present)...", flush=True)
        prints = fetch_alert_windows(unique, cache_dir=cache_dir, api_base=args.api_base, holes=holes)
        if not prints.empty:
            prints = prints.sort_values("ts_event").reset_index(drop=True)
        print(f"holdout prints={len(prints)}", flush=True)
    print("Scoring G x N x X grid...", flush=True)
    for split_name, split_dates in (("discovery", discovery_dates), ("holdout", holdout_dates)):
        split_panel = panel[panel["et_date"].astype(str).isin(split_dates)].copy()
        split_prints = prints if split_name == "holdout" else pd.DataFrame()
        for g in G_GRID:
            for n in N_GRID:
                for x in X_GRID:
                    scored = replay_bars(split_panel, split_prints, ReplayConfig(g_ticks=g, n_ticks=n, x_ticks=x))
                    summary = summarize_trades(scored, f"{split_name}_G{g:g}_N{n:g}_X{x:g}")
                    summary.update({
                        "split": split_name,
                        "g": g,
                        "n_tp_ticks": float(n),
                        "n_sl_ticks": float(x),
                        "n_holes": len(holes),
                    })
                    grid_rows.append(summary)
        if split_name == "holdout" and not prints.empty:
            filled = replay_bars(split_panel, prints, primary_cfg)
            clocks = score_clocks_on_trades(filled, prints, primary_cfg)
            if not clocks.empty:
                clock_rows.append(clocks)

    grid = pd.DataFrame(grid_rows)
    grid_path = output_dir / "holdout_grid.csv"
    grid.to_csv(grid_path, index=False)

    clocks = pd.concat(clock_rows, ignore_index=True) if clock_rows else pd.DataFrame()
    if not clocks.empty:
        clocks.to_csv(output_dir / "holdout_clocks_not_mbp1_l1.csv", index=False)

    unfiltered = replay_bars(
        panel[panel["et_date"].astype(str).isin(holdout_dates)],
        prints,
        ReplayConfig(g_ticks=TODAY_G, n_ticks=TODAY_N, x_ticks=TODAY_X, require_agree=False, apply_v1_veto=False),
    )
    vetoed = replay_bars(
        panel[panel["et_date"].astype(str).isin(holdout_dates)],
        prints,
        ReplayConfig(g_ticks=TODAY_G, n_ticks=TODAY_N, x_ticks=TODAY_X, require_agree=True, apply_v1_veto=True),
    )
    rth_holdout = replay_bars(
        panel[panel["et_date"].astype(str).isin(holdout_dates) & panel["is_rth"]],
        prints,
        ReplayConfig(g_ticks=TODAY_G, n_ticks=TODAY_N, x_ticks=TODAY_X, rth_only=True),
    )
    track_d = {
        "unfiltered": summarize_trades(unfiltered, "holdout_session_only"),
        "vetoes": summarize_trades(vetoed, "holdout_session_agree_v1"),
        "rth_primary": summarize_trades(rth_holdout, "holdout_rth_G2_N2_X2"),
        "note": "Official bid/ask when TBBO hours exist; last-print diagnostic otherwise.",
    }
    write_json(output_dir / "track_d_veto_lift.json", track_d)

    primary = grid[
        (grid["split"] == "holdout")
        & (grid["g"] == TODAY_G)
        & (grid["n_tp_ticks"] == TODAY_N)
        & (grid["n_sl_ticks"] == TODAY_X)
    ]
    primary_row = primary.iloc[0].to_dict() if not primary.empty else {}
    ship = bool(primary_row.get("ship_gate", False))
    summary = {
        "ok": True,
        "mode": "holdout",
        "discovery_dates": discovery_dates,
        "holdout_dates": holdout_dates,
        "holes": sorted(holes),
        "primary": primary_row,
        "ship_gate": ship,
        "track_d": track_d,
        "clock_label": CLOCK_LABEL,
        "note": "Do not retune live V2.7. ME final holdout was not opened.",
    }
    write_json(output_dir / "holdout_summary.json", summary)
    _write_holdout_md(output_dir / "holdout_report.md", summary, grid, clocks)
    return summary


def _write_holdout_md(path: Path, summary: dict[str, Any], grid: pd.DataFrame, clocks: pd.DataFrame) -> None:
    lines = [
        "# Executable PnL holdout",
        "",
        "Session-gap entry + agree + V1 aggress veto + price exits. `ship_gate` stays false unless n>=50 and after-cost >= +0.5 ticks.",
        "Print/mid-change clocks are labeled NOT_MBP1_L1. ME V2 final holdout was not opened.",
        "",
        f"Holdout dates: {summary['holdout_dates'][0] if summary['holdout_dates'] else 'none'} to {summary['holdout_dates'][-1] if summary['holdout_dates'] else 'none'}.",
        f"PG holes skipped: {len(summary['holes'])}.",
        f"Primary G=2 N=2 X=2 ship_gate: {summary['ship_gate']}.",
        "",
        "## Grid (holdout)",
        "",
    ]
    hold = grid[grid["split"] == "holdout"] if not grid.empty and "split" in grid.columns else grid
    if hold.empty:
        lines.append("No holdout grid rows.")
    else:
        cols = [c for c in ("slice", "n", "n_filled", "mean_bidask", "mean_after_cost", "mean_last_print", "ship_gate") if c in hold.columns]
        lines.append(_md_table(hold[cols].to_dict("records"), cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_experiment_doc(
    path: Path,
    today: dict[str, Any] | None,
    holdout: dict[str, Any] | None,
    corrections: dict[str, Any] | None = None,
) -> None:
    lines = [
        "# Aggression V2 executable PnL experiments",
        "",
        "Research-only. Live V1 / V1.2 / V2 / V2.1 / V2.2 / V2.3 / V2.4 / V2.6 / V2.7 / V3 were not modified, restarted, or written.",
        "Writes only under `workspace/analysis/aggression_v2_executable_pnl/`.",
        "",
        "Entry and exit **are** session `|residual|/tick >= G`, same-bar B1_W10 sign agree, V1 aggress-with-fade veto, and first of +N / −X / 10m on bid/ask. They are not flags on the live 10m nowcast fade.",
        "",
        "Marks: decision = completed-bar close (`bucket_ts + 1m`). Fill = first TY print `ts_event >= decision`; long pays ask, short sells bid. Round-trip cost = 0.5 tick.",
        "Clocks on AccessAPI TBBO prints/mid changes are labeled `NOT_MBP1_L1`. ME V2 final holdout stays locked.",
        "",
    ]
    if today:
        lines += ["## Today session (2026-08-31 18:00 ET)", "", f"V1 source: `{today.get('v1_source')}`. Prints: {today.get('n_prints')}.", ""]
        lines.append(_md_table(today.get("slices") or [], ["slice", "n", "n_filled", "sum_bidask", "mean_after_cost", "sum_last_print", "n_tp", "n_sl"]))
        lines.append("")
    if holdout:
        lines += ["## Holdout", "", f"ship_gate: `{holdout.get('ship_gate')}`. Holes: {len(holdout.get('holes') or [])}.", ""]
        primary = holdout.get("primary") or {}
        if primary:
            lines.append(_md_table([primary], [c for c in ("slice", "n", "n_filled", "mean_after_cost", "ship_gate") if c in primary]))
        lines.append("")
    lines += [
        "## Isolation",
        "",
        "| Constraint | Status |",
        "| --- | --- |",
        "| Edit live dashboards / ports / *_live | No |",
        "| SOD / Tailscale / Telegram | No |",
        "| Open ME V2 final holdout | No |",
        "| Output dir | `workspace/analysis/aggression_v2_executable_pnl/` |",
        "| Corrections dir | `workspace/analysis/aggression_v2_executable_pnl/corrections/` |",
        "",
        "## Marks (C3)",
        "",
        "Every correction cell emits `pnl_last_print`, `pnl_mid`, and `pnl_bidask_after_cost`. Official rank key is bid/ask after 0.5-tick cost. No cell may pass on last-print alone.",
        "",
        "## Nowcast is not a predictor (C4)",
        "",
        "Track A R² lift vs random walk is ~10⁻⁵. Closed. This study does not fit a new Ridge. Live V2.7 `% via price` (median 0 this session) is the same statement.",
        "",
        "## Summary schema (I8)",
        "",
        "`n_trades` = filled trade count. `n_tp_ticks` / `n_sl_ticks` are barrier parameters. Do not read `n` as the TP grid value.",
        "",
    ]
    if corrections:
        lines += [
            "## Correction cells",
            "",
            f"Holdout ship_gate: `{corrections.get('ship_gate')}`. Live 5063 was not changed.",
            "",
            f"C4 closed: `{json.dumps(corrections.get('C4_nowcast_closed') or {}, default=str)}`.",
            "",
            "L1 autopsy is `workspace/analysis/aggression_v2_executable_pnl/corrections/v27_session_autopsy_20260902.md`.",
            "**not authorization to change 5063.**",
            "",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Session-gap executable PnL research (today + holdout).")
    parser.add_argument("--mode", choices=("today", "probe", "holdout", "corrections", "all"), default="all")
    parser.add_argument("--api-base", default=API_BASE)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--v2-live", default=str(V2_LIVE))
    parser.add_argument("--v27-live", default=str(V27_LIVE))
    parser.add_argument("--g", type=float, default=TODAY_G)
    parser.add_argument("--n", type=float, default=TODAY_N)
    parser.add_argument("--x", type=float, default=TODAY_X)
    parser.add_argument("--fetch-curve", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    today = None
    holdout = None
    corrections = None
    if args.mode == "corrections":
        from scripts.aggression_v2_executable_pnl_corrections import run_corrections

        args.skip_fetch = True
        corrections = run_corrections(args)
        print(json.dumps({
            "corrections_ship_gate": corrections.get("ship_gate"),
            "today_n_prints": (corrections.get("today") or {}).get("n_prints"),
            "holdout_n_prints": (corrections.get("holdout") or {}).get("n_prints"),
            "L1": {k: (corrections.get("L1") or {}).get(k) for k in ("n_trades", "n_z_reset_via_le_0", "n_nowcast_only")},
        }, indent=2, default=str), flush=True)
        doc = PROJECT_ROOT / "docs" / "aggression_v2_executable_pnl_experiments.md"
        write_experiment_doc(doc, None, None, corrections)
        print(f"wrote {doc}", flush=True)
        return
    if args.mode in {"probe", "all"}:
        try:
            probe = probe_ty_date_range(args.api_base)
            probe["ok"] = True
        except Exception as exc:
            probe = {"ok": False, "error": str(exc)}
        write_json(output_dir / "ty_date_range.json", probe)
        print(json.dumps({"probe": {k: probe.get(k) for k in ("ok", "min_date", "max_date", "count", "error")}}, indent=2), flush=True)
    if args.mode in {"today", "all"}:
        today = run_today(args)
        print(json.dumps({"today_slices": today["slices"], "v1_source": today["v1_source"], "n_prints": today["n_prints"]}, indent=2, default=str), flush=True)
    if args.mode in {"holdout", "all"}:
        holdout = run_holdout(args)
        print(json.dumps({"holdout_ship_gate": holdout["ship_gate"], "primary": holdout.get("primary"), "n_holes": len(holdout.get("holes") or [])}, indent=2, default=str), flush=True)
    doc = PROJECT_ROOT / "docs" / "aggression_v2_executable_pnl_experiments.md"
    write_experiment_doc(doc, today, holdout)
    print(f"wrote {doc}", flush=True)


if __name__ == "__main__":
    main()
