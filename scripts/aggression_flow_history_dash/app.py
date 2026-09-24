"""Isolated read-only V1 flow-history viewer on port 5064."""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from flask import Flask, jsonify, render_template, request
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Flask is required. Install with: pip install flask") from exc

from scripts.aggression_dashboard.core import SYMBOL_PARENT_MAP
from scripts.aggression_flow_history_dash.cache import cached_daily, cached_history, feed_for, latest_bar_ts
from scripts.aggression_flow_history_dash.daily import (
    build_diagnostic,
    build_today_outlook,
    load_intraday,
)
from scripts.aggression_flow_history_dash.events import (
    FAMILY_LABELS,
    events_status,
    load_tagged_events,
    start_events_cache,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
STATUS_LABEL = "DESCRIPTIVE / RESEARCH-ONLY / NON-DIRECTIONAL"
SYMBOLS = (*SYMBOL_PARENT_MAP, "CURVE")


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


def _symbol(raw: str | None) -> str:
    symbol = str(raw or "TY").strip().upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"Unsupported symbol '{symbol}'.")
    return symbol


def _filter_family(rows: list[dict[str, Any]], family: str | None) -> list[dict[str, Any]]:
    name = str(family or "all").strip().lower()
    if not name or name == "all":
        return rows
    weekday = name[:1].upper() + name[1:3].lower()
    if weekday in {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}:
        return [row for row in rows if row.get("weekday") == weekday]
    flag = {
        "auction": "is_auction",
        "supply": "is_auction",
        "month_end": "is_month_end",
        "tier1": "is_tier1",
        "nfp": "is_nfp",
        "cpi": "is_cpi",
        "fomc": "is_fomc",
    }.get(name)
    if flag is None:
        raise ValueError(f"Unknown family '{family}'.")
    return [row for row in rows if row.get(flag)]


def _filter_dates(rows: list[dict[str, Any]], start: date | None, end: date | None) -> list[dict[str, Any]]:
    out = rows
    if start is not None:
        begin = start.isoformat()
        out = [row for row in out if str(row.get("date") or "") >= begin]
    if end is not None:
        stop = end.isoformat()
        out = [row for row in out if str(row.get("date") or "") <= stop]
    return out


def _feed(symbol: str) -> dict[str, Any]:
    return feed_for(symbol)


@app.get("/")
def index() -> Any:
    return render_template(
        "index.html",
        symbols=list(SYMBOLS),
        status_label=STATUS_LABEL,
        families=FAMILY_LABELS,
    )


@app.get("/api/status")
def api_status() -> Any:
    symbol = str(request.args.get("symbol") or "TY").strip().upper()
    feed = feed_for(symbol)
    return jsonify({
        "success": True,
        "label": STATUS_LABEL,
        "last_ts_utc": latest_bar_ts(symbol),
        "feed": feed,
        "events": events_status(),
    })


@app.get("/api/daily")
def api_daily() -> Any:
    try:
        symbol = _symbol(request.args.get("symbol"))
        start = _parse_day(request.args.get("start"))
        end = _parse_day(request.args.get("end"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    try:
        rows = _filter_family(
            _filter_dates(cached_daily(symbol), start, end),
            request.args.get("family"),
        )
        return jsonify({
            "success": True,
            "label": STATUS_LABEL,
            "symbol": symbol,
            "rows": rows,
            "count": len(rows),
        })
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        logger.exception("daily API failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/api/intraday")
def api_intraday() -> Any:
    try:
        symbol = _symbol(request.args.get("symbol"))
        day = _parse_day(request.args.get("date"))
        if day is None:
            raise ValueError("date is required (YYYY-MM-DD).")
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    try:
        payload = load_intraday(symbol, day)
        return jsonify({"success": True, "label": STATUS_LABEL, **payload})
    except Exception as exc:
        logger.exception("intraday API failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/api/diagnostic")
def api_diagnostic() -> Any:
    try:
        symbol = _symbol(request.args.get("symbol"))
        start = _parse_day(request.args.get("start"))
        end = _parse_day(request.args.get("end"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    try:
        rows = _filter_dates(cached_daily(symbol), start, end)
        return jsonify({
            "success": True,
            "symbol": symbol,
            **build_diagnostic(rows),
        })
    except Exception as exc:
        logger.exception("diagnostic API failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/api/today")
def api_today() -> Any:
    try:
        symbol = _symbol(request.args.get("symbol"))
        day = _parse_day(request.args.get("date"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    try:
        payload = build_today_outlook(symbol, day=day, history=cached_history(symbol))
        return jsonify({"success": True, "feed": _feed(symbol), **payload})
    except Exception as exc:
        logger.exception("today API failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/api/bootstrap")
def api_bootstrap() -> Any:
    """One fold of the daily tape plus analog, diagnostic, and LIVE badge."""
    try:
        symbol = _symbol(request.args.get("symbol"))
        day = _parse_day(request.args.get("date"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    try:
        rows = cached_daily(symbol)
        return jsonify({
            "success": True,
            "label": STATUS_LABEL,
            "symbol": symbol,
            "rows": rows,
            "count": len(rows),
            "feed": _feed(symbol),
            "diagnostic": build_diagnostic(rows),
            **build_today_outlook(symbol, day=day, history=rows),
        })
    except Exception as exc:
        logger.exception("bootstrap API failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.get("/api/events")
def api_events() -> Any:
    try:
        start = _parse_day(request.args.get("start"))
        end = _parse_day(request.args.get("end"))
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    try:
        return jsonify({
            "success": True,
            "rows": load_tagged_events(start=start, end=end),
        })
    except Exception as exc:
        logger.exception("events API failed")
        return jsonify({"success": False, "error": str(exc)}), 500


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="V1 flow-history viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5064)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logger.info("Starting flow-history viewer on %s:%s", args.host, args.port)
    start_events_cache(f"mdp_{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
