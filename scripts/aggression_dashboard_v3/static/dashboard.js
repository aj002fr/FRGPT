const STATUS = document.getElementById("status");
const META = document.getElementById("meta");
const REFRESH_BTN = document.getElementById("refresh-btn");
const PAUSE_BTN = document.getElementById("pause-btn");
const AUTO_REFRESH = document.getElementById("auto-refresh");
const MODE_SELECT = document.getElementById("mode-select");
const END_TIME_GROUP = document.getElementById("end-time-group");
const FRESHNESS_BANNER = document.getElementById("freshness-banner");
const FLOW_DOMINANCE_BANNER = document.getElementById("flow-dominance-banner");
const FLOW_DOMINANCE_MAIN = document.getElementById("flow-dominance-main");
const FLOW_DOMINANCE_DETAIL = document.getElementById("flow-dominance-detail");
const FLOW_DOMINANCE_BETA = document.getElementById("flow-dominance-beta");
const FLOW_DOMINANCE_NOTE = document.getElementById("flow-dominance-note");
const FLOW_DOMINANCE_LABEL = document.getElementById("flow-dominance-label");
const FLOW_DOMINANCE_HOVER = document.getElementById("flow-dominance-hover");
const FLOW_DOMINANCE_HOVER_ASOF = document.getElementById("flow-dominance-hover-asof");
const FLOW_DOMINANCE_HOVER_MAIN = document.getElementById("flow-dominance-hover-main");
const FLOW_DOMINANCE_HOVER_DETAIL = document.getElementById("flow-dominance-hover-detail");
const FLOW_DOMINANCE_HOVER_LABEL = document.getElementById("flow-dominance-hover-label");
const BANNER_ON_HOVER = document.getElementById("banner-on-hover");
const PANEL2_CAPTION = document.getElementById("panel-p2-caption");
const PANEL1_STATES_CAPTION = document.getElementById("panel-p1-states-caption");
const LABEL_TEXT = "PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE";
const PANEL_IDS = ["panel-p1", "panel-p1-states", "panel-p2", "panel-p3", "panel-p4", "panel-p5"];
const HOVER_BANNER_IDLE = {
  status: "WARMING",
  state: "WARMING",
  headline: "HOVER BANNER",
  reason: "Hover any chart timestamp to see the 15-minute six-state at that print.",
};
const LIVE_FLUSH_MS = 1000;
const LIVE_MAX_POINTS = 80000;
const BANNER_ROWS_MAX = 100000;

let refreshTimer = null;
let statusTimer = null;
let liveSource = null;
let paused = false;
let inFlight = false;
let bannerSeries = [];
let bannerByX = new Map();
let hoverEventsBound = false;
let hoveredBannerX = null;
let lastStripRuns = [];
let lastLookbackStart = null;
let currentPanels = null;
let pendingPrints = [];
let flushTimer = null;
let chartsDirty = false;
let stripDirty = false;

function selectedAggressionSymbols() {
  return Array.from(document.querySelectorAll("#aggression-symbols input:checked"))
    .map((input) => input.value);
}

function requestPayload() {
  return {
    aggression_symbols: selectedAggressionSymbols(),
    price_symbol: document.getElementById("price-symbol").value,
    start_iso: document.getElementById("start-time").value,
    end_iso: MODE_SELECT.value === "historical" ? document.getElementById("end-time").value : null,
    mode: MODE_SELECT.value,
    dv01_adjust: document.getElementById("dv01-adjust").checked,
    lookback_hours: Number(document.getElementById("lookback").value || 2),
  };
}

function setStatus(message, kind = "") {
  STATUS.textContent = message;
  STATUS.className = kind;
}

function formatAge(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "n/a";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
}

function formatEtTime(date = new Date()) {
  return date.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }) + " ET";
}

let lastDelay = null;

function delayText() {
  if (!lastDelay) {
    return "";
  }
  const parts = [];
  if (lastDelay.stream != null && Number.isFinite(lastDelay.stream)) {
    parts.push(`exchange to server ${lastDelay.stream.toFixed(1)}s`);
  }
  if (lastDelay.page != null && Number.isFinite(lastDelay.page)) {
    parts.push(`server to page ${lastDelay.page.toFixed(1)}s`);
  }
  return parts.length ? ` · ${parts.join(" · ")}` : "";
}

function updateFreshnessBanner(liveStatus) {
  const status = liveStatus || {};
  const mode = MODE_SELECT.value;
  FRESHNESS_BANNER.className = "freshness-banner";
  if (mode === "historical") {
    FRESHNESS_BANNER.classList.add("historical");
    FRESHNESS_BANNER.textContent = "HISTORICAL MODE";
    return;
  }
  const age = status.last_tick_age_sec;
  const hasConn = Object.prototype.hasOwnProperty.call(status, "connected");
  const connected = Boolean(status.connected);
  const marketOpen = status.market_open !== false;
  const source = status.source === "databento_backup" ? "DATABENTO BACKUP" : "ACCESSAPI";
  if (!hasConn) {
    FRESHNESS_BANNER.classList.add("stale");
    FRESHNESS_BANNER.textContent = "ACCESSAPI CONNECTING";
    return;
  }
  if (!connected) {
    FRESHNESS_BANNER.classList.add("down");
    FRESHNESS_BANNER.textContent = `${source} DOWN${status.error ? ` - ${status.error}` : ""}`;
  } else if (age != null && age < 30) {
    FRESHNESS_BANNER.classList.add("live");
    FRESHNESS_BANNER.textContent = `${source} LIVE - last tick ${formatAge(age)} ago${delayText()}`;
  } else {
    FRESHNESS_BANNER.classList.add("stale");
    FRESHNESS_BANNER.textContent = `${source} ${marketOpen ? "STALE" : "MARKET CLOSED"} - last tick ${formatAge(age)} ago`;
  }
}

