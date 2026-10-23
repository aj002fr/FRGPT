#!/usr/bin/env python3
"""Read-only V2 convergence / continuation / formation research.

Hard isolation:
- Does NOT import or modify scripts/aggression_dashboard(_v2|_v3)
- Does NOT write to aggression_v2_live / aggression_v3_live
- Does NOT touch ports 5050/5052/5053 or SOD launchers
- Writes only under workspace/analysis/aggression_v2_convergence/
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
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
HORIZONS = (15, 30, 60)
Z_THRESHOLDS = (1.5, 2.0, 2.5)
FORMATION_WINDOW = 10
EVENT_BLACKOUT_MINUTES = 10
# Cash-hours style RTH for rates futures (ET)
RTH_START_MIN = 8 * 60 + 0  # 08:00
RTH_END_MIN = 15 * 60 + 0  # 15:00

V2_LIVE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_live"
WALKFORWARD = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_convergence"


def _safe_mean(s: pd.Series) -> float:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(s.mean()) if len(s) else float("nan")


def _safe_median(s: pd.Series) -> float:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(s.median()) if len(s) else float("nan")


def _as_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True, format="mixed")


def _et_minutes(ts: pd.Series) -> pd.Series:
    et = ts.dt.tz_convert(EASTERN)
    return et.dt.hour * 60 + et.dt.minute


def _is_rth(ts: pd.Series) -> pd.Series:
    mins = _et_minutes(ts)
    return (mins >= RTH_START_MIN) & (mins < RTH_END_MIN)


def _chronological_split(dates: list[str], train_frac: float = 0.7) -> tuple[list[str], list[str]]:
    ordered = sorted(d for d in dates if d)
    if len(ordered) <= 1:
        return ordered, []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_frac)))
    return ordered[:cut], ordered[cut:]


def load_event_times(db_path: Path) -> pd.DataFrame:
    """Load US event timestamps from economic_events (read-only)."""
    if not db_path.exists():
        return pd.DataFrame(columns=["event_ts", "et_date"])
    try:
        with sqlite3.connect(str(db_path)) as conn:
            rows = pd.read_sql_query(
                """
                SELECT event_date
                FROM economic_events
                WHERE country LIKE '%United States%' OR country = 'US' OR country IS NULL
                """,
                conn,
            )
    except sqlite3.Error:
        return pd.DataFrame(columns=["event_ts", "et_date"])
    if rows.empty:
        return pd.DataFrame(columns=["event_ts", "et_date"])
    ts = pd.to_datetime(rows["event_date"], errors="coerce")
    # naive timestamps treated as Eastern wall clock
    aware = []
    for t in ts:
        if pd.isna(t):
            continue
        t = pd.Timestamp(t)
        if t.tzinfo is None:
            t = t.tz_localize(EASTERN, ambiguous="NaT", nonexistent="NaT")
        else:
            t = t.tz_convert(EASTERN)
        if pd.isna(t):
            continue
        aware.append(t)
    if not aware:
        return pd.DataFrame(columns=["event_ts", "et_date"])
    out = pd.DataFrame({"event_ts": aware})
    out["et_date"] = out["event_ts"].dt.strftime("%Y-%m-%d")
    out["event_ts_utc"] = out["event_ts"].dt.tz_convert("UTC")
    return out


def mark_event_blackout(ts_utc: pd.Series, events: pd.DataFrame, minutes: int = EVENT_BLACKOUT_MINUTES) -> pd.Series:
    if events.empty or ts_utc.empty:
        return pd.Series(False, index=ts_utc.index)
    event_ns = (
        pd.to_datetime(events["event_ts_utc"], utc=True)
        .dropna()
        .dt.tz_convert("UTC")
        .dt.tz_localize(None)
        .sort_values()
        .to_numpy(dtype="datetime64[ns]")
    )
    vals = pd.to_datetime(ts_utc, utc=True).dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(dtype="datetime64[ns]")
    idx = np.searchsorted(event_ns, vals)
    blackout = np.zeros(len(vals), dtype=bool)
    window = np.timedelta64(int(minutes), "m")
    for i, v in enumerate(vals):
        if np.isnat(v):
            continue
        for j in (idx[i] - 1, idx[i]):
            if 0 <= j < len(event_ns) and not np.isnat(event_ns[j]):
                if abs(v - event_ns[j]) <= window:
                    blackout[i] = True
                    break
    return pd.Series(blackout, index=ts_utc.index)


def load_walkforward(path: Path, model: str = "ridge") -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    if "model" in df.columns:
        df = df[df["model"] == model].copy()
    df = df.sort_values("bucket_ts").drop_duplicates("bucket_ts", keep="last")
    df["et_date"] = df["et_date"].astype(str) if "et_date" in df else df["bucket_ts"].dt.tz_convert(EASTERN).dt.strftime("%Y-%m-%d")
    # Causal residual z within each ET day (expanding) — research proxy for live prior_blend
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_values("bucket_ts").copy()
        resid = d["residual"].astype(float)
        mu = resid.expanding(min_periods=30).mean()
        sd = resid.expanding(min_periods=30).std(ddof=1).replace(0.0, np.nan)
        d["residual_z"] = (resid - mu) / sd
        parts.append(d)
    out = pd.concat(parts, ignore_index=True)
    out = out.set_index("bucket_ts").sort_index()
    return out


def load_signal_logs(live_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(glob.glob(str(live_dir / "signal_log_*.csv"))):
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = _as_utc(df["ts"])
    df = df.dropna(subset=["ts"]).sort_values("ts").drop_duplicates("ts", keep="last")
    df["et_date"] = df["ts"].dt.tz_convert(EASTERN).dt.strftime("%Y-%m-%d")
    df = df.set_index("ts").sort_index()
    return df


def load_alert_log(live_dir: Path) -> pd.DataFrame:
    path = live_dir / "alert_log.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["opened_ts"] = _as_utc(df["opened_ts"])
    df["et_date"] = df["et_date"].astype(str)
    return df


def annotate_formation(df: pd.DataFrame, window: int = FORMATION_WINDOW) -> pd.DataFrame:
    """Tag price-led vs flow-led residual formation over prior window."""
    out = df.copy()
    price = out["price"].astype(float)
    fv = out["fair_value"].astype(float)
    out["d_price"] = price.diff(window)
    out["d_fv"] = fv.diff(window)
    out["d_resid"] = out["d_price"] - out["d_fv"]
    # Magnitudes in ticks
    out["d_price_ticks"] = out["d_price"] / TICK_SIZE
    out["d_fv_ticks"] = out["d_fv"] / TICK_SIZE
    abs_p = out["d_price"].abs()
    abs_f = out["d_fv"].abs()
    # Price-led: price move dominates FV move and residual change aligns with price
    price_led = (abs_p >= abs_f * 1.25) & (abs_p > EPS) & (np.sign(out["d_price"]) == np.sign(out["d_resid"]))
    flow_led = (abs_f >= abs_p * 1.25) & (abs_f > EPS) & (np.sign(-out["d_fv"]) == np.sign(out["d_resid"]))
    mixed = ~(price_led | flow_led)
    out["formation"] = "MIXED"
    out.loc[price_led.fillna(False), "formation"] = "PRICE_LED"
    out.loc[flow_led.fillna(False), "formation"] = "FLOW_LED"
    out.loc[mixed.fillna(False) & out["d_resid"].isna(), "formation"] = "UNKNOWN"
    return out


def add_forward_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Add forward price changes and fade/follow PnL in ticks (gross)."""
    parts = []
    for _, day in df.groupby("et_date", sort=False):
        d = day.sort_index().copy()
        resid = d["residual"].astype(float)
        # Positive residual => price above FV => fade = short = -price change
        fade_sign = -np.sign(resid.replace(0, np.nan))
        follow_sign = np.sign(resid.replace(0, np.nan))
        for h in HORIZONS:
            fut = d["price"].shift(-h)
            chg = fut - d["price"]
            chg_ticks = chg / TICK_SIZE
            d[f"future_change_ticks_{h}m"] = chg_ticks
            fade = fade_sign * chg_ticks
            follow = follow_sign * chg_ticks
            d[f"fade_pnl_ticks_{h}m"] = fade
            d[f"follow_pnl_ticks_{h}m"] = follow
            d[f"fade_hit_{h}m"] = fade > 0
            d[f"follow_hit_{h}m"] = follow > 0
            # Adverse for follow: worst adverse move against follow over horizon (approx using end if path unavailable)
            # Use min of cumulative path if we can; else use negative part of end PnL as proxy
            # Path-based adverse: rolling min of signed path — approximate with end for speed
            d[f"follow_adverse_ticks_{h}m"] = (-follow).clip(lower=0.0)
            d[f"fade_adverse_ticks_{h}m"] = (-fade).clip(lower=0.0)
        parts.append(d)
    return pd.concat(parts).sort_index()


