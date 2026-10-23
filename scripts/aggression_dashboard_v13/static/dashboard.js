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
const FLOW_DOMINANCE_LABEL = document.getElementById("flow-dominance-label");
const FLOW_DOMINANCE_HOVER = document.getElementById("flow-dominance-hover");
const FLOW_DOMINANCE_HOVER_ASOF = document.getElementById("flow-dominance-hover-asof");
const FLOW_DOMINANCE_HOVER_MAIN = document.getElementById("flow-dominance-hover-main");
const FLOW_DOMINANCE_HOVER_DETAIL = document.getElementById("flow-dominance-hover-detail");
const FLOW_DOMINANCE_HOVER_LABEL = document.getElementById("flow-dominance-hover-label");
const BANNER_ON_HOVER = document.getElementById("banner-on-hover");
const FLOW_DOMINANCE_LABEL_TEXT =
  "DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL / NOT RANGE-TREND VALIDATED";
const PANEL_IDS = ["panel-p1", "panel-p2", "panel-p3", "panel-p4", "panel-p5"];
const FLOW_BANNER_IDLE = {
  status: "WARMING",
  state: "WARMING",
  reason: "Hover any chart timestamp to see the completed-bar banner at that time.",
};
let refreshTimer = null;
let paused = false;
let inFlight = false;
let bannerSeries = [];
let bannerByX = new Map();
let hoverEventsBound = false;
let hoveredBannerX = null;

function selectedAggressionSymbols() {
  return Array.from(document.querySelectorAll("#aggression-symbols input:checked"))
    .map((input) => input.value);
}

