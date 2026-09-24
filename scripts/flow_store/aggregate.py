"""Sparse 1-minute canonical bars with V1's DV01 formulas.

`B` = buy aggress (+1), `A` = sell aggress (-1); `N` prints are excluded entirely (V1 counted
them in trade_count). A minute gets a row only if it has at least one B/A print. Minutes are
bucketed by the microsecond event time both AccessAPI endpoints share.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from scripts.flow_store.normalize import Trade
from scripts.flow_store.settings import MINUTE_US


@dataclass(frozen=True)
class Bar:
    buy_contracts: float
    sell_contracts: float
    buy_flow: float
    sell_flow: float
    signed_contracts: float
    signed_flow: float
    trade_count: int
    trade_price: Optional[float]

    def as_dict(self) -> dict:
        return asdict(self)


def minute_floor_us(ts_us: int) -> int:
    return int(ts_us) - int(ts_us) % MINUTE_US


def bars_from_trades(trades: Iterable[Trade], *, dv01_unit: float) -> dict[int, Bar]:
    """Bars keyed by minute start (UTC microseconds). Trades must be in tape order within a µs."""
    unit = float(dv01_unit)
    acc: dict[int, list] = {}
    for trade in sorted(trades, key=lambda t: t.ts_us):
        if trade.side == "B":
            sign = 1
        elif trade.side == "A":
            sign = -1
        else:
            continue
        minute = minute_floor_us(trade.ts_us)
        row = acc.setdefault(minute, [0.0, 0.0, 0.0, 0.0, 0, None])
        size = float(trade.size)
        if sign == 1:
            row[0] += size
            row[2] += size * unit
        else:
            row[1] += size
            row[3] += size * unit
        row[4] += 1
        row[5] = trade.price
    return {
        minute: Bar(
            buy_contracts=b,
            sell_contracts=s,
            buy_flow=bf,
            sell_flow=sf,
            signed_contracts=b - s,
            signed_flow=bf - sf,
            trade_count=n,
            trade_price=px,
        )
        for minute, (b, s, bf, sf, n, px) in acc.items()
    }
