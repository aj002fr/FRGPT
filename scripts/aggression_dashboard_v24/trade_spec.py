"""Paper trade-spec engine for isolated V2.4.

Turns the V2 residual DISLOCATION alert into a fully specified paper trade:
next-bar entry, fixed stop, fixed target, time stop, one position at a time,
explicit round-trip cost. Nothing here is executable.

Offline evidence for this spec is FAIL (0 PASS in 576 cells; the LIFO edge
inverts out of sample). See docs/aggression_v2_lifo_dislocation_trade_spec_experiments.md.
This module exists to give the alert clear entry/exit structure and to
accumulate forward, out-of-sample paper evidence — not to assert an edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

import numpy as np
import pandas as pd

TICK = 1.0 / 64.0

STRATEGY_STATUS = "PAPER / NOT VALIDATED / NON-EXECUTABLE"
SPEC_LABEL = "PAPER TRADE SPEC"

# Defaults mirror the representative cell scored by the offline harness so the
# live book and the research artifact describe the same spec.
DEFAULT_Z_ENTRY = 2.0
DEFAULT_STOP_TICKS = 3.0
DEFAULT_TARGET_TICKS = 2.0
DEFAULT_HORIZON_MIN = 30
DEFAULT_COST_TICKS = 0.5

RESEARCH_CONTEXT = {
    "verdict": "FAIL",
    "cells_scored": 576,
    "cells_passed": 0,
    "explore_holdout_corr": 0.234,
    "specs_positive_after_cost_both_samples": 0,
    "live_v2_gross_ticks_30m": 0.022,
    "note": (
        "Offline harness found no spec positive after 0.5-tick cost in both explore "
        "and holdout. The LIFO gate helped in-sample and inverted out of sample. "
        "Paper only."
    ),
}


@dataclass
class SpecConfig:
    """A fully specified, testable trade rule."""

    name: str
    z_entry: float = DEFAULT_Z_ENTRY
    lifo_gate: bool = False
    stop_ticks: float | None = DEFAULT_STOP_TICKS
    target_ticks: float | None = DEFAULT_TARGET_TICKS
    horizon_min: int = DEFAULT_HORIZON_MIN
    cost_ticks: float = DEFAULT_COST_TICKS

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def describe(self) -> str:
        gate = " and trade_intensity <= session p33 (LIFO)" if self.lifo_gate else ""
        stop = f"{self.stop_ticks:g}t stop" if self.stop_ticks is not None else "no stop"
        target = f"{self.target_ticks:g}t target" if self.target_ticks is not None else "no target"
        return (
            f"Fade when |residual_z| >= {self.z_entry:g}{gate}. "
            f"Enter at next bar, {stop}, {target}, {self.horizon_min}m time stop, "
            f"{self.cost_ticks:g}t round-trip cost."
        )


def _utc(ts: Any) -> pd.Timestamp:
    value = pd.Timestamp(ts)
    return value.tz_localize("UTC") if value.tzinfo is None else value.tz_convert("UTC")


def _et_date(ts: pd.Timestamp) -> str:
    return str(_utc(ts).tz_convert("America/New_York").date())


@dataclass
class PaperBook:
    """One spec's paper book: armed signal, open trade, and closed trades."""

    config: SpecConfig
    armed: dict[str, Any] | None = None
    open_trade: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = field(default_factory=list)
    last_ts: pd.Timestamp | None = None

    # ---------------------------------------------------------------- update

    def update(self, bars: pd.DataFrame) -> None:
        """Advance the book over completed bars.

        ``bars`` must be time-indexed with ``price``, ``residual``,
        ``residual_z`` and (when gated) ``trade_int_lo``. The most recent bar is
        excluded because it is still forming.
        """
        if bars is None or bars.empty:
            return
        frame = bars.dropna(subset=["price", "residual", "residual_z"]).copy()
        if len(frame) < 2:
            return
        frame = frame.iloc[:-1]  # newest bar is still forming
        if self.last_ts is not None:
            frame = frame[pd.DatetimeIndex(frame.index) > self.last_ts]
        if frame.empty:
            return
        for ts, row in frame.iterrows():
            self._step(_utc(ts), row)
            self.last_ts = _utc(ts)

    def _step(self, ts: pd.Timestamp, row: pd.Series) -> None:
        price = float(row["price"])
        if self.open_trade is not None:
            self._manage_open(ts, price)
            return
        if self.armed is not None:
            self._enter(ts, price)
            return
        self._maybe_arm(ts, row)

    # ------------------------------------------------------------ transitions

    def _maybe_arm(self, ts: pd.Timestamp, row: pd.Series) -> None:
        z = row.get("residual_z")
        if z is None or not np.isfinite(float(z)):
            return
        if abs(float(z)) < self.config.z_entry:
            return
        if self.config.lifo_gate and not bool(row.get("trade_int_lo", False)):
            return
        residual = float(row["residual"])
        if residual == 0.0:
            return
        self.armed = {
            "signal_ts": ts.isoformat(),
            "signal_price": float(row["price"]),
            "signal_z": float(z),
            "signal_residual": residual,
            "direction_sign": -1.0 if residual > 0 else 1.0,
            "side": "SHORT" if residual > 0 else "LONG",
        }

    def _enter(self, ts: pd.Timestamp, price: float) -> None:
        armed = self.armed or {}
        direction = float(armed.get("direction_sign", 0.0))
        cfg = self.config
        stop_price = price - direction * cfg.stop_ticks * TICK if cfg.stop_ticks is not None else None
        target_price = price + direction * cfg.target_ticks * TICK if cfg.target_ticks is not None else None
        self.open_trade = {
            **armed,
            "entry_ts": ts.isoformat(),
            "entry_price": price,
            "stop_price": stop_price,
            "target_price": target_price,
            "deadline_ts": (ts + pd.Timedelta(minutes=cfg.horizon_min)).isoformat(),
            "et_date": _et_date(ts),
            "mae_ticks": 0.0,
            "mfe_ticks": 0.0,
            "status": "open",
        }
        self.armed = None

    def _manage_open(self, ts: pd.Timestamp, price: float) -> None:
        trade = self.open_trade
        if trade is None:
            return
        cfg = self.config
        direction = float(trade["direction_sign"])
        entry = float(trade["entry_price"])
        excursion = direction * (price - entry) / TICK
        trade["mae_ticks"] = min(float(trade.get("mae_ticks", 0.0)), excursion)
        trade["mfe_ticks"] = max(float(trade.get("mfe_ticks", 0.0)), excursion)

        # Conservative ordering: a bar that spans both levels is booked as a stop.
        if cfg.stop_ticks is not None and excursion <= -cfg.stop_ticks:
            self._close(ts, price, "stop", -float(cfg.stop_ticks))
            return
        if cfg.target_ticks is not None and excursion >= cfg.target_ticks:
            self._close(ts, price, "target", float(cfg.target_ticks))
            return
        if ts >= _utc(trade["deadline_ts"]):
            self._close(ts, price, "time", float(excursion))

    def _close(self, ts: pd.Timestamp, price: float, reason: str, gross_ticks: float) -> None:
        trade = self.open_trade
        if trade is None:
            return
        entry_ts = _utc(trade["entry_ts"])
        trade.update(
            {
                "exit_ts": ts.isoformat(),
                "exit_price": price,
                "exit_reason": reason,
                "gross_ticks": float(gross_ticks),
                "cost_ticks": float(self.config.cost_ticks),
                "net_ticks": float(gross_ticks) - float(self.config.cost_ticks),
                "minutes_held": float((ts - entry_ts).total_seconds() / 60.0),
                "status": "closed",
            }
        )
        self.trades.append(trade)
        self.open_trade = None

    # ---------------------------------------------------------------- reading

    def live_panel(self, last_price: float | None) -> dict[str, Any]:
        """Current actionable state: what to do right now, at what prices."""
        cfg = self.config
        base = {
            "spec": cfg.as_dict(),
            "spec_text": cfg.describe(),
            "state": "FLAT",
            "side": None,
            "entry_price": None,
            "stop_price": None,
            "target_price": None,
            "deadline_ts": None,
            "unrealized_ticks": None,
            "mae_ticks": None,
            "mfe_ticks": None,
            "instruction": "No qualifying dislocation. Flat.",
        }
        if self.armed is not None:
            base.update(
                {
                    "state": "ARMED",
                    "side": self.armed.get("side"),
                    "instruction": (
                        f"Signal fired at z={float(self.armed.get('signal_z', 0.0)):.2f}. "
                        f"Paper entry {self.armed.get('side')} at the next bar open."
                    ),
                }
            )
            return base
        trade = self.open_trade
        if trade is not None:
            direction = float(trade["direction_sign"])
            entry = float(trade["entry_price"])
            unreal = (direction * (float(last_price) - entry) / TICK) if last_price is not None else None
            base.update(
                {
                    "state": "IN_TRADE",
                    "side": trade.get("side"),
                    "entry_price": entry,
                    "stop_price": trade.get("stop_price"),
                    "target_price": trade.get("target_price"),
                    "deadline_ts": trade.get("deadline_ts"),
                    "unrealized_ticks": unreal,
                    "mae_ticks": trade.get("mae_ticks"),
                    "mfe_ticks": trade.get("mfe_ticks"),
                    "instruction": (
                        f"Paper {trade.get('side')} from {entry:.5f}. "
                        f"Exit on stop, target, or {cfg.horizon_min}m time stop."
                    ),
                }
            )
        return base

    def stats(self) -> dict[str, Any]:
        return summarize_trades(self.trades, self.config)


