"""Trade-to-trade flow-attribution banner for Aggression V3."""

from __future__ import annotations

import math
from datetime import datetime, time
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v3.core import tick_size

EASTERN = ZoneInfo("America/New_York")
BANNER_SERIES_MAX_POINTS = 4000
LABEL = "PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE"
WINDOW_MIN = 5
OVERNIGHT_WINDOW_MIN = 15
OVERNIGHT_START = time(18, 0)
OVERNIGHT_END = time(3, 0)
QUIET_TICKS = 1.0
IDENTITY_TOL = 1e-6
GAP_SEC = WINDOW_MIN * 60
NS = 1_000_000_000


def window_minutes(ts: Any) -> int:
    """15 minutes from 18:00 ET inclusive through 03:00 ET exclusive; 5 minutes otherwise."""
    value = pd.Timestamp(ts)
    if value.tzinfo is None:
        value = value.tz_localize("UTC")
    else:
        value = value.tz_convert("UTC")
    clock = value.tz_convert(EASTERN).time()
    if clock >= OVERNIGHT_START or clock < OVERNIGHT_END:
        return OVERNIGHT_WINDOW_MIN
    return WINDOW_MIN


def window_minutes_for_ns(ts_ns: Any) -> np.ndarray:
    """Per-print window length, matching `window_minutes` on each UTC nanosecond timestamp."""
    stamps = pd.to_datetime(np.asarray(ts_ns, dtype=np.int64), unit="ns", utc=True).tz_convert(EASTERN)
    minute_of_day = np.asarray(stamps.hour, dtype=np.int64) * 60 + np.asarray(stamps.minute, dtype=np.int64)
    overnight = (minute_of_day >= 18 * 60) | (minute_of_day < 3 * 60)
    return np.where(overnight, OVERNIGHT_WINDOW_MIN, WINDOW_MIN).astype(np.int64)


def earlier_print_reason(minutes: int) -> str:
    return f"No print yet at the {minutes}-minute-earlier endpoint since the session or configuration reset."


