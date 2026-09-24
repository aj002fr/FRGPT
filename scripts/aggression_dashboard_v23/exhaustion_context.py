"""Display-only move-exhaustion context for isolated V2.3.

Signals from research-supported families only (E03/E11/E12/E13/E15/E16 +
reduced E18/E21/E22 readouts). Not after-cost validated (E26 failed).
Not long/short executable. Holdout never opened.
"""

from __future__ import annotations

from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard.core import load_dv01_table

EASTERN = ZoneInfo("America/New_York")

STRATEGY_STATUS = "PROVISIONAL / RESEARCH-ONLY / NOT AFTER-COST VALIDATED"
BANNER_LABEL = STRATEGY_STATUS

MOVE_Z_THR = 2.5
FLOW_Z_THR = 1.5
V4_ABS_Z = 2.0
EXH_HIGH_THR = 0.65
EXH_ABSTAIN_THR = 0.55
OPENING_START = time(1, 0)
OPENING_END = time(3, 0)
TICK = 1.0 / 64.0
UNIT_DV01 = {"FV": 42.0, "TU": 36.0, "TY": 85.0, "US": 125.0}
EPS = 1e-12

SIGNAL_EXPLAINERS = {
    "move_event": (
        "E01 B_price_plus_flow: |move_z_5m|≥2.5 and same-sign |flow_z_5m|≥1.5. "
        "Primary event universe for the exhaustion study."
    ),
    "v4_align": (
        "E03 V4: |residual_z|≥2 and residual sign aligns with the recent move. "
        "Best pre-cost selective fade lead (~+0.84 ticks). Not cost-validated."
    ),
    "liq_context": (
        "E11: wide vs tight spread / depth context. Wide-spread windows had higher "
        "exhaustion rates (~66% vs ~55%). Non-directional."
    ),
    "book_confirm": (
        "E12: microprice / size imbalance vs price. Book support fading confirmed "
        "exhaustion better than price alone (+10.4pp AUC). Collinear signals on TBBO."
    ),
    "residual_dynamics": (
        "E13: price/FV velocity vs static |z|. Dynamics beat static residual "
        "(+9.5pp AUC). Attractive fade-pocket claim was not confirmed."
    ),
    "curve_flow": (
        "E15: duration-norm neighbour DV01. Curve-wide aggressive flow → continuation "
        "lean (not a long/short). Target-only residual absorption not supported."
    ),
    "stand_aside": (
        "E16: do not signal in opening (01:00–03:00 ET) or calendar roll proxy "
        "(days 1–12 of Mar/Jun/Sep/Dec). Exclusion improved pooled AUC on eval."
    ),
    "exh_prob": (
        "Reduced live score inspired by E18/E27 (not the offline F7 pickle). "
        "High confidence only; abstain otherwise. E26 failed after costs — display only."
    ),
    "ext_bound": (
        "E21-style additional-extension risk bounds (q50/q80 ticks) while a move event "
        "is active. Research-promising coverage offline; live is a heuristic readout."
    ),
    "rev_mag": (
        "E22-style expected 5m reversal ticks if exhaustion is likely. Diagnostic only."
    ),
}


def _causal_robust_z(series: pd.Series) -> pd.Series:
    x = pd.to_numeric(series, errors="coerce")
    past = x.shift(1)
    med = past.expanding(min_periods=20).median()
    mad = (past - med).abs().expanding(min_periods=20).median()
    scale = (1.4826 * mad).replace(0.0, np.nan)
    std = past.expanding(min_periods=20).std(ddof=1)
    scale = scale.fillna(std).clip(lower=EPS)
    return (x - med) / scale


def _is_roll(et_date: str) -> bool:
    d = pd.Timestamp(et_date)
    return bool(d.month in (3, 6, 9, 12) and 1 <= d.day <= 12)


