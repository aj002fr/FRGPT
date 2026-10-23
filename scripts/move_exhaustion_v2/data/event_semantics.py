"""MBP-1 event semantics for Move Exhaustion V2 X0.

Mirrors ``config/frozen/data_store.yaml`` ``mbp1_semantics``:

* ``action`` in {A(dd), C(ancel), M(odify), R(eset/clear), T(rade), F(ill)}.
  Databento MBP-1 ``F`` (fill) is classified as a trade (same aggressor map as ``T``).
* For trade rows ``side`` is the **aggressor**: ``B`` = aggressive buy
  (lifted ask) -> ``AGG_BUY``, ``A`` = aggressive sell (hit bid) ->
  ``AGG_SELL`` (Assumption A1).
* Valid ``side=N`` records are retained as explicit ``*_UNKNOWN`` classes;
  they are excluded from side-specific flow rather than assigned a side.
* Signed modify rows are decomposed from the consecutive L1 state diff into
  add-like (size up) / cancel-like (size down) / cancel+add (price change)
  components (Assumption A4; PROGRAM.md §1.2).

The module is pure and fails closed on unknown actions/sides or a
modify before an established L1 state. Signed modifies with no L1
price/size/count change map to ``MOD_UNKNOWN`` (priority/time change).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import pandas as pd

ACTION_NAMES: Dict[str, str] = {
    "A": "add",
    "C": "cancel",
    "M": "modify",
    "R": "reset_clear",
    "T": "trade",
    "F": "fill",  # Databento fill; classified as a trade
}
SIDE_NAMES: Dict[str, str] = {"B": "bid", "A": "ask", "N": "none"}
AGGRESSOR_MAP: Dict[str, str] = {"B": "AGG_BUY", "A": "AGG_SELL"}

AGG_BUY = "AGG_BUY"
AGG_SELL = "AGG_SELL"
AGG_UNKNOWN = "AGG_UNKNOWN"
ADD_BID = "ADD_BID"
ADD_ASK = "ADD_ASK"
ADD_UNKNOWN = "ADD_UNKNOWN"
CXL_BID = "CXL_BID"
CXL_ASK = "CXL_ASK"
CXL_UNKNOWN = "CXL_UNKNOWN"
MOD_ADD_LIKE = "MOD_ADD_LIKE"
MOD_CXL_LIKE = "MOD_CXL_LIKE"
MOD_CXL_ADD = "MOD_CXL_ADD"
MOD_UNKNOWN = "MOD_UNKNOWN"
RESET = "RESET"

EVENT_CLASSES: tuple[str, ...] = (
    AGG_BUY,
    AGG_SELL,
    AGG_UNKNOWN,
    ADD_BID,
    ADD_ASK,
    ADD_UNKNOWN,
    CXL_BID,
    CXL_ASK,
    CXL_UNKNOWN,
    MOD_ADD_LIKE,
    MOD_CXL_LIKE,
    MOD_CXL_ADD,
    MOD_UNKNOWN,
    RESET,
)

# Keys of the L1 state dicts produced by :func:`l1_state`.
L1_KEYS: tuple[str, ...] = (
    "bid_px", "ask_px", "bid_sz", "ask_sz", "bid_ct", "ask_ct",
)


class EventSemanticsError(ValueError):
    """Raised for MBP-1 events outside the declared semantics (fail closed)."""


def normalize_action(action: Any) -> str:
    value = str(action or "").strip().upper()
    if value not in ACTION_NAMES:
        raise EventSemanticsError(
            f"unknown MBP-1 action {action!r}; expected one of {sorted(ACTION_NAMES)}"
        )
    return value


def normalize_side(side: Any) -> str:
    value = str(side or "").strip().upper()
    if value not in SIDE_NAMES:
        raise EventSemanticsError(
            f"unknown MBP-1 side {side!r}; expected one of {sorted(SIDE_NAMES)}"
        )
    return value


def l1_state(
    row: Mapping[str, Any],
    *,
    bid_px_col: str = "bid_px_00",
    ask_px_col: str = "ask_px_00",
    bid_sz_col: str = "bid_sz_00",
    ask_sz_col: str = "ask_sz_00",
    bid_ct_col: str = "bid_ct_00",
    ask_ct_col: str = "ask_ct_00",
) -> Dict[str, Any]:
    """Extract the post-event L1 state from one MBP-1 row."""
    return {
        "bid_px": row.get(bid_px_col),
        "ask_px": row.get(ask_px_col),
        "bid_sz": row.get(bid_sz_col),
        "ask_sz": row.get(ask_sz_col),
        "bid_ct": row.get(bid_ct_col),
        "ask_ct": row.get(ask_ct_col),
    }


def decompose_modify(
    prev_l1: Mapping[str, Any],
    curr_l1: Mapping[str, Any],
    side: Any,
) -> str:
    """Split a modify into add-like / cancel-like / cancel+add components.

    Comparison uses the modified side's L1 price/size before (``prev_l1``)
    and after (``curr_l1``) the event:
    * price changed           -> ``MOD_CXL_ADD``
    * same price, size up/down -> ``MOD_ADD_LIKE`` / ``MOD_CXL_LIKE``
    * same price/size, order count up/down -> corresponding class
    * same price, size, and order count -> ``MOD_UNKNOWN`` (L1-invisible
      priority/time change; no decomposable add/cancel)
    * modify before an established L1 state -> :class:`EventSemanticsError`
    """
    s = normalize_side(side)
    if s == "N":
        raise EventSemanticsError("modify with side=N cannot be decomposed")
    px_key = "bid_px" if s == "B" else "ask_px"
    sz_key = "bid_sz" if s == "B" else "ask_sz"
    ct_key = "bid_ct" if s == "B" else "ask_ct"
    prev_px = prev_l1.get(px_key)
    prev_sz = prev_l1.get(sz_key)
    curr_px = curr_l1.get(px_key)
    curr_sz = curr_l1.get(sz_key)
    prev_ct = prev_l1.get(ct_key)
    curr_ct = curr_l1.get(ct_key)
    if prev_px is None or pd.isna(prev_px) or pd.isna(curr_px):
        raise EventSemanticsError("modify before an established L1 state")
    if prev_px != curr_px:
        return MOD_CXL_ADD
    if curr_sz > prev_sz:
        return MOD_ADD_LIKE
    if curr_sz < prev_sz:
        return MOD_CXL_LIKE
    if pd.notna(prev_ct) and pd.notna(curr_ct) and curr_ct > prev_ct:
        return MOD_ADD_LIKE
    if pd.notna(prev_ct) and pd.notna(curr_ct) and curr_ct < prev_ct:
        return MOD_CXL_LIKE
    return MOD_UNKNOWN


def classify_event(
    action: Any,
    side: Any,
    prev_l1: Optional[Mapping[str, Any]] = None,
    curr_l1: Optional[Mapping[str, Any]] = None,
) -> str:
    """Map one MBP-1 (action, side) row to its event class.

    ``prev_l1``/``curr_l1`` are required for modify rows (used by
    :func:`decompose_modify`); they are ignored for other actions.
    """
    a = normalize_action(action)
    s = normalize_side(side)
    if a in ("T", "F"):
        return AGG_UNKNOWN if s == "N" else AGGRESSOR_MAP[s]
    if a == "R":
        return RESET
    if a == "A":
        return ADD_UNKNOWN if s == "N" else (ADD_BID if s == "B" else ADD_ASK)
    if a == "C":
        return CXL_UNKNOWN if s == "N" else (CXL_BID if s == "B" else CXL_ASK)
    # a == "M"
    if s == "N":
        return MOD_UNKNOWN
    if prev_l1 is None or curr_l1 is None:
        raise EventSemanticsError("modify decomposition requires prev and curr L1 state")
    return decompose_modify(prev_l1, curr_l1, s)
