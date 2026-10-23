const STATUS = document.getElementById("status");
const META = document.getElementById("meta");
const REFRESH_BTN = document.getElementById("refresh-btn");
const PAUSE_BTN = document.getElementById("pause-btn");
const FRESHNESS_BANNER = document.getElementById("freshness-banner");
const FLOW_STATE_BANNER = document.getElementById("flow-state-banner");
const FLOW_STATE_MAIN = document.getElementById("flow-state-main");
const FLOW_DOMINANCE_BANNER = document.getElementById("flow-dominance-banner");
const FLOW_DOMINANCE_MAIN = document.getElementById("flow-dominance-main");
const FLOW_DOMINANCE_DETAIL = document.getElementById("flow-dominance-detail");
const FLOW_DOMINANCE_BETA = document.getElementById("flow-dominance-beta");
const FLOW_DOMINANCE_NOTE = document.getElementById("flow-dominance-note");
const FLOW_DOMINANCE_LABEL = document.getElementById("flow-dominance-label");
const FLOW_DOMINANCE_HOVER = document.getElementById("flow-dominance-hover");
const FLOW_DOMINANCE_HOVER_ASOF = document.getElementById("flow-dominance-hover-asof");
const FLOW_DOMINANCE_HOVER_MAIN = document.getElementById("flow-dominance-hover-main");
const FLOW_HOVER_GROUP = document.getElementById("flow-hover-group");
const FLOW_HOVER_GROUP_MAIN = document.getElementById("flow-hover-group-main");
const FLOW_HOVER_FINE = document.getElementById("flow-hover-fine");
const FLOW_DOMINANCE_HOVER_DETAIL = document.getElementById("flow-dominance-hover-detail");
const FLOW_DOMINANCE_HOVER_LABEL = document.getElementById("flow-dominance-hover-label");
const BANNER_ON_HOVER = document.getElementById("banner-on-hover");
const DASHBOARD_ASSET_URL = document.currentScript
  ? document.currentScript.src
  : new URL("/static/dashboard.js", window.location.href).href;

function ensureExhaustionSoundControl() {
  const existing = document.getElementById("exhaustion-sound");
  if (existing) {
    return existing;
  }
  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = "exhaustion-sound";
  const label = document.createElement("label");
  label.className = "inline";
  label.append(input, document.createTextNode(" Exhaustion sound"));
  const anchor = BANNER_ON_HOVER && BANNER_ON_HOVER.closest("label");
  const actions = document.querySelector(".control-col.actions");
  if (anchor) {
    anchor.after(label);
  } else if (actions) {
    actions.append(label);
  }
  return input;
}

const EXHAUSTION_SOUND = ensureExhaustionSoundControl();
const PANEL2_CAPTION = document.getElementById("panel-p2-caption");
const PANEL1_STATES_CAPTION = document.getElementById("panel-p1-states-caption");
const LABEL_TEXT = "PROVISIONAL / RESEARCH-ONLY / TRADE-TO-TRADE / NON-EXECUTABLE";
const PANEL_IDS = ["panel-p1", "panel-p1-states", "panel-p2", "panel-p3", "panel-p4"];
// Panels drawn from the snapshot's shared `prints` columns, and the fields each reads (panels.PRINT_PANEL_FIELDS).
const PRINT_PANEL_FIELDS = {
  p1: ["total_aggress", "price"],
  p3: ["buy_aggress", "sell_aggress", "total_aggress", "price"],
  p4: ["price", "implied_price"],
};
const EXHAUSTION_SOUND_STORAGE_KEY = "aggression-v3-exhaustion-sound";
const DEPLOYMENT_VALIDATOR_STORAGE_KEY = "aggression-v3-dashboard-validator";
const DEPLOYMENT_POLL_MS = 30000;
const HOVER_BANNER_IDLE = {
  status: "WARMING",
  state: "WARMING",
  group_headline: "STATE",
  headline: "STATE2",
  reason: "Hover any chart timestamp to see the states at that print.",
};
const LIVE_FLUSH_MS = 1000;
const LIVE_MAX_POINTS = 80000;
const BANNER_ROWS_MAX = 100000;

