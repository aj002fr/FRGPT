"""One normalization for AccessAPI live (WebSocket TBBO) and historical (/historical/trades) trades.

Identity is shared by both endpoints: (contract, event time in microseconds, exact price, size,
side) plus an occurrence number among identical keys. The historical endpoint has no sequence
and microsecond timestamps rounded from the live nanoseconds (verified 2026-09-24: 126/126 live
ZN trades matched after rounding, 73/126 after truncation).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional

import numpy as np
import pandas as pd

from scripts.flow_store.settings import ROOT_TO_SYMBOL

PRICE_SCALE = 10 ** 9


@dataclass(frozen=True)
class Trade:
    symbol: str
    contract: str
    ts_event_ns: int
    ts_us: int
    price_nanos: int
    size: int
    side: str
    source: str
    sequence: Optional[int] = None
    raw: Optional[str] = None

    @property
    def key(self) -> tuple[str, int, int, int, str]:
        return (self.contract, self.ts_us, self.price_nanos, self.size, self.side)

    @property
    def price(self) -> float:
        return self.price_nanos / PRICE_SCALE


def trade_id(key: tuple[str, int, int, int, str], occurrence: int) -> str:
    contract, ts_us, price_nanos, size, side = key
    return f"{contract}|{ts_us}|{price_nanos}|{size}|{side}|{occurrence}"


def round_us(ts_ns: int) -> int:
    """Nanoseconds to microseconds, half to even (the historical store's rounding)."""
    q, r = divmod(int(ts_ns), 1000)
    if r > 500 or (r == 500 and q % 2 == 1):
        q += 1
    return q


def parse_ts_ns(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        if isinstance(value, (int, np.integer)):
            return int(value)
        if isinstance(value, np.datetime64):
            return int(value.astype("datetime64[ns]").astype(np.int64))
        stamp = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return int(stamp.value)


def price_nanos(value: Any) -> Optional[int]:
    try:
        scaled = Decimal(str(value).strip()) * PRICE_SCALE
    except (InvalidOperation, ValueError):
        return None
    if scaled != scaled.to_integral_value():
        return None
    return int(scaled)


def normalize_side(value: Any) -> str:
    token = str(value or "").strip().upper()
    if token in ("B", "BID"):
        return "B"
    if token in ("A", "ASK"):
        return "A"
    return "N"


def normalize_record(data: Mapping[str, Any], *, source: str, keep_raw: bool = False) -> Optional[Trade]:
    """A live WebSocket record or historical row as a Trade; None if it is not a usable trade."""
    action = data.get("action")
    if action is not None and str(action) != "T":
        return None
    contract = str(data.get("symbol") or "").strip().upper()
    symbol = ROOT_TO_SYMBOL.get(contract[:2])
    if symbol is None:
        return None
    ts_ns = parse_ts_ns(data.get("ts_event"))
    px = price_nanos(data.get("price"))
    try:
        size = int(Decimal(str(data.get("size"))))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if ts_ns is None or px is None or px <= 0 or size <= 0:
        return None
    sequence = data.get("sequence")
    try:
        sequence = int(sequence) if sequence not in (None, "") else None
    except (TypeError, ValueError):
        sequence = None
    raw = None
    if keep_raw:
        import json

        raw = json.dumps({k: (str(v) if not isinstance(v, (str, int, float, type(None))) else v) for k, v in data.items()},
                         separators=(",", ":"))
    return Trade(
        symbol=symbol,
        contract=contract,
        ts_event_ns=ts_ns,
        ts_us=round_us(ts_ns),
        price_nanos=px,
        size=size,
        side=normalize_side(data.get("side")),
        source=source,
        sequence=sequence,
        raw=raw,
    )


def normalize_frame(frame: pd.DataFrame, *, source: str) -> list[Trade]:
    """Historical rows (ts_event, symbol, price, size, side, ...) as Trades, in row order."""
    if frame is None or frame.empty:
        return []
    out: list[Trade] = []
    for record in frame.to_dict("records"):
        trade = normalize_record(record, source=source)
        if trade is not None:
            out.append(trade)
    return out


def normalize_records(records: Iterable[Mapping[str, Any]], *, source: str, keep_raw: bool = False) -> list[Trade]:
    return [t for t in (normalize_record(r, source=source, keep_raw=keep_raw) for r in records) if t is not None]
