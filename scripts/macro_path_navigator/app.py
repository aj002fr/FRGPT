#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, Response, jsonify, render_template, request

from scripts.macro_path_navigator.constants import DEFAULT_PORT
from scripts.macro_path_navigator.events import (
    cycle_payload,
    load_events_between,
    load_adhoc_events,
    load_fomc_speaker_lean,
    save_adhoc_event,
)
from scripts.macro_path_navigator.health import NavigatorHealth, iso_now
from scripts.macro_path_navigator.headlines import load_headlines
from scripts.macro_path_navigator.live import LiveDataManager
from scripts.macro_path_navigator.price_history import load_history
from scripts.macro_path_navigator.distributions import (
    build_distribution,
    project_distribution,
    upcoming_distribution_events,
)
from scripts.macro_path_navigator.signals import build_signals

app = Flask(__name__, template_folder="templates", static_folder="static")
HEALTH = NavigatorHealth()
LIVE = LiveDataManager(start_price=False, start_flow=False)
ASSET_VERSION = "20260629_mvp"
_FEEDS_STARTED = False


def _start_feeds_background() -> None:
    global _FEEDS_STARTED
    if _FEEDS_STARTED:
        return
    _FEEDS_STARTED = True

    def _run() -> None:
        LIVE.start_price()
        LIVE.start_flow()

    threading.Thread(target=_run, daemon=True).start()


def refresh_health() -> dict[str, Any]:
    _start_feeds_background()
    feeds = LIVE.health_feeds()
    for name, status in feeds.items():
        HEALTH.update_feed(name, status)
    HEALTH.update_state(last_dashboard_tick=iso_now())
    return HEALTH.snapshot()


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/")
def index() -> str:
    return render_template("index.html", asset_version=ASSET_VERSION)


@app.get("/api/health")
def api_health() -> Any:
    return jsonify(refresh_health())


@app.get("/api/health/feeds")
def api_health_feeds() -> Any:
    return jsonify(refresh_health().get("feeds", {}))


@app.get("/api/health/state")
def api_health_state() -> Any:
    return jsonify(refresh_health().get("state", {}))


@app.get("/api/health/attribution")
def api_health_attribution() -> Any:
    return jsonify(refresh_health().get("attribution", {}))


@app.get("/api/cycle")
def api_cycle() -> Any:
    try:
        payload = cycle_payload()
        HEALTH.update_state(last_cycle_load=iso_now(), cycle_event_count=len(payload.get("events", [])))
        return jsonify(payload)
    except Exception as exc:
        HEALTH.set_error(str(exc))
        return jsonify({"error": str(exc), "events": []}), 500


@app.get("/api/runway")
def api_runway() -> Any:
    _start_feeds_background()
    payload = LIVE.runway_payload()
    HEALTH.update_state(last_runway_load=iso_now())
    return jsonify(payload)


@app.get("/api/signals")
def api_signals() -> Any:
    try:
        _start_feeds_background()
        runway = LIVE.runway_payload()
        payload = build_signals(runway)
        HEALTH.update_state(last_signal_build=iso_now(), signal_count=len(payload.get("signals", [])))
        return jsonify(payload)
    except Exception as exc:
        HEALTH.set_error(str(exc))
        return jsonify({"error": str(exc), "signals": []}), 500


@app.get("/api/price_history")
def api_price_history() -> Any:
    window = request.args.get("window", "1M")
    interval = request.args.get("interval") or None
    try:
        payload = load_history(window=window, interval=interval)
        HEALTH.update_state(last_price_history_load=iso_now(), price_history_window=payload.get("window"))
        return jsonify(payload)
    except Exception as exc:
        HEALTH.set_error(str(exc))
        return jsonify({"error": str(exc), "rows": []}), 500


def _event_name_from_request() -> str:
    event_name = request.args.get("event_name")
    if event_name:
        return event_name
    event_id = request.args.get("event_id") or ""
    if event_id.startswith("upcoming:"):
        parts = event_id.split(":", 2)
        if len(parts) >= 3:
            return parts[1]
    if event_id.startswith("bbg:"):
        parts = event_id.split(":", 2)
        if len(parts) >= 3:
            return parts[1]
    return event_id


