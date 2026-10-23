"""Live HTML dashboard for Redis pre-alert stream."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import redis
from flask import Flask, jsonify, request

# Ensure repo root is importable when running script directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings


ET_TZ = ZoneInfo("America/New_York")

app = Flask(__name__)


def _build_client() -> redis.Redis:
    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )


def _parse_message(stream_id: str, fields: dict[str, str]) -> dict[str, str]:
    scheduled_raw = fields.get("scheduled_time_et", "")
    try:
        scheduled = datetime.fromisoformat(scheduled_raw)
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=ET_TZ)
        scheduled_fmt = scheduled.astimezone(ET_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        scheduled_fmt = scheduled_raw

    return {
        "stream_id": stream_id,
        "event_id": fields.get("event_id", ""),
        "event_name": fields.get("event_name", ""),
        "country": fields.get("country", ""),
        "scheduled_time_et": scheduled_raw,
        "scheduled_time_display": scheduled_fmt,
        "source_file": fields.get("source_file", ""),
    }


@app.get("/api/alerts")
def api_alerts():
    limit = int(request.args.get("limit", "200"))
    limit = max(1, min(limit, 1000))

    client = _build_client()
    stream_key = settings.REDIS_EVENT_STREAM
    if not client.exists(stream_key):
        return jsonify(
            {
                "stream": stream_key,
                "count": 0,
                "length": 0,
                "last_generated_id": "",
                "items": [],
                "note": "Stream key does not exist yet. Start publisher and wait for first pre-alert.",
            }
        )

    entries = client.xrevrange(stream_key, count=limit)
    # xrevrange returns newest->oldest; reverse for display.
    items = [_parse_message(stream_id, fields) for stream_id, fields in reversed(entries)]

    info = client.xinfo_stream(stream_key)
    return jsonify(
        {
            "stream": stream_key,
            "count": len(items),
            "length": info.get("length", 0),
            "last_generated_id": info.get("last-generated-id", ""),
            "items": items,
        }
    )


@app.get("/")
def index():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Redis Pre-Alert Dashboard</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 16px; background: #0f172a; color: #e2e8f0; }
    h1 { margin: 0 0 8px; font-size: 22px; }
    .meta { margin-bottom: 12px; color: #94a3b8; }
    .ok { color: #22c55e; }
    .err { color: #ef4444; }
    table { border-collapse: collapse; width: 100%; background: #111827; }
    th, td { border: 1px solid #334155; padding: 8px; text-align: left; font-size: 13px; }
    th { background: #1f2937; position: sticky; top: 0; }
    .controls { margin-bottom: 12px; display: flex; gap: 12px; align-items: center; }
    input, button { background: #1f2937; color: #e2e8f0; border: 1px solid #334155; padding: 6px 8px; }
    .wrap { max-height: 75vh; overflow: auto; border: 1px solid #334155; }
  </style>
</head>
<body>
  <h1>Redis Pre-Alert Dashboard</h1>
  <div class="meta" id="status">Loading...</div>
  <div class="controls">
    <label>Rows: <input id="limit" type="number" value="200" min="1" max="1000"></label>
    <label>Refresh (sec): <input id="refreshSec" type="number" value="3" min="1" max="60"></label>
    <button onclick="refreshNow()">Refresh now</button>
  </div>
  <div class="wrap">
    <table>
      <thead>
        <tr>
          <th>Stream ID</th>
          <th>Event Name</th>
          <th>Country</th>
          <th>Scheduled Time (ET)</th>
          <th>Source</th>
          <th>Event ID</th>
        </tr>
      </thead>
      <tbody id="rows"></tbody>
    </table>
  </div>

  <script>
    let timer = null;

    function renderRows(items) {
      const tbody = document.getElementById("rows");
      tbody.innerHTML = "";
      for (const row of items) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${row.stream_id || ""}</td>
          <td>${row.event_name || ""}</td>
          <td>${row.country || ""}</td>
          <td>${row.scheduled_time_display || row.scheduled_time_et || ""}</td>
          <td>${row.source_file || ""}</td>
          <td>${row.event_id || ""}</td>
        `;
        tbody.appendChild(tr);
      }
    }

    async function fetchAlerts() {
      const status = document.getElementById("status");
      const limit = document.getElementById("limit").value || "200";
      try {
        const res = await fetch(`/api/alerts?limit=${encodeURIComponent(limit)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const now = new Date().toLocaleTimeString();
        status.className = "meta ok";
        status.textContent = `OK | stream=${data.stream} | displayed=${data.count} | stream_length=${data.length} | last_id=${data.last_generated_id} | refreshed=${now}`;
        renderRows(data.items || []);
      } catch (err) {
        status.className = "meta err";
        status.textContent = `Error loading alerts: ${err}`;
      }
    }

    function setupTimer() {
      if (timer) clearInterval(timer);
      const sec = parseInt(document.getElementById("refreshSec").value || "3", 10);
      timer = setInterval(fetchAlerts, Math.max(1, sec) * 1000);
    }

    function refreshNow() {
      fetchAlerts();
      setupTimer();
    }

    document.getElementById("refreshSec").addEventListener("change", setupTimer);
    document.getElementById("limit").addEventListener("change", fetchAlerts);
    refreshNow();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Redis pre-alert HTML dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Flask bind host")
    parser.add_argument("--port", type=int, default=5071, help="Flask bind port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()