def summarize_trades(trades: Iterable[dict[str, Any]], config: SpecConfig) -> dict[str, Any]:
    """After-cost paper statistics. Honest about small samples."""
    rows = [t for t in trades if t.get("status") == "closed"]
    out: dict[str, Any] = {
        "n_trades": len(rows),
        "n_dates": 0,
        "mean_gross_ticks": None,
        "mean_net_ticks": None,
        "total_net_ticks": None,
        "hit_rate": None,
        "mean_mae_ticks": None,
        "median_minutes_held": None,
        "exit_mix": {},
        "evidence_note": (
            "Paper only. Offline gate verdict for this spec family is FAIL; "
            "live counts below are forward evidence, not a validated edge."
        ),
    }
    if not rows:
        return out
    frame = pd.DataFrame(rows)
    gross = pd.to_numeric(frame["gross_ticks"], errors="coerce")
    net = pd.to_numeric(frame["net_ticks"], errors="coerce")
    out.update(
        {
            "n_dates": int(frame["et_date"].nunique()),
            "mean_gross_ticks": float(gross.mean()),
            "mean_net_ticks": float(net.mean()),
            "total_net_ticks": float(net.sum()),
            "hit_rate": float((net > 0).mean()),
            "mean_mae_ticks": float(pd.to_numeric(frame["mae_ticks"], errors="coerce").mean()),
            "median_minutes_held": float(pd.to_numeric(frame["minutes_held"], errors="coerce").median()),
            "exit_mix": {k: int(v) for k, v in frame["exit_reason"].value_counts().items()},
            "cost_ticks": float(config.cost_ticks),
        }
    )
    return out


def equity_curve(trades: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [t for t in trades if t.get("status") == "closed"]
    curve: list[dict[str, Any]] = []
    running = 0.0
    for trade in rows:
        running += float(trade.get("net_ticks") or 0.0)
        curve.append(
            {
                "x": str(trade.get("exit_ts")),
                "net_ticks": float(trade.get("net_ticks") or 0.0),
                "cumulative_net_ticks": running,
            }
        )
    return curve
