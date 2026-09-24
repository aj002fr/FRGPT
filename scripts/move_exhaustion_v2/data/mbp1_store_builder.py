"""Pure MBP-1 -> per-day derived event store frame (X0; PROGRAM.md §1.2).

``derive_day_frame`` consumes one day's raw MBP-1 rows (post-event L1 state,
canonicalised by ``(ts_event, sequence)``) and produces the derived columns
research code consumes downstream:

* ``event_class``: AGG_BUY/AGG_SELL/ADD_*/CXL_*/MOD_*/RESET per
  :mod:`event_semantics` (modify rows decomposed from the consecutive L1
  state diff);
* ``pre_bid_sz/pre_ask_sz/pre_bid_px/pre_ask_px``: the **previous row's**
  post-event book (pre-event depth per spec §4) -- never the current row's
  post-event state;
* ``mid`` and ``spread_ticks`` from the current row's L1;
* ``mid_change`` and the four per-session cumulative clocks
  ``n_lob_events``, ``n_trades``, ``n_price_changes``, ``wall_clock``
  (config/frozen/data_store.yaml builder.derived_columns.clocks).

Reset (``R``) rows re-seed the carried book state: the row after a reset
derives its pre-event depth from the reset row's post-event L1.  The clocks
are per-session cumulative counters and are *not* reset by ``R`` rows.

The function is pure and chunk-friendly: one day's frame in, one day's
derived frame out, no module-level state, so per-month / DuckDB
orchestration can call it per chunk.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .event_semantics import (
    ADD_ASK,
    ADD_BID,
    ADD_UNKNOWN,
    AGG_BUY,
    AGG_SELL,
    AGG_UNKNOWN,
    CXL_ASK,
    CXL_BID,
    CXL_UNKNOWN,
    MOD_ADD_LIKE,
    MOD_CXL_ADD,
    MOD_CXL_LIKE,
    MOD_UNKNOWN,
    RESET,
    EventSemanticsError,
)
from .session import session_bounds

PRE_EVENT_DEPTH_COLUMNS: tuple[str, ...] = (
    "pre_bid_sz",
    "pre_ask_sz",
    "pre_bid_px",
    "pre_ask_px",
)

REQUIRED_RAW_COLUMNS: tuple[str, ...] = (
    "ts_event",
    "sequence",
    "action",
    "side",
    "bid_px_00",
    "ask_px_00",
    "bid_sz_00",
    "ask_sz_00",
)

CLOCK_COLUMNS: tuple[str, ...] = (
    "n_lob_events",
    "n_trades",
    "n_price_changes",
    "wall_clock",
)

DERIVED_COLUMNS: tuple[str, ...] = (
    ("event_class", "mid", "mid_change", "spread_ticks")
    + PRE_EVENT_DEPTH_COLUMNS
    + CLOCK_COLUMNS
)

DEFAULT_TICK_SIZE = 0.015625  # 1/64 point (config/frozen/episodes.yaml)


def derive_day_frame(
    frame: pd.DataFrame,
    *,
    trading_date: str,
    tick_size: float = DEFAULT_TICK_SIZE,
    contract: Optional[str] = None,
) -> pd.DataFrame:
    """Derive the event store frame for one trading date (pure).

    Parameters
    ----------
    frame
        Raw MBP-1 rows with ``ts_event``, ``sequence``, ``action``, ``side``
        and the ``bid/ask_px/sz_00`` post-event L1 columns.  Extra columns
        (e.g. ``ts_recv``, ``price``, ``size``) are preserved.
    trading_date
        ET trading date of the session (defines the wall-clock origin via
        :func:`session.session_bounds`).
    tick_size
        Instrument tick size in price units (ZN = 1/64).
    contract
        Optional front-contract stamp (canonical ``ZNH6`` form) added as a
        ``contract`` column.

    Returns
    -------
    pd.DataFrame
        Canonically sorted (``ts_event``, ``sequence``) frame with the raw
        columns plus all :data:`DERIVED_COLUMNS`.

    Raises
    ------
    ValueError
        Missing required raw columns or invalid tick size.
    EventSemanticsError
        Unknown action/side or an undecomposable signed modify.
    """
    missing = [c for c in REQUIRED_RAW_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"frame missing required MBP-1 columns: {sorted(missing)}")
    if not (isinstance(tick_size, (int, float)) and tick_size > 0):
        raise ValueError(f"tick_size must be a positive number, got {tick_size!r}")
    if not frame.columns.is_unique:
        raise ValueError("frame has duplicate column names")

    work = frame.copy()
    work["ts_event"] = pd.to_datetime(work["ts_event"], utc=True, errors="raise")
    work["sequence"] = pd.to_numeric(work["sequence"], errors="raise")
    work = work.sort_values(["ts_event", "sequence"], kind="stable").reset_index(drop=True)

    start_utc = session_bounds(trading_date)[0]

    bid_px = pd.to_numeric(work["bid_px_00"], errors="coerce").astype(float)
    ask_px = pd.to_numeric(work["ask_px_00"], errors="coerce").astype(float)
    bid_sz = pd.to_numeric(work["bid_sz_00"], errors="coerce").astype(float)
    ask_sz = pd.to_numeric(work["ask_sz_00"], errors="coerce").astype(float)
    bid_ct = pd.to_numeric(
        work.get("bid_ct_00", pd.Series(np.nan, index=work.index)),
        errors="coerce",
    ).astype(float)
    ask_ct = pd.to_numeric(
        work.get("ask_ct_00", pd.Series(np.nan, index=work.index)),
        errors="coerce",
    ).astype(float)

    # Previous row's post-event book (pre-event depth).  Shift(1) is the
    # previous row in canonical order; after an R row the reset row's L1 is
    # carried, which is exactly the "state re-seeded" requirement.
    pre_bid_px = bid_px.shift(1)
    pre_ask_px = ask_px.shift(1)
    pre_bid_sz = bid_sz.shift(1)
    pre_ask_sz = ask_sz.shift(1)

    mid = (bid_px + ask_px) / 2.0
    prev_mid = mid.shift(1)
    mid_change = (
        prev_mid.notna() & mid.notna() & (mid != prev_mid)
    ).astype(int)
    spread_ticks = (ask_px - bid_px) / float(tick_size)

    event_class = _classify_events_vectorized(
        work, bid_px, ask_px, bid_sz, ask_sz, bid_ct, ask_ct,
    )

    actions = work["action"].astype(str).str.upper()
    is_trade = actions.isin(["T", "F"])
    is_lob = actions.isin(["A", "C", "M", "R"])
    n_trades = is_trade.cumsum()
    n_lob_events = is_lob.cumsum()

    # n_price_changes counts rows whose quote (bid or ask price) changed vs
    # the previous row; the first row of a session (and the first row after a
    # reset re-seed) has no prior quote, so it is not counted as a change.
    bid_prev = bid_px.shift(1)
    ask_prev = ask_px.shift(1)
    quote_changed = (
        (bid_px.notna() & bid_prev.notna() & (bid_px != bid_prev))
        | (ask_px.notna() & ask_prev.notna() & (ask_px != ask_prev))
    ).astype(int)
    n_price_changes = quote_changed.cumsum()

    wall_clock = (work["ts_event"] - start_utc).dt.total_seconds()

    out = work.copy()
    out["event_class"] = event_class
    out["mid"] = mid
    out["mid_change"] = mid_change
    out["spread_ticks"] = spread_ticks
    out["pre_bid_px"] = pre_bid_px
    out["pre_ask_px"] = pre_ask_px
    out["pre_bid_sz"] = pre_bid_sz
    out["pre_ask_sz"] = pre_ask_sz
    out["n_lob_events"] = n_lob_events
    out["n_trades"] = n_trades
    out["n_price_changes"] = n_price_changes
    out["wall_clock"] = wall_clock
    if contract is not None:
        out["contract"] = contract
    return out


def _classify_events_vectorized(
    work: pd.DataFrame,
    bid_px: pd.Series,
    ask_px: pd.Series,
    bid_sz: pd.Series,
    ask_sz: pd.Series,
    bid_ct: pd.Series,
    ask_ct: pd.Series,
) -> pd.Series:
    """Vectorised event_class assignment; raises on any out-of-semantics row."""
    actions = work["action"].astype(str).str.upper().to_numpy()
    sides = work["side"].astype(str).str.upper().to_numpy()
    is_t = np.isin(actions, ["T", "F"])
    is_a = actions == "A"
    is_c = actions == "C"
    is_m = actions == "M"
    is_r = actions == "R"
    known_action = is_t | is_a | is_c | is_m | is_r
    if not known_action.all():
        bad = np.flatnonzero(~known_action)[0]
        raise EventSemanticsError(
            f"unknown MBP-1 action at row {work.index[bad]}: {actions[bad]!r}"
        )
    known_side = np.isin(sides, ["A", "B", "N"])
    if not known_side.all():
        bad = np.flatnonzero(~known_side)[0]
        raise EventSemanticsError(
            f"unknown MBP-1 side at row {work.index[bad]}: {sides[bad]!r}"
        )
    out = pd.Series(index=work.index, dtype=object)
    out[is_r] = RESET
    out[is_t & (sides == "B")] = AGG_BUY
    out[is_t & (sides == "A")] = AGG_SELL
    out[is_t & (sides == "N")] = AGG_UNKNOWN
    out[is_a & (sides == "B")] = ADD_BID
    out[is_a & (sides == "A")] = ADD_ASK
    out[is_a & (sides == "N")] = ADD_UNKNOWN
    out[is_c & (sides == "B")] = CXL_BID
    out[is_c & (sides == "A")] = CXL_ASK
    out[is_c & (sides == "N")] = CXL_UNKNOWN
    out[is_m & (sides == "N")] = MOD_UNKNOWN
    is_signed_m = is_m & (sides != "N")
    if is_signed_m.any():
        side = sides[is_signed_m]
        prev_px = np.where(side == "B", bid_px.shift(1).to_numpy()[is_signed_m],
                           ask_px.shift(1).to_numpy()[is_signed_m])
        prev_sz = np.where(side == "B", bid_sz.shift(1).to_numpy()[is_signed_m],
                           ask_sz.shift(1).to_numpy()[is_signed_m])
        curr_px = np.where(side == "B", bid_px.to_numpy()[is_signed_m],
                           ask_px.to_numpy()[is_signed_m])
        curr_sz = np.where(side == "B", bid_sz.to_numpy()[is_signed_m],
                           ask_sz.to_numpy()[is_signed_m])
        prev_ct = np.where(side == "B", bid_ct.shift(1).to_numpy()[is_signed_m],
                           ask_ct.shift(1).to_numpy()[is_signed_m])
        curr_ct = np.where(side == "B", bid_ct.to_numpy()[is_signed_m],
                           ask_ct.to_numpy()[is_signed_m])
        unestablished = np.isnan(prev_px.astype(float)) | np.isnan(curr_px.astype(float))
        modify_rows = np.flatnonzero(is_signed_m)
        invalid_unestablished = unestablished & (modify_rows != 0)
        if invalid_unestablished.any():
            bad = modify_rows[np.flatnonzero(invalid_unestablished)[0]]
            raise EventSemanticsError(
                f"modify before an established L1 state at row {work.index[bad]}"
            )
        px_change = ~unestablished & (prev_px != curr_px)
        sz_up = (curr_sz > prev_sz)
        sz_down = (curr_sz < prev_sz)
        ct_up = (curr_ct > prev_ct)
        ct_down = (curr_ct < prev_ct)
        classes = np.select(
            [px_change, sz_up, sz_down, ct_up, ct_down],
            [MOD_CXL_ADD, MOD_ADD_LIKE, MOD_CXL_LIKE,
             MOD_ADD_LIKE, MOD_CXL_LIKE],
            default=MOD_UNKNOWN,
        )
        out[is_signed_m] = classes
    return out