function flowBannerStyle(state) {
  const styleByState = {
    AGGRESS_DOMINANT: "aggress",
    IMPLIED_PASSIVE_DOMINANT: "passive",
    JOINTLY_DRIVEN: "jointly",
    CONTESTED: "contested",
    BALANCED: "balanced",
    QUIET: "quiet",
    WARMING: "warming",
    UNAVAILABLE: "unavailable",
  };
  return styleByState[state] || "unavailable";
}

function updateFlowDominanceBanner(context) {
  const flow = context || {};
  const status = flow.status || "UNAVAILABLE";
  const state = flow.state || status;
  FLOW_DOMINANCE_BANNER.className = `flow-dominance-banner ${flowBannerStyle(state)}`;
  FLOW_DOMINANCE_MAIN.textContent = flow.headline || String(state || "UNAVAILABLE").replaceAll("_", " ");
  if (status === "READY" && flow.contribution_line) {
    FLOW_DOMINANCE_DETAIL.textContent = flow.contribution_line;
  } else {
    FLOW_DOMINANCE_DETAIL.textContent = flow.reason || "Waiting for a 15-minute trade-to-trade window.";
  }
  const asOf = flow.as_of ? ` · as of ${formatEtTime(new Date(flow.as_of))}` : "";
  FLOW_DOMINANCE_BETA.textContent = flow.beta_line ? `${flow.beta_line}${asOf}` : "";
  FLOW_DOMINANCE_NOTE.textContent = flow.implied_passive_text
    || "Implied passive is price movement left unexplained by measured aggression under the displayed reference beta; it is not observed passive order flow.";
  FLOW_DOMINANCE_LABEL.textContent = flow.label || LABEL_TEXT;
}

function bannerHoverEnabled() {
  return Boolean(BANNER_ON_HOVER && BANNER_ON_HOVER.checked);
}

function storeBannerSeries(rows) {
  bannerSeries = Array.isArray(rows) ? rows.slice() : [];
  bannerByX = new Map();
  bannerSeries.forEach((row) => {
    candidateXKeys(row.x).forEach((key) => bannerByX.set(key, row));
  });
}

