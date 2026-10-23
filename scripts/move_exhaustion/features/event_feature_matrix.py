"""Reusable event-level feature matrix for E17–E22 (cached parquet)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.move_exhaustion.data.contracts import OUTPUT_ROOT, TICK_SIZE
from scripts.move_exhaustion.data.loader import join_v2_panel
from scripts.move_exhaustion.data.quality import assert_no_forbidden_writes
from scripts.move_exhaustion.events.shock_detector import annotate_move_features
from scripts.move_exhaustion.features.absorption import (
    apparent_absorption_features,
    apparent_absorption_score,
    fit_absorption_z_stats,
)
from scripts.move_exhaustion.features.cross_tenor import cross_tenor_flow_confirmation_features
from scripts.move_exhaustion.features.flow_categories import additional_extension_ticks
from scripts.move_exhaustion.features.impact_efficiency import (
    horizon_efficiency_bundle,
    impact_efficiency_decay_features,
)
from scripts.move_exhaustion.features.per_tick import per_tick_features
from scripts.move_exhaustion.features.residual_dynamics import residual_dynamics_features
from scripts.move_exhaustion.features.session_regimes import (
    load_us_auction_events,
    load_us_high_importance_events,
    session_regime_features,
    train_high_vol_threshold,
)
from scripts.move_exhaustion.features.trade_composition import large_small_composition_features
from scripts.move_exhaustion.labels.barriers import first_barrier_label
from scripts.move_exhaustion.reporting.ml_common import (
    LABEL_HORIZON_MINUTES,
    R_TICKS,
    C_TICKS,
    chronological_split,
    load_event_universe,
    load_holdout_dates,
    max_reversal_ticks,
)
from scripts.move_exhaustion.simulation.policies import _ts

CACHE_PATH = OUTPUT_ROOT / "experiments" / "_shared" / "event_feature_matrix.parquet"
CACHE_META = OUTPUT_ROOT / "experiments" / "_shared" / "event_feature_matrix_meta.json"

# 5s/15s/30s horizons collapse to 1 bar on 1m grid (documented approximation).
GRID_NOTE = (
    "Frozen 1m TBBO decision grid: sub-minute horizons (5s/15s/30s) are "
    "approximated by the current 1m bar where noted in feature modules."
)


def _panel_snapshot(
    panel: pd.DataFrame, event: dict[str, Any], observation_time: Any | None = None
) -> pd.Series | None:
    obs = _ts(observation_time or event["event_start_time"])
    day = panel[panel["et_date"] == event["et_date"]].sort_values("bucket_ts")
    hist = day[day["bucket_ts"] <= obs]
    if hist.empty:
        return None
    return hist.iloc[-1]


def _base_row_features(row: pd.Series | None, direction: int) -> dict[str, float | None]:
    if row is None:
        return {}
    move_z = float(pd.to_numeric(row.get("move_z_5m"), errors="coerce"))
    flow_z = float(pd.to_numeric(row.get("flow_z_5m"), errors="coerce"))
    resid_z = float(pd.to_numeric(row.get("residual_z"), errors="coerce"))
    out: dict[str, float | None] = {
        "move_z_5m": move_z if np.isfinite(move_z) else None,
        "abs_move_z_5m": abs(move_z) if np.isfinite(move_z) else None,
        "signed_move_z_5m": direction * move_z if np.isfinite(move_z) else None,
        "move_ticks_5m": float(pd.to_numeric(row.get("move_ticks_5m"), errors="coerce")),
        "flow_z_5m": flow_z if np.isfinite(flow_z) else None,
        "signed_flow_z_5m": direction * flow_z if np.isfinite(flow_z) else None,
        "residual_z": resid_z if np.isfinite(resid_z) else None,
        "abs_residual_z": abs(resid_z) if np.isfinite(resid_z) else None,
        "signed_residual_z": direction * resid_z if np.isfinite(resid_z) else None,
        "displayed_depth": float(pd.to_numeric(row.get("displayed_depth_mean"), errors="coerce")),
        "spread": float(pd.to_numeric(row.get("spread_mean"), errors="coerce")),
        "realized_vol": float(pd.to_numeric(row.get("realized_vol"), errors="coerce")),
        "opposing_depth": float(
            pd.to_numeric(
                row.get("opposing_depth_mean", row.get("displayed_depth_mean")),
                errors="coerce",
            )
        ),
    }
    for k, v in list(out.items()):
        if v is not None and not np.isfinite(v):
            out[k] = None
    return out


def event_features_at_time(
    panel: pd.DataFrame,
    event: dict[str, Any],
    *,
    observation_time: Any | None = None,
    aux: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Causal feature bundle at observation_time (default event start)."""
    aux = aux or {}
    obs_event = {**event, "event_start_time": observation_time or event["event_start_time"]}
    direction = int(event["event_direction"])
    feats: dict[str, Any] = {}

    snap = _panel_snapshot(panel, event, observation_time)
    feats.update(_base_row_features(snap, direction))
    feats.update(horizon_efficiency_bundle(panel, obs_event))
    feats.update(impact_efficiency_decay_features(panel, obs_event))
    feats.update(per_tick_features(panel, obs_event))
    feats.update(residual_dynamics_features(panel, obs_event))
    feats.update(cross_tenor_flow_confirmation_features(panel, obs_event))
    feats.update(large_small_composition_features(panel, obs_event))
    feats.update(
        apparent_absorption_features(panel, obs_event)
    )
    if aux.get("absorption_zstats"):
        feats["apparent_absorption_score"] = apparent_absorption_score(feats, aux["absorption_zstats"])
    feats.update(
        session_regime_features(
            panel,
            obs_event,
            macro_events=aux.get("macro_events"),
            auction_events=aux.get("auction_events"),
            high_vol_threshold=aux.get("high_vol_threshold", 2.5),
        )
    )
    for flag in (
        "is_opening_period",
        "is_late_session",
        "is_high_volatility",
        "is_scheduled_macro",
        "is_auction_window",
        "is_roll_period",
        "is_ordinary",
    ):
        if flag in feats:
            feats[flag] = bool(feats[flag])
    return feats


