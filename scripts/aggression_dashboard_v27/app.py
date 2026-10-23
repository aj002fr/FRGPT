#!/usr/bin/env python3
"""Isolated Aggression V2.7: V2.2 copy + B1 10-minute local fair value.

Manual-only on port 5063. Writes only under workspace/analysis/aggression_v27_live/.
Does not modify live V1/V1.2/V2/V2.1/V2.2/V2.3/V2.4/V2.6/V3 or their stores.
Named V2.7 because V2.3 is already the move-exhaustion monitor.
"""

from __future__ import annotations

import json
import logging
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

from scripts.aggression_dashboard_v27.alerts import AlertStore, attach_session_residual
from scripts.aggression_dashboard_v27.features import build_live_features
from scripts.aggression_dashboard_v27.lifo_context import LIFO_FRIENDLY_LABEL
from scripts.aggression_dashboard_v27.lifo_store import LifoStore
from scripts.aggression_dashboard_v27.live_buffer import LiveTbboBuffer
from scripts.aggression_dashboard_v27.model_state import RidgeModelState
from scripts.dashboard_runtime.serve import add_server_args, configure_logging, install_request_timing, serve


SHORT_HORIZON_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_short_horizon_fv"
PREDICTIONS_PATH = SHORT_HORIZON_DIR / "holdout_predictions.csv"
MODEL_METRICS_PATH = SHORT_HORIZON_DIR / "construction_metrics_holdout.csv"
EXPLAINER_PATH = PROJECT_ROOT / "docs" / "aggression_v2_short_horizon_fv_experiments.md"
LIVE_STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v27_live"
PORT = 5063
STRATEGY_STATUS = "PROVISIONAL / RESEARCH-ONLY / LOCAL-10M-FV / NOT AFTER-COST VALIDATED"
SWEET_SPOT_RULE = {
    "threshold": 1.5,
    "horizon": 10,
    "condition": "B1_W10 enter |z|>=1.5 and session 2-6 ticks same sign, no same-sign re-entry; exit |z|<0.5 after 5m or flip; 10m is a mark not a flatten",
    "n": 506,
    "p_adverse_le_3_5m": 0.980113,
    "p_adverse_le_3_10m": 0.932421,
    "median_lean_duration_min": 5.5,
    "note": "2026-08-31 stability sweet-spot. Not an after-cost hit rate.",
}

app = Flask(__name__, template_folder="templates", static_folder="static")
LIVE_BUFFER = LiveTbboBuffer()
MODEL_STATE = RidgeModelState()
ALERTS = AlertStore()
LIFO = LifoStore()
_LIVE_STARTED = False
_EVALUATOR_THREAD: threading.Thread | None = None
_EVALUATOR_RUNNING = False
_SIGNAL_LOG_LAST_TS: dict[str, pd.Timestamp] = {}
ASSET_VERSION = "20260922_v27_lifo_lean_color"


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


def _evaluate_live_once() -> tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    raw = LIVE_BUFFER.snapshot()
    features = build_live_features(raw)
    model_df, model_meta = MODEL_STATE.predict(features)
    model_df = attach_session_residual(model_df)
    alert_summary = ALERTS.update(model_df) if not model_df.empty else ALERTS.summary(None)
    lifo_summary = LIFO.update(model_df) if not model_df.empty else LIFO.payload(None)
    return model_df, model_meta, alert_summary, lifo_summary


PRODUCER_POLL_SEC = 1.0
PRODUCER_MAX_SEC = 20.0
_PRODUCED_LOCK = threading.Lock()
_PRODUCED: Dict[str, Any] = {"result": None}
_PRODUCED_READY = threading.Event()


def _background_evaluator() -> None:
    """The only live evaluation: once per new TBBO tick (at most 1/s, at least every 20 s).

    Requests read the latest result, so alert/LIFO stores update once per tick, not per viewer.
    """
    last_tick: Any = object()
    last_run = 0.0
    while _EVALUATOR_RUNNING:
        tick = LIVE_BUFFER.status().get("last_tick_ts")
        if tick != last_tick or time.monotonic() - last_run >= PRODUCER_MAX_SEC:
            last_tick, last_run = tick, time.monotonic()
            try:
                result = _evaluate_live_once()
                with _PRODUCED_LOCK:
                    _PRODUCED["result"] = result
                _PRODUCED_READY.set()
                _append_signal_log(result[0])
            except Exception:
                logging.getLogger(__name__).exception("V2.7 live evaluation failed")
        time.sleep(PRODUCER_POLL_SEC)


def _latest_evaluation(timeout: float = 15.0):
    if not _PRODUCED_READY.wait(timeout):
        return None
    with _PRODUCED_LOCK:
        return _PRODUCED["result"]


