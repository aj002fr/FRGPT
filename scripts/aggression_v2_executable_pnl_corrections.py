#!/usr/bin/env python3
"""Frozen one-change-per-cell paper corrections + live V2.7 session autopsy.

Writes only under workspace/analysis/aggression_v2_executable_pnl/corrections/.
Does not import, write, or restart V1–V2.7/V3, SOD, Tailscale, or Telegram.
Live 5063 stays B1_W10. ship_gate stays false unless a holdout cell has
n_filled>=50 and after-cost bid/ask >= +0.5 ticks.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.aggression_dashboard.flow_dominance_context import (
    RAW_TO_DISPLAY,
    WINDOW_MIN,
    _session_expanding_z,
)
from scripts.aggression_dashboard.hf_model import _ols_params
from scripts.aggression_v2_executable_pnl_research import (
    API_BASE,
    EASTERN,
    HOLDOUT_END,
    HOLDOUT_START,
    OUTPUT_DIR,
    RT_COST_TICKS,
    TICK_SIZE,
    TODAY_G,
    TODAY_N,
    TODAY_SESSION_START_ET,
    TODAY_X,
    V27_LIVE,
    V2_LIVE,
    ReplayConfig,
    _utc,
    annotate_correction_features,
    annotate_session_flags,
    fetch_tbbo_chunks,
    filter_session_day,
    load_oos_panel,
    load_signal_logs,
    load_v27_episodes,
    reconstruct_v1_from_feature_matrix,
    reconstruct_v1_from_tbbo,
    replay_bars,
    residual_ticks,
    session_start_et,
    summarize_trades,
    write_json,
)

L1_SESSION_START_ET = "2026-09-01 18:00:00"
V1_LIVE_URL = "http://127.0.0.1:5050/api/aggression"
MID_GRIDS = ((2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (4.0, 2.0))
I5_STAMPS_ET = (
    "2026-09-01 00:15:00",
    "2026-09-01 04:00:00",
    "2026-09-01 12:07:00",
    "2026-09-01 12:15:00",
    "2026-09-01 15:00:00",
)


def corrections_dir(output_dir: Path) -> Path:
    path = output_dir / "corrections"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_cached_prints(cache_dir: Path) -> pd.DataFrame:
    if not cache_dir.exists():
        return pd.DataFrame()
    parts = []
    for path in sorted(cache_dir.glob("*.parquet")):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        if frame.empty or "ts_event" not in frame.columns:
            continue
        frame["ts_event"] = pd.to_datetime(frame["ts_event"], utc=True, errors="coerce")
        parts.append(frame.dropna(subset=["ts_event"]))
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    out = out.drop_duplicates(["ts_event", "price", "size", "side"] if "size" in out.columns else ["ts_event"])
    return out.sort_values("ts_event").reset_index(drop=True)


def persist_fractions(bars: pd.DataFrame, trades: pd.DataFrame, horizons=(30, 60, 120)) -> dict[str, Any]:
    if bars.empty or trades.empty:
        return {"n_alerts": 0}
    indexed = bars.set_index(pd.to_datetime(bars["bucket_ts"], utc=True)).sort_index()
    sess = pd.to_numeric(indexed["session_residual"], errors="coerce") / TICK_SIZE
    rows = []
    for rec in trades.to_dict("records"):
        ts = _utc(rec["decision_bar_ts"])
        if ts not in sess.index:
            near = sess.index.searchsorted(ts)
            if near >= len(sess.index):
                continue
            ts = sess.index[near]
        start_abs = _series_float(sess.loc[ts])
        item = {"decision_bar_ts": str(ts), "start_abs_ticks": start_abs}
        for minutes in horizons:
            future = ts + pd.Timedelta(minutes=int(minutes))
            if future in sess.index:
                val = _series_float(sess.loc[future])
            else:
                later = sess.loc[ts:future]
                val = _series_float(later.iloc[-1]) if not later.empty else np.nan
            item[f"abs_ticks_{minutes}m"] = abs(val) if np.isfinite(val) else None
            item[f"still_ge_g_{minutes}m"] = bool(np.isfinite(val) and abs(val) >= TODAY_G)
            item[f"still_ge_0_{minutes}m"] = bool(np.isfinite(val) and abs(val) > 0)
        rows.append(item)
    frame = pd.DataFrame(rows)
    out: dict[str, Any] = {"n_alerts": int(len(frame))}
    for minutes in horizons:
        col = f"still_ge_g_{minutes}m"
        if col in frame:
            out[f"p_still_ge_g_{minutes}m"] = float(frame[col].mean()) if len(frame) else None
            out[f"p_still_outside_0_{minutes}m"] = float(frame[f"still_ge_0_{minutes}m"].mean()) if len(frame) else None
    return out


def c2_split(trades: pd.DataFrame) -> list[dict[str, Any]]:
    if trades.empty:
        return []
    work = trades.copy()
    work["c2_bucket"] = np.where(
        work.get("v1_state", "") == "QUIET",
        "QUIET",
        np.where(work.get("same_sign_trend", False), "SAME_SIGN_TREND", "OPPOSITE_OR_FLAT"),
    )
    rows = []
    for name, group in work.groupby("c2_bucket", dropna=False):
        summary = summarize_trades(group, str(name))
        summary["v1_states"] = group["v1_state"].value_counts(dropna=False).to_dict() if "v1_state" in group else {}
        longs = group[group.get("side", 0) > 0] if "side" in group else group.iloc[0:0]
        if not longs.empty and (longs.get("v1_state") == "IMPLIED_PASSIVE_DOMINANT").any():
            passive_longs = longs[longs["v1_state"] == "IMPLIED_PASSIVE_DOMINANT"]
            summary["passive_longs"] = summarize_trades(passive_longs, "passive_longs")
        rows.append(summary)
    return rows


def last_print_gap_by_hour(trades: pd.DataFrame) -> list[dict[str, Any]]:
    if trades.empty or "fill_ts" not in trades:
        return []
    work = trades[trades.get("fill_status") == "filled"].copy()
    if work.empty:
        return []
    work["hour_et"] = pd.to_datetime(work["fill_ts"], utc=True).dt.tz_convert(EASTERN).dt.hour
    last = pd.to_numeric(work["pnl_last_print_ticks"], errors="coerce")
    bid = pd.to_numeric(work["pnl_bidask_ticks"], errors="coerce")
    work["last_minus_bidask"] = last - bid
    rows = []
    for hour, group in work.groupby("hour_et"):
        gap = pd.to_numeric(group["last_minus_bidask"], errors="coerce")
        rows.append({
            "hour_et": int(hour),
            "n_filled": int(len(group)),
            "mean_last_minus_bidask": float(gap.mean()) if gap.notna().any() else None,
            "n_sl": int((group["exit_reason"] == "SL").sum()),
            "n_tp": int((group["exit_reason"] == "TP").sum()),
        })
    return rows


def baseline_cfg(**overrides: Any) -> ReplayConfig:
    payload = dict(
        g_ticks=TODAY_G,
        n_ticks=TODAY_N,
        x_ticks=TODAY_X,
        require_agree=True,
        apply_v1_veto=True,
    )
    payload.update(overrides)
    return ReplayConfig(**payload)


def score_cell(name: str, bars: pd.DataFrame, prints: pd.DataFrame, cfg: ReplayConfig) -> dict[str, Any]:
    print(f"scoring {name} bars={len(bars)} prints={len(prints)}", flush=True)
    trades = replay_bars(bars, prints, cfg)
    summary = summarize_trades(trades, name)
    summary["cell"] = name
    summary["official_rank_key"] = "pnl_bidask_after_cost"
    summary["last_print_is_official"] = False
    return {"summary": summary, "trades": trades}


def annotate_contributions_by_group(frame: pd.DataFrame, beta: float, group_col: str) -> pd.DataFrame:
    out = frame.copy()
    grouped = out.groupby(group_col, sort=False)
    out["aggress_contrib_1m"] = float(beta) * grouped["total_aggress"].diff()
    out["passive_contrib_1m"] = grouped["residual"].diff()
    out["delta_price_1m"] = grouped["price"].diff()
    for source, target in (
        ("aggress_contrib_1m", "aggress_window"),
        ("passive_contrib_1m", "passive_window"),
        ("delta_price_1m", "delta_price_window"),
    ):
        out[target] = grouped[source].transform(
            lambda series: series.rolling(WINDOW_MIN, min_periods=WINDOW_MIN).sum()
        )
    out["z_aggress"] = out.groupby(group_col, sort=False)["aggress_window"].transform(_session_expanding_z)
    out["z_passive"] = out.groupby(group_col, sort=False)["passive_window"].transform(_session_expanding_z)
    return out


def stamp_v1_grouped(bars: pd.DataFrame, model_df: pd.DataFrame, beta: float, source: str, group_col: str) -> pd.DataFrame:
    out = bars.copy()
    annotated = annotate_contributions_by_group(model_df, float(beta), group_col)
    annotated["v1_state"] = annotated.get("research_state", pd.Series(index=annotated.index, dtype=str))
    if "research_state" not in annotated.columns:
        from scripts.aggression_dashboard.flow_dominance_context import classify_dominance_state

        annotated["research_state"] = [
            classify_dominance_state(z_a, z_p, d_p)
            for z_a, z_p, d_p in zip(
                annotated["z_aggress"], annotated["z_passive"], annotated["delta_price_window"]
            )
        ]
    annotated["v1_state"] = annotated["research_state"].map(RAW_TO_DISPLAY).fillna("WARMING")
    lookup = annotated[["v1_state", "z_aggress", "z_passive"]].shift(1)
    ts = pd.to_datetime(out["bucket_ts"], utc=True)
    aligned = lookup.reindex(ts)
    out["v1_state"] = aligned["v1_state"].to_numpy()
    out["z_aggress"] = pd.to_numeric(aligned["z_aggress"], errors="coerce").to_numpy()
    out["z_passive"] = pd.to_numeric(aligned["z_passive"], errors="coerce").to_numpy()
    out["v1_source"] = source
    out["v1_state"] = out["v1_state"].fillna("WARMING")
    return out


def i6_calendar_vs_session(bars: pd.DataFrame) -> dict[str, Any]:
    """Compare expanding-z grouped by calendar et_date vs 18:00 session_id."""
    work = annotate_session_flags(bars.copy())
    if "agg_1m" not in work.columns and "price" not in work.columns:
        return {"ok": False, "reason": "need price/aggress series"}
    work = work.sort_values("bucket_ts")
    work["session_id"] = work["bucket_ts"].map(lambda ts: session_start_et(ts).isoformat())
    if "agg_1m" in work.columns:
        work["total_aggress"] = work.groupby("et_date", sort=False)["agg_1m"].cumsum()
    else:
        work["total_aggress"] = np.nan
    if work["total_aggress"].isna().all():
        return {"ok": False, "reason": "no aggress series on today tape"}
    alpha, beta, reason = _ols_params(work["total_aggress"], work["price"])
    if reason or not np.isfinite(beta):
        return {"ok": False, "reason": str(reason or "beta")}
    model = pd.DataFrame(
        {
            "total_aggress": work["total_aggress"].to_numpy(),
            "residual": (work["price"] - (alpha + beta * work["total_aggress"])).to_numpy(),
            "price": work["price"].to_numpy(),
            "et_date": work["et_date"].to_numpy(),
            "session_id": work["session_id"].to_numpy(),
        },
        index=pd.DatetimeIndex(pd.to_datetime(work["bucket_ts"], utc=True)),
    )
    cal = stamp_v1_grouped(work, model, float(beta), "CALENDAR_ET_DATE", "et_date")
    sess = stamp_v1_grouped(work, model, float(beta), "SESSION_1800", "session_id")
    et = pd.to_datetime(work["bucket_ts"], utc=True).dt.tz_convert(EASTERN)
    overnight = (et.dt.hour < 8) | (et.dt.hour >= 18)
    compare = pd.DataFrame({
        "bucket_ts": work["bucket_ts"],
        "et_hour": et.dt.hour,
        "calendar_state": cal["v1_state"],
        "session_state": sess["v1_state"],
        "calendar_z_passive": cal["z_passive"],
        "session_z_passive": sess["z_passive"],
        "overnight": overnight.to_numpy(),
    })
    flips = compare[compare["calendar_state"] != compare["session_state"]]
    overnight_flips = flips[flips["overnight"]]
    ready = compare[
        compare["calendar_state"].isin(["AGGRESS_DOMINANT", "IMPLIED_PASSIVE_DOMINANT", "BALANCED", "QUIET"])
        | compare["session_state"].isin(["AGGRESS_DOMINANT", "IMPLIED_PASSIVE_DOMINANT", "BALANCED", "QUIET"])
    ]
    ready_flips = ready[ready["calendar_state"] != ready["session_state"]]
    return {
        "ok": True,
        "n_bars": int(len(compare)),
        "n_ready": int(len(ready)),
        "n_disagree": int(len(flips)),
        "n_ready_disagree": int(len(ready_flips)),
        "n_overnight_disagree": int(len(overnight_flips)),
        "overnight_flip_share": float(len(overnight_flips) / max(1, int(overnight.sum()))),
        "use_session_id_for_i7": bool(len(ready_flips) > 0 or len(overnight_flips) > 0),
        "sample": compare.loc[overnight].head(8).to_dict("records"),
    }


def i5_disagreement(today_bars: pd.DataFrame, cache_dir: Path, fetch_curve: bool) -> dict[str, Any]:
    ty_only = today_bars[["bucket_ts", "v1_state", "z_aggress", "z_passive", "v1_source"]].copy() if "v1_state" in today_bars else pd.DataFrame()
    curve_stamp = pd.DataFrame()
    if fetch_curve:
        start = pd.Timestamp(TODAY_SESSION_START_ET, tz=EASTERN).tz_convert("UTC")
        end = start + pd.Timedelta(days=1)
        curve = {}
        for symbol in ("TY", "FV", "TU", "US"):
            cached = load_cached_prints(cache_dir / symbol)
            if cached.empty:
                continue
            curve[symbol] = cached.rename(columns={"ts_event": "ts"})
        if curve:
            curve_stamp = reconstruct_v1_from_tbbo(curve, today_bars)
    live_rows = []
    for stamp in I5_STAMPS_ET:
        as_of = pd.Timestamp(stamp, tz=EASTERN)
        live_rows.append(post_v1_historical(as_of))
    live = pd.DataFrame(live_rows)
    rows = []
    for stamp in I5_STAMPS_ET:
        ts = pd.Timestamp(stamp, tz=EASTERN).tz_convert("UTC").floor("min")
        bar_ts = ts - pd.Timedelta(minutes=1)
        item = {"stamp_et": stamp, "bar_ts": str(bar_ts)}
        if not ty_only.empty:
            match = today_bars.loc[pd.to_datetime(today_bars["bucket_ts"], utc=True) == bar_ts]
            if not match.empty:
                item["ty_state"] = match.iloc[0].get("v1_state")
                item["ty_z_passive"] = _safe(match.iloc[0].get("z_passive"))
        if not curve_stamp.empty:
            match = curve_stamp.loc[pd.to_datetime(curve_stamp["bucket_ts"], utc=True) == bar_ts]
            if not match.empty:
                item["curve_state"] = match.iloc[0].get("v1_state")
                item["curve_z_passive"] = _safe(match.iloc[0].get("z_passive"))
        live_match = live[live.get("stamp_et") == stamp] if not live.empty and "stamp_et" in live else pd.DataFrame()
        if not live_match.empty:
            item["live5050_state"] = live_match.iloc[0].get("state")
            item["live5050_z_passive"] = live_match.iloc[0].get("z_passive")
            item["live5050_error"] = live_match.iloc[0].get("error")
        rows.append(item)
    return {
        "ok": True,
        "note": "One-off diagnosis. Not posted inside the holdout loop.",
        "rows": rows,
        "live_5050_ok": bool(live["ok"].any()) if not live.empty and "ok" in live else False,
    }


def post_v1_historical(as_of: pd.Timestamp) -> dict[str, Any]:
    start = session_start_et(as_of)
    payload = {
        "mode": "historical",
        "price_symbol": "TY",
        "start_iso": start.tz_convert(EASTERN).isoformat(),
        "end_iso": as_of.tz_convert(EASTERN).isoformat(),
        "lookback_hours": 16,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        V1_LIVE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            raw = json.loads(response.read().decode("utf-8"))
        dom = raw.get("flow_dominance") or {}
        passive = (dom.get("implied_passive") or {})
        return {
            "ok": True,
            "stamp_et": as_of.tz_convert(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
            "state": dom.get("state") or dom.get("status"),
            "z_passive": passive.get("z"),
            "z_passive_trend": passive.get("trend"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "stamp_et": as_of.tz_convert(EASTERN).strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(exc),
        }


def _safe(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _series_float(value: Any) -> float:
    if isinstance(value, pd.Series):
        value = value.iloc[-1]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number


def run_tape(name: str, bars: pd.DataFrame, prints: pd.DataFrame) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    baseline = score_cell("baseline_G2_agree_aggress_plus2_minus2", bars, prints, baseline_cfg())
    cells["baseline"] = baseline["summary"]
    persist = persist_fractions(bars, baseline["trades"])
    cells["C1_persist"] = persist
    for label, cfg in (
        ("C1_G_and_peak", baseline_cfg(require_peak_shrink=True)),
        ("C1_G_and_z", baseline_cfg(require_session_z=1.5, g_max_ticks=6.0)),
        ("C1_RTH_only", baseline_cfg(rth_only=True)),
    ):
        cells[label] = score_cell(label, bars, prints, cfg)["summary"]
    c2 = score_cell("C2_same_sign_increasing_veto", bars, prints, baseline_cfg(apply_c2_veto=True))
    cells["C2"] = c2["summary"]
    cells["C2_split"] = c2_split(baseline["trades"])
    cells["C3_last_print_gap_by_hour"] = last_print_gap_by_hour(baseline["trades"])
    for sl_mid, tp_mid in MID_GRIDS:
        label = f"I1_mid_SL{sl_mid:g}_TP{tp_mid:g}"
        cells[label] = score_cell(
            label,
            bars,
            prints,
            baseline_cfg(n_ticks=tp_mid, x_ticks=sl_mid, exit_space="mid"),
        )["summary"]
    cells["I2_no_same_sign_reentry"] = score_cell(
        "I2_no_same_sign_reentry", bars, prints, baseline_cfg(block_same_sign_reentry=True),
    )["summary"]
    cells["I2_cooldown_15m"] = score_cell(
        "I2_cooldown_15m", bars, prints, baseline_cfg(cooldown_minutes=15.0),
    )["summary"]
    for minutes in (1, 5):
        label = f"I3_minhold_{minutes}m"
        cells[label] = score_cell(
            label,
            bars,
            prints,
            baseline_cfg(min_hold_minutes_tp=minutes, min_hold_minutes_sl=minutes),
        )["summary"]
    for mode in ("first_print", "twap_5s", "next_bar"):
        label = f"I4_fill_{mode}"
        cells[label] = score_cell(label, bars, prints, baseline_cfg(fill_mode=mode))["summary"]
    cells["I7_passive_increasing"] = score_cell(
        "I7_passive_increasing", bars, prints, baseline_cfg(apply_passive_increasing_veto=True),
    )["summary"]
    cells["I7_balanced_two_leg_diag"] = score_cell(
        "I7_balanced_two_leg_diag", bars, prints, baseline_cfg(apply_balanced_two_leg_veto=True),
    )["summary"]
    cells["combo_I1_mid32_I2_I7"] = score_cell(
        "combo_I1_mid32_I2_I7",
        bars,
        prints,
        baseline_cfg(
            n_ticks=2.0,
            x_ticks=3.0,
            exit_space="mid",
            block_same_sign_reentry=True,
            apply_passive_increasing_veto=True,
        ),
    )["summary"]
    ship = any(bool(item.get("ship_gate")) for item in cells.values() if isinstance(item, dict))
    return {
        "tape": name,
        "n_bars": int(len(bars)),
        "n_prints": int(len(prints)),
        "cells": cells,
        "ship_gate": bool(ship),
        "baseline_trades": baseline["trades"],
    }


def write_corrections_md(path: Path, today: dict[str, Any], holdout: dict[str, Any]) -> None:
    keys = [
        "cell", "n_trades", "n_filled", "mean_bidask", "mean_after_cost", "mean_last_print",
        "mean_mid", "n_tp", "n_sl", "p_tp_before_sl", "mean_time_in_market_sec", "ship_gate",
    ]

    def rows(bundle: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for name, item in (bundle.get("cells") or {}).items():
            if isinstance(item, dict) and "n_filled" in item:
                row = dict(item)
                row.setdefault("cell", name)
                out.append(row)
        return out

    lines = [
        "# Executable PnL correction cells",
        "",
        "Research-only. One frozen change per cell. Official rank is bid/ask after 0.5-tick cost.",
        "Last-print is never official. Live V2.7 on 5063 was not retuned.",
        "",
        "## Today paper session (2026-08-31 18:00 ET)",
        "",
        _table(rows(today), keys),
        "",
        "C1 persist (fraction still `|session ticks| >= 2`): "
        + json.dumps(today.get("cells", {}).get("C1_persist") or {}, default=str),
        "",
        "## Holdout",
        "",
        _table(rows(holdout), keys),
        "",
        f"Any holdout ship_gate: `{holdout.get('ship_gate')}`.",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_none_"
    use = [c for c in cols if any(c in row for row in rows)]
    header = "| " + " | ".join(use) + " |"
    sep = "| " + " | ".join("---" for _ in use) + " |"
    body = []
    for row in rows:
        body.append("| " + " | ".join(_fmt(row.get(c)) for c in use) + " |")
    return "\n".join([header, sep, *body])


def _fmt(value: Any) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return ""
    if isinstance(value, float):
        return f"{value:.3f}"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def run_l1_autopsy(out_dir: Path) -> dict[str, Any]:
    start = pd.Timestamp(L1_SESSION_START_ET, tz=EASTERN)
    end = start + pd.Timedelta(days=1)
    episodes = load_v27_episodes(V27_LIVE / "alert_episodes.json")
    if episodes.empty:
        payload = {"ok": False, "reason": "no episodes"}
        (out_dir / "v27_session_autopsy_20260902.md").write_text(
            "# V2.7 session autopsy 2026-09-02\n\nNo episodes.\n\n**not authorization to change 5063.**\n",
            encoding="utf-8",
        )
        return payload
    work = episodes.copy()
    work["opened_ts"] = pd.to_datetime(work["opened_ts"], utc=True, errors="coerce")
    day = work.loc[(work["opened_ts"] >= start.tz_convert("UTC")) & (work["opened_ts"] < end.tz_convert("UTC"))].copy()
    logs = load_signal_logs(V2_LIVE, V27_LIVE, ["2026-09-01", "2026-09-02"])
    logs = filter_session_day(logs, L1_SESSION_START_ET) if not logs.empty else logs
    if not logs.empty:
        ty_live = load_cached_prints(OUTPUT_DIR / "tbbo_cache" / "TY")
        if not ty_live.empty:
            logs = reconstruct_v1_from_tbbo({"TY": ty_live.rename(columns={"ts_event": "ts"})}, logs)
        logs = annotate_correction_features(logs)
    indexed = logs.set_index(pd.to_datetime(logs["bucket_ts"], utc=True)) if not logs.empty else pd.DataFrame()
    rows = []
    for rec in day.to_dict("records"):
        opened = _utc(rec["opened_ts"]).floor("min")
        bar = None
        if not indexed.empty:
            if opened in indexed.index:
                bar = indexed.loc[opened]
            elif (opened - pd.Timedelta(minutes=1)) in indexed.index:
                bar = indexed.loc[opened - pd.Timedelta(minutes=1)]
            if isinstance(bar, pd.DataFrame):
                bar = bar.iloc[-1]
        session_ticks = residual_ticks(float(bar["session_residual"])) if bar is not None and pd.notna(bar.get("session_residual")) else None
        local_z = _safe(bar.get("local_residual_z")) if bar is not None else _safe(rec.get("open_residual_z"))
        agree = None
        if bar is not None:
            agree = bool(np.sign(float(bar.get("session_residual") or 0)) == np.sign(float(bar.get("local_residual") or 0)))
        rows.append({
            "opened_ts": rec["opened_ts"].isoformat() if hasattr(rec["opened_ts"], "isoformat") else str(rec["opened_ts"]),
            "direction": rec.get("direction"),
            "exit_reason": rec.get("exit_reason"),
            "dir_pnl_trade_ticks": rec.get("dir_pnl_trade_ticks"),
            "price_share_of_reversion": rec.get("price_share_of_reversion"),
            "local_z": local_z,
            "session_ticks": session_ticks,
            "agree": agree,
            "v1_state": None if bar is None else bar.get("v1_state"),
            "z_passive": None if bar is None else _safe(bar.get("z_passive")),
            "z_passive_trend": None if bar is None else bar.get("z_passive_trend"),
            "nowcast_only": bool(local_z is not None and abs(local_z) >= 1.5 and (session_ticks is None or abs(session_ticks) < 2)),
        })
    frame = pd.DataFrame(rows)
    z_reset = frame[frame["exit_reason"] == "Z_RESET"] if not frame.empty else frame
    via = pd.to_numeric(frame.get("price_share_of_reversion"), errors="coerce") if not frame.empty else pd.Series(dtype=float)
    z_via = pd.to_numeric(z_reset.get("price_share_of_reversion"), errors="coerce") if not z_reset.empty else pd.Series(dtype=float)
    shorts = frame[frame["direction"].astype(str).str.contains("SHORT", na=False)] if not frame.empty else frame
    short_rich_inc = shorts[
        (pd.to_numeric(shorts.get("session_ticks"), errors="coerce") > 0)
        & (shorts.get("z_passive_trend") == "INCREASING")
    ] if not shorts.empty else shorts
    lines = [
        "# V2.7 session autopsy 2026-09-02",
        "",
        "Read-only join of live episodes to V2 session residual and a one-off V1 completed-bar stamp.",
        "",
        f"Session start: `{L1_SESSION_START_ET}` ET. Trades in window: {len(frame)}.",
        f"Sum dir ticks: {_fmt(pd.to_numeric(frame.get('dir_pnl_trade_ticks'), errors='coerce').sum() if not frame.empty else None)}.",
        f"Z_RESET: {int((frame['exit_reason']=='Z_RESET').sum()) if not frame.empty else 0}. FLIP: {int((frame['exit_reason']=='FLIP').sum()) if not frame.empty else 0}.",
        f"Median % via price: {_fmt(via.median() if via.notna().any() else None)}.",
        f"Z_RESET with % via price <= 0 (FV chased): {int((z_via <= 0).sum()) if z_via.notna().any() else 0} / {int(len(z_reset))}.",
        f"Shorts that were session-rich and V1 passive INCREASING: {int(len(short_rich_inc))}.",
        f"Local |z|>=1.5 while |session ticks|<2 (nowcast-only): {int(frame['nowcast_only'].sum()) if not frame.empty else 0}.",
        "",
        _table(frame.to_dict("records"), [
            "opened_ts", "direction", "exit_reason", "dir_pnl_trade_ticks",
            "price_share_of_reversion", "local_z", "session_ticks", "agree",
            "v1_state", "z_passive_trend", "nowcast_only",
        ]),
        "",
        "**not authorization to change 5063.**",
        "",
    ]
    lead = None
    if not frame.empty:
        worst = frame.copy()
        worst["_pnl"] = pd.to_numeric(worst["dir_pnl_trade_ticks"], errors="coerce")
        if worst["_pnl"].notna().any():
            lead = worst.loc[worst["_pnl"].idxmin()]
            as_of = _utc(lead["opened_ts"])
            live = post_v1_historical(as_of.tz_convert(EASTERN))
            lines += [
                "",
                f"One-off 5050 historical POST at worst trade `{lead['opened_ts']}`: "
                + json.dumps(live, default=str),
                "",
            ]
    path = out_dir / "v27_session_autopsy_20260902.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    frame.to_csv(out_dir / "v27_session_autopsy_20260902.csv", index=False)
    return {
        "ok": True,
        "n_trades": int(len(frame)),
        "n_z_reset_via_le_0": int((z_via <= 0).sum()) if z_via.notna().any() else 0,
        "n_nowcast_only": int(frame["nowcast_only"].sum()) if not frame.empty else 0,
        "n_short_rich_passive_increasing": int(len(short_rich_inc)),
        "path": str(path),
        "not_authorization_to_change_5063": True,
    }


def run_corrections(args: Any) -> dict[str, Any]:
    output_dir = Path(getattr(args, "output_dir", OUTPUT_DIR))
    out_dir = corrections_dir(output_dir)
    cache_dir = output_dir / "tbbo_cache"

    today_bars = load_signal_logs(Path(getattr(args, "v2_live", V2_LIVE)), Path(getattr(args, "v27_live", V27_LIVE)), ["2026-08-31", "2026-09-01"])
    today_bars = filter_session_day(today_bars, TODAY_SESSION_START_ET)
    today_prints = load_cached_prints(cache_dir / "TY")
    if today_prints.empty and not getattr(args, "skip_fetch", True):
        start = pd.Timestamp(TODAY_SESSION_START_ET, tz=EASTERN).tz_convert("UTC")
        today_prints = fetch_tbbo_chunks(start, start + pd.Timedelta(days=1), symbol="TY", cache_dir=cache_dir / "TY", api_base=getattr(args, "api_base", API_BASE))
    if not today_prints.empty:
        today_bars = reconstruct_v1_from_tbbo({"TY": today_prints.rename(columns={"ts_event": "ts"})}, today_bars)
    today_bundle = run_tape("today_20260831_1800", today_bars, today_prints)
    today_bundle["baseline_trades"].to_csv(out_dir / "today_baseline_trades.csv", index=False)

    panel = load_oos_panel()
    panel = reconstruct_v1_from_feature_matrix(panel)
    holdout_dates = [d for d in sorted(panel["et_date"].astype(str).unique()) if HOLDOUT_START <= d <= HOLDOUT_END]
    holdout_panel = panel[panel["et_date"].astype(str).isin(holdout_dates)].copy()
    holdout_prints = load_cached_prints(cache_dir / "holdout")
    holdout_bundle = run_tape("holdout", holdout_panel, holdout_prints)
    holdout_bundle["baseline_trades"].to_csv(out_dir / "holdout_baseline_trades.csv", index=False)

    i5 = i5_disagreement(today_bars, cache_dir, bool(getattr(args, "fetch_curve", False)))
    i6 = i6_calendar_vs_session(today_bars)
    five = holdout_panel[holdout_panel["et_date"].astype(str).isin(holdout_dates[:5])].copy()
    i6_holdout = i6_calendar_vs_session(five) if not five.empty else {"ok": False, "reason": "no holdout sample"}

    l1 = run_l1_autopsy(out_dir)
    write_corrections_md(out_dir / "corrections_report.md", today_bundle, holdout_bundle)

    today_cells = {k: v for k, v in today_bundle["cells"].items() if not isinstance(v, pd.DataFrame)}
    holdout_cells = {k: v for k, v in holdout_bundle["cells"].items() if not isinstance(v, pd.DataFrame)}
    summary = {
        "ok": True,
        "mode": "corrections",
        "today": {"n_bars": today_bundle["n_bars"], "n_prints": today_bundle["n_prints"], "cells": today_cells, "ship_gate": today_bundle["ship_gate"]},
        "holdout": {"n_bars": holdout_bundle["n_bars"], "n_prints": holdout_bundle["n_prints"], "cells": holdout_cells, "ship_gate": holdout_bundle["ship_gate"]},
        "I5": i5,
        "I6_today": i6,
        "I6_holdout_sample": i6_holdout,
        "C4_nowcast_closed": {
            "track_a_r2_lift_vs_rw": 1e-5,
            "new_ridge_fit": False,
            "note": "Track A R2 lift vs random walk ~10^-5 is closed. Do not add another Ridge.",
        },
        "L1": l1,
        "ship_gate": False,
        "not_authorization_to_change_5063": True,
    }
    if holdout_bundle["ship_gate"]:
        summary["ship_gate"] = True
    write_json(out_dir / "corrections_summary.json", summary)
    return summary


# Late import alias so corrections_dir helper and OUTPUT_DIR stay consistent.
CORRECTIONS_DIR = OUTPUT_DIR / "corrections"