@app.get("/api/distribution")
def api_distribution() -> Any:
    event_name = _event_name_from_request()
    window = request.args.get("window", "2h")
    scenario = request.args.get("scenario", "baseline")
    if not event_name:
        return jsonify({"error": "event_id or event_name is required"}), 400
    try:
        runway = LIVE.runway_payload()
        current_price = runway.get("current_price")
        dist = build_distribution(event_name, window=window, scenario=scenario)
        dist["projected_price"] = project_distribution(dist, current_price)
        dist["current_price"] = current_price
        return jsonify(dist)
    except Exception as exc:
        HEALTH.set_error(str(exc))
        return jsonify({"error": str(exc), "values_bps": [], "stats": {"n": 0}}), 500


@app.get("/api/distributions")
def api_distributions() -> Any:
    window = request.args.get("window", "2h")
    scenario = request.args.get("scenario", "baseline")
    limit = int(request.args.get("limit", "6"))
    try:
        runway = LIVE.runway_payload()
        current_price = runway.get("current_price")
        bands = []
        for event in upcoming_distribution_events(limit=limit):
            try:
                dist = build_distribution(event["canonical_name"], window=window, scenario=scenario)
                stats = dist.get("stats") or {}
                bands.append({
                    **event,
                    "window": window,
                    "scenario": scenario,
                    "stats": {k: stats.get(k) for k in ("n", "mean", "median", "p5", "p25", "p50", "p75", "p95", "rally_prob", "selloff_prob")},
                    "n": dist.get("n"),
                    "n_eff": dist.get("n_eff"),
                    "conditioning": dist.get("conditioning"),
                    "surprise_statuses": dist.get("surprise_statuses"),
                    "projected_price": project_distribution(dist, current_price),
                })
            except Exception as event_exc:
                bands.append({**event, "error": str(event_exc), "projected_price": {}})
        return jsonify({"current_price": current_price, "window": window, "scenario": scenario, "events": bands})
    except Exception as exc:
        HEALTH.set_error(str(exc))
        return jsonify({"error": str(exc), "events": []}), 500


@app.get("/api/headlines")
def api_headlines() -> Any:
    start = request.args.get("start")
    end = request.args.get("end")
    limit = int(request.args.get("limit", "500"))
    bucket = request.args.get("bucket") or None
    if not start or not end:
        return jsonify({"error": "start and end query params are required", "rows": [], "clusters": []}), 400
    try:
        return jsonify(load_headlines(start=start, end=end, limit=limit, bucket=bucket))
    except Exception as exc:
        HEALTH.set_error(str(exc))
        return jsonify({"error": str(exc), "rows": [], "clusters": []}), 500


@app.get("/api/events_range")
def api_events_range() -> Any:
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"error": "start and end query params are required", "events": []}), 400
    try:
        return jsonify({"events": load_events_between(start, end)})
    except Exception as exc:
        HEALTH.set_error(str(exc))
        return jsonify({"error": str(exc), "events": []}), 500


@app.get("/api/adhoc_events")
def api_adhoc_events() -> Any:
    return jsonify({"events": load_adhoc_events()})


@app.post("/api/adhoc_events")
def api_add_adhoc_event() -> Any:
    payload = request.get_json(silent=True) or {}
    try:
        event = save_adhoc_event(payload)
        return jsonify({"ok": True, "event": event})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/speaker_lean")
def api_speaker_lean() -> Any:
    return jsonify(load_fomc_speaker_lean())


@app.get("/api/stream")
def api_stream() -> Response:
    def generate():
        _start_feeds_background()
        while True:
            payload = {
                "health": refresh_health(),
                "runway": LIVE.runway_payload(),
            }
            try:
                payload["signals"] = build_signals(payload["runway"])
            except Exception as exc:
                payload["signals"] = {"error": str(exc), "signals": []}
            yield f"data: {json.dumps(payload, default=str)}\n\n"
            time.sleep(3)

    return Response(generate(), mimetype="text/event-stream")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Macro Path Navigator live dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()