def _append_signal_log(model_df: pd.DataFrame) -> None:
    if model_df.empty:
        return
    cols = ["price", "fair_value", "residual", "residual_z"]
    if any(col not in model_df.columns for col in cols):
        return
    data = model_df.dropna(subset=["price", "fair_value", "residual", "residual_z"]).copy()
    if data.empty:
        return
    LIVE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    idx = pd.DatetimeIndex(data.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    data.index = idx
    et_dates = data.index.tz_convert("America/New_York").strftime("%Y%m%d")
    for et_date in sorted(set(et_dates)):
        day_rows = data[et_dates == et_date].copy()
        if day_rows.empty:
            continue
        path = LIVE_STORE_DIR / f"signal_log_{et_date}.csv"
        last_ts = _SIGNAL_LOG_LAST_TS.get(et_date)
        if last_ts is None and path.exists():
            try:
                existing = pd.read_csv(path, usecols=["ts"], parse_dates=["ts"])
                if not existing.empty:
                    last_ts = pd.to_datetime(existing["ts"], utc=True).max()
            except Exception:
                last_ts = None
        if last_ts is not None:
            day_rows = day_rows[day_rows.index > last_ts]
        if day_rows.empty:
            if last_ts is not None:
                _SIGNAL_LOG_LAST_TS[et_date] = pd.Timestamp(last_ts)
            continue
        out = day_rows[cols].reset_index(names="ts")
        out["ts"] = pd.to_datetime(out["ts"], utc=True).map(lambda ts: ts.isoformat())
        out.to_csv(path, mode="a", header=not path.exists(), index=False)
        _SIGNAL_LOG_LAST_TS[et_date] = pd.Timestamp(day_rows.index.max())


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _finite(value: Any) -> Any:
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    return value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {key: _finite(value) for key, value in row.items()}
        for row in df.to_dict(orient="records")
    ]


