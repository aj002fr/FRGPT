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
    FRESHNESS_BANNER.textContent = `${source} LIVE - last tick ${formatAge(age)} ago`;
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
  });
}

function panelDataToArrays(panel) {
  const rows = panel.data || [];
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
  const { rows, x } = panelDataToArrays(panel);
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

function renderVolumePanel(elementId, panel) {
  const { rows, x } = panelDataToArrays(panel);
  const traces = (panel.traces || []).map((traceConfig) => {
    if (traceConfig.type === "bar") {
      return makeBarTrace(traceConfig, x, rows);
    }
    return makeLineTrace(panel, traceConfig, x, rows);
  });
  const layout = baseLayout(panel, 480);
  layout.barmode = "relative";
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

function addLargeResidualMarkers(traces, panel) {
  const points = panel.large_residuals || [];
  if (!points.length) return;
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
  const { rows, x } = panelDataToArrays(panel);
  const traces = (panel.traces || []).map((traceConfig) => makeLineTrace(panel, traceConfig, x, rows));
  addLargeResidualMarkers(traces, panel);
  Plotly.react(elementId, traces, baseLayout(panel), { responsive: true });
}

function renderPayload(payload) {
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
}

function parseStreamPayload(event) {
  try {
    return JSON.parse(event.data);
  } catch (_error) {
    return null;
  }
}

function extendTraceFields(elementId, prints, fields) {
  const element = document.getElementById(elementId);
  if (!element || !element.data || !prints.length) {
    return;
  }
  const xs = prints.map((row) => row.x);
  const update = { x: [], y: [] };
  const indices = [];
  fields.forEach((field, index) => {
    if (index >= element.data.length) {
      return;
    }
    update.x.push(xs);
    update.y.push(prints.map((row) => row[field]));
    indices.push(index);
  });
  if (!indices.length) {
    return;
  }
  Plotly.extendTraces(element, update, indices);
  if (lastLookbackStart) {
    Plotly.relayout(elementId, {
      "xaxis.range": [lastLookbackStart, xs[xs.length - 1]],
    });
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
  }
  const p1 = document.getElementById("panel-p1");
  const range = p1 && p1.data && p1.data[0] && p1.data[0].x && p1.data[0].x.length
    ? [p1.data[0].x[0], p1.data[0].x[p1.data[0].x.length - 1]]
    : lastLookbackStart
      ? [lastLookbackStart, strip.run && strip.run.x1]
      : null;
  renderStateTimeline("panel-p1-states", { data: lastStripRuns, title: "Six-state through lookback" }, range);
}

function applyTradeDelta(delta) {
  const prints = Array.isArray(delta.prints) ? delta.prints : [];
  if (delta.lookback_start) {
    lastLookbackStart = delta.lookback_start;
  }
  if (prints.length) {
    extendTraceFields("panel-p1", prints, ["total_aggress", "price"]);
    extendTraceFields("panel-p2", prints, ["buysell_ratio", "price"]);
    extendTraceFields("panel-p3", prints, ["buy_aggress", "sell_aggress", "price", "total_aggress"]);
    extendTraceFields("panel-p4", prints, ["price", "implied_price"]);
    appendBannerRows(prints);
    applyStripEdge(delta.strip);
  }
  if (delta.flow_dominance) {
    updateFlowDominanceBanner(delta.flow_dominance);
  }
  if (delta.live_status) {
    updateFreshnessBanner(delta.live_status);
  }
  const flow = delta.meta && delta.meta.flow;
  if (flow && META && META.textContent) {
    META.textContent = META.textContent.replace(
      /Prints: [^|]+/,
      `Prints: ${(flow.trade_count || 0).toLocaleString()} `,
    );
  }
  restoreHoverBanner();
  setStatus(`Live ${formatEtTime()}.`, "");
}

function closeLiveStream() {
  if (liveSource) {
    liveSource.close();
    liveSource = null;
  }
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

updateModeUi();
updateHoverBanner(HOVER_BANNER_IDLE, false);
refreshDashboard(false);
