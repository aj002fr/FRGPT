"""Observational store for V2.3 exhaustion-context snapshots."""

from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from scripts.aggression_dashboard_v23.exhaustion_context import (
    STRATEGY_STATUS,
    annotate_exhaustion_panel,
    latest_exhaustion_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v23_live"
STATE_PATH = STORE_DIR / "exhaustion_state.json"
LOG_PATH = STORE_DIR / "exhaustion_context_log.csv"


class ExhaustionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_badges: tuple[str, ...] = ()
        STORE_DIR.mkdir(parents=True, exist_ok=True)

    def update(self, model_df: pd.DataFrame) -> dict[str, Any]:
        with self._lock:
            panel = annotate_exhaustion_panel(model_df) if not model_df.empty else model_df
            context = latest_exhaustion_context(panel)
            badges = tuple(context.get("active_badges") or [])
            if badges and badges != self._last_badges:
                self._append_log(context)
                self._last_badges = badges
            elif not badges:
                self._last_badges = ()
            payload = {
                "context": context,
                "strategy_status": STRATEGY_STATUS,
                "store": str(STORE_DIR),
            }
            STATE_PATH.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            return payload

    def payload(self, model_df: pd.DataFrame | None) -> dict[str, Any]:
        if model_df is None or model_df.empty:
            return {
                "context": latest_exhaustion_context(pd.DataFrame()),
                "strategy_status": STRATEGY_STATUS,
                "store": str(STORE_DIR),
            }
        return self.update(model_df)

    def _append_log(self, context: dict[str, Any]) -> None:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        row = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "exh_state": context.get("exh_state"),
            "exh_prob": context.get("exh_prob"),
            "stand_aside": context.get("stand_aside"),
            "stand_aside_reason": context.get("stand_aside_reason"),
            "move_event": context.get("move_event"),
            "v4_align": context.get("v4_align"),
            "wide_spread": context.get("wide_spread"),
            "book_fading": context.get("book_fading"),
            "dynamics_lean": context.get("dynamics_lean"),
            "curve_wide": context.get("curve_wide"),
            "badges": "|".join(context.get("active_badges") or []),
            "residual_z": context.get("residual_z"),
        }
        write_header = not LOG_PATH.exists()
        with LOG_PATH.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            if write_header:
                writer.writeheader()
            writer.writerow(row)