function requestPayload() {
  return {
    universe: document.getElementById("universe").value,
    aggression_symbols: selectedAggressionSymbols(),
    price_symbol: document.getElementById("price-symbol").value,
    start_iso: document.getElementById("start-time").value,
    end_iso: MODE_SELECT.value === "historical" ? document.getElementById("end-time").value : null,
    mode: MODE_SELECT.value,
    dv01_adjust: document.getElementById("dv01-adjust").checked,
    lookback_hours: Number(document.getElementById("lookback").value || 2),
    bucket: "1min",
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
  const connected = Boolean(status.connected);
  const marketOpen = status.market_open !== false;
  const source = status.source === "databento_backup"
    ? "DATABENTO BACKUP"
    : "ACCESSAPI";
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

function flowStateText(state) {
  return String(state || "UNAVAILABLE").replaceAll("_", " ");
}

function flowTicksText(value) {
  return Number.isFinite(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(1)} ticks` : "n/a ticks";
}

function flowBannerStyle(state) {
  const styleByState = {
    AGGRESS_DOMINANT: "aggress",
    IMPLIED_PASSIVE_DOMINANT: "passive",
    BALANCED: "balanced",
    QUIET: "quiet",
    WARMING: "warming",
    UNAVAILABLE: "unavailable",
  };
  return styleByState[state] || "unavailable";
}

function flowBannerDetail(flow) {
  const status = flow.status || "UNAVAILABLE";
  if (status !== "READY") {
    return flow.reason || "Waiting for completed one-minute data.";
  }
  const transition = flow.transition || {};
  const age = Number.isFinite(transition.minutes_in_state)
    ? `${transition.minutes_in_state}m`
    : "n/a";
  const shiftText = transition.shifted && transition.from_state
    ? `shifted from ${flowStateText(transition.from_state)} ${age} ago`
    : `holding ${age}`;
  const aggress = flow.aggress || {};
  const passive = flow.implied_passive || {};
  return [
    shiftText,
    `aggress ${String(aggress.trend || "warming").toLowerCase()} (${flowTicksText(aggress.ticks)})`,
    `implied passive ${String(passive.trend || "warming").toLowerCase()} (${flowTicksText(passive.ticks)})`,
  ].join(" · ");
}

function updateFlowDominanceBanner(context) {
  const flow = context || {};
  const status = flow.status || "UNAVAILABLE";
  const state = flow.state || status;
  FLOW_DOMINANCE_BANNER.className = `flow-dominance-banner ${flowBannerStyle(state)}`;
  FLOW_DOMINANCE_MAIN.textContent = flowStateText(state);
  FLOW_DOMINANCE_DETAIL.textContent = flowBannerDetail(flow);
  const asOf = flow.as_of ? ` · as of ${formatEtTime(new Date(flow.as_of))}` : "";
  FLOW_DOMINANCE_LABEL.textContent = `${flow.label || FLOW_DOMINANCE_LABEL_TEXT}${asOf}`;
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

function updateHoverBanner(context, hovered) {
  if (!FLOW_DOMINANCE_HOVER) {
    return;
  }
  if (!bannerHoverEnabled()) {
    FLOW_DOMINANCE_HOVER.classList.add("hidden");
    return;
  }
  FLOW_DOMINANCE_HOVER.classList.remove("hidden");
  const flow = context || FLOW_BANNER_IDLE;
  const status = flow.status || "UNAVAILABLE";
  const state = flow.state || status;
  FLOW_DOMINANCE_HOVER.className = `flow-dominance-banner flow-dominance-hover ${flowBannerStyle(state)}`;
  FLOW_DOMINANCE_HOVER_ASOF.textContent = hovered && flow.as_of
    ? `AS OF ${formatEtTime(new Date(flow.as_of))}`
    : "HOVER A TIMESTAMP";
  FLOW_DOMINANCE_HOVER_MAIN.textContent = flowStateText(state);
  FLOW_DOMINANCE_HOVER_DETAIL.textContent = flowBannerDetail(flow);
  FLOW_DOMINANCE_HOVER_LABEL.textContent = flow.label || FLOW_DOMINANCE_LABEL_TEXT;
}

function clearHoverBanner() {
  hoveredBannerX = null;
  updateHoverBanner(FLOW_BANNER_IDLE, false);
}

function restoreHoverBanner() {
  if (!bannerHoverEnabled() || hoveredBannerX == null) {
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
      reason: "No completed-bar banner at or before this timestamp.",
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
  hoverEventsBound = true;
  PANEL_IDS.forEach((id) => {
    const element = document.getElementById(id);
    if (!element) {
      return;
    }
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
      width: 1.2,
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
      title: layout.legend_title ? { text: layout.legend_title } : undefined,
      orientation: "h",
      y: -0.28,
      x: 0.5,
      xanchor: "center",
      yanchor: "top",
    },
    xaxis: { title: layout.xaxis_title || "", rangeslider: { visible: false } },
    yaxis: { title: layout.yaxis_title || "" },
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

function addLargeResidualMarkers(traces, panel, yField) {
  const points = panel.large_residuals || [];
  if (!points.length) return;
  traces.push({
    x: points.map((point) => point.x),
    y: points.map((point) => point.y),
    name: "Large Residuals",
    type: "scattergl",
    mode: "markers",
    marker: { color: panel.large_residual_color || "#b00020", size: 7 },
    hovertemplate: `${yField}: %{y:,.5f}<br>%{x}<extra></extra>`,
  });
}

function renderPredictionPanel(elementId, panel) {
  const { rows, x } = panelDataToArrays(panel);
  const traces = (panel.traces || []).map((traceConfig) => makeLineTrace(panel, traceConfig, x, rows));
  addLargeResidualMarkers(traces, panel, "Price");
  Plotly.react(elementId, traces, baseLayout(panel), { responsive: true });
}

function renderResidualPanel(elementId, panel) {
  const { rows, x } = panelDataToArrays(panel);
  const traces = (panel.traces || []).map((traceConfig) => makeLineTrace(panel, traceConfig, x, rows));
  addLargeResidualMarkers(traces, panel, "Residual");
  const layout = baseLayout(panel);
  layout.shapes = [{
    type: "line",
    xref: "paper",
    x0: 0,
    x1: 1,
    y0: 0,
    y1: 0,
    line: { color: "#777", width: 1, dash: "dot" },
  }];
  Plotly.react(elementId, traces, layout, { responsive: true });
}

function renderPayload(payload) {
  renderDualAxisPanel("panel-p1", payload.panels.p1);
  renderDualAxisPanel("panel-p2", payload.panels.p2);
  renderVolumePanel("panel-p3", payload.panels.p3);
  renderPredictionPanel("panel-p4", payload.panels.p4);
  renderPredictionPanel("panel-p5", payload.panels.p5);

  const flow = payload.meta?.flow || {};
  const fairValue = payload.meta?.fair_value || {};
  const intraday = fairValue.intraday || {};
  const tenday = fairValue.tenday || {};
  updateFreshnessBanner(payload.meta?.live_status);
  updateFlowDominanceBanner(payload.flow_dominance);
  storeBannerSeries(payload.flow_dominance_series);
  restoreHoverBanner();
  bindBannerHoverEvents();
  const align = payload.panels?.p1?.axis_align || {};
  const alignText = align.beta == null
    ? ""
    : ` | Axis alpha: ${align.alpha.toFixed(4)} beta: ${align.beta.toExponential(2)}`;
  const intradayText = intraday.ok
    ? `Intra beta: ${intraday.beta.toExponential(2)} R2: ${intraday.r2.toFixed(3)}`
    : "Intra beta: warming";
  const tendayText = tenday.ok
    ? `10D beta: ${tenday.beta.toExponential(2)} R2: ${tenday.r2.toFixed(3)}`
    : "10D beta: loading";
  META.textContent = `Trades: ${(flow.trade_count || 0).toLocaleString()} | Aggress final: ${(flow.total_aggress_final || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} | ${intradayText} | ${tendayText}${alignText}`;
}

async function refreshDashboard(silent = false) {
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
    setStatus(MODE_SELECT.value === "live" ? "Loading live aggression data..." : "Loading historical Databento data...", "loading");
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
  if (AUTO_REFRESH.checked && !paused) {
    const intervalMs = MODE_SELECT.value === "live" ? 1000 : 60_000;
    refreshTimer = setInterval(() => refreshDashboard(true), intervalMs);
  }
}

function updateModeUi() {
  const isHistorical = MODE_SELECT.value === "historical";
  END_TIME_GROUP.classList.toggle("hidden", !isHistorical);
  PAUSE_BTN.textContent = paused ? "Resume" : isHistorical ? "Pause" : "Pause Live";
  updateFreshnessBanner({ mode: MODE_SELECT.value });
  updateFlowDominanceBanner({
    status: "WARMING",
    state: "WARMING",
    reason: "Refreshing flow-dominance context for the selected mode.",
  });
  configureAutoRefresh();
}

REFRESH_BTN.addEventListener("click", () => refreshDashboard(false));
AUTO_REFRESH.addEventListener("change", configureAutoRefresh);
BANNER_ON_HOVER.addEventListener("change", () => {
  if (!bannerHoverEnabled()) {
    FLOW_DOMINANCE_HOVER.classList.add("hidden");
    return;
  }
  if (hoveredBannerX == null) {
    updateHoverBanner(FLOW_BANNER_IDLE, false);
    return;
  }
  restoreHoverBanner();
});
MODE_SELECT.addEventListener("change", () => {
  paused = false;
  clearHoverBanner();
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
document.getElementById("dv01-adjust").addEventListener("change", () => refreshDashboard(false));
document.getElementById("lookback").addEventListener("change", () => refreshDashboard(false));
document.getElementById("price-symbol").addEventListener("change", () => refreshDashboard(false));
document.querySelectorAll("#aggression-symbols input").forEach((input) => {
  input.addEventListener("change", () => refreshDashboard(false));
});

updateModeUi();
clearHoverBanner();
refreshDashboard(false);