def _predictions() -> pd.DataFrame:
    if not PREDICTIONS_PATH.exists():
        return pd.DataFrame(columns=["bucket_ts", "et_date", "model", "price", "fair_value", "residual", "residual_z"])
    df = pd.read_csv(PREDICTIONS_PATH, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    if "construction" in df.columns:
        df = df[df["construction"] == "B1_W10"].copy()
    df["model"] = "ridge_b1_w10"
    df.sort_values(["model", "et_date", "bucket_ts"], inplace=True)
    return df


def _residual_z_by_day(df: pd.DataFrame) -> pd.Series:
    if "residual_z" in df.columns and df["residual_z"].notna().any():
        return df["residual_z"]
    return df.groupby("et_date")["residual"].transform(
        lambda x: (x - x.mean()) / (x.std(ddof=1) if x.std(ddof=1) else np.nan)
    )


def _model_metrics() -> list[dict[str, Any]]:
    if not MODEL_METRICS_PATH.exists():
        return []
    metrics = pd.read_csv(MODEL_METRICS_PATH)
    keep = metrics[metrics["construction"].isin(["session", "B1_W10"])].copy() if "construction" in metrics.columns else metrics
    return _records(keep)


def _best_holdout_rules() -> list[dict[str, Any]]:
    return [dict(SWEET_SPOT_RULE)]


def _best_conditional_rules() -> list[dict[str, Any]]:
    return []


def _driver_rows(model: str) -> list[dict[str, Any]]:
    del model
    return []


def _plain_english(model: str, date: str, latest: dict[str, Any], holdout: list[dict[str, Any]]) -> str:
    if not latest:
        return "No prediction rows available for the selected model/date."
    residual = float(latest.get("residual") or 0.0)
    residual_z = latest.get("residual_z")
    state = "rich" if residual > 0 else "cheap" if residual < 0 else "fair"
    direction = "fade lower / short bias" if residual > 0 else "fade higher / long bias" if residual < 0 else "no directional bias"
    strongest = holdout[0] if holdout else SWEET_SPOT_RULE
    if strongest:
        rule = (
            f"Sweet-spot display rule is {strongest.get('condition')} "
            f"(median lean {strongest.get('median_lean_duration_min')}m; "
            f"P(5m adverse<=3 ticks)={float(strongest.get('p_adverse_le_3_5m') or 0)*100:.1f}%)."
        )
    else:
        rule = "No holdout rule is available."
    z_text = "n/a" if residual_z is None else f"{float(residual_z):.2f}"
    return (
        f"Selected model {model} on {date}: latest price is {state} vs 10-minute local fair value "
        f"(residual {residual:.5f}, z {z_text}). Research direction would be {direction}. {rule} "
        "This is research-only and does not include costs or execution delay."
    )


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
    pred = _predictions()
    models = sorted(pred["model"].dropna().unique().tolist()) if not pred.empty else ["ridge_b1_w10"]
    dates = sorted(pred["et_date"].dropna().unique().tolist()) if not pred.empty else []
    return jsonify({
        "models": models,
        "dates": dates,
        "default_model": "ridge_b1_w10",
        "default_date": dates[-1] if dates else "",
        "metrics": _model_metrics(),
        "holdout_rules": _best_holdout_rules()[:10],
        "conditional_rules": _best_conditional_rules()[:10],
        "explainer": EXPLAINER_PATH.read_text(encoding="utf-8") if EXPLAINER_PATH.exists() else "",
        "version": "V2.7",
        "strategy_status": STRATEGY_STATUS,
        "lifo_friendly_label": LIFO_FRIENDLY_LABEL,
        "construction": "B1_W10",
        "isolation": {
            "port": PORT,
            "store": str(LIVE_STORE_DIR),
            "v2_untouched": True,
            "v22_untouched": True,
            "v23_untouched": True,
        },
    })


@app.get("/api/research")
def api_research() -> Any:
    model = str(request.args.get("model") or "ridge")
    date = str(request.args.get("date") or "")
    pred = _predictions()
    data = pred[pred["model"] == model].copy()
    if date:
        data = data[data["et_date"] == date].copy()
    if data.empty:
        return jsonify({"success": False, "error": "No rows for selected model/date."}), 404
    data["residual_z"] = _residual_z_by_day(data)
    data["x"] = data["bucket_ts"].dt.tz_convert("America/New_York").dt.tz_localize(None).astype(str)
    latest = data.iloc[-1].replace({np.nan: None}).to_dict()
    holdout = _best_holdout_rules()[:10]
    return jsonify({
        "success": True,
        "model": model,
        "date": date or "all",
        "series": _records(data[["x", "price", "fair_value", "residual", "residual_z"]]),
        "latest": {key: _finite(value) for key, value in latest.items()},
        "drivers": _driver_rows(model),
        "plain_english": _plain_english(model, date or "all dates", latest, holdout),
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
    produced = _latest_evaluation()
    if produced is None:
        return jsonify({
            "success": False,
            "error": "Waiting for the first live evaluation.",
            "feed": LIVE_BUFFER.status(),
            "model": MODEL_STATE.status(),
            "lifo": LIFO.payload(None),
            "alerts": ALERTS.summary(None),
        }), 202
    model_df, model_meta, alert_summary, lifo_summary = produced
    if model_df.empty:
        return jsonify({
            "success": False,
            "error": "Waiting for live TBBO rows and model warmup.",
            "feed": LIVE_BUFFER.status(),
            "model": model_meta,
            "lifo": lifo_summary,
            "alerts": ALERTS.summary(None),
        }), 202
    data = model_df.tail(240).copy()
    data["x"] = pd.DatetimeIndex(data.index).tz_convert("America/New_York").tz_localize(None).astype(str)
    latest = data.iloc[-1].replace({np.nan: None}).to_dict()
    residual = float(latest.get("residual") or 0.0)
    z = latest.get("residual_z")
    abs_z = abs(float(z)) if z is not None else 0.0
    state = "DISLOCATION" if abs_z >= 2.0 else "WATCH" if abs_z >= 1.5 else "NONE"
    direction = "SHORT / fade rich" if residual > 0 else "LONG / fade cheap" if residual < 0 else "none"
    message = (
        f"{state}: residual z={float(z):.2f}, price is "
        f"{'rich' if residual > 0 else 'cheap' if residual < 0 else 'fair'} vs 10-minute local fair value. "
        f"Research direction: {direction}. Success means |z| closes below 0.5 after a 5-minute min-hold, or a flip. 10m is a mark, not a flatten."
    ) if state != "NONE" and z is not None else "No active local-10m lean. Live model is monitoring B1_W10 residual z-score."
    return jsonify({
        "success": True,
        "state": state,
        "message": message,
        "feed": LIVE_BUFFER.status(),
        "model": model_meta,
        "latest": {key: _finite(value) for key, value in latest.items()},
        "series": _records(data[["x", "price", "fair_value", "residual", "residual_z"]]),
        "alerts": alert_summary,
        "lifo": lifo_summary,
        "benchmark": dict(SWEET_SPOT_RULE),
    })


@app.get("/api/alert_log")
def api_alert_log() -> Any:
    _ensure_live()
    scope = str(request.args.get("scope") or "today").lower()
    rows = ALERTS.log_rows("all" if scope == "all" else "today")
    summary = ALERTS.summary(None)
    stats = summary.get("session_stats", {}) if scope == "today" else summary.get("stats", {})
    return jsonify({
        "success": True,
        "scope": scope,
        "rows": rows[-200:],
        "stats": stats,
        "session_stats": summary.get("session_stats", {}),
        "benchmark": dict(SWEET_SPOT_RULE),
    })


@app.get("/api/lifo")
def api_lifo() -> Any:
    _ensure_live()
    scope = str(request.args.get("scope") or "today").lower()
    produced = _latest_evaluation()
    lifo_summary = produced[3] if produced is not None else LIFO.payload(None)
    return jsonify({
        "success": True,
        "scope": scope,
        **lifo_summary,
        "log_rows": LIFO.read_log("all" if scope == "all" else "today")[-200:],
    })


def main() -> None:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Aggression V2.7 research dashboard (V2.2 + local 10m FV)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--debug", action="store_true")
    add_server_args(parser)
    args = parser.parse_args()
    configure_logging(f"v27_{args.port}")
    install_request_timing(app)
    logging.getLogger().info(
        "Starting Aggression V2.7 on %s:%s (%s, %s)",
        args.host,
        args.port,
        STRATEGY_STATUS,
        args.server,
    )
    serve(app, host=args.host, port=args.port, server=args.server, debug=args.debug)


if __name__ == "__main__":
    main()