function candidateXKeys(value) {
  const keys = new Set();
  if (value == null) {
    return keys;
  }
  if (typeof value === "string") {
    const normalized = value.trim().replace(" ", "T");
    keys.add(normalized);
    keys.add(normalized.slice(0, 19));
    keys.add(normalized.slice(0, 16));
  }
  const date = value instanceof Date
    ? value
    : new Date(typeof value === "string" ? value.replace(" ", "T") : value);
  if (!Number.isNaN(date.getTime())) {
    keys.add(date.toISOString().slice(0, 19));
    keys.add(date.toISOString().slice(0, 16));
    const local = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}`;
    keys.add(local);
    keys.add(local.slice(0, 16));
  }
  return keys;
}

function lookupBannerSnapshot(x) {
  const keys = candidateXKeys(x);
  for (const key of keys) {
    if (bannerByX.has(key)) {
      return bannerByX.get(key);
    }
  }
  const hoverDate = x instanceof Date
    ? x
    : new Date(typeof x === "string" ? x.replace(" ", "T") : x);
  if (Number.isNaN(hoverDate.getTime()) || !bannerSeries.length) {
    return null;
  }
  let found = null;
  const hoverMs = hoverDate.getTime();
  for (const row of bannerSeries) {
    const rowDate = new Date(String(row.x || "").replace(" ", "T"));
    if (Number.isNaN(rowDate.getTime())) {
      continue;
    }
    if (rowDate.getTime() <= hoverMs) {
      found = row;
    } else {
      break;
    }
  }
  return found;
}

function hoverBannerDetail(flow) {
  const status = flow.status || "UNAVAILABLE";
  if (status === "READY" && flow.contribution_line) {
    return flow.contribution_line;
  }
  return flow.reason || "No 15-minute window at this timestamp.";
}

function updateHoverBanner(context, hovered) {
  if (!FLOW_DOMINANCE_HOVER) {
    return;
  }
  if (!bannerHoverEnabled()) {
    FLOW_DOMINANCE_HOVER.classList.add("hidden");
    return;
  }
  const flow = context || HOVER_BANNER_IDLE;
  const status = flow.status || "UNAVAILABLE";
  const state = flow.state || status;
  FLOW_DOMINANCE_HOVER.className = `flow-dominance-banner flow-dominance-hover ${flowBannerStyle(state)}`;
  FLOW_DOMINANCE_HOVER_ASOF.textContent = hovered && flow.as_of
    ? `AS OF ${formatEtTime(new Date(flow.as_of))}`
    : "HOVER A TIMESTAMP";
  FLOW_DOMINANCE_HOVER_MAIN.textContent = flow.headline || String(state || "UNAVAILABLE").replaceAll("_", " ");
  FLOW_DOMINANCE_HOVER_DETAIL.textContent = hoverBannerDetail(flow);
  FLOW_DOMINANCE_HOVER_LABEL.textContent = flow.label || LABEL_TEXT;
}

function clearHoverBanner() {
  hoveredBannerX = null;
  updateHoverBanner(HOVER_BANNER_IDLE, false);
}

function restoreHoverBanner() {
  if (!bannerHoverEnabled() || hoveredBannerX == null) {
    updateHoverBanner(HOVER_BANNER_IDLE, false);
    return;
  }
  const snapshot = lookupBannerSnapshot(hoveredBannerX);
  if (!snapshot) {
    return;
  }
  updateHoverBanner(snapshot, true);
}

function onPanelHover(event) {
  if (!bannerHoverEnabled()) {
    return;
  }
  const point = event && event.points && event.points[0];
  if (!point) {
    return;
  }
  hoveredBannerX = point.x;
  const snapshot = lookupBannerSnapshot(point.x);
  if (!snapshot) {
    updateHoverBanner({
      status: "WARMING",
      state: "WARMING",
      headline: "WARMING",
      reason: "No six-state snapshot at or before this timestamp.",
    }, true);
    return;
  }
  updateHoverBanner(snapshot, true);
}

function onPanelUnhover() {
  restoreHoverBanner();
}

function bindBannerHoverEvents() {
  if (hoverEventsBound) {
    return;
  }
  const ready = PANEL_IDS.every((id) => {
    const element = document.getElementById(id);
    return element && typeof element.on === "function";
  });
  if (!ready) {
    return;
  }
  hoverEventsBound = true;
  PANEL_IDS.forEach((id) => {
    const element = document.getElementById(id);
    element.on("plotly_hover", onPanelHover);
    element.on("plotly_unhover", onPanelUnhover);
    if (ZOOM_RERENDER[id]) {
      element.on("plotly_relayout", (eventData) => onPanelRelayout(id, eventData));
    }
  });
}

const ZOOM_RERENDER = {
  "panel-p1": (panels) => panels.p1 && renderDualAxisPanel("panel-p1", panels.p1),
  "panel-p2": (panels) => panels.p2 && renderDualAxisPanel("panel-p2", panels.p2),
  "panel-p3": (panels) => panels.p3 && renderVolumePanel("panel-p3", panels.p3),
  "panel-p4": (panels) => panels.p4 && renderPredictionPanel("panel-p4", panels.p4),
  "panel-p5": (panels) => panels.p5 && renderPredictionPanel("panel-p5", panels.p5),
};

function onPanelRelayout(elementId, eventData) {
  if (!currentPanels || !eventData) {
    return;
  }
  const keys = Object.keys(eventData);
  if (keys.some((key) => key.startsWith("xaxis.range") || key === "xaxis.autorange")) {
    ZOOM_RERENDER[elementId](currentPanels);
  }
}

function visibleXRange(elementId) {
  const element = document.getElementById(elementId);
  const xaxis = element && element._fullLayout && element._fullLayout.xaxis;
  if (!xaxis || xaxis.autorange !== false || !Array.isArray(xaxis.range)) {
    return null;
  }
  return xaxis.range.map((value) => String(value).replace(" ", "T"));
}

function plotBuckets(elementId) {
  const element = document.getElementById(elementId);
  const width = element && element.clientWidth ? element.clientWidth : 1200;
  return Math.max(400, Math.min(4000, Math.round(width)));
}

function lowerBoundX(rows, value) {
  let lo = 0;
  let hi = rows.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (String(rows[mid].x) < value) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

function decimateRows(rows, fields, range, buckets) {
  let lo = 0;
  let hi = rows.length;
  if (range) {
    lo = Math.max(0, lowerBoundX(rows, range[0]) - 1);
    hi = Math.min(rows.length, lowerBoundX(rows, range[1]) + 1);
  }
  const count = hi - lo;
  if (count <= buckets * 4) {
    return lo === 0 && hi === rows.length ? rows : rows.slice(lo, hi);
  }
  const out = [];
  const step = count / buckets;
  for (let bucket = 0; bucket < buckets; bucket += 1) {
    const start = lo + Math.floor(bucket * step);
    const end = Math.min(hi, lo + Math.floor((bucket + 1) * step));
    if (start >= end) {
      continue;
    }
    const keep = new Set([start, end - 1]);
    fields.forEach((field) => {
      let minIndex = -1;
      let maxIndex = -1;
      let minValue = Infinity;
      let maxValue = -Infinity;
      for (let i = start; i < end; i += 1) {
        const value = rows[i][field];
        if (value == null || !Number.isFinite(value)) {
          continue;
        }
        if (value < minValue) {
          minValue = value;
          minIndex = i;
        }
        if (value > maxValue) {
          maxValue = value;
          maxIndex = i;
        }
      }
      if (minIndex >= 0) {
        keep.add(minIndex);
      }
      if (maxIndex >= 0) {
        keep.add(maxIndex);
      }
    });
    Array.from(keep).sort((a, b) => a - b).forEach((index) => out.push(rows[index]));
  }
  return out;
}

function panelDataToArrays(panel, elementId) {
  const fields = (panel.traces || []).map((traceConfig) => traceConfig.field);
  const rows = decimateRows(panel.data || [], fields, visibleXRange(elementId), plotBuckets(elementId));
  const x = rows.map((row) => row.x);
  return { rows, x };
}

function makeLineTrace(panel, traceConfig, x, rows) {
  return {
    x,
    y: rows.map((row) => row[traceConfig.field]),
    name: traceConfig.name,
    type: "scattergl",
    mode: "lines",
    yaxis: traceConfig.axis === "y2" ? "y2" : traceConfig.axis === "y3" ? "y3" : "y",
    line: {
      color: traceConfig.color,
      width: traceConfig.width || 1.2,
      dash: traceConfig.dash || "solid",
      shape: traceConfig.shape || "linear",
    },
    connectgaps: traceConfig.connectgaps !== false,
    hovertemplate: `${traceConfig.name}: %{y:,.5f}<br>%{x}<extra></extra>`,
  };
}

function makeBarTrace(traceConfig, x, rows) {
  return {
    x,
    y: rows.map((row) => row[traceConfig.field]),
    name: traceConfig.name,
    type: "bar",
    yaxis: traceConfig.axis === "y2" ? "y2" : traceConfig.axis === "y3" ? "y3" : "y",
    marker: { color: traceConfig.color, opacity: 0.55 },
    hovertemplate: `${traceConfig.name}: %{y:,.0f}<br>%{x}<extra></extra>`,
  };
}

function baseLayout(panel, height = 420) {
  const layout = panel.layout || {};
  return {
    title: { text: panel.title, x: 0.02, xanchor: "left", font: { size: 15 } },
    template: "plotly",
    height,
    uirevision: MODE_SELECT.value,
    margin: { l: 68, r: 82, t: 58, b: 86 },
    hovermode: "x unified",
    legend: {
      orientation: "h",
      y: -0.28,
      x: 0.5,
      xanchor: "center",
      yanchor: "top",
    },
    xaxis: { title: layout.xaxis_title || "Time (ET)", rangeslider: { visible: false } },
    yaxis: { title: layout.yaxis_title || "", zeroline: true },
  };
}

function renderDualAxisPanel(elementId, panel) {
  const { rows, x } = panelDataToArrays(panel, elementId);
  const traces = (panel.traces || []).map((traceConfig) => {
    if (traceConfig.type === "bar") {
      return makeBarTrace(traceConfig, x, rows);
    }
    return makeLineTrace(panel, traceConfig, x, rows);
  });
  const layout = baseLayout(panel);
  if (panel.layout?.yaxis_range) {
    layout.yaxis.range = panel.layout.yaxis_range;
  }
  if (panel.axis_align?.aggress_range) {
    layout.yaxis.range = panel.axis_align.aggress_range;
  }
  if ((panel.traces || []).some((t) => t.axis === "y2")) {
    layout.yaxis2 = {
      title: panel.layout?.yaxis2_title || "",
      overlaying: "y",
      side: "right",
      showgrid: false,
    };
    if (panel.axis_align?.price_range) {
      layout.yaxis2.range = panel.axis_align.price_range;
    }
  }
  Plotly.react(elementId, traces, layout, { responsive: true });
}

function makeSpikeTrace(traceConfig, rows) {
  const x = [];
  const y = [];
  rows.forEach((row) => {
    const value = row[traceConfig.field];
    if (value == null || !Number.isFinite(value) || value === 0) {
      return;
    }
    x.push(row.x, row.x, row.x);
    y.push(0, value, null);
  });
  return {
    x,
    y,
    name: traceConfig.name,
    type: "scattergl",
    mode: "lines",
    yaxis: traceConfig.axis === "y2" ? "y2" : traceConfig.axis === "y3" ? "y3" : "y",
    line: { color: traceConfig.color, width: 1 },
    connectgaps: false,
    hoverinfo: "skip",
  };
}

function renderVolumePanel(elementId, panel) {
  const { rows, x } = panelDataToArrays(panel, elementId);
  const traces = (panel.traces || []).map((traceConfig) => {
    if (traceConfig.type === "bar") {
      return makeSpikeTrace(traceConfig, rows);
    }
    const trace = makeLineTrace(panel, traceConfig, x, rows);
    if (traceConfig.field === "total_aggress") {
      trace.customdata = rows.map((row) => [row.buy_aggress || 0, row.sell_aggress || 0]);
      trace.hovertemplate = `${traceConfig.name}: %{y:,.0f}<br>Buy aggress: %{customdata[0]:,.0f}<br>Sell aggress: %{customdata[1]:,.0f}<br>%{x}<extra></extra>`;
    }
    return trace;
  });
  const layout = baseLayout(panel, 480);
  layout.yaxis2 = {
    title: panel.layout?.yaxis2_title || "",
    overlaying: "y",
    side: "right",
    anchor: "free",
    position: 0.92,
    showgrid: false,
  };
  layout.yaxis3 = {
    title: panel.layout?.yaxis3_title || "",
    overlaying: "y",
    side: "right",
    showgrid: false,
  };
  Plotly.react(elementId, traces, layout, { responsive: true });
}

function addLargeResidualMarkers(traces, panel, elementId) {
  const all = panel.large_residuals || [];
  if (!all.length) return;
  const points = decimateRows(all, ["y"], visibleXRange(elementId), plotBuckets(elementId));
  traces.push({
    x: points.map((point) => point.x),
    y: points.map((point) => point.y),
    name: "Large Residuals",
    type: "scattergl",
    mode: "markers",
    marker: { color: panel.large_residual_color || "#7f7f7f", size: 7 },
    hovertemplate: "Last trade: %{y:,.5f}<br>%{x}<extra></extra>",
  });
}

function panelTimeRange(panel) {
  const rows = panel && panel.data ? panel.data : [];
  if (!rows.length) {
    return null;
  }
  return [rows[0].x, rows[rows.length - 1].x];
}

function renderStateTimeline(elementId, panel, rangeX) {
  const runs = (panel && panel.data) || [];
  const byState = new Map();
  runs.forEach((run) => {
    const start = new Date(String(run.x0 || "").replace(" ", "T"));
    const end = new Date(String(run.x1 || run.x0 || "").replace(" ", "T"));
    if (Number.isNaN(start.getTime())) {
      return;
    }
    let width = end.getTime() - start.getTime();
    if (!Number.isFinite(width) || width <= 0) {
      width = 1000;
    }
    const mid = new Date(start.getTime() + width / 2);
    if (!byState.has(run.state)) {
      byState.set(run.state, {
        xs: [],
        ys: [],
        widths: [],
        color: run.color,
        name: run.headline || String(run.state || "").replaceAll("_", " "),
      });
    }
    const bucket = byState.get(run.state);
    bucket.xs.push(mid);
    bucket.ys.push(1);
    bucket.widths.push(width);
  });
  const traces = Array.from(byState.values()).map((bucket) => ({
    type: "bar",
    x: bucket.xs,
    y: bucket.ys,
    width: bucket.widths,
    name: bucket.name,
    marker: { color: bucket.color },
    hovertemplate: `${bucket.name}<br>%{x}<extra></extra>`,
  }));
  const layout = baseLayout(panel || {}, 280);
  layout.margin = { l: 68, r: 82, t: 36, b: 36 };
  layout.yaxis = { visible: false, range: [0, 1.15], fixedrange: true, showticklabels: false };
  layout.bargap = 0;
  layout.legend = {
    orientation: "h",
    y: -0.35,
    x: 0.5,
    xanchor: "center",
    yanchor: "top",
  };
  if (rangeX) {
    layout.xaxis.range = rangeX;
  }
  Plotly.react(elementId, traces, layout, { responsive: true });
}

function renderPredictionPanel(elementId, panel) {
  const { rows, x } = panelDataToArrays(panel, elementId);
  const traces = (panel.traces || []).map((traceConfig) => makeLineTrace(panel, traceConfig, x, rows));
  addLargeResidualMarkers(traces, panel, elementId);
  Plotly.react(elementId, traces, baseLayout(panel), { responsive: true });
}

const TAPE_PANEL_FIELDS = {
  p1: ["total_aggress", "price"],
  p3: ["buy_aggress", "sell_aggress", "total_aggress", "price"],
  p4: ["price", "implied_price"],
};

function isoFromUs(us) {
  const ms = Math.floor(us / 1000);
  return new Date(ms).toISOString().slice(0, 23) + String(us - ms * 1000).padStart(3, "0");
}

function compactRows(block, fields) {
  const dt = (block && block.dt) || [];
  const cols = (block && block.cols) || {};
  const names = fields || Object.keys(cols);
  const rows = new Array(dt.length);
  let us = block ? block.x0 : 0;
  for (let i = 0; i < dt.length; i += 1) {
    us += dt[i];
    const row = { x: isoFromUs(us) };
    names.forEach((field) => {
      const col = cols[field];
      row[field] = col ? col[i] : null;
    });
    rows[i] = row;
  }
  return rows;
}

// Compact live snapshots carry columns and decimated history (panels.py); rebuild the row format.
function expandCompactPayload(payload) {
  const panels = payload && payload.panels;
  if (!panels) {
    return;
  }
  const tape = payload.tape;
  Object.entries(panels).forEach(([key, panel]) => {
    if (!panel) {
      return;
    }
    if (panel.compact) {
      panel.data = compactRows(panel.compact).concat(panel.data || []);
      panel.compact = null;
    } else if (tape && panel.from_tape && TAPE_PANEL_FIELDS[key]) {
      panel.data = compactRows(tape, TAPE_PANEL_FIELDS[key]).concat(panel.data || []);
      panel.from_tape = false;
    }
    if (panel.large_residuals_compact) {
      panel.large_residuals = compactRows(panel.large_residuals_compact, ["price"]).map((row) => ({ x: row.x, y: row.price }));
      panel.large_residuals_compact = null;
    }
  });
  payload.tape = null;
}

function renderPayload(payload) {
  expandCompactPayload(payload);
  currentPanels = payload.panels || {};
  const p1Rows = payload.panels && payload.panels.p1 && payload.panels.p1.data;
  if (p1Rows && p1Rows.length) {
    lastLookbackStart = p1Rows[0].x;
  }
  renderDualAxisPanel("panel-p1", payload.panels.p1);
  lastStripRuns = ((payload.panels.p1_states && payload.panels.p1_states.data) || []).map((run) => ({ ...run }));
  renderStateTimeline("panel-p1-states", payload.panels.p1_states || { data: lastStripRuns }, panelTimeRange(payload.panels.p1));
  if (PANEL1_STATES_CAPTION) {
    PANEL1_STATES_CAPTION.textContent = (payload.panels.p1_states && payload.panels.p1_states.caption) || "";
  }
  renderDualAxisPanel("panel-p2", payload.panels.p2);
  if (PANEL2_CAPTION) {
    PANEL2_CAPTION.textContent = payload.panels.p2.caption || "";
  }
  renderVolumePanel("panel-p3", payload.panels.p3);
  renderPredictionPanel("panel-p4", payload.panels.p4);
  renderPredictionPanel("panel-p5", payload.panels.p5);
  const flow = payload.meta?.flow || {};
  const beta = payload.meta?.beta_ref || {};
  updateFreshnessBanner(payload.meta?.live_status);
  updateFlowDominanceBanner(payload.flow_dominance);
  storeBannerSeries(payload.flow_dominance_series);
  bindBannerHoverEvents();
  restoreHoverBanner();
  const betaText = beta.ok
    ? `beta_ref: ${Number(beta.beta_ref).toExponential(2)} | last ${beta.lookback_hours || 3}h to ${beta.tod_bucket || "?"} ET | yield display: ${Number(beta.beta_display_yield_bp_per_1m).toFixed(4)}`
    : `beta_ref: unavailable${beta.reason ? ` (${beta.reason})` : ""}`;
  META.textContent = `Prints: ${(flow.trade_count || 0).toLocaleString()} | Aggress final: ${(flow.total_aggress_final || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} | ${betaText}`;
  const lastX = p1Rows && p1Rows.length ? p1Rows[p1Rows.length - 1].x : null;
  if (lastX != null) {
    pendingPrints = pendingPrints.filter((row) => row.x > lastX);
  }
  chartsDirty = false;
  stripDirty = false;
  if (pendingPrints.length) {
    scheduleLiveFlush();
  }
}

function parseStreamPayload(event) {
  try {
    return JSON.parse(event.data);
  } catch (_error) {
    return null;
  }
}

function appendPanelRows(panel, prints, fields) {
  if (!panel) {
    return;
  }
  if (!Array.isArray(panel.data)) {
    panel.data = [];
  }
  const rows = panel.data;
  prints.forEach((point) => {
    const row = { x: point.x };
    fields.forEach((field) => {
      row[field] = point[field] == null ? null : point[field];
    });
    rows.push(row);
  });
}

function trimRowsBefore(rows, start, xOf, maxRows) {
  let cut = 0;
  if (start) {
    while (cut < rows.length && String(xOf(rows[cut]) || "") < start) {
      cut += 1;
    }
  }
  if (maxRows && rows.length - cut > maxRows) {
    cut = rows.length - maxRows;
  }
  return cut > 0 ? rows.splice(0, cut) : [];
}

function computeAxisAlignment(rows) {
  let aMin = Infinity;
  let aMax = -Infinity;
  let pMin = Infinity;
  let pMax = -Infinity;
  rows.forEach((row) => {
    const aggress = row.total_aggress;
    if (aggress == null || !Number.isFinite(aggress)) {
      return;
    }
    aMin = Math.min(aMin, aggress);
    aMax = Math.max(aMax, aggress);
    const price = row.price;
    if (price != null && Number.isFinite(price)) {
      pMin = Math.min(pMin, price);
      pMax = Math.max(pMax, price);
    }
  });
  const aSpan = aMax - aMin;
  const pSpan = pMax - pMin;
  if (!(aSpan > 0) || !(pSpan > 0)) {
    return null;
  }
  return {
    aggress_range: [aMin - aSpan * 0.05, aMax + aSpan * 0.05],
    price_range: [pMin - pSpan * 0.05, pMax + pSpan * 0.05],
  };
}

function trimBannerSeries() {
  const removed = trimRowsBefore(bannerSeries, lastLookbackStart, (row) => row.x, BANNER_ROWS_MAX);
  removed.forEach((row) => {
    candidateXKeys(row.x).forEach((key) => {
      if (bannerByX.get(key) === row) {
        bannerByX.delete(key);
      }
    });
  });
}

function scheduleLiveFlush() {
  if (flushTimer === null) {
    flushTimer = setTimeout(flushLive, LIVE_FLUSH_MS);
  }
}

function flushLive() {
  flushTimer = null;
  if (!currentPanels) {
    pendingPrints = [];
    return;
  }
  if (pendingPrints.length) {
    const prints = pendingPrints;
    pendingPrints = [];
    appendPanelRows(currentPanels.p1, prints, ["total_aggress", "price"]);
    appendPanelRows(currentPanels.p2, prints, ["buysell_ratio", "price"]);
    appendPanelRows(currentPanels.p3, prints, ["buy_aggress", "sell_aggress", "total_aggress", "price"]);
    appendPanelRows(currentPanels.p4, prints, ["price", "implied_price"]);
    ["p1", "p2", "p3", "p4"].forEach((key) => {
      const panel = currentPanels[key];
      if (panel && Array.isArray(panel.data)) {
        trimRowsBefore(panel.data, lastLookbackStart, (row) => row.x, LIVE_MAX_POINTS);
      }
    });
    trimBannerSeries();
    chartsDirty = true;
  }
  if ((chartsDirty || stripDirty) && !document.hidden) {
    renderLiveCharts();
  }
}

function renderLiveCharts() {
  if (chartsDirty) {
    if (currentPanels.p1) {
      currentPanels.p1.axis_align = computeAxisAlignment(currentPanels.p1.data || []);
      renderDualAxisPanel("panel-p1", currentPanels.p1);
    }
    if (currentPanels.p2) {
      renderDualAxisPanel("panel-p2", currentPanels.p2);
    }
    if (currentPanels.p3) {
      renderVolumePanel("panel-p3", currentPanels.p3);
    }
    if (currentPanels.p4) {
      renderPredictionPanel("panel-p4", currentPanels.p4);
    }
    chartsDirty = false;
    stripDirty = true;
  }
  if (stripDirty) {
    trimRowsBefore(lastStripRuns, lastLookbackStart, (run) => run.x1 || run.x0, 0);
    renderStateTimeline(
      "panel-p1-states",
      { data: lastStripRuns, title: "Six-state through lookback" },
      panelTimeRange(currentPanels.p1),
    );
    stripDirty = false;
  }
}

function appendBannerRows(prints) {
  prints.forEach((row) => {
    const hover = row.hover || row.banner;
    if (!hover) {
      return;
    }
    const item = { ...hover, x: row.x || hover.x };
    bannerSeries.push(item);
    candidateXKeys(item.x).forEach((key) => bannerByX.set(key, item));
  });
}

function applyStripEdge(strip) {
  if (!strip) {
    return;
  }
  if (strip.action === "extend" && strip.run && lastStripRuns.length) {
    lastStripRuns[lastStripRuns.length - 1] = { ...strip.run };
  } else if (strip.action === "open" && strip.run) {
    lastStripRuns.push({ ...strip.run });
  } else if (strip.action === "close" && strip.run && lastStripRuns.length) {
    lastStripRuns[lastStripRuns.length - 1] = { ...strip.run };
  } else {
    return;
  }
  stripDirty = true;
}

function applyTradeDelta(delta) {
  const prints = Array.isArray(delta.prints) ? delta.prints : [];
  if (delta.lookback_start) {
    lastLookbackStart = delta.lookback_start;
  }
  if (prints.length) {
    prints.forEach((row) => pendingPrints.push(row));
    appendBannerRows(prints);
    applyStripEdge(delta.strip);
    scheduleLiveFlush();
  }
  if (delta.flow_dominance) {
    updateFlowDominanceBanner(delta.flow_dominance);
  }
  if (delta.server_ts != null) {
    lastDelay = { stream: delta.stream_lag_sec, page: Math.max(0, (Date.now() - delta.server_ts) / 1000) };
  }
  if (delta.live_status) {
    updateFreshnessBanner(delta.live_status);
  }
  const flow = delta.meta && delta.meta.flow;
  if (flow && META && META.textContent) {
    let text = META.textContent.replace(
      /Prints: [^|]+/,
      `Prints: ${(flow.trade_count || 0).toLocaleString()} `,
    );
    if (flow.total_aggress_final != null) {
      text = text.replace(
        /Aggress final: [^|]+/,
        `Aggress final: ${Number(flow.total_aggress_final).toLocaleString(undefined, { maximumFractionDigits: 0 })} `,
      );
    }
    META.textContent = text;
  }
  restoreHoverBanner();
  setStatus(`Live ${formatEtTime()}.`, "");
}

function closeLiveStream() {
  if (liveSource) {
    liveSource.close();
    liveSource = null;
  }
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  pendingPrints = [];
}

function openLiveStream() {
  if (paused) {
    return;
  }
  closeLiveStream();
  setStatus("Connecting live stream...", "loading");
  liveSource = new EventSource("/api/live/stream");
  liveSource.addEventListener("snapshot", (event) => {
    const payload = parseStreamPayload(event);
    if (!payload) {
      return;
    }
    renderPayload(payload);
    setStatus(`Live ${formatEtTime()}.`, "");
  });
  liveSource.addEventListener("history", (event) => {
    const payload = parseStreamPayload(event);
    if (!payload) {
      return;
    }
    renderPayload(payload);
    setStatus(`Live ${formatEtTime()}.`, "");
  });
  liveSource.addEventListener("beta", (event) => {
    const payload = parseStreamPayload(event);
    if (!payload) {
      return;
    }
    renderPayload(payload);
    setStatus(`Live ${formatEtTime()}.`, "");
  });
  liveSource.addEventListener("trade", (event) => {
    const delta = parseStreamPayload(event);
    if (delta) {
      applyTradeDelta(delta);
    }
  });
  liveSource.addEventListener("status", (event) => {
    const status = parseStreamPayload(event);
    if (status) {
      updateFreshnessBanner(status);
    }
  });
  liveSource.addEventListener("loading", (event) => {
    const payload = parseStreamPayload(event) || {};
    if (payload.flow_dominance) {
      updateFlowDominanceBanner(payload.flow_dominance);
    }
    if (payload.live_status) {
      updateFreshnessBanner(payload.live_status);
    }
    setStatus(payload.reason || "Waiting for 15-minute history...", "loading");
  });
  liveSource.onerror = () => {
    setStatus("Live stream reconnecting...", "error");
  };
}

async function refreshDashboard(silent = false) {
  if (MODE_SELECT.value === "live") {
    if (paused) {
      setStatus("Paused.", "");
      closeLiveStream();
      return;
    }
    openLiveStream();
    return;
  }
  closeLiveStream();
  if (paused) {
    setStatus("Paused.", "");
    return;
  }
  if (inFlight) {
    return;
  }
  const symbols = selectedAggressionSymbols();
  if (!symbols.length) {
    setStatus("Select at least one aggression symbol.", "error");
    return;
  }
  if (!silent) {
    setStatus("Loading historical Databento data...", "loading");
    REFRESH_BTN.disabled = true;
  }
  inFlight = true;
  try {
    const response = await fetch("/api/aggression", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload()),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      if (payload.flow_dominance) {
        updateFlowDominanceBanner(payload.flow_dominance);
      }
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    renderPayload(payload);
    setStatus(`Updated ${formatEtTime()}.`, "");
  } catch (error) {
    setStatus(`Error: ${error.message}`, "error");
  } finally {
    inFlight = false;
    if (!silent) {
      REFRESH_BTN.disabled = false;
    }
  }
}

function configureAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (MODE_SELECT.value === "live") {
    return;
  }
  if (AUTO_REFRESH.checked && !paused) {
    refreshTimer = setInterval(() => refreshDashboard(true), 60_000);
  }
}

async function refreshLiveStatus() {
  if (MODE_SELECT.value !== "live" || paused) {
    return;
  }
  try {
    const response = await fetch("/api/live_status", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    updateFreshnessBanner(await response.json());
  } catch (_error) {
    /* keep last banner */
  }
}

function configureLiveStatusPoll() {
  if (statusTimer) {
    clearInterval(statusTimer);
    statusTimer = null;
  }
  if (MODE_SELECT.value !== "live") {
    updateFreshnessBanner({ mode: MODE_SELECT.value });
  }
}

function updateModeUi() {
  const isHistorical = MODE_SELECT.value === "historical";
  END_TIME_GROUP.classList.toggle("hidden", !isHistorical);
  PAUSE_BTN.textContent = paused ? "Resume" : isHistorical ? "Pause" : "Pause Live";
  if (isHistorical) {
    closeLiveStream();
  }
  configureLiveStatusPoll();
  configureAutoRefresh();
}

REFRESH_BTN.addEventListener("click", () => refreshDashboard(false));
AUTO_REFRESH.addEventListener("change", configureAutoRefresh);
MODE_SELECT.addEventListener("change", () => {
  paused = false;
  updateModeUi();
  refreshDashboard(false);
});
PAUSE_BTN.addEventListener("click", () => {
  paused = !paused;
  updateModeUi();
  if (!paused) {
    refreshDashboard(false);
  }
});
document.getElementById("dv01-adjust").addEventListener("change", () => {
  if (MODE_SELECT.value === "historical") {
    refreshDashboard(false);
  }
});
document.getElementById("lookback").addEventListener("change", () => {
  if (MODE_SELECT.value === "historical") {
    refreshDashboard(false);
  }
});
if (BANNER_ON_HOVER) {
  BANNER_ON_HOVER.addEventListener("change", () => {
    if (!BANNER_ON_HOVER.checked) {
      clearHoverBanner();
      if (FLOW_DOMINANCE_HOVER) {
        FLOW_DOMINANCE_HOVER.classList.add("hidden");
      }
    } else {
      restoreHoverBanner();
    }
  });
}
document.getElementById("price-symbol").addEventListener("change", () => {
  if (MODE_SELECT.value === "historical") {
    refreshDashboard(false);
  }
});
document.querySelectorAll("#aggression-symbols input").forEach((input) => {
  input.addEventListener("change", () => {
    if (MODE_SELECT.value === "historical") {
      refreshDashboard(false);
    }
  });
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    return;
  }
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  flushLive();
});

updateModeUi();
updateHoverBanner(HOVER_BANNER_IDLE, false);
refreshDashboard(false);
