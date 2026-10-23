#!/usr/bin/env python3
"""Isolated Aggression V2.4 dashboard: V2 copy + paper trade-spec tracker.

Manual-only on port 5060. Writes only under workspace/analysis/aggression_v24_live/.
Does not modify live V1/V1.2/V2/V2.1/V2.2/V2.3/V3 or their stores.

Paper only. The offline gate verdict for this spec family is FAIL; this surface
exists to give the alert explicit entry/exit structure and to accumulate forward
out-of-sample evidence. It emits no executable long/short.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

from scripts.aggression_dashboard_v24.features import build_live_features
from scripts.aggression_dashboard_v24.live_buffer import LiveTbboBuffer
from scripts.aggression_dashboard_v24.model_state import RidgeModelState
from scripts.aggression_dashboard_v24.paper_store import BOOK_SPECS, PaperTradeStore
from scripts.aggression_dashboard_v24.trade_spec import (
    RESEARCH_CONTEXT,
    SPEC_LABEL,
    STRATEGY_STATUS,
)

LIVE_STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v24_live"
PORT = 5060

app = Flask(__name__, template_folder="templates", static_folder="static")
LIVE_BUFFER = LiveTbboBuffer()
MODEL_STATE = RidgeModelState()
PAPER = PaperTradeStore()
_LIVE_STARTED = False
_EVALUATOR_THREAD: threading.Thread | None = None
_EVALUATOR_RUNNING = False
ASSET_VERSION = "20260804_v24_paper"


def _ensure_live() -> None:
    global _LIVE_STARTED, _EVALUATOR_THREAD, _EVALUATOR_RUNNING
    if _LIVE_STARTED:
        return
    LIVE_BUFFER.start()
    MODEL_STATE.ensure_fit()
    _EVALUATOR_RUNNING = True
    _EVALUATOR_THREAD = threading.Thread(target=_background_evaluator, daemon=True)
    _EVALUATOR_THREAD.start()
    _LIVE_STARTED = True


def _evaluate_live_once() -> tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    raw = LIVE_BUFFER.snapshot()
    features = build_live_features(raw)
    model_df, model_meta = MODEL_STATE.predict(features)
    paper = PAPER.update(model_df) if not model_df.empty else PAPER.payload(None)
    return model_df, model_meta, paper


def _background_evaluator() -> None:
    while _EVALUATOR_RUNNING:
        try:
            _evaluate_live_once()
        except Exception:
            pass
        time.sleep(20.0)


def _finite(value: Any) -> Any:
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: _finite(value) for key, value in row.items()}
        for row in df.to_dict(orient="records")
    ]


@app.get("/")
def index() -> str:
    return render_template("index.html", asset_version=ASSET_VERSION)


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/api/config")
def api_config() -> Any:
    return jsonify({
        "success": True,
        "version": "V2.4",
        "strategy_status": STRATEGY_STATUS,
        "spec_label": SPEC_LABEL,
        "research_context": dict(RESEARCH_CONTEXT),
        "specs": [cfg.as_dict() | {"description": cfg.describe()} for cfg in BOOK_SPECS],
        "isolation": {
            "port": PORT,
            "store": str(LIVE_STORE_DIR),
            "v2_untouched": True,
            "executable": False,
        },
    })


@app.get("/api/signal_status")
def api_signal_status() -> Any:
    _ensure_live()
    return jsonify({
        "success": True,
        "feed": LIVE_BUFFER.status(),
        "model": MODEL_STATE.status(),
    })


@app.get("/api/live_signal")
def api_live_signal() -> Any:
    _ensure_live()
    model_df, model_meta, paper = _evaluate_live_once()
    if model_df.empty:
        return jsonify({
            "success": False,
            "error": "Waiting for live TBBO rows and model warmup.",
            "feed": LIVE_BUFFER.status(),
            "model": model_meta,
            "paper": paper,
        }), 202
    data = model_df.tail(240).copy()
    data["x"] = pd.DatetimeIndex(data.index).tz_convert("America/New_York").tz_localize(None).astype(str)
    latest = data.iloc[-1].replace({np.nan: None}).to_dict()
    cols = [c for c in ["x", "price", "fair_value", "residual", "residual_z"] if c in data.columns]
    return jsonify({
        "success": True,
        "feed": LIVE_BUFFER.status(),
        "model": model_meta,
        "latest": {key: _finite(value) for key, value in latest.items()},
        "series": _records(data[cols]),
        "paper": paper,
    })


@app.get("/api/paper")
def api_paper() -> Any:
    _ensure_live()
    scope = str(request.args.get("scope") or "today").lower()
    book = str(request.args.get("book") or "")
    _, _, paper = _evaluate_live_once()
    payload = {"success": True, "scope": scope, **paper}
    if book:
        payload["log_rows"] = PAPER.read_log(book, "all" if scope == "all" else "today")
    return jsonify(payload)


def main() -> None:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Aggression V2.4 paper trade-spec dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.getLogger().info(
        "Starting Aggression V2.4 on %s:%s (%s)", args.host, args.port, STRATEGY_STATUS
    )
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
