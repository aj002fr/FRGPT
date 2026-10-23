#!/usr/bin/env python3
"""Isolated Aggression V2.6: reduced C018 fade lean + LIFO feedback.

Manual-only on port 5062. All writes stay under
workspace/analysis/aggression_v26_live/. No execution or external messaging.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template, request

from scripts.aggression_dashboard_v26.alerts import AlertStore
from scripts.aggression_dashboard_v26.c018_context import (
    C018_LABEL,
    STRATEGY_STATUS,
    T3_RANGE_CUTOFF_TICKS,
)
from scripts.aggression_dashboard_v26.c018_store import C018Store
from scripts.aggression_dashboard_v26.features import build_live_features
from scripts.aggression_dashboard_v26.feedback_store import ALERT_KINDS, FeedbackStore
from scripts.aggression_dashboard_v26.lifo_context import LIFO_FRIENDLY_LABEL
from scripts.aggression_dashboard_v26.lifo_store import LifoStore
from scripts.aggression_dashboard_v26.live_buffer import LiveTbboBuffer
from scripts.aggression_dashboard_v26.model_state import RidgeModelState

ARTIFACT_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models"
PREDICTIONS_PATH = ARTIFACT_DIR / "walkforward_predictions.csv"
MODEL_METRICS_PATH = ARTIFACT_DIR / "model_metrics.json"
COEF_PATH = ARTIFACT_DIR / "model_coefficients_importance.csv"
CONDITIONAL_PATH = ARTIFACT_DIR / "conditional_signal_summary.json"
HOLDOUT_PATH = ARTIFACT_DIR / "holdout_signal_summary.json"
EXPLAINER_PATH = PROJECT_ROOT / "docs" / "aggression_v2_model_explainer.md"
LIVE_STORE_DIR = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v26_live"
PORT = 5062

app = Flask(__name__, template_folder="templates", static_folder="static")
LIVE_BUFFER = LiveTbboBuffer()
MODEL_STATE = RidgeModelState()
ALERTS = AlertStore()
LIFO = LifoStore()
C018 = C018Store()
FEEDBACK = FeedbackStore()
_LIVE_STARTED = False
_EVALUATOR_THREAD: threading.Thread | None = None
_EVALUATOR_RUNNING = False
_SIGNAL_LOG_LAST_TS: dict[str, pd.Timestamp] = {}
ASSET_VERSION = "20260817_v26_c018_feedback"


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


def _evaluate_live_once() -> tuple[
    pd.DataFrame,
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
]:
    raw = LIVE_BUFFER.snapshot()
    features = build_live_features(raw)
    model_df, model_meta = MODEL_STATE.predict(features)
    if model_df.empty:
        return (
            model_df,
            model_meta,
            ALERTS.summary(None),
            LIFO.payload(None),
            C018.payload(None),
        )
    return (
        model_df,
        model_meta,
        ALERTS.update(model_df),
        LIFO.update(model_df),
        C018.update(model_df),
    )


def _feedback_payload() -> dict[str, Any]:
    return FEEDBACK.daily_payload(
        LIFO.snapshot_episodes(),
        C018.snapshot_episodes(),
    )


def _background_evaluator() -> None:
    while _EVALUATOR_RUNNING:
        try:
            model_df, _, _, _, _ = _evaluate_live_once()
            _append_signal_log(model_df)
        except Exception:
            pass
        time.sleep(20.0)


def _append_signal_log(model_df: pd.DataFrame) -> None:
    if model_df.empty:
        return
    cols = ["price", "fair_value", "residual", "residual_z"]
    if any(col not in model_df.columns for col in cols):
        return
    data = model_df.dropna(subset=cols).copy()
    if data.empty:
        return
    LIVE_STORE_DIR.mkdir(parents=True, exist_ok=True)
    index = pd.DatetimeIndex(data.index)
    data.index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    et_dates = data.index.tz_convert("America/New_York").strftime("%Y%m%d")
    for et_date in sorted(set(et_dates)):
        day_rows = data[et_dates == et_date].copy()
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
    if isinstance(value, (np.floating, float)) and not np.isfinite(float(value)):
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
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


def _driver_rows(model: str) -> list[dict[str, Any]]:
    coef = _coef()
    data = coef[
        (coef["model"] == model)
        & (~coef["feature"].astype(str).str.startswith("__"))
    ].copy()
    if data.empty:
        return []
    grouped = data.groupby(["feature", "kind"], as_index=False)["value"].agg(
        mean="mean",
        mean_abs=lambda x: x.abs().mean(),
        count="count",
    )
    grouped.sort_values("mean_abs", ascending=False, inplace=True)
    return _records(grouped.head(20))


def _model_metrics() -> list[dict[str, Any]]:
    return _read_json(MODEL_METRICS_PATH).get("model_metrics") or []


def _best_holdout_rules() -> list[dict[str, Any]]:
    return _read_json(HOLDOUT_PATH).get("best_holdout_rules") or []


def _best_conditional_rules() -> list[dict[str, Any]]:
    return _read_json(CONDITIONAL_PATH).get("best_conditions") or []


def _plain_english(model: str, date: str, latest: dict[str, Any]) -> str:
    if not latest:
        return "No prediction rows available for the selected model/date."
    residual = float(latest.get("residual") or 0.0)
    state = "rich" if residual > 0 else "cheap" if residual < 0 else "fair"
    z_value = latest.get("residual_z")
    z_text = "n/a" if z_value is None else f"{float(z_value):.2f}"
    return (
        f"Selected model {model} on {date}: latest price is {state} vs fair value "
        f"(residual {residual:.5f}, z {z_text}). This V2 readout remains research-only. "
        "C018 and LIFO feedback do not authorize execution."
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
    dates = sorted(pred["et_date"].dropna().unique().tolist())
    models = sorted(pred["model"].dropna().unique().tolist())
    return jsonify({
        "success": True,
        "models": models,
        "dates": dates,
        "default_model": "ridge" if "ridge" in set(models) else str(models[0]),
        "default_date": dates[-1],
        "metrics": _model_metrics(),
        "holdout_rules": _best_holdout_rules()[:10],
        "conditional_rules": _best_conditional_rules()[:10],
        "explainer": EXPLAINER_PATH.read_text(encoding="utf-8") if EXPLAINER_PATH.exists() else "",
        "version": "V2.6",
        "strategy_status": STRATEGY_STATUS,
        "lifo_friendly_label": LIFO_FRIENDLY_LABEL,
        "c018_label": C018_LABEL,
        "c018_range_cutoff_ticks": T3_RANGE_CUTOFF_TICKS,
        "isolation": {
            "port": PORT,
            "store": str(LIVE_STORE_DIR),
            "manual_only": True,
            "executable": False,
            "v2_v22_v23_v24_untouched": True,
            "external_messaging": False,
        },
    })


@app.get("/api/research")
def api_research() -> Any:
    model = str(request.args.get("model") or "ridge")
    date = str(request.args.get("date") or "")
    data = _predictions()
    data = data[data["model"] == model].copy()
    if date:
        data = data[data["et_date"] == date].copy()
    if data.empty:
        return jsonify({"success": False, "error": "No rows for selected model/date."}), 404
    data["residual_z"] = _residual_z_by_day(data)
    data["x"] = (
        data["bucket_ts"]
        .dt.tz_convert("America/New_York")
        .dt.tz_localize(None)
        .astype(str)
    )
    latest = data.iloc[-1].replace({np.nan: None}).to_dict()
    return jsonify({
        "success": True,
        "model": model,
        "date": date or "all",
        "series": _records(data[["x", "price", "fair_value", "residual", "residual_z"]]),
        "latest": {key: _finite(value) for key, value in latest.items()},
        "drivers": _driver_rows(model),
        "plain_english": _plain_english(model, date or "all dates", latest),
    })


@app.get("/api/signal_status")
def api_signal_status() -> Any:
    _ensure_live()
    return jsonify({
        "success": True,
        "feed": LIVE_BUFFER.status(),
        "model": MODEL_STATE.status(),
    })


@app.get("/api/live")
@app.get("/api/live_signal")
def api_live() -> Any:
    _ensure_live()
    model_df, model_meta, alert_summary, lifo_summary, c018_summary = _evaluate_live_once()
    feedback = _feedback_payload()
    if model_df.empty:
        return jsonify({
            "success": False,
            "error": "Waiting for live TBBO rows and model warmup.",
            "feed": LIVE_BUFFER.status(),
            "model": model_meta,
            "lifo": lifo_summary,
            "c018": c018_summary,
            "feedback": feedback,
        }), 202
    data = model_df.tail(240).copy()
    index = pd.DatetimeIndex(data.index)
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    data["x"] = index.tz_convert("America/New_York").tz_localize(None).astype(str)
    latest = data.iloc[-1].replace({np.nan: None}).to_dict()
    residual = float(latest.get("residual") or 0.0)
    z_value = latest.get("residual_z")
    abs_z = abs(float(z_value)) if z_value is not None else 0.0
    state = "DISLOCATION" if abs_z >= 2.0 else "WATCH" if abs_z >= 1.5 else "NONE"
    message = (
        f"{state}: residual z={float(z_value):.2f}; price is "
        f"{'rich' if residual > 0 else 'cheap' if residual < 0 else 'fair'} vs V2 fair value. "
        "Research display only."
    ) if state != "NONE" and z_value is not None else (
        "No active V2 dislocation. Live model is monitoring causal residual z-score."
    )
    return jsonify({
        "success": True,
        "strategy_status": STRATEGY_STATUS,
        "state": state,
        "message": message,
        "feed": LIVE_BUFFER.status(),
        "model": model_meta,
        "latest": {key: _finite(value) for key, value in latest.items()},
        "series": _records(data[["x", "price", "fair_value", "residual", "residual_z"]]),
        "alerts": alert_summary,
        "lifo": lifo_summary,
        "c018": c018_summary,
        "feedback": feedback,
    })


@app.get("/api/alert_log")
def api_alert_log() -> Any:
    _ensure_live()
    scope = str(request.args.get("scope") or "today").lower()
    rows = ALERTS.log_rows("all" if scope == "all" else "today")
    return jsonify({
        "success": True,
        "scope": scope,
        "rows": rows[-200:],
        "stats": ALERTS.summary(None).get("stats", {}),
    })


@app.get("/api/lifo")
def api_lifo() -> Any:
    _ensure_live()
    scope = str(request.args.get("scope") or "today").lower()
    _, _, _, lifo_summary, _ = _evaluate_live_once()
    return jsonify({
        "success": True,
        "scope": scope,
        **lifo_summary,
        "log_rows": LIFO.read_log("all" if scope == "all" else "today")[-200:],
    })


@app.get("/api/c018")
def api_c018() -> Any:
    _ensure_live()
    _, _, _, _, c018_summary = _evaluate_live_once()
    return jsonify(c018_summary)


@app.post("/api/feedback")
def api_feedback() -> Any:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"success": False, "error": "Expected a JSON object."}), 400
    episode_id = str(body.get("episode_id") or "").strip()
    alert_kind = str(body.get("alert_kind") or "").strip().lower()
    if not episode_id:
        return jsonify({"success": False, "error": "episode_id is required."}), 400
    if alert_kind not in ALERT_KINDS:
        return jsonify({"success": False, "error": "alert_kind must be lifo or c018."}), 400
    if "correct" not in body or (
        body["correct"] is not None and not isinstance(body["correct"], bool)
    ):
        return jsonify({"success": False, "error": "correct must be true, false, or null."}), 400
    correct = body["correct"]
    note_value = body.get("note")
    if note_value is not None and not isinstance(note_value, str):
        return jsonify({"success": False, "error": "note must be a string."}), 400
    note = note_value.strip()[:500] if isinstance(note_value, str) else None
    source = LIFO if alert_kind == "lifo" else C018
    previous = source.get_episode(episode_id)
    if previous is None:
        return jsonify({"success": False, "error": "Episode not found for alert_kind."}), 404
    feedback_ts = datetime.now(timezone.utc).isoformat()
    audit = FEEDBACK.record(
        episode_id=episode_id,
        alert_kind=alert_kind,
        correct=correct,
        note=note,
        previous_correct=previous.get("human_correct"),
        previous_status=previous.get("human_feedback_status"),
        feedback_ts=feedback_ts,
    )
    episode = source.set_feedback(
        episode_id,
        correct,
        note=note,
        feedback_ts=feedback_ts,
    )
    return jsonify({
        "success": True,
        "episode": episode,
        "audit": audit,
        "feedback": _feedback_payload(),
    })


def main() -> None:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Aggression V2.6 C018 + LIFO feedback dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.getLogger().info(
        "Starting Aggression V2.6 on %s:%s (%s)",
        args.host,
        args.port,
        STRATEGY_STATUS,
    )
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