NO_EARLIER_PRINT_REASON = earlier_print_reason(WINDOW_MIN)
IMPLIED_PASSIVE_TEXT = (
    "Residual (implied passive) is price movement left unexplained by measured aggression "
    "under the displayed reference beta; it is not observed passive order flow."
)
# STATE (coarse) -> (headline, colour).
GROUP_STYLE: dict[str, tuple[str, str]] = {
    "THIN_LIQ": ("THIN LIQ", "#747474"),
    "QUIET": ("QUIET", "#E8E8E8"),
    "AGGRESS_DOM": ("AGGRESS DOM", "#D86DCD"),
    "BALANCED": ("BALANCED", "#FFFF00"),
    "EXHAUSTION": ("EXHAUSTION", "#8ED974"),
}
BUY_COLOR = "#A7C9EC"
BUY_OFFSETTING_COLOR = "#D9E9F8"
SELL_COLOR = "#E7776B"
SELL_OFFSETTING_COLOR = "#F6C7AB"
QUIET_COLOR = "#E8E8E8"
# STATE2 (fine) -> (headline, colour, family, STATE). Blue is buy-side pressure, red sell-side.
STATE_STYLE: dict[str, tuple[str, str, str, str]] = {
    "SELLER_STRIKE": ("SELLER STRIKE", BUY_COLOR, "buy", "THIN_LIQ"),
    "BUYER_STRIKE": ("BUYER STRIKE", SELL_COLOR, "sell", "THIN_LIQ"),
    "QUIET": ("QUIET", QUIET_COLOR, "neutral", "QUIET"),
    "AGGRESS_DOM_BUY": ("AGGRESS DOM", BUY_COLOR, "buy", "AGGRESS_DOM"),
    "AGGRESS_DOM_THIN_LIQ_BUY": ("AGGRESS DOM, THIN LIQUIDITY", BUY_COLOR, "buy", "AGGRESS_DOM"),
    "PASSIVE_SELLERS_ABSORBING_BUYERS": ("PASSIVE SELLERS ABSORBING BUYERS", BUY_COLOR, "buy", "BALANCED"),
    "PASSIVE_SELLERS_DOMINATING_BUYERS": ("PASSIVE SELLERS DOMINATING BUYERS", SELL_COLOR, "sell", "EXHAUSTION"),
    "PASSIVE_SELLERS_OFFSETTING_BUYERS": ("PASSIVE SELLERS OFFSETTING BUYERS", BUY_OFFSETTING_COLOR, "buy", "BALANCED"),
    "PASSIVE_BUYERS_DOMINATING_SELLERS": ("PASSIVE BUYERS DOMINATING SELLERS", BUY_COLOR, "buy", "EXHAUSTION"),
    "AGGRESS_DOM_THIN_LIQ_SELL": ("AGGRESS DOM, THIN LIQUIDITY", SELL_COLOR, "sell", "AGGRESS_DOM"),
    "PASSIVE_BUYERS_ABSORBING_SELLERS": ("PASSIVE BUYERS ABSORBING SELLERS", SELL_COLOR, "sell", "BALANCED"),
    "AGGRESS_DOM_SELL": ("AGGRESS DOM", SELL_COLOR, "sell", "AGGRESS_DOM"),
    "PASSIVE_BUYERS_OFFSETTING_SELLERS": ("PASSIVE BUYERS OFFSETTING SELLERS", SELL_OFFSETTING_COLOR, "sell", "BALANCED"),
}
# (sign a, sign d) -> STATE2; sign is +1 above q, -1 below -q, else 0.
_BY_AGGRESS_AND_PRICE: dict[tuple[int, int], str] = {
    (0, 1): "SELLER_STRIKE",
    (0, 0): "QUIET",
    (0, -1): "BUYER_STRIKE",
    (1, 0): "PASSIVE_SELLERS_OFFSETTING_BUYERS",
    (1, -1): "PASSIVE_SELLERS_DOMINATING_BUYERS",
    (-1, 0): "PASSIVE_BUYERS_OFFSETTING_SELLERS",
    (-1, 1): "PASSIVE_BUYERS_DOMINATING_SELLERS",
}
# Aggression and price move the same way: (sign a, sign r) -> STATE2.
_SAME_DIRECTION: dict[tuple[int, int], str] = {
    (1, 0): "AGGRESS_DOM_BUY",
    (1, 1): "AGGRESS_DOM_THIN_LIQ_BUY",
    (1, -1): "PASSIVE_SELLERS_ABSORBING_BUYERS",
    (-1, 0): "AGGRESS_DOM_SELL",
    (-1, -1): "AGGRESS_DOM_THIN_LIQ_SELL",
    (-1, 1): "PASSIVE_BUYERS_ABSORBING_SELLERS",
}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sign_class(value: float, q: float) -> int:
    if value > q:
        return 1
    if value < -q:
        return -1
    return 0


def classify_state(d: float, a: float, r: float, *, q: float = QUIET_TICKS) -> str:
    """STATE2 for one READY window from actual move d, aggression a and residual r (ticks).

    |x| == q counts as small. Aggression and price pick the state; the residual only splits
    windows where aggression and price move the same way.
    """
    sign_a, sign_d, sign_r = _sign_class(a, q), _sign_class(d, q), _sign_class(r, q)
    if sign_a != 0 and sign_a == sign_d:
        return _SAME_DIRECTION[(sign_a, sign_r)]
    return _BY_AGGRESS_AND_PRICE[(sign_a, sign_d)]


def state_style(state: str) -> tuple[str, Optional[str], str, Optional[str]]:
    """(headline, colour, family, STATE) for any banner state, including WARMING / UNAVAILABLE."""
    return STATE_STYLE.get(state, (str(state).replace("_", " "), None, "neutral", None))