def _cell_stats(sub: pd.DataFrame, side: str, horizon: int, split: str, label: str, **extra: Any) -> dict[str, Any]:
    pnl_col = f"{side}_pnl_ticks_{horizon}m"
    hit_col = f"{side}_hit_{horizon}m"
    adv_col = f"{side}_adverse_ticks_{horizon}m"
    pnl = pd.to_numeric(sub[pnl_col], errors="coerce") if pnl_col in sub else pd.Series(dtype=float)
    valid = pnl.dropna()
    n = int(len(valid))
    mean_pnl = float(valid.mean()) if n else float("nan")
    med_pnl = float(valid.median()) if n else float("nan")
    hit = _safe_mean(sub[hit_col].astype(float)) if hit_col in sub and n else float("nan")
    adv = _safe_median(sub[adv_col]) if adv_col in sub else float("nan")
    return {
        "split": split,
        "label": label,
        "side": side,
        "horizon_m": int(horizon),
        "n": n,
        "hit_rate": hit,
        "mean_pnl_ticks": mean_pnl,
        "median_pnl_ticks": med_pnl,
        "mean_pnl_after_cost_ticks": (mean_pnl - COST_TICKS) if n else float("nan"),
        "median_adverse_ticks": adv,
        **extra,
    }


def score_threshold_table(df: pd.DataFrame, split: str, mask: pd.Series | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = df if mask is None else df[mask]
    for zmin in Z_THRESHOLDS:
        sub = base[base["residual_z"].abs() >= zmin]
        for h in HORIZONS:
            for side in ("fade", "follow"):
                rows.append(
                    _cell_stats(
                        sub,
                        side,
                        h,
                        split,
                        f"|z|>={zmin}",
                        z_threshold=float(zmin),
                        formation="ALL",
                    )
                )
    return rows


def score_formation_table(df: pd.DataFrame, split: str, mask: pd.Series | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = df if mask is None else df[mask]
    for zmin in Z_THRESHOLDS:
        for formation in ("PRICE_LED", "FLOW_LED", "MIXED"):
            sub = base[(base["residual_z"].abs() >= zmin) & (base["formation"] == formation)]
            for h in HORIZONS:
                for side in ("fade", "follow"):
                    rows.append(
                        _cell_stats(
                            sub,
                            side,
                            h,
                            split,
                            f"{formation}|z|>={zmin}",
                            z_threshold=float(zmin),
                            formation=formation,
                        )
                    )
    return rows


def score_alert_episodes(alerts: pd.DataFrame, split: str, holdout_dates: set[str] | None = None) -> list[dict[str, Any]]:
    """Episode-level fade vs follow using logged dir_pnl (fade framing)."""
    if alerts.empty:
        return []
    df = alerts.copy()
    if holdout_dates is not None:
        df = df[df["et_date"].isin(holdout_dates)]
    rows = []
    for zmin in Z_THRESHOLDS:
        sub = df[pd.to_numeric(df["open_residual_z"], errors="coerce").abs() >= zmin]
        for h in (15, 30):
            fade_col = f"dir_pnl_{h}m"
            adv_col = f"adverse_{h}m"
            if fade_col not in sub:
                continue
            fade_ticks = pd.to_numeric(sub[fade_col], errors="coerce") / TICK_SIZE
            follow_ticks = -fade_ticks
            n = int(fade_ticks.dropna().shape[0])
            for side, pnl in (("fade", fade_ticks), ("follow", follow_ticks)):
                valid = pnl.dropna()
                mean_pnl = float(valid.mean()) if len(valid) else float("nan")
                adv = pd.to_numeric(sub[adv_col], errors="coerce") / TICK_SIZE if adv_col in sub else pd.Series(dtype=float)
                rows.append(
                    {
                        "split": split,
                        "source": "alert_log",
                        "label": f"episode_|z|>={zmin}",
                        "side": side,
                        "horizon_m": h,
                        "n": n,
                        "hit_rate": float((valid > 0).mean()) if len(valid) else float("nan"),
                        "mean_pnl_ticks": mean_pnl,
                        "median_pnl_ticks": float(valid.median()) if len(valid) else float("nan"),
                        "mean_pnl_after_cost_ticks": (mean_pnl - COST_TICKS) if len(valid) else float("nan"),
                        "median_adverse_ticks": _safe_median(adv) if side == "fade" else _safe_median((-follow_ticks).clip(lower=0)),
                        "z_threshold": float(zmin),
                        "mae_to_resolve_ticks_median": _safe_median(sub.get("mae_to_resolve_ticks", pd.Series(dtype=float))),
                    }
                )
    # price_share coverage caveat stats
    share = pd.to_numeric(df.get("price_share_of_reversion", pd.Series(dtype=float)), errors="coerce")
    rows.append(
        {
            "split": split,
            "source": "alert_log",
            "label": "price_share_coverage",
            "side": "meta",
            "horizon_m": 0,
            "n": int(len(df)),
            "n_with_price_share": int(share.notna().sum()),
            "price_share_median": _safe_median(share),
            "price_share_gt_0_5_rate": _safe_mean((share > 0.5).astype(float)),
            "price_share_lt_0_5_rate": _safe_mean((share < 0.5).astype(float)),
            "reversion_status_counts": df["reversion_status"].value_counts().to_dict() if "reversion_status" in df else {},
        }
    )
    return rows


def evaluate_gate(formation_holdout: pd.DataFrame, follow_holdout: pd.DataFrame) -> dict[str, Any]:
    """Apply plan gate; require a positive after-cost winner, not just a split magnitude."""
    f30_all = formation_holdout[
        (formation_holdout["horizon_m"] == 30)
        & (formation_holdout["z_threshold"] == 2.0)
        & (formation_holdout["formation"].isin(["PRICE_LED", "FLOW_LED"]))
    ].copy()
    f30_follow = f30_all[f30_all["side"] == "follow"]
    price = f30_follow[f30_follow["formation"] == "PRICE_LED"]
    flow = f30_follow[f30_follow["formation"] == "FLOW_LED"]
    price_n = int(price["n"].iloc[0]) if not price.empty else 0
    flow_n = int(flow["n"].iloc[0]) if not flow.empty else 0
    price_hit = float(price["hit_rate"].iloc[0]) if not price.empty and price_n else float("nan")
    flow_hit = float(flow["hit_rate"].iloc[0]) if not flow.empty and flow_n else float("nan")
    price_exp = float(price["mean_pnl_after_cost_ticks"].iloc[0]) if not price.empty and price_n else float("nan")
    flow_exp = float(flow["mean_pnl_after_cost_ticks"].iloc[0]) if not flow.empty and flow_n else float("nan")
    hit_diff_pp = (price_hit - flow_hit) * 100.0 if np.isfinite(price_hit) and np.isfinite(flow_hit) else float("nan")
    exp_diff = (price_exp - flow_exp) if np.isfinite(price_exp) and np.isfinite(flow_exp) else float("nan")

    formation_split_mag = (
        price_n >= 50
        and flow_n >= 50
        and (
            (np.isfinite(hit_diff_pp) and abs(hit_diff_pp) >= 8.0)
            or (np.isfinite(exp_diff) and abs(exp_diff) >= 0.5)
        )
    )

    candidates = f30_all[f30_all["n"] >= 50].copy()
    best_row: dict[str, Any] = {}
    best_ok = False
    if not candidates.empty:
        candidates = candidates.sort_values("mean_pnl_after_cost_ticks", ascending=False)
        best_row = candidates.iloc[0].to_dict()
        best_ok = float(best_row.get("mean_pnl_after_cost_ticks") or -999) >= 0.5

    formation_ok = bool(formation_split_mag and best_ok)

    all_follow = follow_holdout[
        (follow_holdout["horizon_m"] == 30)
        & (follow_holdout["side"] == "follow")
        & (follow_holdout["z_threshold"] == 2.0)
        & (follow_holdout["formation"] == "ALL")
    ]
    follow_n = int(all_follow["n"].iloc[0]) if not all_follow.empty else 0
    follow_ac = float(all_follow["mean_pnl_after_cost_ticks"].iloc[0]) if not all_follow.empty else float("nan")

    all_fade = follow_holdout[
        (follow_holdout["horizon_m"] == 30)
        & (follow_holdout["side"] == "fade")
        & (follow_holdout["z_threshold"] == 2.0)
        & (follow_holdout["formation"] == "ALL")
    ]
    fade_n = int(all_fade["n"].iloc[0]) if not all_fade.empty else 0
    fade_ac = float(all_fade["mean_pnl_after_cost_ticks"].iloc[0]) if not all_fade.empty else float("nan")

    expectancy_ok = (follow_n >= 50 and np.isfinite(follow_ac) and follow_ac >= 0.5) or best_ok
    passed = bool(formation_ok or expectancy_ok)

    return {
        "passed": passed,
        "formation_ok": bool(formation_ok),
        "formation_split_magnitude_ok": bool(formation_split_mag),
        "expectancy_ok": bool(expectancy_ok),
        "best_compound_ok": bool(best_ok),
        "price_led_n": price_n,
        "flow_led_n": flow_n,
        "price_led_hit_30m_follow": price_hit,
        "flow_led_hit_30m_follow": flow_hit,
        "hit_diff_pp_follow": hit_diff_pp,
        "price_led_after_cost_30m_follow": price_exp,
        "flow_led_after_cost_30m_follow": flow_exp,
        "exp_diff_ticks_follow": exp_diff,
        "follow_all_n": follow_n,
        "follow_all_after_cost_30m": follow_ac,
        "fade_all_n": fade_n,
        "fade_all_after_cost_30m": fade_ac,
        "best_compound": best_row,
        "gate_rules": {
            "min_n": 50,
            "min_after_cost_ticks": 0.5,
            "min_formation_hit_diff_pp": 8.0,
            "min_formation_exp_diff_ticks": 0.5,
            "cost_ticks": COST_TICKS,
            "formation_requires_positive_winner": True,
        },
        "recommendation": (
            "PASS: build V2.1 only after explicit user approval"
            if passed
            else "FAIL: do not build V2.1; keep logging; leave V1/V2/V3 untouched"
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    events = load_event_times(Path(args.events_db))
    walk = load_walkforward(Path(args.walkforward), model=args.model)
    walk = annotate_formation(walk, window=args.formation_window)
    walk = add_forward_outcomes(walk)
    walk = walk.reset_index()
    walk["is_rth"] = _is_rth(walk["bucket_ts"])
    walk["event_blackout"] = mark_event_blackout(walk["bucket_ts"], events, EVENT_BLACKOUT_MINUTES)

    # Prefer walkforward for long history; also load live signal logs for cross-check
    signals = load_signal_logs(Path(args.live_dir))
    alerts = load_alert_log(Path(args.live_dir))

    dates = sorted(walk["et_date"].dropna().unique().tolist())
    train_dates, holdout_dates = _chronological_split(dates, train_frac=args.train_frac)
    train_set, holdout_set = set(train_dates), set(holdout_dates)

    walk_train = walk[walk["et_date"].isin(train_set)]
    walk_hold = walk[walk["et_date"].isin(holdout_set)]

    # Episode downsample for minute autocorrelation (every 15m) on holdout gate counts
    def _episode(df: pd.DataFrame) -> pd.DataFrame:
        parts = []
        for _, day in df.groupby("et_date", sort=False):
            parts.append(day.iloc[:: args.episode_every])
        return pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]

    hold_ep = _episode(walk_hold)
    train_ep = _episode(walk_train)

    threshold_rows: list[dict[str, Any]] = []
    formation_rows: list[dict[str, Any]] = []
    for split_name, frame in (
        ("train", walk_train),
        ("holdout", walk_hold),
        ("holdout_episode", hold_ep),
        ("train_episode", train_ep),
        ("holdout_rth", walk_hold[walk_hold["is_rth"]]),
        ("holdout_rth_no_event", walk_hold[walk_hold["is_rth"] & ~walk_hold["event_blackout"]]),
        ("holdout_episode_rth_no_event", hold_ep[hold_ep["is_rth"] & ~hold_ep["event_blackout"]]),
    ):
        threshold_rows.extend(score_threshold_table(frame, split_name))
        formation_rows.extend(score_formation_table(frame, split_name))

    threshold_df = pd.DataFrame(threshold_rows)
    formation_df = pd.DataFrame(formation_rows)

    alert_rows = []
    alert_rows.extend(score_alert_episodes(alerts, "all_alerts"))
    alert_rows.extend(score_alert_episodes(alerts, "holdout_alerts", holdout_set if holdout_set else None))
    # If alert dates don't overlap walkforward holdout, still report full alert sample as live-era
    alert_df = pd.DataFrame(alert_rows)

    # Live signal-log minute cross-check (shorter history)
    signal_stats = []
    if not signals.empty:
        sig = signals.reset_index().rename(columns={"ts": "bucket_ts"})
        # Align column names
        if "fair_value" in sig and "residual" in sig:
            sig = annotate_formation(sig.set_index("bucket_ts"), window=args.formation_window).reset_index()
            sig["et_date"] = sig["bucket_ts"].dt.tz_convert(EASTERN).dt.strftime("%Y-%m-%d")
            sig = add_forward_outcomes(sig.set_index("bucket_ts")).reset_index()
            sig["is_rth"] = _is_rth(sig["bucket_ts"])
            sig["event_blackout"] = mark_event_blackout(sig["bucket_ts"], events, EVENT_BLACKOUT_MINUTES)
            signal_stats.extend(score_threshold_table(sig, "live_signals"))
            signal_stats.extend(score_formation_table(sig, "live_signals"))
            signal_stats.extend(score_threshold_table(sig[sig["is_rth"] & ~sig["event_blackout"]], "live_signals_rth_no_event"))
            signal_stats.extend(score_formation_table(sig[sig["is_rth"] & ~sig["event_blackout"]], "live_signals_rth_no_event"))
    signal_df = pd.DataFrame(signal_stats)

    gate = evaluate_gate(
        formation_df[formation_df["split"] == "holdout_episode"],
        threshold_df[threshold_df["split"] == "holdout_episode"],
    )
    gate_rth = evaluate_gate(
        formation_df[formation_df["split"] == "holdout_episode_rth_no_event"],
        threshold_df[threshold_df["split"] == "holdout_episode_rth_no_event"],
    )
    gate["rth_no_event_gate"] = gate_rth
    gate["train_dates"] = train_dates
    gate["holdout_dates"] = holdout_dates
    gate["n_walk_train"] = int(len(walk_train))
    gate["n_walk_holdout"] = int(len(walk_hold))
    gate["n_holdout_episodes"] = int(len(hold_ep))
    gate["price_share_caveat"] = {
        "note": "price_share_of_reversion is only filled on a subset of resolved alert episodes; do not over-claim the ~94% FV-to-price figure",
        "alert_rows": alert_df[alert_df["label"] == "price_share_coverage"].to_dict(orient="records"),
    }
    gate["isolation"] = {
        "modified_v1_v2_v3": False,
        "wrote_live_stores": False,
        "touched_ports_or_sod": False,
        "outputs_only_under": str(output_dir),
    }

    # Artifacts
    thr_path = output_dir / "fade_vs_follow_stats.csv"
    form_path = output_dir / "formation_stats.csv"
    alert_path = output_dir / "alert_episode_stats.csv"
    signal_path = output_dir / "live_signal_stats.csv"
    gate_path = output_dir / "convergence_gate.json"
    summary_path = output_dir / "convergence_summary.json"
    sample_path = output_dir / "annotated_holdout_sample.csv"

    threshold_df.to_csv(thr_path, index=False)
    formation_df.to_csv(form_path, index=False)
    alert_df.to_csv(alert_path, index=False)
    if not signal_df.empty:
        signal_df.to_csv(signal_path, index=False)
    walk_hold.tail(20000).to_csv(sample_path, index=False)
    gate_path.write_text(json.dumps(gate, indent=2, default=str), encoding="utf-8")

    summary = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "cost_ticks": COST_TICKS,
        "formation_window": args.formation_window,
        "z_thresholds": list(Z_THRESHOLDS),
        "horizons": list(HORIZONS),
        "rth_et": f"{RTH_START_MIN // 60:02d}:{RTH_START_MIN % 60:02d}-{RTH_END_MIN // 60:02d}:{RTH_END_MIN % 60:02d}",
        "event_blackout_minutes": EVENT_BLACKOUT_MINUTES,
        "n_event_times": int(len(events)),
        "gate": gate,
        "artifacts": {
            "fade_vs_follow": str(thr_path),
            "formation": str(form_path),
            "alerts": str(alert_path),
            "live_signals": str(signal_path) if not signal_df.empty else None,
            "gate": str(gate_path),
            "summary": str(summary_path),
            "sample": str(sample_path),
        },
        "notes": [
            "Read-only research; V1/V2/V3 code and live stores were not modified.",
            "Walkforward residual_z is a causal expanding within-day proxy (not identical to live prior_blend_v1).",
            "Alert dir_pnl_* is the V2 fade framing; follow = -fade.",
            "Meta-labeling and V2.1 build are out of scope unless gate passes and user approves.",
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 convergence / continuation / formation research (read-only).")
    parser.add_argument("--walkforward", default=str(WALKFORWARD))
    parser.add_argument("--live-dir", default=str(V2_LIVE_DIR))
    parser.add_argument("--events-db", default=str(EVENTS_DB))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--model", default="ridge")
    parser.add_argument("--formation-window", type=int, default=FORMATION_WINDOW)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--episode-every", type=int, default=15)
    args = parser.parse_args()
    summary = run(args)
    g = summary["gate"]
    print(
        json.dumps(
            {
                "ok": summary["ok"],
                "passed": g["passed"],
                "formation_ok": g["formation_ok"],
                "formation_split_magnitude_ok": g.get("formation_split_magnitude_ok"),
                "expectancy_ok": g["expectancy_ok"],
                "best_compound_ok": g["best_compound_ok"],
                "best_compound": {
                    "formation": g.get("best_compound", {}).get("formation"),
                    "side": g.get("best_compound", {}).get("side"),
                    "n": g.get("best_compound", {}).get("n"),
                    "mean_pnl_after_cost_ticks": g.get("best_compound", {}).get("mean_pnl_after_cost_ticks"),
                    "hit_rate": g.get("best_compound", {}).get("hit_rate"),
                },
                "hit_diff_pp_follow": g.get("hit_diff_pp_follow"),
                "exp_diff_ticks_follow": g.get("exp_diff_ticks_follow"),
                "follow_all_after_cost_30m": g["follow_all_after_cost_30m"],
                "fade_all_after_cost_30m": g.get("fade_all_after_cost_30m"),
                "price_led_n": g["price_led_n"],
                "flow_led_n": g["flow_led_n"],
                "rth_no_event_passed": g["rth_no_event_gate"]["passed"],
                "recommendation": g["recommendation"],
                "artifacts": summary["artifacts"],
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
