#!/usr/bin/env python3
"""Isolated Aggression V2.2 research dashboard: V2 copy + LIFO FRIENDLY alert.

Manual-only on port 5058. Writes only under workspace/analysis/aggression_v22_live/.
Does not modify live V1/V2/V3 or their stores.
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

from scripts.aggression_dashboard_v22.alerts import AlertStore
from scripts.aggression_dashboard_v22.features import build_live_features
from scripts.aggression_dashboard_v22.lifo_context import LIFO_FRIENDLY_LABEL, STRATEGY_STATUS
from scripts.aggression_dashboard_v22.lifo_store import LifoStore
from scripts.aggression_dashboard_v22.live_buffer import LiveTbboBuffer
from scripts.aggression_dashboard_v22.model_state import RidgeModelState
from scripts.dashboard_runtime.serve import add_server_args, configure_logging, install_request_timing, serve


ARTIFACT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models"
PREDICTIONS_PATH = ARTIFACT_DIR / "walkforward_predictions.csv"
MODEL_METRICS_PATH = ARTIFACT_DIR / "model_metrics.json"
COEF_PATH = ARTIFACT_DIR / "model_coefficients_importance.csv"
CONDITIONAL_PATH = ARTIFACT_DIR / "conditional_signal_summary.json"
HOLDOUT_PATH = ARTIFACT_DIR / "holdout_signal_summary.json"
EXPLAINER_PATH = PROJECT_ROOT / "docs" / "aggression_v2_model_explainer.md"
LIVE_STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v22_live"

app = Flask(__name__, template_folder="templates", static_folder="static")
LIVE_BUFFER = LiveTbboBuffer()
MODEL_STATE = RidgeModelState()
ALERTS = AlertStore()
LIFO = LifoStore()
_LIVE_STARTED = False
_EVALUATOR_THREAD: threading.Thread | None = None
_EVALUATOR_RUNNING = False
_SIGNAL_LOG_LAST_TS: dict[str, pd.Timestamp] = {}
ASSET_VERSION = "20260922_v22_lifo_lean_color"


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
                logging.getLogger(__name__).exception("V2.2 live evaluation failed")
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
    df = pd.read_csv(PREDICTIONS_PATH, parse_dates=["bucket_ts"])
    df["bucket_ts"] = pd.to_datetime(df["bucket_ts"], utc=True)
    df.sort_values(["model", "et_date", "bucket_ts"], inplace=True)
    return df


def _coef() -> pd.DataFrame:
    if not COEF_PATH.exists():
        return pd.DataFrame(columns=["model", "feature", "value", "kind"])
    return pd.read_csv(COEF_PATH)


def _residual_z_by_day(df: pd.DataFrame) -> pd.Series:
    return df.groupby("et_date")["residual"].transform(
        lambda x: (x - x.mean()) / (x.std(ddof=1) if x.std(ddof=1) else np.nan)
    )


def _model_metrics() -> list[dict[str, Any]]:
    raw = _read_json(MODEL_METRICS_PATH)
    return raw.get("model_metrics") or []


def _best_holdout_rules() -> list[dict[str, Any]]:
    raw = _read_json(HOLDOUT_PATH)
    return raw.get("best_holdout_rules") or []


def _best_conditional_rules() -> list[dict[str, Any]]:
    raw = _read_json(CONDITIONAL_PATH)
    return raw.get("best_conditions") or []


def _driver_rows(model: str) -> list[dict[str, Any]]:
    coef = _coef()
    if coef.empty:
        return []
    data = coef[(coef["model"] == model) & (~coef["feature"].astype(str).str.startswith("__"))].copy()
    if data.empty:
        return []
    grouped = data.groupby(["feature", "kind"], as_index=False)["value"].agg(
        mean="mean",
        mean_abs=lambda x: x.abs().mean(),
        count="count",
    )
    grouped.sort_values("mean_abs", ascending=False, inplace=True)
    return _records(grouped.head(20))


def _plain_english(model: str, date: str, latest: dict[str, Any], holdout: list[dict[str, Any]]) -> str:
    if not latest:
        return "No prediction rows available for the selected model/date."
    residual = float(latest.get("residual") or 0.0)
    residual_z = latest.get("residual_z")
    state = "rich" if residual > 0 else "cheap" if residual < 0 else "fair"
    direction = "fade lower / short bias" if residual > 0 else "fade higher / long bias" if residual < 0 else "no directional bias"
    strongest = holdout[0] if holdout else {}
    if strongest:
        rule = f"Best historical holdout rule is |z| >= {strongest.get('threshold')} over {strongest.get('horizon')}m with hit rate {float(strongest.get('hit_rate', 0))*100:.1f}%."
    else:
        rule = "No holdout rule is available."
    z_text = "n/a" if residual_z is None else f"{float(residual_z):.2f}"
    return (
        f"Selected model {model} on {date}: latest price is {state} vs fair value "
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
    return jsonify({
        "models": sorted(pred["model"].dropna().unique().tolist()),
        "dates": sorted(pred["et_date"].dropna().unique().tolist()),
        "default_model": "ridge" if "ridge" in set(pred["model"]) else str(pred["model"].iloc[0]),
        "default_date": sorted(pred["et_date"].dropna().unique().tolist())[-1],
        "metrics": _model_metrics(),
        "holdout_rules": _best_holdout_rules()[:10],
        "conditional_rules": _best_conditional_rules()[:10],
        "explainer": EXPLAINER_PATH.read_text(encoding="utf-8") if EXPLAINER_PATH.exists() else "",
        "version": "V2.2",
        "strategy_status": STRATEGY_STATUS,
        "lifo_friendly_label": LIFO_FRIENDLY_LABEL,
        "isolation": {
            "port": 5058,
            "store": str(LIVE_STORE_DIR),
            "v2_untouched": True,
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
        }), 202
    model_df, model_meta, alert_summary, lifo_summary = produced
    if model_df.empty:
        return jsonify({
            "success": False,
            "error": "Waiting for live TBBO rows and model warmup.",
            "feed": LIVE_BUFFER.status(),
            "model": model_meta,
            "lifo": lifo_summary,
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
        f"{'rich' if residual > 0 else 'cheap' if residual < 0 else 'fair'} vs V2 fair value. "
        f"Research direction: {direction}. Success means |z| closes below 1.0 within 30m."
    ) if state != "NONE" and z is not None else "No active V2 dislocation. Live model is monitoring causal residual z-score."
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
        "benchmark": {
            "rule": "|z| >= 2.0, 30m fade direction",
            "holdout_hit_rate": 0.810989010989011,
            "holdout_avg_directional_move": 0.06472306910569106,
            "note": "Backtest used historical residuals; live uses causal z-score and close-within-30m outcome.",
        },
    })


@app.get("/api/alert_log")
def api_alert_log() -> Any:
    _ensure_live()
    scope = str(request.args.get("scope") or "today").lower()
    rows = ALERTS.log_rows("all" if scope == "all" else "today")
    summary = ALERTS.summary(None)
    return jsonify({
        "success": True,
        "scope": scope,
        "rows": rows[-200:],
        "stats": summary.get("stats", {}),
        "benchmark": {
            "rule": "|z| >= 2.0, 30m fade direction",
            "holdout_hit_rate": 0.810989010989011,
            "holdout_avg_directional_move": 0.06472306910569106,
            "note": "Live success is close below |z|<1.0 within 30m; backtest benchmark is directional.",
        },
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

    parser = argparse.ArgumentParser(description="Aggression V2.2 research dashboard (V2 + LIFO)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5058)
    parser.add_argument("--debug", action="store_true")
    add_server_args(parser)
    args = parser.parse_args()
    configure_logging(f"v22_{args.port}")
    install_request_timing(app)
    logging.getLogger().info(
        "Starting Aggression V2.2 on %s:%s (%s, %s)",
        args.host,
        args.port,
        STRATEGY_STATUS,
        args.server,
    )
    serve(app, host=args.host, port=args.port, server=args.server, debug=args.debug)


if __name__ == "__main__":
    main()
