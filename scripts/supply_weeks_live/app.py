"""Isolated live 2Y/5Y/7Y supply-weeks viewer on port 5065."""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from flask import Flask, jsonify, render_template, request
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Flask is required. Install with: pip install flask") from exc

from scripts.aggression_flow_history_dash.cache import feed_for, latest_bar_ts
from scripts.aggression_flow_history_dash.events import events_status, start_events_cache
from scripts.dashboard_runtime.serve import (
    ResponseCache,
    add_server_args,
    cached_response,
    configure_logging,
    install_request_timing,
    serve,
)
from scripts.supply_weeks_live.cache import build_payload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
STATUS_LABEL = "DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL"
_RESPONSES = ResponseCache(wait_sec=60.0)


def _month_end() -> str:
    value = str(request.args.get("month_end") or "all").strip().lower()
    if value not in {"all", "month_end", "non_month_end"}:
        raise ValueError("month_end must be all, month_end, or non_month_end.")
    return value


def _flag(name: str) -> bool:
    return str(request.args.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _filters() -> dict[str, Any]:
    return {
        "month_end": _month_end(),
        "drop_tier1": _flag("drop_tier1"),
        "drop_month_end": _flag("drop_month_end"),
    }


def _public_flow(flow: dict[str, Any] | None) -> dict[str, Any]:
    block = flow or {}
    return {
        "has_flow": bool(block.get("has_flow")),
        "windows": block.get("windows") or {},
        "clock": block.get("clock") or {},
    }


def _public_current(current: dict[str, Any] | None) -> dict[str, Any] | None:
    if not current:
        return None
    return {
        "week_key": current.get("week_key"),
        "week_info": current.get("week_info"),
        "current_window": current.get("current_window"),
        "flow": _public_flow(current.get("flow")),
        "price_data": current.get("price_data") or {"hours": [], "closes": []},
    }


def _public_weeks(weeks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for week in weeks:
        out.append(
            {
                "week_key": week.get("week_key"),
                "week_info": week.get("week_info"),
                "price_data": week.get("price_data") or {"hours": [], "closes": []},
                "flow": _public_flow(week.get("flow")),
                "is_current": bool(week.get("is_current")),
                "current_window": week.get("current_window"),
            }
        )
    return out


@app.get("/")
def index() -> Any:
    return render_template("index.html", status_label=STATUS_LABEL)


def _tick() -> tuple:
    """Data tick for cached responses: newest TY bar and the wall-clock minute."""
    return (latest_bar_ts("TY"), int(time.time() // 60))


def _bootstrap_payload(filters: dict[str, Any], force: bool) -> tuple[dict[str, Any], int]:
    try:
        payload = build_payload(force=force, **filters)
        return {
            "success": True,
            "label": STATUS_LABEL,
            "weeks": _public_weeks(payload["weeks"]),
            "windows": payload["windows"],
            "compare": payload.get("compare") or {},
            "current": _public_current(payload["current"]),
            "feed": feed_for("TY"),
            "events": events_status(),
        }, 200
    except Exception as exc:
        logger.exception("bootstrap failed")
        return {"success": False, "error": str(exc)}, 500


def _live_payload(filters: dict[str, Any]) -> tuple[dict[str, Any], int]:
    try:
        payload = build_payload(**filters)
        return {
            "success": True,
            "label": STATUS_LABEL,
            "windows": payload["windows"],
            "compare": payload.get("compare") or {},
            "current": _public_current(payload["current"]),
            "feed": feed_for("TY"),
            "events": events_status(),
        }, 200
    except Exception as exc:
        logger.exception("live failed")
        return {"success": False, "error": str(exc)}, 500


@app.get("/api/bootstrap")
def api_bootstrap() -> Any:
    try:
        filters = _filters()
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    force = str(request.args.get("force") or "") in {"1", "true", "yes"}
    if force:
        payload, status = _bootstrap_payload(filters, True)
        return jsonify(payload), status
    key = ("bootstrap", tuple(sorted(filters.items())))
    return cached_response(_RESPONSES, key, _tick(), lambda: _bootstrap_payload(filters, False))


@app.get("/api/live")
def api_live() -> Any:
    try:
        filters = _filters()
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    key = ("live", tuple(sorted(filters.items())))
    return cached_response(_RESPONSES, key, _tick(), lambda: _live_payload(filters))


@app.get("/api/runtime")
def api_runtime() -> Any:
    """Response-cache counters (builds vs hits) and events cache state, for supervision and rehearsal."""
    return jsonify({"success": True, "responses": _RESPONSES.stats(), "events": events_status()})


def _warmup() -> None:
    try:
        build_payload()
        logger.info("closed-week cache ready")
    except Exception:
        logger.exception("warmup failed")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Live 2Y/5Y/7Y supply-weeks viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5065)
    parser.add_argument("--debug", action="store_true")
    add_server_args(parser)
    args = parser.parse_args()
    configure_logging(f"supply_{args.port}")
    install_request_timing(app)
    logger.info("Starting supply-weeks live viewer on %s:%s (%s)", args.host, args.port, args.server)
    start_events_cache(f"mdp_{args.port}")
    threading.Thread(target=_warmup, daemon=True).start()
    serve(app, host=args.host, port=args.port, server=args.server, debug=args.debug)


if __name__ == "__main__":
    main()
