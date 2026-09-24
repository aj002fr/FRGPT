"""Isolated live 2Y/5Y/7Y supply-weeks viewer on port 5065."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from flask import Flask, jsonify, render_template, request
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Flask is required. Install with: pip install flask") from exc

from scripts.aggression_flow_history_dash.cache import feed_for
from scripts.aggression_flow_history_dash.events import events_status, start_events_cache
from scripts.supply_weeks_live.cache import build_payload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
STATUS_LABEL = "DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL"


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


@app.get("/api/bootstrap")
def api_bootstrap() -> Any:
    try:
        filters = _filters()
        force = str(request.args.get("force") or "") in {"1", "true", "yes"}
        payload = build_payload(force=force, **filters)
        return jsonify(
            {
                "success": True,
                "label": STATUS_LABEL,
                "weeks": _public_weeks(payload["weeks"]),
                "windows": payload["windows"],
                "compare": payload.get("compare") or {},
                "current": _public_current(payload["current"]),
                "feed": feed_for("TY"),
                "events": events_status(),
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("bootstrap failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/api/live")
def api_live() -> Any:
    try:
        payload = build_payload(**_filters())
        return jsonify(
            {
                "success": True,
                "label": STATUS_LABEL,
                "windows": payload["windows"],
                "compare": payload.get("compare") or {},
                "current": _public_current(payload["current"]),
                "feed": feed_for("TY"),
                "events": events_status(),
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("live failed")
        return jsonify({"success": False, "error": str(exc)}), 500


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
    args = parser.parse_args()
    logger.info("Starting supply-weeks live viewer on %s:%s", args.host, args.port)
    start_events_cache(f"mdp_{args.port}")
    threading.Thread(target=_warmup, daemon=True).start()
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