STATE_IDS: tuple[str, ...] = tuple(STATE_STYLE)
STATUS_NAMES: tuple[str, ...] = ("READY", "WARMING", "UNAVAILABLE")
STATUS_READY, STATUS_WARMING, STATUS_UNAVAILABLE = 0, 1, 2
MISSING_ENDPOINT_REASON = "Missing last-trade price or aggression at an endpoint."


def _build_state_lookup() -> np.ndarray:
    index = {state: position for position, state in enumerate(STATE_IDS)}
    lookup = np.zeros((3, 3, 3), dtype=np.int8)
    for sign_a in (-1, 0, 1):
        for sign_d in (-1, 0, 1):
            for sign_r in (-1, 0, 1):
                if sign_a != 0 and sign_a == sign_d:
                    state = _SAME_DIRECTION[(sign_a, sign_r)]
                else:
                    state = _BY_AGGRESS_AND_PRICE[(sign_a, sign_d)]
                lookup[sign_a + 1, sign_d + 1, sign_r + 1] = index[state]
    return lookup


_STATE_LOOKUP = _build_state_lookup()


def classify_states(d: Any, a: Any, r: Any, *, q: float = QUIET_TICKS) -> np.ndarray:
    """Vectorised `classify_state`: an index into STATE_IDS per window. Inputs must be finite."""
    codes = [
        np.where(values > q, 2, np.where(values < -q, 0, 1))
        for values in (np.asarray(a, dtype=float), np.asarray(d, dtype=float), np.asarray(r, dtype=float))
    ]
    return _STATE_LOOKUP[codes[0], codes[1], codes[2]]