let liveSource = null;
let paused = false;
let bannerSeries = [];
let bannerByX = new Map();
let stateStyles = null;
let hoverEventsBound = false;
let hoveredBannerX = null;
let lastStripRuns = [];
let lastGroupRuns = [];
let lastStateLegend = [
  { legend: "THIN LIQ", color: "#747474" },
  { legend: "QUIET", color: "#E8E8E8" },
  { legend: "AGGRESS DOM", color: "#D86DCD" },
  { legend: "BALANCED", color: "#FFFF00" },
  { legend: "EXHAUSTION", color: "#8ED974" },
];
let lastLookbackStart = null;
let currentPanels = null;
let pendingPrints = [];
let flushTimer = null;
let chartsDirty = false;
let stripDirty = false;
let lastReadyFlowGroup = null;
let audioContext = null;
let deploymentValidator = null;
let pendingDeploymentValidator = null;
let deploymentCheckRunning = false;

try {
  EXHAUSTION_SOUND.checked = localStorage.getItem(EXHAUSTION_SOUND_STORAGE_KEY) === "1";
} catch (_error) {
  EXHAUSTION_SOUND.checked = false;
}

async function playExhaustionChime() {
  if (!EXHAUSTION_SOUND.checked) {
    return;
  }
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    return;
  }
  try {
    audioContext = audioContext || new AudioContextClass();
    if (audioContext.state === "suspended") {
      await audioContext.resume();
    }
    const start = audioContext.currentTime;
    [
      { frequency: 659.25, offset: 0 },
      { frequency: 880, offset: 0.16 },
    ].forEach(({ frequency, offset }) => {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const noteStart = start + offset;
      const noteEnd = noteStart + 0.13;
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(frequency, noteStart);
      gain.gain.setValueAtTime(0.0001, noteStart);
      gain.gain.exponentialRampToValueAtTime(0.16, noteStart + 0.015);
      gain.gain.exponentialRampToValueAtTime(0.0001, noteEnd);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start(noteStart);
      oscillator.stop(noteEnd);
    });
  } catch (_error) {
    // Audio remains armed; another user interaction may satisfy browser policy.
  }
}

function dashboardValidator(response) {
  return response.headers.get("etag") || response.headers.get("last-modified");
}

function reloadForDeployment(validator) {
  try {
    localStorage.setItem(DEPLOYMENT_VALIDATOR_STORAGE_KEY, validator);
  } catch (_error) {
    // Reload protection also comes from accepting the new validator on startup.
  }
  const target = new URL(window.location.href);
  target.searchParams.set("ui_v", String(Date.now()));
  window.location.replace(target.toString());
}

async function checkDashboardDeployment() {
  if (document.hidden || deploymentCheckRunning) {
    return;
  }
  deploymentCheckRunning = true;
  try {
    const response = await fetch(DASHBOARD_ASSET_URL, {
      method: "HEAD",
      cache: "no-store",
    });
    if (!response.ok) {
      return;
    }
    const validator = dashboardValidator(response);
    if (!validator) {
      return;
    }
    if (deploymentValidator === null) {
      deploymentValidator = validator;
      try {
        localStorage.setItem(DEPLOYMENT_VALIDATOR_STORAGE_KEY, validator);
      } catch (_error) {
        // Deployment checks still work for this page without persistent storage.
      }
      return;
    }
    if (validator !== deploymentValidator) {
      pendingDeploymentValidator = validator;
      reloadForDeployment(validator);
    }
  } catch (_error) {
    // A transient network failure should not interrupt the live dashboard.
  } finally {
    deploymentCheckRunning = false;
  }
}