def annotate_exhaustion_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Add causal exhaustion-context columns to a live model panel."""
    out = df.copy()
    if out.empty:
        return out
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx
    et = idx.tz_convert(EASTERN)
    if "et_date" not in out.columns:
        out["et_date"] = et.strftime("%Y-%m-%d")

    price = pd.to_numeric(out.get("price"), errors="coerce")
    out["move_ticks_5m"] = price.diff(5) / TICK
    out["move_z_5m"] = _causal_robust_z(out["move_ticks_5m"])
    flow = pd.to_numeric(out.get("agg_5m"), errors="coerce")
    out["flow_z_5m"] = _causal_robust_z(flow)
    direction = np.sign(out["move_ticks_5m"]).replace(0.0, np.nan)
    out["move_event"] = (
        (out["move_z_5m"].abs() >= MOVE_Z_THR)
        & ((direction * out["flow_z_5m"]) >= FLOW_Z_THR)
    ).fillna(False)

    rz = pd.to_numeric(out.get("residual_z"), errors="coerce")
    out["v4_align"] = (
        (rz.abs() >= V4_ABS_Z) & (np.sign(rz) == np.sign(out["move_ticks_5m"])) & out["move_ticks_5m"].notna()
    ).fillna(False)

    # Liquidity regimes (session expanding)
    spread = pd.to_numeric(out.get("spread_mean"), errors="coerce")
    depth = pd.to_numeric(out.get("displayed_depth_mean"), errors="coerce")
    spr_hi = spread.groupby(out["et_date"], sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=30).quantile(0.70)
    )
    out["wide_spread"] = (spread >= spr_hi).fillna(False)
    out["spread_regime"] = np.where(out["wide_spread"], "wide_spread", "tight_or_normal")

    # Microprice / imbalance (E12)
    imb = pd.to_numeric(out.get("size_imbalance_mean"), errors="coerce")
    mp = pd.to_numeric(out.get("microprice_dev_mean"), errors="coerce")
    if mp.isna().all():
        mp = imb  # collinear on research panel
    out["directional_book"] = direction * mp
    book_lo = out["directional_book"].groupby(out["et_date"], sort=False).transform(
        lambda s: s.shift(1).expanding(min_periods=30).quantile(0.30)
    )
    out["book_fading"] = (out["directional_book"] <= book_lo).fillna(False)

    # Residual dynamics (E13)
    fv = pd.to_numeric(out.get("fair_value"), errors="coerce")
    out["price_velocity"] = price.diff(5) / TICK
    out["fv_velocity"] = fv.diff(5) / TICK
    out["residual_z_velocity"] = rz.diff(5)
    # price slowing while residual still large → dynamics lean
    out["dynamics_lean"] = (
        (rz.abs() >= 1.0)
        & (out["price_velocity"].abs() < out["price_velocity"].abs().shift(1).rolling(10, min_periods=3).median())
    ).fillna(False)

    # Curve flow (E15) duration-norm
    units = load_dv01_table() or UNIT_DV01
    neigh = []
    for sym in ("FV", "TU", "US"):
        col = f"{sym}_agg_5m"
        if col in out.columns:
            u = float(units.get(sym, UNIT_DV01.get(sym, 1.0)))
            neigh.append(direction * pd.to_numeric(out[col], errors="coerce") / max(u, EPS))
    if neigh:
        curve = pd.concat(neigh, axis=1).mean(axis=1)
    else:
        curve = pd.Series(0.0, index=out.index)
    ty_u = float(units.get("TY", UNIT_DV01["TY"]))
    target = direction * flow / max(ty_u, EPS)
    out["curve_directional_flow_factor"] = curve
    out["target_directional_flow"] = target
    out["curve_wide"] = ((target > 0) & (curve > 0) & (curve >= 0.25 * target.abs().clip(lower=EPS))).fillna(False)

    # Stand-aside (E16)
    et_times = pd.Series([t.time() for t in et], index=out.index)
    out["is_opening"] = et_times.map(lambda t: OPENING_START <= t < OPENING_END)
    out["is_roll"] = out["et_date"].map(_is_roll)
    out["stand_aside"] = (out["is_opening"] | out["is_roll"]).fillna(False)

    # Reduced exhaustion score (display heuristic; not offline F7)
    # Higher → more exhaustion lean. Curve-wide subtracts (continuation).
    z_abs = rz.abs().fillna(0.0)
    score = (
        0.25 * (z_abs / 3.0).clip(0, 1.5)
        + 0.20 * out["book_fading"].astype(float)
        + 0.15 * out["wide_spread"].astype(float)
        + 0.20 * out["dynamics_lean"].astype(float)
        + 0.20 * out["move_event"].astype(float)
        - 0.25 * out["curve_wide"].astype(float)
    )
    # logistic squash to ~[0,1]
    out["exh_prob"] = 1.0 / (1.0 + np.exp(-2.2 * (score - 0.45)))
    out.loc[out["stand_aside"], "exh_prob"] = np.nan  # force abstain in stand-aside
    out["exh_state"] = np.where(
        out["stand_aside"],
        "STAND_ASIDE",
        np.where(
            out["exh_prob"] >= EXH_HIGH_THR,
            "HIGH_CONF_EXHAUSTION",
            np.where(out["exh_prob"] >= EXH_ABSTAIN_THR, "POSSIBLE_EXHAUSTION", "NO_SIGNAL"),
        ),
    )

    # Extension / reversal heuristics when move event active
    abs_move = out["move_ticks_5m"].abs()
    out["ext_q50"] = np.where(out["move_event"], np.maximum(1.0, 0.5 * abs_move), np.nan)
    out["ext_q80"] = np.where(out["move_event"], np.maximum(2.0, 1.0 * abs_move), np.nan)
    out["rev_mag_5m"] = np.where(
        out["exh_state"].isin(["HIGH_CONF_EXHAUSTION", "POSSIBLE_EXHAUSTION"]),
        np.maximum(1.0, 0.6 * abs_move.fillna(2.0)),
        np.nan,
    )
    return out


def latest_exhaustion_context(df: pd.DataFrame) -> dict[str, Any]:
    """Latest-bar exhaustion context for API/UI."""
    empty = {
        "label": BANNER_LABEL,
        "strategy_status": STRATEGY_STATUS,
        "stand_aside": False,
        "stand_aside_reason": "",
        "move_event": False,
        "v4_align": False,
        "spread_regime": "unknown",
        "wide_spread": False,
        "book_fading": False,
        "dynamics_lean": False,
        "curve_wide": False,
        "curve_directional_flow_factor": None,
        "exh_prob": None,
        "exh_state": "NO_SIGNAL",
        "ext_q50": None,
        "ext_q80": None,
        "rev_mag_5m": None,
        "residual_z": None,
        "active_badges": [],
        "explainers": SIGNAL_EXPLAINERS,
        "non_executable": True,
        "after_cost_note": "E26 primary policy mean net ≈ -0.54 ticks after costs — not tradable.",
    }
    if df.empty:
        return empty
    panel = df if "exh_state" in df.columns else annotate_exhaustion_panel(df)
    if panel.empty:
        return empty
    row = panel.iloc[-1]

    def _f(key: str) -> float | None:
        v = row.get(key)
        try:
            fv = float(v)
            return fv if np.isfinite(fv) else None
        except Exception:
            return None

    reasons = []
    if bool(row.get("is_opening", False)):
        reasons.append("opening_period")
    if bool(row.get("is_roll", False)):
        reasons.append("roll_period")

    badges = []
    if bool(row.get("stand_aside", False)):
        badges.append("STAND ASIDE")
    if bool(row.get("move_event", False)):
        badges.append("MOVE EVENT")
    if bool(row.get("v4_align", False)):
        badges.append("V4 ALIGN")
    if bool(row.get("wide_spread", False)):
        badges.append("WIDE SPREAD")
    if bool(row.get("book_fading", False)):
        badges.append("BOOK FADING")
    if bool(row.get("dynamics_lean", False)):
        badges.append("DYNAMICS LEAN")
    if bool(row.get("curve_wide", False)):
        badges.append("CURVE WIDE (continuation lean)")
    state = str(row.get("exh_state") or "NO_SIGNAL")
    if state == "HIGH_CONF_EXHAUSTION":
        badges.append("HIGH CONF EXH")
    elif state == "POSSIBLE_EXHAUSTION":
        badges.append("POSSIBLE EXH")

    return {
        "label": BANNER_LABEL,
        "strategy_status": STRATEGY_STATUS,
        "stand_aside": bool(row.get("stand_aside", False)),
        "stand_aside_reason": "+".join(reasons),
        "move_event": bool(row.get("move_event", False)),
        "v4_align": bool(row.get("v4_align", False)),
        "spread_regime": str(row.get("spread_regime") or "unknown"),
        "wide_spread": bool(row.get("wide_spread", False)),
        "book_fading": bool(row.get("book_fading", False)),
        "dynamics_lean": bool(row.get("dynamics_lean", False)),
        "curve_wide": bool(row.get("curve_wide", False)),
        "curve_directional_flow_factor": _f("curve_directional_flow_factor"),
        "exh_prob": _f("exh_prob"),
        "exh_state": state,
        "ext_q50": _f("ext_q50"),
        "ext_q80": _f("ext_q80"),
        "rev_mag_5m": _f("rev_mag_5m"),
        "residual_z": _f("residual_z"),
        "price_velocity": _f("price_velocity"),
        "fv_velocity": _f("fv_velocity"),
        "active_badges": badges,
        "explainers": SIGNAL_EXPLAINERS,
        "non_executable": True,
        "after_cost_note": "E26 primary policy mean net ≈ -0.54 ticks after costs — not tradable.",
    }