def window_states(
    ts_ns: Any,
    prices: Any,
    aggress: Any,
    sessions: Any,
    beta: Any,
    tau: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The clock's window ending at every print, with the live banner's rules.

    The start endpoint is the last print at or before `t - W(t)`, where W is 15 minutes from
    18:00 to 03:00 ET and 5 minutes otherwise. It must be in the same session and after the last
    print gap longer than W(t). `beta` is a scalar or one value per print. Returns
    (status, state, d, a, r); state is -1 when status is not READY.
    """
    ts = np.asarray(ts_ns, dtype=np.int64)
    n = len(ts)
    status = np.full(n, STATUS_WARMING, dtype=np.int8)
    state = np.full(n, -1, dtype=np.int8)
    d = np.full(n, np.nan)
    a = np.full(n, np.nan)
    r = np.full(n, np.nan)
    if n == 0:
        return status, state, d, a, r
    price_arr = np.asarray(prices, dtype=float)
    aggress_arr = np.asarray(aggress, dtype=float)
    session_arr = np.asarray(sessions)
    beta_arr = np.broadcast_to(np.asarray(beta, dtype=float), (n,))
    minutes = window_minutes_for_ns(ts)
    prev = np.searchsorted(ts, ts - minutes * np.int64(60 * NS), side="right") - 1
    has_prev = prev >= 0
    prev = np.clip(prev, 0, None)
    last_gap = np.where(
        minutes == OVERNIGHT_WINDOW_MIN,
        _last_gap_ts(ts, np.int64(OVERNIGHT_WINDOW_MIN * 60 * NS)),
        _last_gap_ts(ts, np.int64(WINDOW_MIN * 60 * NS)),
    )
    has_prev &= (last_gap < 0) | (ts[prev] >= last_gap)
    has_prev &= session_arr[prev] == session_arr
    with np.errstate(invalid="ignore"):
        dd = (price_arr - price_arr[prev]) / tau
        aa = beta_arr * (aggress_arr - aggress_arr[prev]) / tau
        rr = dd - aa
    beta_ok = np.isfinite(beta_arr) & (beta_arr > 0)
    finite = np.isfinite(dd) & np.isfinite(aa)
    ready = has_prev & beta_ok & finite
    status[has_prev & beta_ok & ~finite] = STATUS_UNAVAILABLE
    status[ready] = STATUS_READY
    d[ready] = dd[ready]
    a[ready] = aa[ready]
    r[ready] = rr[ready]
    if ready.any():
        state[ready] = classify_states(dd[ready], aa[ready], rr[ready])
    return status, state, d, a, r


def contribution_line(d: float, a: float, b: float, minutes: int = WINDOW_MIN) -> str:
    return f"{minutes}m price {d:+.1f} ticks · aggression {a:+.1f} · residual {b:+.1f}"


def _last_gap_ts(ts: np.ndarray, gap_ns: int) -> np.ndarray:
    """Timestamp of the first print after the latest gap longer than `gap_ns`, or -1."""
    last = np.full(len(ts), np.int64(-1))
    if len(ts) < 2:
        return last
    gap_starts = ts[1:][np.diff(ts) > gap_ns]
    if len(gap_starts) == 0:
        return last
    position = np.searchsorted(gap_starts, ts, side="right") - 1
    valid = position >= 0
    last[valid] = gap_starts[position[valid]]
    return last


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


def _last_gap_reset(
    frame: pd.DataFrame,
    as_of: Optional[pd.Timestamp] = None,
    gap_sec: float = GAP_SEC,
) -> Optional[pd.Timestamp]:
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
    gaps = idx[deltas > gap_sec]
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


def _beta_line_text(
    *,
    beta_ref: Optional[float],
    beta_display: Optional[float],
    beta_payload: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """One banner line naming the beta source: tag day-OLS when present, else the legacy mean."""
    payload = beta_payload or {}
    tag = payload.get("tag")
    if tag:
        head = f"Reference beta: {tag} day OLS"
        n = payload.get("n_sessions_used")
        if n:
            head += f", {n} days"
        head += " (display units: yield bp per $1 million DV01)"
    elif payload.get("window") == "whole_session" or payload.get("version") == "whole_session_v1":
        head = "Reference beta: 10-session mean (display units: yield bp per $1 million DV01)"
    else:
        head = "Reference beta: day-tag OLS (display units: yield bp per $1 million DV01)"
    if beta_display is not None and math.isfinite(beta_display):
        return f"{head} {beta_display:+.4f}"
    if beta_ref is not None and math.isfinite(beta_ref):
        return head
    return None


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
    beta_payload: Optional[dict[str, Any]] = None,
    as_of: Optional[str] = None,
    tick: Optional[float] = None,
) -> dict[str, Any]:
    headline, color, family, group = state_style(state)
    group_headline, group_color = GROUP_STYLE.get(group, (headline, None))
    contribution = None
    minutes = window_minutes(as_of) if as_of is not None else WINDOW_MIN
    if status == "READY" and d is not None and a is not None and b is not None:
        contribution = contribution_line(d, a, b, minutes)
    beta_line = _beta_line_text(beta_ref=beta_ref, beta_display=beta_display, beta_payload=beta_payload)
    return {
        "status": status,
        "state": state,
        "headline": headline,
        "color": color,
        "family": family,
        "group": group,
        "group_headline": group_headline,
        "group_color": group_color,
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
            "window_min": minutes,
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
    """Classify the latest WINDOW_MIN trade-to-trade window."""
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
            beta_payload=beta_payload,
            as_of=as_of_iso,
            tick=tau,
        )

    work = frame.copy()
    work.index = pd.to_datetime(work.index, utc=True)
    work = work.sort_index()
    minutes = window_minutes(as_of_ts)
    gap_reset = _last_gap_reset(work, as_of_ts, gap_sec=minutes * 60)
    if gap_reset is not None:
        work = work.loc[work.index >= gap_reset]
        if work.empty:
            return _payload(
                "WARMING",
                "WARMING",
                reason="Waiting for prints after a data gap.",
                beta_ref=beta_ref,
                beta_display=beta_display,
                beta_payload=beta_payload,
                as_of=as_of_iso,
                tick=tau,
            )

    earlier = as_of_ts - pd.Timedelta(minutes=minutes)
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
            beta_payload=beta_payload,
            as_of=as_of_iso,
            tick=tau,
        )
    if k_now is None:
        return _payload("UNAVAILABLE", "UNAVAILABLE", reason="No last-trade price at the current endpoint.", as_of=as_of_iso, tick=tau)
    if k_prev is None:
        return _payload(
            "WARMING",
            "WARMING",
            reason=earlier_print_reason(minutes),
            beta_ref=beta_ref,
            beta_display=beta_display,
            beta_payload=beta_payload,
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
        beta_payload=beta_payload,
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
            beta_payload=beta_payload,
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
            beta_payload=beta_payload,
            as_of=as_of_iso,
            tick=tau,
        )
    return _ready_from_points(
        as_of_iso=as_of_iso,
        tau=tau,
        beta_ref=beta_ref,
        beta_display=beta_display,
        beta_payload=beta_payload,
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
    beta_payload: Optional[dict[str, Any]] = None,
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
    state = classify_state(d, a, b)
    return _payload(
        "READY",
        state,
        d=d,
        a=a,
        b=b,
        beta_ref=beta_ref,
        beta_display=beta_display,
        beta_payload=beta_payload,
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
    """State snapshots for chart hover. Does not change the live banner."""
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
                beta_payload=beta_payload,
                as_of=pd.Timestamp(ts).isoformat(),
                tick=tau,
            )
            payload["x"] = _to_et_naive_iso(ts)
            rows.append(payload)
        return rows

    idx = pd.DatetimeIndex(work.index)
    idx_ns = idx.asi8
    t_ns = sample.asi8
    sample_minutes = window_minutes_for_ns(t_ns)
    window_ns = sample_minutes * np.int64(60 * NS)
    prices = pd.to_numeric(work["price"], errors="coerce").to_numpy(dtype=float)
    aggress = pd.to_numeric(work["total_aggress"], errors="coerce").to_numpy(dtype=float)
    sessions = work["session_id"].astype(str).to_numpy() if "session_id" in work.columns else None

    def gap_starts(gap_sec: float) -> np.ndarray:
        if len(idx_ns) < 2:
            return np.array([], dtype=np.int64)
        deltas = np.diff(idx_ns) / float(NS)
        return idx_ns[1:][deltas > gap_sec]

    def last_gap(starts: np.ndarray) -> np.ndarray:
        out = np.full(len(sample), -1, dtype=np.int64)
        if len(starts) == 0:
            return out
        gpos = np.searchsorted(starts, t_ns, side="right") - 1
        valid_g = gpos >= 0
        out[valid_g] = starts[gpos[valid_g]]
        return out

    last_gap_ns = np.where(
        sample_minutes == OVERNIGHT_WINDOW_MIN,
        last_gap(gap_starts(OVERNIGHT_WINDOW_MIN * 60)),
        last_gap(gap_starts(WINDOW_MIN * 60)),
    )

    now_pos = np.searchsorted(idx_ns, t_ns, side="right") - 1
    prev_pos = np.searchsorted(idx_ns, t_ns - window_ns, side="right") - 1
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
                beta_payload=beta_payload,
                as_of=as_of_iso,
                tick=tau,
            )
        elif not prev_ok[i]:
            payload = _payload(
                "WARMING",
                "WARMING",
                reason=earlier_print_reason(int(sample_minutes[i])),
                beta_ref=beta_ref,
                beta_display=beta_display,
                beta_payload=beta_payload,
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
                beta_payload=beta_payload,
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
                    beta_payload=beta_payload,
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
                        beta_payload=beta_payload,
                        as_of=as_of_iso,
                        tick=tau,
                    )
                else:
                    payload = _payload(
                        "READY",
                        classify_state(d, a, b),
                        d=d,
                        a=a,
                        b=b,
                        beta_ref=beta_ref,
                        beta_display=beta_display,
                        beta_payload=beta_payload,
                        as_of=as_of_iso,
                        tick=tau,
                    )
        payload["x"] = _to_et_naive_iso(ts)
        rows.append(payload)
    return rows