def build_event_feature_matrix(
    output_root: Path = OUTPUT_ROOT,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Build or load cached event-level features + labels for B_price_plus_flow."""
    assert_no_forbidden_writes(CACHE_PATH)
    if CACHE_PATH.exists() and not refresh:
        return pd.read_parquet(CACHE_PATH)

    events, detector, holdout = load_event_universe(output_root)
    panel, _ = join_v2_panel()
    panel = panel[~panel["et_date"].isin(holdout)].copy()
    panel = annotate_move_features(panel)
    prices = panel.sort_values("bucket_ts").set_index("bucket_ts")["price"]

    dates = sorted(events["et_date"].dropna().unique().tolist())
    train_dates, _ = chronological_split(dates, 0.7)

    macro = load_us_high_importance_events()
    auctions = load_us_auction_events()

    # Train-only high-vol threshold and absorption z-stats
    tmp_mz = []
    for _, ev in events.iterrows():
        start = _ts(ev["event_start_time"])
        day = panel[panel["et_date"] == ev["et_date"]].sort_values("bucket_ts")
        hist = day[day["bucket_ts"] <= start]
        mz = None
        if not hist.empty:
            v = abs(float(pd.to_numeric(hist.iloc[-1].get("move_z_5m"), errors="coerce")))
            mz = v if np.isfinite(v) else None
        tmp_mz.append({"et_date": ev["et_date"], "abs_move_z_5m": mz})
    thr = train_high_vol_threshold(
        pd.DataFrame(tmp_mz).loc[
            pd.DataFrame(tmp_mz)["et_date"].isin(train_dates), "abs_move_z_5m"
        ]
    )

    # Build provisional absorption rows on train for z-stats
    train_abs_rows = []
    aux_proto = {
        "macro_events": macro,
        "auction_events": auctions,
        "high_vol_threshold": thr,
    }
    for _, ev in events[events["et_date"].isin(train_dates)].iterrows():
        train_abs_rows.append(apparent_absorption_features(panel, ev.to_dict()))
    zstats = fit_absorption_z_stats(pd.DataFrame(train_abs_rows))
    aux = {**aux_proto, "absorption_zstats": zstats}

    rows: list[dict[str, Any]] = []
    for _, ev in events.iterrows():
        event = ev.to_dict()
        feats = event_features_at_time(panel, event, aux=aux)
        lab = first_barrier_label(
            prices,
            start_ts=_ts(event["event_start_time"]),
            direction=int(event["event_direction"]),
            R_ticks=R_TICKS,
            C_ticks=C_TICKS,
            H=pd.Timedelta(minutes=LABEL_HORIZON_MINUTES),
            tick_size=TICK_SIZE["TY"],
        )
        ext = additional_extension_ticks(panel, event, horizon_minutes=LABEL_HORIZON_MINUTES)
        rev5 = max_reversal_ticks(prices, event, horizon_minutes=5)
        rev15 = max_reversal_ticks(prices, event, horizon_minutes=15)
        rev30 = max_reversal_ticks(prices, event, horizon_minutes=30)
        rows.append(
            {
                **event,
                **feats,
                "label": lab.get("label"),
                "label_reason": lab.get("reason"),
                "first_barrier": lab.get("first_barrier"),
                "hit_ts": lab.get("hit_ts"),
                "extension_ticks_15m": ext,
                "max_reversal_ticks_5m": rev5,
                "max_reversal_ticks_15m": rev15,
                "max_reversal_ticks_30m": rev30,
                "event_universe": detector,
                "grid_note": GRID_NOTE,
            }
        )

    df = pd.DataFrame(rows)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE_PATH, index=False)
    meta = {
        "n_events": int(len(df)),
        "event_universe": detector,
        "holdout_excluded": sorted(holdout),
        "high_vol_threshold": thr,
        "grid_note": GRID_NOTE,
        "label": f"first_barrier R={R_TICKS} C={C_TICKS} H={LABEL_HORIZON_MINUTES}m",
    }
    assert_no_forbidden_writes(CACHE_META)
    CACHE_META.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return df


def load_event_feature_matrix(
    output_root: Path = OUTPUT_ROOT, *, refresh: bool = False
) -> pd.DataFrame:
    global CACHE_PATH, CACHE_META
    CACHE_PATH = output_root / "experiments" / "_shared" / "event_feature_matrix.parquet"
    CACHE_META = output_root / "experiments" / "_shared" / "event_feature_matrix_meta.json"
    return build_event_feature_matrix(output_root, refresh=refresh)
