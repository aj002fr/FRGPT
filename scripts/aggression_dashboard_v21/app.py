#!/usr/bin/env python3
"""Isolated Aggression V2.1 event-flow paper dashboard on port 5054."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.aggression_dashboard_v21.alerts import residual_state  # noqa: E402
from scripts.aggression_dashboard_v21.event_flow import EventSchedule  # noqa: E402
from scripts.aggression_dashboard_v21.event_store import EventPaperStore  # noqa: E402
from scripts.aggression_dashboard_v21.features import build_live_features  # noqa: E402
from scripts.aggression_dashboard_v21.live_buffer import LiveTbboBuffer  # noqa: E402
from scripts.aggression_dashboard_v21.magnitude_context import magnitude_context  # noqa: E402
from scripts.aggression_dashboard_v21.model_state import RidgeModelState  # noqa: E402

LIVE_STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v21_live"
LOCK_GATE_PATH = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_event_window_lock" / "lock_entry_gate.json"
ASSET_VERSION = "20260710_v21_magnitude_context"

app = Flask(__name__, template_folder="templates", static_folder="static")
LIVE_BUFFER = LiveTbboBuffer()
MODEL_STATE = RidgeModelState()
SCHEDULE = EventSchedule()
EVENTS = EventPaperStore()
_LIVE_STARTED = False
_EVALUATOR_RUNNING = False
_EVALUATOR_THREAD: threading.Thread | None = None
_SIGNAL_LOG_LAST_TS: dict[str, pd.Timestamp] = {}


def _finite(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _records(data: pd.DataFrame) -> list[dict[str, Any]]:
    return [{key: _finite(value) for key, value in row.items()} for row in data.to_dict(orient="records")]


def _read_gate() -> dict[str, Any]:
    if not LOCK_GATE_PATH.exists():
        return {}
    try:
        return json.loads(LOCK_GATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ensure_live() -> None:
    global _LIVE_STARTED, _EVALUATOR_RUNNING, _EVALUATOR_THREAD
    if _LIVE_STARTED:
        return
    LIVE_BUFFER.start()
    SCHEDULE.refresh(force=True)
    _EVALUATOR_RUNNING = True
    _EVALUATOR_THREAD = threading.Thread(target=_background_evaluator, daemon=True)
    _EVALUATOR_THREAD.start()
    _LIVE_STARTED = True


def _evaluate_once() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    raw = LIVE_BUFFER.snapshot()
    features = build_live_features(raw)
    schedule, schedule_status = SCHEDULE.snapshot()
    EVENTS.update(features, schedule, LIVE_BUFFER.status())
    try:
        model_df, model_meta = MODEL_STATE.predict(features)
    except Exception as exc:
        model_df, model_meta = pd.DataFrame(), {"ok": False, "reason": str(exc)}
    return features, model_df, model_meta, schedule_status


def _background_evaluator() -> None:
    while _EVALUATOR_RUNNING:
        try:
            _, model_df, _, _ = _evaluate_once()
            _append_signal_log(model_df)
        except Exception:
            pass
        time.sleep(20.0)


def _append_signal_log(model_df: pd.DataFrame) -> None:
    if model_df.empty:
        return
    cols = ["price", "fair_value", "residual", "residual_z"]
    data = model_df.dropna(subset=cols).copy()
    if data.empty:
        return
    LIVE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    idx = pd.DatetimeIndex(data.index)
    data.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    et_dates = data.index.tz_convert("America/New_York").strftime("%Y%m%d")
    for et_date in sorted(set(et_dates)):
        day = data[et_dates == et_date].copy()
        path = LIVE_STORE_DIR / f"signal_log_{et_date}.csv"
        last_ts = _SIGNAL_LOG_LAST_TS.get(et_date)
        if last_ts is None and path.exists():
            try:
                existing = pd.read_csv(path, usecols=["ts"])
                last_ts = pd.to_datetime(existing["ts"], utc=True).max()
            except Exception:
                last_ts = None
        if last_ts is not None:
            day = day[day.index > last_ts]
        if day.empty:
            continue
        out = day[cols].reset_index(names="ts")
        out["ts"] = pd.to_datetime(out["ts"], utc=True).map(lambda ts: ts.isoformat())
        out.to_csv(path, mode="a", header=not path.exists(), index=False)
        _SIGNAL_LOG_LAST_TS[et_date] = day.index.max()


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/")
def index() -> str:
    return render_template("index.html", asset_version=ASSET_VERSION)


@app.get("/api/config")
def api_config() -> Any:
    gate = _read_gate()
    return jsonify(
        {
            "success": True,
            "version": "V2.1",
            "port": 5054,
            "strategy_status": "PROVISIONAL / RESEARCH-ONLY",
            "entry_semantics": "T+5 flow lock; paper entry at observed lock price",
            "horizons_after_lock": [5, 15, 30],
            "cost_ticks": 0.5,
            "corrected_research": gate,
            "isolation": {"store": str(LIVE_STORE_DIR), "v1_v2_v3_modified": False},
        }
    )


@app.get("/api/signal_status")
def api_signal_status() -> Any:
    _ensure_live()
    _, schedule_status = SCHEDULE.snapshot()
    return jsonify(
        {
            "success": True,
            "feed": LIVE_BUFFER.status(),
            "model": MODEL_STATE.status(),
            "events_db": schedule_status,
        }
    )


@app.get("/api/live_signal")
def api_live_signal() -> Any:
    _ensure_live()
    _, model_df, model_meta, _ = _evaluate_once()
    if model_df.empty:
        return jsonify(
            {
                "success": False,
                "error": "Waiting for live TBBO rows and model warmup.",
                "feed": LIVE_BUFFER.status(),
                "model": model_meta,
            }
        ), 202
    data = model_df.tail(240).copy()
    data["x"] = pd.DatetimeIndex(data.index).tz_convert("America/New_York").tz_localize(None).astype(str)
    latest = {key: _finite(value) for key, value in data.iloc[-1].to_dict().items()}
    context = residual_state(latest)
    magnitude = magnitude_context(model_df)
    return jsonify(
        {
            "success": True,
            **context,
            "feed": LIVE_BUFFER.status(),
            "model": model_meta,
            "latest": latest,
            "magnitude_context": magnitude,
            "series": _records(data[["x", "price", "fair_value", "residual", "residual_z"]]),
        }
    )


@app.get("/api/event_flow")
def api_event_flow() -> Any:
    _ensure_live()
    _evaluate_once()
    schedule, schedule_status = SCHEDULE.snapshot()
    return jsonify(EVENTS.payload(schedule, schedule_status, LIVE_BUFFER.status()))


@app.get("/api/event_log")
def api_event_log() -> Any:
    _ensure_live()
    scope = str(request.args.get("scope") or "today").lower()
    return jsonify({"success": True, "scope": scope, "rows": EVENTS.log_rows("all" if scope == "all" else "today")})


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggression V2.1 event-flow paper dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5054)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