function selectedAggressionSymbols() {
  return Array.from(document.querySelectorAll("#aggression-symbols input:checked"))
    .map((input) => input.value);
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
  FRESHNESS_BANNER.className = "freshness-banner";
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

function windowLabel(flow) {
  const minutes = flow && flow.parameters && flow.parameters.window_min;
  return minutes ? `${minutes}-minute ` : "";
}

function applyBannerStyle(element, color, status, baseClass) {
  if (color) {
    element.className = `${baseClass} state-colored`;
    element.style.setProperty("--state-color", color);
    return;
  }
  element.className = `${baseClass} ${status === "WARMING" ? "warming" : "unavailable"}`;
  element.style.removeProperty("--state-color");
}

function updateFlowDominanceBanner(context) {
  const flow = context || {};
  const status = flow.status || "UNAVAILABLE";
  const fallback = String(flow.state || status).replaceAll("_", " ");
  if (status === "READY" && flow.group) {
    if (
      lastReadyFlowGroup !== null
      && lastReadyFlowGroup !== "EXHAUSTION"
      && flow.group === "EXHAUSTION"
    ) {
      playExhaustionChime();
    }
    lastReadyFlowGroup = flow.group;
  }
  applyBannerStyle(FLOW_STATE_BANNER, flow.group_color, status, "flow-dominance-banner flow-state-banner");
  FLOW_STATE_MAIN.textContent = flow.group_headline || fallback;
  applyBannerStyle(FLOW_DOMINANCE_BANNER, flow.color, status, "flow-dominance-banner");
  FLOW_DOMINANCE_MAIN.textContent = flow.headline || fallback;
  if (status === "READY" && flow.contribution_line) {
    FLOW_DOMINANCE_DETAIL.textContent = flow.contribution_line;
  } else {
    FLOW_DOMINANCE_DETAIL.textContent = flow.reason || `Waiting for a ${windowLabel(flow)}trade-to-trade window.`;
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

// Hover rows arrive compact (status, state, as_of, window_min, contribution_line, reason).
// Rebuild the rest the way banner._payload does; full rows from an older server pass through.
function expandBanner(row) {
  if (!row || row.headline !== undefined) {
    return row;
  }
  const styles = stateStyles || {};
  const style = (styles.states || {})[row.state];
  const headline = style ? style[0] : String(row.state || "").replaceAll("_", " ");
  const group = style ? style[3] : null;
  const groupStyle = group ? (styles.groups || {})[group] : null;
  return {
    ...row,
    headline,
    color: style ? style[1] : null,
    family: style ? style[2] : "neutral",
    group,
    group_headline: groupStyle ? groupStyle[0] : headline,
    group_color: groupStyle ? groupStyle[1] : null,
    label: styles.label || LABEL_TEXT,
    reason: row.reason ?? null,
    contribution_line: row.contribution_line ?? null,
    parameters: { window_min: row.window_min },
  };
}

// Rebuild Panel 1/3/4 rows from the shared columns: `from` to the end, minus `skip` (panels.print_panel_rows).
function expandPrintColumns(payload) {
  const columns = payload && payload.prints;
  const panels = payload && payload.panels;
  if (!columns || !panels || !Array.isArray(columns.x)) {
    return;
  }
  Object.entries(PRINT_PANEL_FIELDS).forEach(([key, fields]) => {
    const panel = panels[key];
    if (!panel || !panel.rows || Array.isArray(panel.data)) {
      return;
    }
    const skip = new Set(panel.rows.skip || []);
    const rows = [];
    for (let i = panel.rows.from || 0; i < columns.x.length; i += 1) {
      if (skip.has(i)) {
        continue;
      }
      const row = { x: columns.x[i] };
      fields.forEach((field) => {
        const value = columns[field] ? columns[field][i] : null;
        row[field] = value === undefined ? null : value;
      });
      rows.push(row);
    }
    panel.data = rows;
  });
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
      return expandBanner(bannerByX.get(key));
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
  return expandBanner(found);
}

function hoverBannerDetail(flow) {
  const status = flow.status || "UNAVAILABLE";
  if (status === "READY" && flow.contribution_line) {
    return flow.contribution_line;
  }
  return flow.reason || `No ${windowLabel(flow)}window at this timestamp.`;
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
  const flow = context || HOVER_BANNER_IDLE;
  const status = flow.status || "UNAVAILABLE";
  const fallback = String(flow.state || status).replaceAll("_", " ");
  applyBannerStyle(FLOW_HOVER_GROUP, flow.group_color, status, "flow-dominance-banner flow-hover-block");
  FLOW_HOVER_GROUP_MAIN.textContent = flow.group_headline || fallback;
  applyBannerStyle(FLOW_HOVER_FINE, flow.color, status, "flow-dominance-banner flow-hover-block");
  FLOW_DOMINANCE_HOVER_MAIN.textContent = flow.headline || fallback;
  FLOW_DOMINANCE_HOVER_ASOF.textContent = hovered && flow.as_of
    ? `AS OF ${formatEtTime(new Date(flow.as_of))}`
    : "HOVER A TIMESTAMP";
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
      group_headline: "WARMING",
      headline: "WARMING",
      reason: "No state snapshot at or before this timestamp.",
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
    uirevision: panel.uirevision || "live",
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

function stripRowTraces(runs, base, rowName, shownLegends) {
  const buckets = new Map();
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
    const legend = run.legend || run.headline || String(run.state || "").replaceAll("_", " ");
    const key = `${legend}|${run.color}`;
    if (!buckets.has(key)) {
      buckets.set(key, { legend, color: run.color, xs: [], widths: [], names: [] });
    }
    const bucket = buckets.get(key);
    bucket.xs.push(new Date(start.getTime() + width / 2));
    bucket.widths.push(width);
    bucket.names.push(run.headline || legend);
  });
  return Array.from(buckets.values()).map((bucket) => {
    const showlegend = !shownLegends.has(bucket.legend);
    shownLegends.add(bucket.legend);
    return {
      type: "bar",
      x: bucket.xs,
      y: bucket.xs.map(() => 1),
      base,
      width: bucket.widths,
      name: bucket.legend,
      legendgroup: bucket.legend,
      showlegend,
      marker: { color: bucket.color },
      text: bucket.names,
      textposition: "none",
      hovertemplate: `${rowName}: %{text}<br>%{x}<extra></extra>`,
    };
  });
}

function persistentStateTraces(entries, shownLegends, anchorX) {
  const x = anchorX || "2000-01-01T00:00:00";
  return (entries || []).filter((entry) => entry && entry.legend && entry.color).map((entry) => {
    shownLegends.add(entry.legend);
    return {
      type: "bar",
      x: [x],
      y: [0.01],
      base: -1,
      width: [1],
      marker: { color: entry.color },
      name: entry.legend,
      legendgroup: entry.legend,
      showlegend: true,
      hoverinfo: "skip",
    };
  });
}

function legendAnchor(panel, rangeX) {
  if (rangeX && rangeX[0]) {
    return rangeX[0];
  }
  const runs = (panel && (panel.group_data || panel.data)) || [];
  const first = runs.find((run) => run && run.x0);
  return first ? first.x0 : null;
}

function renderStateTimeline(elementId, panel, rangeX) {
  const shownLegends = new Set();
  const legend = (panel && panel.state_legend && panel.state_legend.length)
    ? panel.state_legend
    : lastStateLegend;
  if (panel && panel.state_legend && panel.state_legend.length) {
    lastStateLegend = panel.state_legend;
  }
  const traces = [
    ...persistentStateTraces(legend, shownLegends, legendAnchor(panel, rangeX)),
    ...stripRowTraces((panel && panel.group_data) || [], 1.1, "STATE", shownLegends),
    ...stripRowTraces((panel && panel.data) || [], 0, "STATE2", shownLegends),
  ];
  const layout = baseLayout(panel || {}, 280);
  layout.margin = { l: 68, r: 82, t: 36, b: 36 };
  layout.yaxis = {
    range: [0, 2.2],
    fixedrange: true,
    tickvals: [1.6, 0.5],
    ticktext: ["STATE", "STATE2"],
    showgrid: false,
    zeroline: false,
  };
  layout.barmode = "overlay";
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

function renderPayload(payload) {
  if (payload.meta && payload.meta.state_styles) {
    stateStyles = payload.meta.state_styles;
  }
  expandPrintColumns(payload);
  currentPanels = payload.panels || {};
  const p1Rows = payload.panels && payload.panels.p1 && payload.panels.p1.data;
  if (p1Rows && p1Rows.length) {
    lastLookbackStart = p1Rows[0].x;
  }
  renderDualAxisPanel("panel-p1", payload.panels.p1);
  const statesPanel = payload.panels.p1_states || {};
  lastStripRuns = (statesPanel.data || []).map((run) => ({ ...run }));
  lastGroupRuns = (statesPanel.group_data || []).map((run) => ({ ...run }));
  renderStateTimeline(
    "panel-p1-states",
    { ...statesPanel, data: lastStripRuns, group_data: lastGroupRuns },
    panelTimeRange(payload.panels.p1),
  );
  if (PANEL1_STATES_CAPTION) {
    PANEL1_STATES_CAPTION.textContent = (payload.panels.p1_states && payload.panels.p1_states.caption) || "";
  }
  renderDualAxisPanel("panel-p2", payload.panels.p2);
  if (PANEL2_CAPTION) {
    PANEL2_CAPTION.textContent = payload.panels.p2.caption || "";
  }
  renderVolumePanel("panel-p3", payload.panels.p3);
  renderPredictionPanel("panel-p4", payload.panels.p4);
  const flow = payload.meta?.flow || {};
  const beta = payload.meta?.beta_ref || {};
  updateFreshnessBanner(payload.meta?.live_status);
  updateFlowDominanceBanner(payload.flow_dominance);
  storeBannerSeries(payload.flow_dominance_series);
  bindBannerHoverEvents();
  restoreHoverBanner();
  const lastSession = beta.last_tag_day
    ? new Date(`${beta.last_tag_day}T12:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" })
    : beta.last_session_end
    ? new Date(beta.last_session_end).toLocaleDateString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric" })
    : "?";
  const betaTag = beta.tag ? `${beta.tag}${beta.tag_detail ? ` (${beta.tag_detail})` : ""}` : "";
  const betaText = beta.ok
    ? (beta.tag
      ? `beta_ref: ${Number(beta.beta_ref).toExponential(2)} | ${betaTag} day OLS, ${beta.n_sessions_used} days to ${lastSession}, ${Number(beta.slope_bps_per_mm).toFixed(3)} bp/$1mm | yield display: ${Number(beta.beta_display_yield_bp_per_1m).toFixed(4)}`
      : `beta_ref: ${Number(beta.beta_ref).toExponential(2)} | ${beta.n_sessions_used || 10} whole sessions to ${lastSession} | yield display: ${Number(beta.beta_display_yield_bp_per_1m).toFixed(4)}`)
    : `beta_ref: unavailable${betaTag ? ` [${betaTag}]` : ""}${beta.reason ? ` (${beta.reason})` : ""}`;
  const request = payload.meta?.request || {};
  const viewLabel = request.aggression_symbols ? `${liveSelectionLabel(request)} | ` : "";
  META.textContent = `${viewLabel}Prints: ${(flow.trade_count || 0).toLocaleString()} | Aggress final: ${(flow.total_aggress_final || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} | ${betaText}`;
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
    trimRowsBefore(lastGroupRuns, lastLookbackStart, (run) => run.x1 || run.x0, 0);
    renderStateTimeline(
      "panel-p1-states",
      { data: lastStripRuns, group_data: lastGroupRuns, title: "States through lookback" },
      panelTimeRange(currentPanels.p1),
    );
    stripDirty = false;
  }
}

function appendBannerRows(prints) {
  prints.forEach((row) => {
    const hover = row.h || row.hover || row.banner;
    if (!hover) {
      return;
    }
    const item = { ...hover, x: row.x || hover.x };
    bannerSeries.push(item);
    candidateXKeys(item.x).forEach((key) => bannerByX.set(key, item));
  });
}

function applyRunEdge(runs, edge) {
  if (!edge || !edge.run) {
    return false;
  }
  if ((edge.action === "extend" || edge.action === "close") && runs.length) {
    runs[runs.length - 1] = { ...edge.run };
    return true;
  }
  if (edge.action === "open") {
    runs.push({ ...edge.run });
    return true;
  }
  return false;
}

function applyStripEdge(strip) {
  if (!strip) {
    return;
  }
  const fineChanged = applyRunEdge(lastStripRuns, strip.state);
  const groupChanged = applyRunEdge(lastGroupRuns, strip.group);
  if (fineChanged || groupChanged) {
    stripDirty = true;
  }
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

function liveSelection() {
  return {
    price: document.getElementById("price-symbol").value,
    chips: selectedAggressionSymbols(),
    dv01: document.getElementById("dv01-adjust").checked,
  };
}

function liveStreamUrl() {
  const selection = liveSelection();
  const params = new URLSearchParams({
    price: selection.price,
    chips: selection.chips.join(","),
    dv01: selection.dv01 ? "1" : "0",
  });
  return `/api/live/stream?${params.toString()}`;
}

function liveSelectionLabel(request) {
  const chips = (request.aggression_symbols || []).join("+") || "?";
  const dv01 = request.dv01_adjust === false ? " (DV01 off)" : "";
  return `Live: ${request.price_symbol || "?"} price vs ${chips}${dv01}`;
}

function clearLiveView(reason) {
  closeLiveStream();
  lastReadyFlowGroup = null;
  currentPanels = {};
  lastStripRuns = [];
  lastGroupRuns = [];
  chartsDirty = false;
  stripDirty = false;
  storeBannerSeries([]);
  PANEL_IDS.forEach((id) => {
    const element = document.getElementById(id);
    if (element && element.data) {
      Plotly.react(element, [], element.layout);
    }
  });
  updateFlowDominanceBanner({ status: "WARMING", state: "WARMING", reason });
  clearHoverBanner();
  META.textContent = "";
}

let selectionTimer = null;
const SELECTION_DEBOUNCE_MS = 400;

function onSelectionChange() {
  if (selectionTimer !== null) {
    clearTimeout(selectionTimer);
  }
  selectionTimer = setTimeout(() => {
    selectionTimer = null;
    const selection = liveSelection();
    clearLiveView(`Switching the live view to ${selection.price} price vs ${selection.chips.join("+")}.`);
    refreshDashboard(false);
  }, SELECTION_DEBOUNCE_MS);
}

function openLiveStream() {
  if (paused) {
    return;
  }
  closeLiveStream();
  setStatus("Connecting live stream...", "loading");
  liveSource = new EventSource(liveStreamUrl());
  liveSource.addEventListener("limit", (event) => {
    const payload = parseStreamPayload(event) || {};
    closeLiveStream();
    setStatus(payload.reason || "Too many live views are open; try the default chips.", "error");
  });
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
    setStatus(payload.reason || "Waiting for banner-window history...", "loading");
  });
  liveSource.onerror = () => {
    setStatus("Live stream reconnecting...", "error");
  };
}

function refreshDashboard() {
  if (paused) {
    setStatus("Paused.", "");
    closeLiveStream();
    return;
  }
  openLiveStream();
}

function updatePauseButton() {
  PAUSE_BTN.textContent = paused ? "Resume" : "Pause Live";
}

const HISTORY_SECTION = document.getElementById("history");
const HISTORY_START = document.getElementById("history-start");
const HISTORY_END = document.getElementById("history-end");
const HISTORY_VIEW = document.getElementById("history-view");
const HISTORY_LOAD = document.getElementById("history-load");
const HISTORY_LAST = document.getElementById("history-last");
const HISTORY_LAST5 = document.getElementById("history-last5");
const HISTORY_STATUS = document.getElementById("history-status");
const HISTORY_WARNINGS = document.getElementById("history-warnings");
const HISTORY_STATE_BANNER = document.getElementById("history-state-banner");
const HISTORY_STATE_MAIN = document.getElementById("history-state-main");
const HISTORY_FINE_BANNER = document.getElementById("history-fine-banner");
const HISTORY_FINE_MAIN = document.getElementById("history-fine-main");
const HISTORY_FINE_DETAIL = document.getElementById("history-fine-detail");
const HISTORY_FINE_BETA = document.getElementById("history-fine-beta");
const HISTORY_STRIP_CAPTION = document.getElementById("history-p1-states-caption");
const HISTORY_ZOOM_DEBOUNCE_MS = 500;
const HISTORY_SLOW_MS = 1500;
const HISTORY_IDLE = {
  status: "WARMING",
  state: "WARMING",
  group_headline: "STATE",
  headline: "STATE2",
  reason: "Load a range to see the states at its end; hover the history charts for any time.",
};

let historyRange = null;
let historyHover = [];
let historyEndState = null;
let historyRevision = "history";
let historySeq = 0;
let historyZoomTimer = null;
let historyQuick = {};
let historyStarted = false;
let historyEventsBound = false;

function formatEtDateTime(date) {
  return `${date.toLocaleString("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })} ET`;
}

function historyKey(value) {
  if (value == null) {
    return "";
  }
  if (value instanceof Date) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}T${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`;
  }
  return String(value).trim().replace(" ", "T").slice(0, 19);
}

function historyShortLabel(value) {
  const text = String(value || "").replace("T", " ");
  return `${text.slice(5, 7)}/${text.slice(8, 10)} ${text.slice(11, 16)}`;
}

function setHistoryStatus(message, kind = "") {
  HISTORY_STATUS.textContent = message;
  HISTORY_STATUS.className = `history-status ${kind}`.trim();
}

function renderHistoryWarnings(warnings) {
  const items = Array.isArray(warnings) ? warnings : [];
  HISTORY_WARNINGS.replaceChildren();
  HISTORY_WARNINGS.classList.toggle("hidden", !items.length);
  if (!items.length) {
    return;
  }
  const list = document.createElement("ul");
  items.forEach((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  });
  HISTORY_WARNINGS.appendChild(list);
}

function applyHistoryBanner(flow, hovered) {
  const state = flow || HISTORY_IDLE;
  const status = state.status || "UNAVAILABLE";
  const fallback = String(state.state || status).replaceAll("_", " ");
  applyBannerStyle(HISTORY_STATE_BANNER, state.group_color, status, "flow-dominance-banner flow-state-banner");
  HISTORY_STATE_MAIN.textContent = state.group_headline || fallback;
  applyBannerStyle(HISTORY_FINE_BANNER, state.color, status, "flow-dominance-banner");
  HISTORY_FINE_MAIN.textContent = state.headline || fallback;
  const asOf = state.as_of ? `${hovered ? "Hovered" : "End of range"}, as of ${formatEtDateTime(new Date(state.as_of))}` : "";
  const detail = status === "READY" && state.contribution_line ? state.contribution_line : state.reason || "";
  HISTORY_FINE_DETAIL.textContent = [asOf, detail].filter(Boolean).join(" · ");
  HISTORY_FINE_BETA.textContent = state.beta_line || (historyEndState && historyEndState.beta_line) || "";
}

function historyRowAt(x) {
  const key = historyKey(x);
  let lo = 0;
  let hi = historyHover.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (historyHover[mid].x <= key) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo > 0 ? historyHover[lo - 1] : null;
}

function renderHistory(payload) {
  historyHover = Array.isArray(payload.hover) ? payload.hover : [];
  historyEndState = payload.flow_dominance || null;
  const panels = payload.panels || {};
  const p1 = { ...(panels.p1 || { data: [] }), uirevision: historyRevision };
  const strip = { ...(panels.p1_states || {}), uirevision: historyRevision };
  renderDualAxisPanel("history-p1", p1);
  renderStateTimeline("history-p1-states", strip, visibleXRange("history-p1") || panelTimeRange(p1));
  if (HISTORY_STRIP_CAPTION) {
    HISTORY_STRIP_CAPTION.textContent = strip.caption || "";
  }
  applyHistoryBanner(historyEndState, false);
  renderHistoryWarnings(payload.warnings);
  const range = payload.range || {};
  const sessions = payload.sessions || [];
  const built = sessions.filter((session) => session.status === "built").length;
  const resolution = payload.resolution || {};
  const stripDetail = resolution.strip_bin_sec ? `strip bins ${resolution.strip_bin_sec}s` : "per-second strip";
  setHistoryStatus(
    `History ${historyShortLabel(range.start_et)}–${historyShortLabel(range.end_et)} ET, `
    + `${sessions.length} session${sessions.length === 1 ? "" : "s"} (${built} built) · ${payload.view_label || ""} · `
    + `${(resolution.rows || 0).toLocaleString()} seconds with prints · ${stripDetail}`,
  );
  bindHistoryEvents();
}

async function loadHistory(range, { zoom = false } = {}) {
  if (!range || !range.start || !range.end) {
    setHistoryStatus("Pick a start and an end time (Eastern).", "error");
    return;
  }
  const seq = ++historySeq;
  const params = new URLSearchParams({ view: range.view, start: range.start, end: range.end });
  setHistoryStatus(zoom ? "Loading zoomed detail..." : "Loading history...", "loading");
  const slow = setTimeout(() => {
    if (seq === historySeq) {
      setHistoryStatus(
        "Still loading: the history worker may be starting, or finishing a session build in the 17:00–18:00 ET break (up to ~20 s).",
        "loading",
      );
    }
  }, HISTORY_SLOW_MS);
  HISTORY_LOAD.disabled = true;
  try {
    const response = await fetch(`/api/history?${params.toString()}`);
    const payload = await response.json();
    if (seq !== historySeq) {
      return;
    }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    if (!zoom) {
      historyRange = { ...range };
      historyRevision = `history-${seq}`;
    }
    renderHistory(payload);
  } catch (error) {
    if (seq === historySeq) {
      setHistoryStatus(`History: ${error.message}`, "error");
    }
  } finally {
    clearTimeout(slow);
    if (seq === historySeq) {
      HISTORY_LOAD.disabled = false;
    }
  }
}

function onHistoryRelayout(eventData) {
  if (!eventData || !historyRange) {
    return;
  }
  let next = null;
  if (eventData["xaxis.autorange"]) {
    next = { ...historyRange };
  } else if (eventData["xaxis.range[0]"] != null && eventData["xaxis.range[1]"] != null) {
    next = { view: historyRange.view, start: historyKey(eventData["xaxis.range[0]"]), end: historyKey(eventData["xaxis.range[1]"]) };
  } else if (Array.isArray(eventData["xaxis.range"])) {
    next = { view: historyRange.view, start: historyKey(eventData["xaxis.range"][0]), end: historyKey(eventData["xaxis.range"][1]) };
  }
  if (!next) {
    return;
  }
  if (!eventData["xaxis.autorange"]) {
    Plotly.relayout("history-p1-states", { "xaxis.range": [next.start, next.end] });
  }
  clearTimeout(historyZoomTimer);
  historyZoomTimer = setTimeout(() => loadHistory(next, { zoom: true }), HISTORY_ZOOM_DEBOUNCE_MS);
}

function bindHistoryEvents() {
  if (historyEventsBound) {
    return;
  }
  const p1 = document.getElementById("history-p1");
  const strip = document.getElementById("history-p1-states");
  if (!p1 || typeof p1.on !== "function" || !strip || typeof strip.on !== "function") {
    return;
  }
  historyEventsBound = true;
  [p1, strip].forEach((element) => {
    element.on("plotly_hover", (event) => {
      const point = event && event.points && event.points[0];
      const row = point ? historyRowAt(point.x) : null;
      if (row) {
        applyHistoryBanner(row, true);
      }
    });
    element.on("plotly_unhover", () => applyHistoryBanner(historyEndState, false));
  });
  p1.on("plotly_relayout", onHistoryRelayout);
}

function historyInputRange() {
  return { view: HISTORY_VIEW.value, start: HISTORY_START.value, end: HISTORY_END.value };
}

function setHistoryInputs(choice) {
  if (!choice) {
    return false;
  }
  HISTORY_START.value = String(choice.start).slice(0, 16);
  HISTORY_END.value = String(choice.end).slice(0, 16);
  return true;
}

async function refreshHistorySessions() {
  try {
    const response = await fetch(`/api/history/sessions?view=${encodeURIComponent(HISTORY_VIEW.value)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    historyQuick = payload.quick_ranges || {};
    HISTORY_LAST.disabled = !historyQuick.last_session;
    HISTORY_LAST5.disabled = !historyQuick.last_5;
    return payload;
  } catch (error) {
    setHistoryStatus(`History sessions: ${error.message}`, "error");
    return null;
  }
}

async function startHistory() {
  if (historyStarted) {
    return;
  }
  historyStarted = true;
  setHistoryStatus("Loading the list of built sessions...", "loading");
  const payload = await refreshHistorySessions();
  if (!payload) {
    return;
  }
  if (setHistoryInputs(historyQuick.last_session)) {
    loadHistory(historyInputRange());
    return;
  }
  const pending = (payload.sessions || []).filter((session) => session.status === "pending").length;
  setHistoryStatus(
    `No sessions are built yet${pending ? ` (${pending} waiting)` : ""}. Sessions are built in the 17:00–18:00 ET break and at weekends.`,
  );
}

function initHistory() {
  if (!HISTORY_SECTION) {
    return;
  }
  applyHistoryBanner(null, false);
  HISTORY_LOAD.addEventListener("click", () => loadHistory(historyInputRange()));
  HISTORY_LAST.addEventListener("click", () => {
    if (setHistoryInputs(historyQuick.last_session)) {
      loadHistory(historyInputRange());
    }
  });
  HISTORY_LAST5.addEventListener("click", () => {
    if (setHistoryInputs(historyQuick.last_5)) {
      loadHistory(historyInputRange());
    }
  });
  HISTORY_VIEW.addEventListener("change", async () => {
    await refreshHistorySessions();
    if (historyRange) {
      loadHistory(historyInputRange());
    }
  });
  if ("IntersectionObserver" in window) {
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        startHistory();
      }
    }, { rootMargin: "200px" });
    observer.observe(HISTORY_SECTION);
  } else {
    startHistory();
  }
}

REFRESH_BTN.addEventListener("click", () => refreshDashboard());
PAUSE_BTN.addEventListener("click", () => {
  paused = !paused;
  updatePauseButton();
  refreshDashboard();
});
document.getElementById("dv01-adjust").addEventListener("change", onSelectionChange);
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
EXHAUSTION_SOUND.addEventListener("change", () => {
  try {
    localStorage.setItem(EXHAUSTION_SOUND_STORAGE_KEY, EXHAUSTION_SOUND.checked ? "1" : "0");
  } catch (_error) {
    // The checkbox still controls sound for this page when storage is unavailable.
  }
  if (EXHAUSTION_SOUND.checked) {
    playExhaustionChime();
  }
});
document.getElementById("price-symbol").addEventListener("change", onSelectionChange);
document.querySelectorAll("#aggression-symbols input").forEach((input) => {
  input.addEventListener("change", () => {
    if (!selectedAggressionSymbols().length) {
      input.checked = true;
      return;
    }
    onSelectionChange();
  });
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    return;
  }
  if (pendingDeploymentValidator) {
    reloadForDeployment(pendingDeploymentValidator);
    return;
  }
  checkDashboardDeployment();
  if (flushTimer !== null) {
    clearTimeout(flushTimer);
    flushTimer = null;
  }
  flushLive();
});

updatePauseButton();
updateHoverBanner(HOVER_BANNER_IDLE, false);
refreshDashboard();
initHistory();
checkDashboardDeployment();
window.setInterval(checkDashboardDeployment, DEPLOYMENT_POLL_MS);
