"""Interactive Snapshot API explorer dashboard (Flask + embedded HTML)."""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, Response, jsonify, request, stream_with_context


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UIKITX_ROOT = Path(r"Z:\uikitxv2")
if UIKITX_ROOT.exists() and str(UIKITX_ROOT) not in sys.path:
    sys.path.insert(0, str(UIKITX_ROOT))

from lib.trading.snapshot.api import Cond, SnapshotAPI  # noqa: E402
from lib.trading.snapshot.api.conditions import FILTERABLE_COLUMNS, VALID_OPS  # noqa: E402
from lib.trading.snapshot.api.trading_day import (  # noqa: E402
    get_current_trading_day,
    get_trading_day_end,
    get_trading_day_start,
)


ALL_COLUMNS = [
    "last_updated",
    "trading_day",
    "bbg_symbol",
    "strike",
    "expiry",
    "live_px",
    "close_px",
    "delta_y",
    "gamma_y",
    "speed_y",
    "vega",
    "theta",
    "implied_vol",
    "pnl_live",
    "pnl_close",
    "npv",
    "vtexp",
    "normalized_exp",
    "instrument_type",
    "open_position",
    "closed_position",
]

ET = ZoneInfo("America/New_York")

app = Flask(__name__)
_api: SnapshotAPI | None = None


def get_api() -> SnapshotAPI:
    """Lazy-init the global SnapshotAPI; auto-reconnects if pool is dead."""
    global _api
    if _api is None:
        _api = SnapshotAPI()
    return _api


def reset_api() -> None:
    """Close the current API instance so the next call creates a fresh one."""
    global _api
    if _api is not None:
        try:
            _api.close()
        except Exception:
            pass
        _api = None


