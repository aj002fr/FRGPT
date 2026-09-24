"""Six-state trade-to-trade attribution banner for Aggression V3."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.core import tick_size

EASTERN = ZoneInfo("America/New_York")
BANNER_SERIES_MAX_POINTS = 4000
LABEL = "PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE"
WINDOW_MIN = 15
DOMINANCE_RATIO = 1.5
QUIET_TICKS = 1.0
IDENTITY_TOL = 1e-6
GAP_SEC = 15 * 60
IMPLIED_PASSIVE_TEXT = (
    "Implied passive is price movement left unexplained by measured aggression "
    "under the displayed reference beta; it is not observed passive order flow."
)
HEADLINE = {
    "QUIET": "QUIET",
    "BALANCED": "BALANCED",
    "AGGRESS_DOMINANT": "AGGRESS DOMINANT",
    "IMPLIED_PASSIVE_DOMINANT": "IMPLIED PASSIVE DOMINANT",
    "JOINTLY_DRIVEN": "JOINTLY DRIVEN",
    "CONTESTED": "CONTESTED",
    "WARMING": "WARMING",
    "UNAVAILABLE": "UNAVAILABLE",
}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def classify_six_state(d: float, a: float, b: float, *, q: float = QUIET_TICKS, r: float = DOMINANCE_RATIO) -> str:
    """Classify one READY window. Quiet-threshold ties stay quiet-price; ratio ties go dominant."""
    m = max(abs(a), abs(b))
    if abs(d) <= q and m <= q:
        return "QUIET"
    if abs(d) <= q and m > q:
        return "BALANCED"
    if abs(d) > q and abs(a) >= r * abs(b):
        return "AGGRESS_DOMINANT"
    if abs(d) > q and abs(b) >= r * abs(a):
        return "IMPLIED_PASSIVE_DOMINANT"
    if abs(d) > q and (a * b) > 0:
        return "JOINTLY_DRIVEN"
    if abs(d) > q and (a * b) < 0:
        return "CONTESTED"
    return "UNAVAILABLE"


def config_key(
    *,
    session_start: datetime,
    price_symbol: str,
    aggression_symbols: Iterable[str],
    dv01_adjust: bool,
) -> tuple:
    """Stable identity for the displayed contract set. Not a READY gate."""
    return (
        session_start.isoformat(),
        price_symbol,
        tuple(aggression_symbols),
        bool(dv01_adjust),
    )


def _to_et_naive_iso(ts: Any) -> str:
    value = pd.Timestamp(ts)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    return value.tz_convert(EASTERN).tz_localize(None).isoformat()


def _last_gap_reset(frame: pd.DataFrame, as_of: Optional[pd.Timestamp] = None) -> Optional[pd.Timestamp]:
    if frame.empty or len(frame) < 2:
        return None
    work = frame
    if as_of is not None:
        work = frame.loc[frame.index <= as_of]
        if work.empty or len(work) < 2:
            return None
    idx = pd.DatetimeIndex(work.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    deltas = idx.to_series().diff().dt.total_seconds()
    gaps = idx[deltas > GAP_SEC]
    if len(gaps) == 0:
        return None
    return pd.Timestamp(gaps[-1])


def _endpoint(frame: pd.DataFrame, as_of: pd.Timestamp) -> Optional[pd.Timestamp]:
    idx = frame.index[frame.index <= as_of]
    if len(idx) == 0:
        return None
    return pd.Timestamp(idx[-1])


def _cell(frame: pd.DataFrame, ts: pd.Timestamp, column: str) -> Any:
    value = frame.loc[ts, column]
    if isinstance(value, pd.Series):
        return value.iloc[-1]
    return value


def _payload(
    status: str,
    state: str,
    *,
    reason: Optional[str] = None,
    d: Optional[float] = None,
    a: Optional[float] = None,
    b: Optional[float] = None,
    beta_ref: Optional[float] = None,
    beta_display: Optional[float] = None,
    as_of: Optional[str] = None,
    tick: Optional[float] = None,
) -> dict[str, Any]:
    headline = HEADLINE.get(state, state.replace("_", " "))
    contribution = None
    if status == "READY" and d is not None and a is not None and b is not None:
        contribution = (
            f"15m price {d:+.1f} ticks · aggression {a:+.1f} · implied passive {b:+.1f}"
        )
    beta_line = None
    if beta_display is not None and math.isfinite(beta_display):
        beta_line = (
            f"Reference beta: 10-session mean (display units: yield bp per $1 million DV01) "
            f"{beta_display:+.4f}"
        )
    elif beta_ref is not None and math.isfinite(beta_ref):
        beta_line = "Reference beta: 10-session mean (display units: yield bp per $1 million DV01)"
    return {
        "status": status,
        "state": state,
        "headline": headline,
        "label": LABEL,
        "non_directional": True,
        "predictive": False,
        "reason": reason,
        "as_of": as_of,
        "contribution_line": contribution,
        "beta_line": beta_line,
        "implied_passive_text": IMPLIED_PASSIVE_TEXT,
        "d_ticks": d,
        "a_ticks": a,
        "b_ticks": b,
        "beta_ref": beta_ref,
        "beta_display_yield_bp_per_1m": beta_display,
        "parameters": {
            "window_min": WINDOW_MIN,
            "dominance_ratio": DOMINANCE_RATIO,
            "quiet_ticks": QUIET_TICKS,
            "tick_size": tick,
        },
    }


def build_banner(
    frame: pd.DataFrame,
    beta_payload: dict[str, Any],
    *,
    price_symbol: str,
    as_of: datetime | pd.Timestamp | None = None,
    live_connected: Optional[bool] = None,
    reset_key: Optional[tuple] = None,
    prior_key: Optional[tuple] = None,
) -> dict[str, Any]:
    """Classify the latest 15-minute trade-to-trade window."""
    tau = tick_size(price_symbol)
    as_of_ts = pd.Timestamp(as_of or datetime.now())
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.tz_localize("UTC")
    else:
        as_of_ts = as_of_ts.tz_convert("UTC")
    as_of_iso = as_of_ts.isoformat()
    beta_ref = _finite((beta_payload or {}).get("beta_ref"))
    beta_display = _finite((beta_payload or {}).get("beta_display_yield_bp_per_1m"))

    if live_connected is False:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="Live feed is disconnected.", as_of=as_of_iso, tick=tau)
    if not tau or tau <= 0:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="Invalid tick size.", as_of=as_of_iso, tick=tau)
    if frame is None or frame.empty or "price" not in frame.columns or "total_aggress" not in frame.columns:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="No last-trade print frame.", as_of=as_of_iso, tick=tau)
    if beta_ref is None or beta_ref <= 0:
        return _payload(
            "WARMING",
            "WARMING",
            reason=(beta_payload or {}).get("reason") or "Waiting for a valid reference beta.",
            beta_ref=beta_ref,
            beta_display=beta_display,
            as_of=as_of_iso,
            tick=tau,
        )

    work = frame.copy()
    work.index = pd.to_datetime(work.index, utc=True)
    work = work.sort_index()
    gap_reset = _last_gap_reset(work, as_of_ts)
    if gap_reset is not None:
        work = work.loc[work.index >= gap_reset]
        if work.empty:
            return _payload(
                "WARMING",
                "WARMING",
                reason="Waiting for prints after a data gap.",
                beta_ref=beta_ref,
                beta_display=beta_display,
                as_of=as_of_iso,
                tick=tau,
            )

    earlier = as_of_ts - pd.Timedelta(minutes=WINDOW_MIN)
    k_now = _endpoint(work, as_of_ts)
    k_prev = _endpoint(work, earlier)
    if (
        k_now is not None
        and k_prev is not None
        and "session_id" in work.columns
        and str(_cell(work, k_now, "session_id")) != str(_cell(work, k_prev, "session_id"))
    ):
        return _payload(
            "WARMING",
            "WARMING",
            reason="Banner window reset after a session boundary.",
            beta_ref=beta_ref,
            beta_display=beta_display,
            as_of=as_of_iso,
            tick=tau,
        )
    if k_now is None:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="No last-trade price at the current endpoint.", as_of=as_of_iso, tick=tau)
    if k_prev is None:
        return _payload(
            "WARMING",
            "WARMING",
            reason="No print yet at the 15-minute-earlier endpoint since the session or configuration reset.",
            beta_ref=beta_ref,
            beta_display=beta_display,
            as_of=as_of_iso,
            tick=tau,
        )

    p_now = _finite(_cell(work, k_now, "price"))
    p_prev = _finite(_cell(work, k_prev, "price"))
    a_now = _finite(_cell(work, k_now, "total_aggress"))
    a_prev = _finite(_cell(work, k_prev, "total_aggress"))
    return _ready_from_points(
        as_of_iso=as_of_iso,
        tau=tau,
        beta_ref=beta_ref,
        beta_display=beta_display,
        p_now=p_now,
        p_prev=p_prev,
        a_now=a_now,
        a_prev=a_prev,
    )


def banner_from_endpoints(
    *,
    price_symbol: str,
    as_of: datetime | pd.Timestamp,
    beta_payload: dict[str, Any],
    live_connected: Optional[bool] = None,
    p_now: Optional[float] = None,
    p_prev: Optional[float] = None,
    a_now: Optional[float] = None,
    a_prev: Optional[float] = None,
    window_reason: Optional[str] = None,
) -> dict[str, Any]:
    """Classify two already-chosen endpoints. The caller owns gap and session checks."""
    tau = tick_size(price_symbol)
    as_of_ts = pd.Timestamp(as_of or datetime.now())
    if as_of_ts.tzinfo is None:
        as_of_ts = as_of_ts.tz_localize("UTC")
    else:
        as_of_ts = as_of_ts.tz_convert("UTC")
    as_of_iso = as_of_ts.isoformat()
    beta_ref = _finite((beta_payload or {}).get("beta_ref"))
    beta_display = _finite((beta_payload or {}).get("beta_display_yield_bp_per_1m"))
    if live_connected is False:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="Live feed is disconnected.", as_of=as_of_iso, tick=tau)
    if not tau or tau <= 0:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="Invalid tick size.", as_of=as_of_iso, tick=tau)
    if beta_ref is None or beta_ref <= 0:
        return _payload(
            "WARMING",
            "WARMING",
            reason=(beta_payload or {}).get("reason") or "Waiting for a valid reference beta.",
            beta_ref=beta_ref,
            beta_display=beta_display,
            as_of=as_of_iso,
            tick=tau,
        )
    if window_reason:
        return _payload(
            "WARMING",
            "WARMING",
            reason=window_reason,
            beta_ref=beta_ref,
            beta_display=beta_display,
            as_of=as_of_iso,
            tick=tau,
        )
    return _ready_from_points(
        as_of_iso=as_of_iso,
        tau=tau,
        beta_ref=beta_ref,
        beta_display=beta_display,
        p_now=p_now,
        p_prev=p_prev,
        a_now=a_now,
        a_prev=a_prev,
    )


def _ready_from_points(
    *,
    as_of_iso: str,
    tau: float,
    beta_ref: Optional[float],
    beta_display: Optional[float],
    p_now: Optional[float],
    p_prev: Optional[float],
    a_now: Optional[float],
    a_prev: Optional[float],
) -> dict[str, Any]:
    if None in (p_now, p_prev, a_now, a_prev):
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="Missing last-trade price or aggression at an endpoint.", as_of=as_of_iso, tick=tau)
    d = (p_now - p_prev) / tau
    a = beta_ref * (a_now - a_prev) / tau
    b = d - a
    if not all(math.isfinite(v) for v in (d, a, b)):
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="Non-finite contribution.", as_of=as_of_iso, tick=tau)
    if abs(d - (a + b)) > IDENTITY_TOL:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="Contribution identity d = a + b failed.", as_of=as_of_iso, tick=tau)
    state = classify_six_state(d, a, b)
    return _payload(
        "READY",
        state,
        d=d,
        a=a,
        b=b,
        beta_ref=beta_ref,
        beta_display=beta_display,
        as_of=as_of_iso,
        tick=tau,
    )


def _sample_as_of(index: pd.DatetimeIndex, max_points: int = BANNER_SERIES_MAX_POINTS) -> pd.DatetimeIndex:
    if len(index) == 0:
        return index
    if len(index) <= max_points:
        return pd.DatetimeIndex(index)
    step = max(1, int(math.ceil(len(index) / max_points)))
    sampled = pd.DatetimeIndex(index[::step])
    last = pd.Timestamp(index[-1])
    if sampled[-1] != last:
        sampled = sampled.append(pd.DatetimeIndex([last]))
    return sampled


def build_banner_series(
    frame: pd.DataFrame,
    beta_payload: dict[str, Any],
    *,
    price_symbol: str,
    as_of_index: Optional[Iterable] = None,
) -> list[dict[str, Any]]:
    """Six-state snapshots for chart hover. Does not change the live banner."""
    if frame is None or frame.empty or "price" not in frame.columns or "total_aggress" not in frame.columns:
        return []
    work = frame.copy()
    work.index = pd.to_datetime(work.index, utc=True)
    work = work.sort_index()
    work = work[~work.index.duplicated(keep="last")]
    source = pd.DatetimeIndex(as_of_index) if as_of_index is not None else work.index
    source = pd.DatetimeIndex(pd.to_datetime(source, utc=True)).sort_values()
    source = source.unique()
    sample = _sample_as_of(source)
    if len(sample) == 0:
        return []

    tau = tick_size(price_symbol)
    beta_ref = _finite((beta_payload or {}).get("beta_ref"))
    beta_display = _finite((beta_payload or {}).get("beta_display_yield_bp_per_1m"))
    rows: list[dict[str, Any]] = []
    if not tau or tau <= 0 or beta_ref is None or beta_ref <= 0:
        reason = (beta_payload or {}).get("reason") or (
            "Invalid tick size." if not tau or tau <= 0 else "Waiting for a valid reference beta."
        )
        status = "UNAVAILABLE" if not tau or tau <= 0 else "WARMING"
        for ts in sample:
            payload = _payload(
                status,
                status,
                reason=reason,
                beta_ref=beta_ref,
                beta_display=beta_display,
                as_of=pd.Timestamp(ts).isoformat(),
                tick=tau,
            )
            payload["x"] = _to_et_naive_iso(ts)
            rows.append(payload)
        return rows

    idx = pd.DatetimeIndex(work.index)
    idx_ns = idx.asi8
    t_ns = sample.asi8
    window_ns = np.int64(WINDOW_MIN * 60 * 1_000_000_000)
    prices = pd.to_numeric(work["price"], errors="coerce").to_numpy(dtype=float)
    aggress = pd.to_numeric(work["total_aggress"], errors="coerce").to_numpy(dtype=float)
    sessions = work["session_id"].astype(str).to_numpy() if "session_id" in work.columns else None

    if len(idx) >= 2:
        deltas = np.diff(idx_ns) / 1_000_000_000.0
        gap_ns = idx_ns[1:][deltas > GAP_SEC]
    else:
        gap_ns = np.array([], dtype=np.int64)

    now_pos = np.searchsorted(idx_ns, t_ns, side="right") - 1
    prev_pos = np.searchsorted(idx_ns, t_ns - window_ns, side="right") - 1
    last_gap_ns = np.full(len(sample), -1, dtype=np.int64)
    if len(gap_ns):
        gpos = np.searchsorted(gap_ns, t_ns, side="right") - 1
        valid_g = gpos >= 0
        last_gap_ns[valid_g] = gap_ns[gpos[valid_g]]

    now_ok = now_pos >= 0
    prev_ok = prev_pos >= 0
    now_ts_ns = np.where(now_ok, idx_ns[np.clip(now_pos, 0, None)], np.int64(-1))
    prev_ts_ns = np.where(prev_ok, idx_ns[np.clip(prev_pos, 0, None)], np.int64(-1))
    now_ok = now_ok & ((last_gap_ns < 0) | (now_ts_ns >= last_gap_ns))
    prev_ok = prev_ok & ((last_gap_ns < 0) | (prev_ts_ns >= last_gap_ns))
    same_session = now_ok & prev_ok
    if sessions is not None:
        same_session = same_session & (sessions[np.clip(now_pos, 0, None)] == sessions[np.clip(prev_pos, 0, None)])

    for i, ts in enumerate(sample):
        as_of_iso = pd.Timestamp(ts).isoformat()
        if not now_ok[i]:
            payload = _payload(
                "UNAVAILABLE",
                "UNAVAILABLE",
                reason="No last-trade price at the current endpoint.",
                beta_ref=beta_ref,
                beta_display=beta_display,
                as_of=as_of_iso,
                tick=tau,
            )
        elif not prev_ok[i]:
            payload = _payload(
                "WARMING",
                "WARMING",
                reason="No print yet at the 15-minute-earlier endpoint since the session or configuration reset.",
                beta_ref=beta_ref,
                beta_display=beta_display,
                as_of=as_of_iso,
                tick=tau,
            )
        elif not same_session[i]:
            payload = _payload(
                "WARMING",
                "WARMING",
                reason="Banner window reset after a session boundary.",
                beta_ref=beta_ref,
                beta_display=beta_display,
                as_of=as_of_iso,
                tick=tau,
            )
        else:
            p_now = _finite(prices[now_pos[i]])
            p_prev = _finite(prices[prev_pos[i]])
            a_now = _finite(aggress[now_pos[i]])
            a_prev = _finite(aggress[prev_pos[i]])
            if None in (p_now, p_prev, a_now, a_prev):
                payload = _payload(
                    "UNAVAILABLE",
                    "UNAVAILABLE",
                    reason="Missing last-trade price or aggression at an endpoint.",
                    beta_ref=beta_ref,
                    beta_display=beta_display,
                    as_of=as_of_iso,
                    tick=tau,
                )
            else:
                d = (p_now - p_prev) / tau
                a = beta_ref * (a_now - a_prev) / tau
                b = d - a
                if not all(math.isfinite(v) for v in (d, a, b)) or abs(d - (a + b)) > IDENTITY_TOL:
                    payload = _payload(
                        "UNAVAILABLE",
                        "UNAVAILABLE",
                        reason="Non-finite contribution.",
                        beta_ref=beta_ref,
                        beta_display=beta_display,
                        as_of=as_of_iso,
                        tick=tau,
                    )
                else:
                    payload = _payload(
                        "READY",
                        classify_six_state(d, a, b),
                        d=d,
                        a=a,
                        b=b,
                        beta_ref=beta_ref,
                        beta_display=beta_display,
                        as_of=as_of_iso,
                        tick=tau,
                    )
        payload["x"] = _to_et_naive_iso(ts)
        rows.append(payload)
    return rows
