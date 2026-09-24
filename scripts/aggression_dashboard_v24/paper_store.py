"""Isolated paper trade-book store for Aggression V2.4.

Runs two books side by side over the same live model stream:
  * DISLOCATION_ONLY   - fade every qualifying dislocation
  * DISLOCATION_X_LIFO - fade only when trade intensity is low (LIFO gate)

The offline harness disagreed with itself across samples on whether the LIFO
gate helps, so running both accumulates the forward evidence that would settle
it. Writes only under workspace/analysis/aggression_v24_live/.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v24.lifo_context import annotate_lifo_flags
from scripts.aggression_dashboard_v24.trade_spec import (
    RESEARCH_CONTEXT,
    SPEC_LABEL,
    STRATEGY_STATUS,
    PaperBook,
    SpecConfig,
    equity_curve,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v24_live"
STATE_NAME = "paper_books.json"

BOOK_SPECS = (
    SpecConfig(name="DISLOCATION_ONLY", lifo_gate=False),
    SpecConfig(name="DISLOCATION_X_LIFO", lifo_gate=True),
)

TRADE_COLUMNS = [
    "book", "et_date", "side", "signal_ts", "signal_z", "entry_ts", "entry_price",
    "stop_price", "target_price", "exit_ts", "exit_price", "exit_reason",
    "gross_ticks", "cost_ticks", "net_ticks", "minutes_held", "mae_ticks", "mfe_ticks",
]


def _json_safe(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, default=_json_safe), encoding="utf-8")
    temp.replace(path)


class PaperTradeStore:
    def __init__(self, store_dir: Path = STORE_DIR) -> None:
        self.store_dir = Path(store_dir)
        self.state_path = self.store_dir / STATE_NAME
        self._lock = threading.Lock()
        self.books: dict[str, PaperBook] = {cfg.name: PaperBook(config=cfg) for cfg in BOOK_SPECS}
        self._logged: dict[str, int] = {name: 0 for name in self.books}
        self._load()

    # -------------------------------------------------------------- lifecycle

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        for name, payload in (raw.get("books") or {}).items():
            book = self.books.get(name)
            if book is None:
                continue
            book.armed = payload.get("armed")
            book.open_trade = payload.get("open_trade")
            book.trades = list(payload.get("trades") or [])
            last_ts = payload.get("last_ts")
            book.last_ts = pd.Timestamp(last_ts) if last_ts else None
            self._logged[name] = len(book.trades)

    def _save(self) -> None:
        payload = {
            "strategy_status": STRATEGY_STATUS,
            "books": {
                name: {
                    "config": book.config.as_dict(),
                    "armed": book.armed,
                    "open_trade": book.open_trade,
                    "trades": book.trades[-500:],
                    "last_ts": book.last_ts.isoformat() if book.last_ts is not None else None,
                }
                for name, book in self.books.items()
            },
        }
        _atomic_json(self.state_path, payload)

    # ----------------------------------------------------------------- update

    def update(self, model_df: pd.DataFrame) -> dict[str, Any]:
        with self._lock:
            if model_df is not None and not model_df.empty:
                annotated = annotate_lifo_flags(model_df)
                for book in self.books.values():
                    book.update(annotated)
                self._append_logs()
                self._save()
            return self._payload_unlocked(model_df)

    def _append_logs(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        for name, book in self.books.items():
            start = self._logged.get(name, 0)
            fresh = book.trades[start:]
            if not fresh:
                continue
            frame = pd.DataFrame(fresh)
            frame["book"] = name
            frame = frame.reindex(columns=TRADE_COLUMNS)
            path = self.store_dir / f"paper_trades_{name.lower()}.csv"
            frame.to_csv(path, mode="a", header=not path.exists(), index=False)
            self._logged[name] = len(book.trades)

    # ---------------------------------------------------------------- reading

    def payload(self, model_df: pd.DataFrame | None = None) -> dict[str, Any]:
        with self._lock:
            return self._payload_unlocked(model_df)

    def _payload_unlocked(self, model_df: pd.DataFrame | None) -> dict[str, Any]:
        last_price = None
        if model_df is not None and not model_df.empty and "price" in model_df.columns:
            prices = pd.to_numeric(model_df["price"], errors="coerce").dropna()
            if not prices.empty:
                last_price = float(prices.iloc[-1])
        return {
            "strategy_status": STRATEGY_STATUS,
            "spec_label": SPEC_LABEL,
            "research_context": dict(RESEARCH_CONTEXT),
            "last_price": last_price,
            "books": [
                {
                    "name": name,
                    "live": book.live_panel(last_price),
                    "stats": book.stats(),
                    "equity": equity_curve(book.trades)[-200:],
                    "recent_trades": [
                        {k: _json_safe(v) for k, v in trade.items()}
                        for trade in book.trades[-25:]
                    ],
                }
                for name, book in self.books.items()
            ],
        }

    def read_log(self, book: str, scope: str = "today") -> list[dict[str, Any]]:
        path = self.store_dir / f"paper_trades_{book.lower()}.csv"
        if not path.exists():
            return []
        try:
            frame = pd.read_csv(path)
        except Exception:
            return []
        if scope == "today" and "et_date" in frame.columns:
            today = str(pd.Timestamp.now(tz="America/New_York").date())
            frame = frame[frame["et_date"].astype(str) == today]
        return [
            {k: _json_safe(v) for k, v in row.items()}
            for row in frame.tail(200).to_dict(orient="records")
        ]