def _to_json_value(value: Any) -> Any:
    """Serialize a cell value to JSON.  Timestamps are normalised to ET."""
    if value is None:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        if hasattr(value, "tzinfo") and value.tzinfo is not None:
            value = value.astimezone(ET)
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (pd.Timedelta,)):
        return str(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    return value


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    out: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        out.append({k: _to_json_value(v) for k, v in row.items()})
    return out


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_int(raw: str | None, name: str) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _parse_columns(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    cols = [c.strip() for c in raw.split(",") if c.strip()]
    return cols or None


def _run_query(fn):
    t0 = time.perf_counter()
    df = fn()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    records = _df_to_records(df)
    return {
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "row_count": len(records),
        "columns": list(df.columns) if isinstance(df, pd.DataFrame) else [],
        "data": records,
    }


@app.errorhandler(Exception)
def on_error(exc: Exception):
    return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/meta")
def api_meta():
    return jsonify(
        {
            "ok": True,
            "all_columns": ALL_COLUMNS,
            "filterable_columns": sorted(FILTERABLE_COLUMNS),
            "operators": sorted(VALID_OPS),
        }
    )


@app.get("/api/live")
def api_live():
    symbol = request.args.get("symbol") or None
    expiry = _parse_int(request.args.get("expiry"), "expiry")
    return jsonify(_run_query(lambda: get_api().live(symbol=symbol, expiry=expiry)))


@app.get("/api/at")
def api_at():
    ts = request.args.get("ts")
    if not ts:
        raise ValueError("ts is required")
    symbol = request.args.get("symbol") or None
    columns = _parse_columns(request.args.get("columns"))
    return jsonify(_run_query(lambda: get_api().at(ts=ts, symbol=symbol, columns=columns)))


@app.get("/api/history")
def api_history():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        raise ValueError("start and end are required")
    symbol = request.args.get("symbol") or None
    interval = _parse_int(request.args.get("interval"), "interval")
    columns = _parse_columns(request.args.get("columns"))
    allow_large = _parse_bool(request.args.get("allow_large"), default=False)
    return jsonify(
        _run_query(
            lambda: get_api().history(
                start=start,
                end=end,
                symbol=symbol,
                interval=interval,
                columns=columns,
                allow_large=allow_large,
            )
        )
    )


@app.get("/api/today")
def api_today():
    symbol = request.args.get("symbol") or None
    live = _parse_bool(request.args.get("live"), default=False)
    columns = _parse_columns(request.args.get("columns"))
    allow_large = _parse_bool(request.args.get("allow_large"), default=False)
    interval = _parse_int(request.args.get("interval"), "interval")

    # SnapshotAPI.today() does not support interval natively; use history over
    # the current trading-day window when sampling is requested.
    if interval:
        td = get_current_trading_day()
        start = get_trading_day_start(td)
        end = get_trading_day_end(td)
        return jsonify(
            _run_query(
                lambda: get_api().history(
                    start=start,
                    end=end,
                    symbol=symbol,
                    interval=interval,
                    columns=columns,
                    allow_large=allow_large,
                )
            )
        )

    return jsonify(
        _run_query(
            lambda: get_api().today(
                symbol=symbol,
                live=live,
                columns=columns,
                allow_large=allow_large,
            )
        )
    )


@app.post("/api/search")
def api_search():
    payload = request.get_json(silent=True) or {}
    start = payload.get("start")
    end = payload.get("end")
    where = payload.get("where") or []
    if not start or not end:
        raise ValueError("start and end are required")
    if not where:
        raise ValueError("where is required and must contain at least one condition")

    conds = []
    for idx, item in enumerate(where):
        if not isinstance(item, dict):
            raise ValueError(f"where[{idx}] must be an object")
        try:
            conds.append(
                Cond(
                    symbol=str(item["symbol"]),
                    column=str(item["column"]),
                    op=str(item["op"]),
                    value=float(item["value"]),
                )
            )
        except KeyError as exc:
            raise ValueError(f"where[{idx}] missing key: {exc}") from exc

    expand = bool(payload.get("expand", True))
    interval = payload.get("interval")
    interval = int(interval) if interval not in (None, "") else None
    columns = payload.get("columns")
    if isinstance(columns, str):
        columns = _parse_columns(columns)
    allow_large = bool(payload.get("allow_large", False))

    return jsonify(
        _run_query(
            lambda: get_api().search(
                start=start,
                end=end,
                where=conds,
                expand=expand,
                interval=interval,
                columns=columns,
                allow_large=allow_large,
            )
        )
    )


@app.get("/api/diagnose")
def api_diagnose():
    t0 = time.perf_counter()
    diag = get_api().diagnose()
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    return jsonify({"ok": True, "elapsed_ms": elapsed_ms, "data": diag})


@app.post("/api/reconnect")
def api_reconnect():
    """Drop the current connection pool and create a fresh one."""
    reset_api()
    diag = get_api().diagnose()
    return jsonify({"ok": True, "message": "reconnected", "data": diag})


@app.get("/api/subscribe")
def api_subscribe():
    symbol = request.args.get("symbol") or None

    def event_stream():
        updates: queue.Queue[str] = queue.Queue(maxsize=200)
        stopped = False

        def callback(df: pd.DataFrame):
            if stopped:
                return
            payload = json.dumps(
                {
                    "type": "snapshot",
                    "row_count": len(df),
                    "data": _df_to_records(df),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            try:
                updates.put_nowait(payload)
            except queue.Full:
                # Keep most recent stream data if client is slow.
                try:
                    _ = updates.get_nowait()
                except queue.Empty:
                    pass
                try:
                    updates.put_nowait(payload)
                except queue.Full:
                    pass

        unsubscribe = get_api().subscribe(callback, symbol=symbol)
        yield "event: status\ndata: " + json.dumps({"message": "subscribed"}) + "\n\n"

        try:
            while True:
                try:
                    chunk = updates.get(timeout=15)
                    yield f"event: snapshot\ndata: {chunk}\n\n"
                except queue.Empty:
                    yield "event: heartbeat\ndata: {}\n\n"
        except GeneratorExit:
            pass
        finally:
            stopped = True
            unsubscribe()

    return Response(
        stream_with_context(event_stream()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/")
def index():
    td = get_current_trading_day()
    td_str = td.isoformat()
    td_start = get_trading_day_start(td)
    # Use 09:00 and 15:00 on the trading day as sensible defaults
    default_start = f"{td_str} 09:00:00"
    default_end = f"{td_str} 15:00:00"
    default_at = f"{td_str} 10:00:00"

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Snapshot API Explorer</title>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    :root {{
      --bg-primary: #0f172a;
      --bg-secondary: #111827;
      --bg-tertiary: #1f2937;
      --text-primary: #e5e7eb;
      --text-muted: #94a3b8;
      --border: #334155;
      --accent: #38bdf8;
      --ok: #22c55e;
      --error: #ef4444;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 16px;
      background: var(--bg-primary);
      color: var(--text-primary);
      font-family: Arial, sans-serif;
    }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    .help {{ color: var(--text-muted); margin-bottom: 10px; font-size: 13px; }}
    .tabs {{ display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 10px; }}
    .tab-btn {{
      background: var(--bg-tertiary);
      color: var(--text-primary);
      border: 1px solid var(--border);
      padding: 7px 12px;
      cursor: pointer;
      font-size: 13px;
    }}
    .tab-btn.active {{ border-color: var(--accent); color: #fff; background: #172554; }}
    .tab {{
      display: none;
      border: 1px solid var(--border);
      padding: 12px;
      background: var(--bg-secondary);
      margin-bottom: 0;
    }}
    .tab.active {{ display: block; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; }}
    label {{ display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 3px; }}
    input, select, textarea, button {{
      width: 100%;
      background: var(--bg-tertiary);
      color: var(--text-primary);
      border: 1px solid var(--border);
      padding: 7px 8px;
      font-size: 13px;
    }}
    textarea {{ min-height: 54px; resize: vertical; }}
    .row {{ display: flex; gap: 8px; align-items: end; }}
    .row .grow {{ flex: 1; }}
    .btn {{
      width: auto;
      cursor: pointer;
      padding: 8px 16px;
      border: 1px solid var(--border);
      background: #172554;
      color: #93c5fd;
      font-weight: bold;
      font-size: 13px;
    }}
    .btn:hover {{ background: #1e3a6e; }}
    .btn-danger {{ background: #3f1d1d; color: #fecaca; font-weight: normal; }}
    .btn-secondary {{ background: #1f2937; color: #d1d5db; font-weight: normal; }}
    .result-banner {{
      margin: 10px 0 6px;
      padding: 8px 12px;
      border-radius: 4px;
      font-size: 13px;
      font-weight: bold;
    }}
    .result-banner.ok {{ background: #052e16; color: #86efac; border: 1px solid #166534; }}
    .result-banner.err {{ background: #450a0a; color: #fca5a5; border: 1px solid #991b1b; }}
    .result-banner.loading {{ background: #1e1b4b; color: #c4b5fd; border: 1px solid #3730a3; }}
    #chart {{
      width: 100%;
      height: 340px;
      border: 1px solid var(--border);
      margin: 6px 0;
    }}
    .table-wrap {{
      border: 1px solid var(--border);
      max-height: 50vh;
      overflow: auto;
      background: #0b1220;
    }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    th, td {{
      border: 1px solid var(--border);
      padding: 5px 6px;
      text-align: left;
      white-space: nowrap;
      max-width: 220px;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    th {{ position: sticky; top: 0; background: #111827; cursor: pointer; }}
    .conditions {{ margin-top: 8px; }}
    .condition-row {{
      display: grid;
      grid-template-columns: 1.2fr 1fr 0.7fr 0.8fr auto;
      gap: 6px;
      margin-bottom: 6px;
    }}
    .small {{ font-size: 11px; color: var(--text-muted); }}
    .pill {{ display: inline-block; margin-right: 6px; margin-top: 4px; }}
    .columns-box {{
      border: 1px solid var(--border);
      padding: 6px 8px;
      max-height: 90px;
      overflow: auto;
      background: #0b1220;
      margin-top: 6px;
    }}
    .sse-box {{
      border: 1px solid var(--border);
      background: #0b1220;
      padding: 8px;
      max-height: 180px;
      overflow: auto;
      font-family: Consolas, monospace;
      font-size: 12px;
      margin-top: 6px;
    }}
  </style>
</head>
<body>
  <h1>Snapshot API Explorer</h1>
  <div class="help">Trading day: <strong>{td_str}</strong> | All inputs pre-filled with defaults -- just click Run.</div>

  <div class="tabs" id="tabs">
    <button class="tab-btn active" data-tab="live">Live</button>
    <button class="tab-btn" data-tab="at">Point-in-Time</button>
    <button class="tab-btn" data-tab="history">History</button>
    <button class="tab-btn" data-tab="today">Today</button>
    <button class="tab-btn" data-tab="search">Search</button>
    <button class="tab-btn" data-tab="subscribe">Subscribe</button>
    <button class="tab-btn" data-tab="diagnose">Diagnostics</button>
  </div>

  <div id="live" class="tab active">
    <div class="grid">
      <div><label>Symbol</label><input id="liveSymbol" value="TYH6 Comdty" /></div>
      <div><label>Expiry (int, optional)</label><input id="liveExpiry" placeholder="e.g. 1" /></div>
    </div>
    <div class="row" style="margin-top:10px;"><button class="btn" onclick="runLive()">Run Live</button></div>
  </div>

  <div id="at" class="tab">
    <div class="grid">
      <div><label>Timestamp (required)</label><input id="atTs" value="{default_at}" /></div>
      <div><label>Symbol</label><input id="atSymbol" value="TYH6 Comdty" /></div>
      <div><label>Columns (comma separated, blank = all)</label><input id="atColsText" placeholder="live_px,delta_y" /></div>
    </div>
    <div class="row" style="margin-top:10px;"><button class="btn" onclick="runAt()">Run Point-in-Time</button></div>
  </div>

  <div id="history" class="tab">
    <div class="grid">
      <div><label>Start (required)</label><input id="histStart" value="{default_start}" /></div>
      <div><label>End (required)</label><input id="histEnd" value="{default_end}" /></div>
      <div><label>Symbol</label><input id="histSymbol" value="TYH6 Comdty" /></div>
      <div><label>Interval (seconds)</label><input id="histInterval" value="60" /></div>
      <div><label>Columns (blank = all)</label><input id="histColsText" placeholder="live_px,delta_y" /></div>
      <div><label>Allow large</label><select id="histLarge"><option value="false">false</option><option value="true">true</option></select></div>
    </div>
    <div class="row" style="margin-top:10px;"><button class="btn" onclick="runHistory()">Run History</button></div>
  </div>

  <div id="today" class="tab">
    <div class="grid">
      <div><label>Symbol</label><input id="todaySymbol" value="TYH6 Comdty" /></div>
      <div><label>Live tail</label><select id="todayLive"><option value="false">false</option><option value="true">true</option></select></div>
      <div><label>Interval (seconds)</label><input id="todayInterval" value="60" /></div>
      <div><label>Columns (blank = all)</label><input id="todayColsText" placeholder="live_px,delta_y" /></div>
      <div><label>Allow large</label><select id="todayLarge"><option value="false">false</option><option value="true">true</option></select></div>
    </div>
    <div class="row" style="margin-top:10px;"><button class="btn" onclick="runToday()">Run Today</button></div>
  </div>

  <div id="search" class="tab">
    <div class="grid">
      <div><label>Start (required)</label><input id="searchStart" value="{default_start}" /></div>
      <div><label>End (required)</label><input id="searchEnd" value="{default_end}" /></div>
      <div><label>Expand</label><select id="searchExpand"><option value="true">true (full portfolio)</option><option value="false">false (matched only)</option></select></div>
      <div><label>Interval (seconds)</label><input id="searchInterval" value="60" /></div>
      <div><label>Allow large</label><select id="searchLarge"><option value="false">false</option><option value="true">true</option></select></div>
      <div><label>Columns (blank = all)</label><input id="searchColsText" placeholder="live_px,delta_y" /></div>
    </div>

    <div class="conditions">
      <div class="row">
        <div class="grow"><strong>Conditions (AND)</strong> <span class="small">-- fill symbol + value then click Run</span></div>
        <button class="btn btn-secondary" onclick="addCondition()">+ Add Condition</button>
      </div>
      <div id="conditionsWrap"></div>
    </div>
    <div class="row" style="margin-top:10px;"><button class="btn" onclick="runSearch()">Run Search</button></div>
  </div>

  <div id="subscribe" class="tab">
    <div class="grid">
      <div><label>Symbol filter (optional)</label><input id="subSymbol" value="TYH6 Comdty" /></div>
    </div>
    <div class="row" style="margin-top:10px;">
      <button class="btn" onclick="startSubscribe()">Start Subscribe</button>
      <button class="btn btn-danger" onclick="stopSubscribe()">Stop</button>
    </div>
    <div class="small">Real-time SSE stream -- updates table + chart on every snapshot (~1/sec).</div>
    <div class="sse-box" id="sseLog"></div>
  </div>

  <div id="diagnose" class="tab">
    <div class="row">
      <button class="btn" onclick="runDiagnose()">Run Diagnose</button>
      <button class="btn btn-danger" onclick="runReconnect()">Reconnect (reset pool)</button>
    </div>
    <div class="small" style="margin-top:6px;">If pg/redis show connected:false, click Reconnect to reset the connection pool.</div>
  </div>

  <div id="resultMeta" class="result-banner loading" style="display:none;"></div>
  <div id="chart"></div>
  <div class="table-wrap"><table id="resultTable"></table></div>

  <details style="margin-top:8px;">
    <summary style="cursor:pointer; color:var(--text-muted); font-size:12px;">Column selector (optional)</summary>
    <div style="margin-top:4px;">
      <span class="pill"><button class="btn btn-secondary" onclick="selectAllColumns()">Select all</button></span>
      <span class="pill"><button class="btn btn-secondary" onclick="clearAllColumns()">Clear</button></span>
    </div>
    <div class="columns-box" id="columnsBox"></div>
  </details>

  <script>
    let ALL_COLUMNS = [];
    let FILTERABLE_COLUMNS = [];
    let OPS = [];
    let currentRows = [];
    let currentSort = {{ col: null, asc: true }};
    let source = null;

    function el(id) {{ return document.getElementById(id); }}
    function val(id) {{ return el(id).value.trim(); }}
    function toBool(v) {{ return String(v).toLowerCase() === "true"; }}
    function toIntOrNull(v) {{ if (!v) return null; const n = parseInt(v, 10); return Number.isNaN(n) ? null : n; }}

    function scrollToResults() {{
      el("resultMeta").scrollIntoView({{ behavior: "smooth", block: "start" }});
    }}

    async function fetchMeta() {{
      const res = await fetch("/api/meta");
      const payload = await res.json();
      ALL_COLUMNS = payload.all_columns || [];
      FILTERABLE_COLUMNS = payload.filterable_columns || [];
      OPS = payload.operators || [];
      renderColumns();
      addCondition(true);
    }}

    function renderColumns() {{
      const html = ALL_COLUMNS.map((c) => `
        <label style="display:inline-block; margin-right:10px; font-size:11px;">
          <input type="checkbox" class="colCheck" value="${{c}}" /> ${{c}}
        </label>
      `).join("");
      el("columnsBox").innerHTML = html;
    }}

    function getSelectedColumns() {{
      return Array.from(document.querySelectorAll(".colCheck:checked")).map((n) => n.value);
    }}
    function selectAllColumns() {{ document.querySelectorAll(".colCheck").forEach((n) => {{ n.checked = true; }}); }}
    function clearAllColumns() {{ document.querySelectorAll(".colCheck").forEach((n) => {{ n.checked = false; }}); }}

    function wireTabs() {{
      document.querySelectorAll(".tab-btn").forEach((btn) => {{
        btn.addEventListener("click", () => {{
          const tab = btn.dataset.tab;
          document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
          document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
          btn.classList.add("active");
          el(tab).classList.add("active");
        }});
      }});
    }}

    function appendSse(text) {{
      const box = el("sseLog");
      box.textContent += text + "\\n";
      box.scrollTop = box.scrollHeight;
    }}

    function addCondition(withDefaults) {{
      const wrap = el("conditionsWrap");
      const row = document.createElement("div");
      row.className = "condition-row";
      const ops = OPS.map((o) => {{
        const sel = (withDefaults && o === "!=") ? " selected" : "";
        return `<option value="${{o}}"${{sel}}>${{o}}</option>`;
      }}).join("");
      const cols = FILTERABLE_COLUMNS.map((c) => {{
        const sel = (withDefaults && c === "open_position") ? " selected" : "";
        return `<option value="${{c}}"${{sel}}>${{c}}</option>`;
      }}).join("");
      const symVal = withDefaults ? "TYH6 Comdty" : "";
      const numVal = withDefaults ? "0" : "";
      row.innerHTML = `
        <input class="cond-symbol" value="${{symVal}}" placeholder="TYH6 Comdty" />
        <select class="cond-col">${{cols}}</select>
        <select class="cond-op">${{ops}}</select>
        <input class="cond-val" type="number" step="any" value="${{numVal}}" />
        <button class="btn btn-danger cond-del" style="padding:6px 8px;">X</button>
      `;
      row.querySelector(".cond-del").addEventListener("click", () => row.remove());
      wrap.appendChild(row);
    }}

    function gatherConditions() {{
      const rows = [];
      document.querySelectorAll("#conditionsWrap .condition-row").forEach((r) => {{
        const symbol = r.querySelector(".cond-symbol").value.trim();
        const column = r.querySelector(".cond-col").value;
        const op = r.querySelector(".cond-op").value;
        const rawVal = r.querySelector(".cond-val").value;
        if (!symbol || rawVal === "") return;
        rows.push({{ symbol, column, op, value: Number(rawVal) }});
      }});
      return rows;
    }}

    function coerceCell(v) {{
      if (v === null || v === undefined) return "";
      if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
      if (typeof v === "object") return JSON.stringify(v);
      return String(v);
    }}

    function renderTable(rows) {{
      const table = el("resultTable");
      if (!rows || rows.length === 0) {{
        table.innerHTML = "<tr><td style='padding:10px; color:#94a3b8;'>No rows returned</td></tr>";
        return;
      }}
      const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
      const head = "<tr>" + cols.map((c) => `<th onclick="sortBy('${{c.replace(/'/g, "\\\\'")}}')">` + c + "</th>").join("") + "</tr>";
      const body = rows.map((r) => "<tr>" + cols.map((c) => `<td title="${{coerceCell(r[c])}}">` + coerceCell(r[c]) + "</td>").join("") + "</tr>").join("");
      table.innerHTML = "<thead>" + head + "</thead><tbody>" + body + "</tbody>";
    }}

    function sortBy(col) {{
      if (currentSort.col === col) currentSort.asc = !currentSort.asc;
      else {{ currentSort.col = col; currentSort.asc = true; }}
      currentRows.sort((a, b) => {{
        const av = a[col]; const bv = b[col];
        if (av === null || av === undefined) return 1;
        if (bv === null || bv === undefined) return -1;
        if (typeof av === "number" && typeof bv === "number") return currentSort.asc ? av - bv : bv - av;
        const aa = String(av).toLowerCase(); const bb = String(bv).toLowerCase();
        if (aa < bb) return currentSort.asc ? -1 : 1;
        if (aa > bb) return currentSort.asc ? 1 : -1;
        return 0;
      }});
      renderTable(currentRows);
    }}

    function renderChart(rows) {{
      const chartEl = el("chart");
      if (!rows || rows.length === 0) {{
        Plotly.purge(chartEl);
        chartEl.innerHTML = "";
        return;
      }}
      const hasX = rows.some((r) => r.last_updated !== undefined);
      const hasY = rows.some((r) => r.live_px !== undefined);
      if (!hasX || !hasY) {{
        Plotly.purge(chartEl);
        chartEl.innerHTML = "";
        return;
      }}
      const x = [];
      const y = [];
      rows.forEach((r) => {{
        if (r.last_updated != null && r.live_px != null) {{
          x.push(r.last_updated);
          y.push(r.live_px);
        }}
      }});
      if (!x.length) {{
        Plotly.purge(chartEl);
        chartEl.innerHTML = "";
        return;
      }}
      Plotly.newPlot(chartEl, [{{ x, y, mode: "lines", name: "live_px" }}], {{
        template: "plotly_dark",
        margin: {{ l: 50, r: 16, t: 16, b: 40 }},
        xaxis: {{ title: "last_updated" }},
        yaxis: {{ title: "live_px" }}
      }}, {{ responsive: true, scrollZoom: true }});
    }}

    function setMeta(text, isError) {{
      const box = el("resultMeta");
      box.style.display = "block";
      box.className = isError ? "result-banner err" : "result-banner ok";
      box.textContent = text;
    }}

    async function callApi(url, opts) {{
      const box = el("resultMeta");
      box.style.display = "block";
      box.className = "result-banner loading";
      box.textContent = "Loading...";
      scrollToResults();
      try {{
        const res = await fetch(url, opts);
        const payload = await res.json();
        if (!res.ok || !payload.ok) {{
          throw new Error(payload.error || ("HTTP " + res.status));
        }}
        currentRows = payload.data || [];
        renderTable(currentRows);
        renderChart(currentRows);
        const cols = payload.columns ? payload.columns.length : (currentRows[0] ? Object.keys(currentRows[0]).length : 0);
        setMeta(`OK  |  ${{payload.row_count ?? currentRows.length}} rows  |  ${{cols}} columns  |  ${{payload.elapsed_ms ?? 0}} ms`, false);
        return payload;
      }} catch (e) {{
        setMeta("Error: " + e.message, true);
        throw e;
      }}
    }}

    async function runLive() {{
      try {{
        const params = new URLSearchParams();
        if (val("liveSymbol")) params.set("symbol", val("liveSymbol"));
        if (val("liveExpiry")) params.set("expiry", val("liveExpiry"));
        await callApi("/api/live?" + params.toString());
      }} catch (e) {{ /* already displayed */ }}
    }}

    async function runAt() {{
      try {{
        const params = new URLSearchParams();
        params.set("ts", val("atTs"));
        if (val("atSymbol")) params.set("symbol", val("atSymbol"));
        const cols = val("atColsText") || getSelectedColumns().join(",");
        if (cols) params.set("columns", cols);
        await callApi("/api/at?" + params.toString());
      }} catch (e) {{ /* already displayed */ }}
    }}

    async function runHistory() {{
      try {{
        const params = new URLSearchParams();
        params.set("start", val("histStart"));
        params.set("end", val("histEnd"));
        if (val("histSymbol")) params.set("symbol", val("histSymbol"));
        if (val("histInterval")) params.set("interval", val("histInterval"));
        const cols = val("histColsText") || getSelectedColumns().join(",");
        if (cols) params.set("columns", cols);
        params.set("allow_large", val("histLarge"));
        await callApi("/api/history?" + params.toString());
      }} catch (e) {{ /* already displayed */ }}
    }}

    async function runToday() {{
      try {{
        const params = new URLSearchParams();
        if (val("todaySymbol")) params.set("symbol", val("todaySymbol"));
        params.set("live", val("todayLive"));
        if (val("todayInterval")) params.set("interval", val("todayInterval"));
        const cols = val("todayColsText") || getSelectedColumns().join(",");
        if (cols) params.set("columns", cols);
        params.set("allow_large", val("todayLarge"));
        await callApi("/api/today?" + params.toString());
      }} catch (e) {{ /* already displayed */ }}
    }}

    async function runSearch() {{
      try {{
        const conds = gatherConditions();
        if (conds.length === 0) {{
          setMeta("Error: Add at least one condition with symbol and value filled in", true);
          scrollToResults();
          return;
        }}
        const body = {{
          start: val("searchStart"),
          end: val("searchEnd"),
          where: conds,
          expand: toBool(val("searchExpand")),
          interval: toIntOrNull(val("searchInterval")),
          allow_large: toBool(val("searchLarge")),
          columns: (val("searchColsText") || getSelectedColumns().join(","))
        }};
        await callApi("/api/search", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(body)
        }});
      }} catch (e) {{ /* already displayed */ }}
    }}

    async function runDiagnose() {{
      try {{
        const box = el("resultMeta");
        box.style.display = "block";
        box.className = "result-banner loading";
        box.textContent = "Loading...";
        scrollToResults();
        const res = await fetch("/api/diagnose");
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || ("HTTP " + res.status));
        const rows = Object.entries(payload.data || {{}}).map(([k, v]) => ({{ key: k, value: JSON.stringify(v) }}));
        currentRows = rows;
        renderTable(rows);
        renderChart([]);
        setMeta(`OK  |  Diagnose complete  |  ${{payload.elapsed_ms}} ms`, false);
      }} catch (e) {{ setMeta("Error: " + e.message, true); }}
    }}

    async function runReconnect() {{
      try {{
        const box = el("resultMeta");
        box.style.display = "block";
        box.className = "result-banner loading";
        box.textContent = "Reconnecting...";
        scrollToResults();
        const res = await fetch("/api/reconnect", {{ method: "POST" }});
        const payload = await res.json();
        if (!res.ok || !payload.ok) throw new Error(payload.error || ("HTTP " + res.status));
        const rows = Object.entries(payload.data || {{}}).map(([k, v]) => ({{ key: k, value: JSON.stringify(v) }}));
        currentRows = rows;
        renderTable(rows);
        renderChart([]);
        setMeta("Reconnected -- pool reset.  Check pg/redis status below.", false);
      }} catch (e) {{ setMeta("Reconnect failed: " + e.message, true); }}
    }}

    function startSubscribe() {{
      stopSubscribe();
      const params = new URLSearchParams();
      if (val("subSymbol")) params.set("symbol", val("subSymbol"));
      source = new EventSource("/api/subscribe?" + params.toString());
      appendSse("connecting...");
      source.addEventListener("status", (ev) => appendSse("status: " + ev.data));
      source.addEventListener("heartbeat", () => appendSse("heartbeat"));
      source.addEventListener("snapshot", (ev) => {{
        appendSse("snapshot: " + ev.data.slice(0, 260));
        try {{
          const payload = JSON.parse(ev.data);
          currentRows = payload.data || [];
          renderTable(currentRows);
          renderChart(currentRows);
          setMeta(`SUBSCRIBE  |  ${{payload.row_count || 0}} rows  |  streaming`, false);
        }} catch (err) {{
          setMeta("Error: " + err.message, true);
        }}
      }});
      source.onerror = () => appendSse("stream error/disconnected");
    }}

    function stopSubscribe() {{
      if (source) {{
        source.close();
        source = null;
        appendSse("stopped");
      }}
    }}

    window.sortBy = sortBy;
    wireTabs();
    fetchMeta();
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot API explorer dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Flask bind host")
    parser.add_argument("--port", type=int, default=5080, help="Flask bind port")
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
