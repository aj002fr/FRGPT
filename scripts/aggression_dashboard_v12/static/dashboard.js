const STATUS = document.getElementById("status");
const META = document.getElementById("meta");
const REFRESH_BTN = document.getElementById("refresh-btn");
const PAUSE_BTN = document.getElementById("pause-btn");
const AUTO_REFRESH = document.getElementById("auto-refresh");
const MODE_SELECT = document.getElementById("mode-select");
const END_TIME_GROUP = document.getElementById("end-time-group");
const FRESHNESS_BANNER = document.getElementById("freshness-banner");
let refreshTimer = null;
let paused = false;
let inFlight = false;

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
  if (!connected) {
    FRESHNESS_BANNER.classList.add("down");
    FRESHNESS_BANNER.textContent = `LIVE FEED DOWN${status.error ? ` - ${status.error}` : ""}`;
  } else if (age != null && age < 30) {
    FRESHNESS_BANNER.classList.add("live");
    FRESHNESS_BANNER.textContent = `LIVE - last tick ${formatAge(age)} ago`;
  } else {
    FRESHNESS_BANNER.classList.add("stale");
    FRESHNESS_BANNER.textContent = `${marketOpen ? "STALE" : "MARKET CLOSED"} - last tick ${formatAge(age)} ago`;
  }
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
    },
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

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(100 * Number(value)).toFixed(1)}%`;
}

function fmtNum(value, digits = 3) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function renderCaution(payload) {
  const caution = payload?.range_caution || payload || {};
  const ctx = caution.context || {};
  const stats = caution.stats || {};
  const lifoStats = caution.lifo_stats || {};
  const soft = ctx.soft_breakout || {};
  const lifo = ctx.lifo_friendly || {};
  const stateEl = document.getElementById("caution-state");
  const state = ctx.state || "NORMAL";
  stateEl.textContent = state;
  stateEl.className = `badge ${state === "CAUTIOUS_RANGE" ? "caution" : "normal"}`;
  document.getElementById("caution-z").textContent = fmtNum(ctx.residual_z, 2);
  document.getElementById("caution-large").textContent = ctx.large ? "YES" : "NO";
  document.getElementById("caution-widen").textContent = ctx.widening_15m ? "YES" : "NO";
  document.getElementById("caution-price").textContent = fmtNum(ctx.price, 5);
  document.getElementById("caution-reasons").textContent = (ctx.reasons || []).join(" · ") || "No caution flags.";

  const lifoEl = document.getElementById("lifo-state");
  const lifoActive = !!lifo.active;
  lifoEl.textContent = lifoActive ? "LIFO FRIENDLY" : "NORMAL";
  lifoEl.className = `badge ${lifoActive ? "lifo" : "normal"}`;
  const alertBanner = document.getElementById("lifo-alert");
  if (alertBanner) {
    alertBanner.classList.toggle("hidden", !lifoActive);
  }

  const softEl = document.getElementById("soft-bo-state");
  const softState = soft.state || "NORMAL";
  softEl.textContent = softState;
  softEl.className = `badge ${softState === "SOFT_BREAKOUT_LEAN" ? "soft-bo" : "normal"}`;
  document.getElementById("soft-bo-age").textContent = fmtNum(soft.residual_large_age_min, 0);
  const persistBits = [
    soft.large_persist_10 ? "≥10" : null,
    soft.large_persist_20 ? "≥20" : null,
  ].filter(Boolean);
  document.getElementById("soft-bo-persist").textContent = persistBits.length ? persistBits.join(" · ") : "NO";
  document.getElementById("soft-bo-z15").textContent = soft.z_ge_1_5 ? "YES" : "NO";
  document.getElementById("soft-bo-fast").textContent = soft.fast_15m ? "YES" : "NO";
  document.getElementById("soft-bo-factors").textContent =
    (soft.active_factors || []).length
      ? `Factors: ${(soft.active_factors || []).join(", ")}`
      : "No soft-breakout PASS factors active.";
  document.getElementById("soft-bo-reasons").textContent =
    (soft.reasons || []).join(" · ") || "";

  document.getElementById("stat-n").textContent = String(stats.n_finalized || 0);
  document.getElementById("stat-range").textContent = fmtPct(stats.range_rate);
  document.getElementById("stat-trend").textContent = fmtPct(stats.trend_rate);
  document.getElementById("stat-amb").textContent = fmtPct(stats.ambiguous_rate);
  document.getElementById("stat-active").textContent = String(stats.n_active || 0);
  document.getElementById("stat-baseline").textContent = fmtPct(stats.reference_validation_range_baseline);
  document.getElementById("lifo-stat-n").textContent = String(lifoStats.n_finalized || 0);
  document.getElementById("lifo-stat-range").textContent = fmtPct(lifoStats.range_rate);
  document.getElementById("lifo-stat-trend").textContent = fmtPct(lifoStats.trend_rate);
  document.getElementById("lifo-stat-amb").textContent = fmtPct(lifoStats.ambiguous_rate);
  document.getElementById("lifo-stat-active").textContent = String(lifoStats.n_active || 0);

  const body = document.getElementById("episode-body");
  const episodes = caution.recent_episodes || [];
  if (!episodes.length) {
    body.innerHTML = `<tr><td colspan="6">No alert episodes yet.</td></tr>`;
    return;
  }
  body.innerHTML = episodes.map((ep) => `
    <tr>
      <td>${ep.lock_ts_utc || "—"}</td>
      <td>${ep.alert_kind === "lifo" ? "LIFO FRIENDLY" : "RANGE CAUTION"}</td>
      <td>${ep.phase || "—"}</td>
      <td>${fmtNum(ep.residual_z, 2)}</td>
      <td>${ep.outcome || "pending"}</td>
      <td>${fmtNum(ep.net_ticks, 2)}</td>
    </tr>
  `).join("");
}

async function refreshCautionLog() {
  const scope = document.getElementById("log-scope").value;
  try {
    const response = await fetch(`/api/caution_log?scope=${encodeURIComponent(scope)}`);
    const payload = await response.json();
    if (!payload.success) return;
    // Prefer live recent episodes from /api/range_caution; log is backup view.
    if (payload.rows && payload.rows.length && !document.getElementById("episode-body").dataset.live) {
      const body = document.getElementById("episode-body");
      body.innerHTML = payload.rows.slice().reverse().slice(0, 20).map((ep) => `
        <tr>
          <td>${ep.lock_ts_utc || "—"}</td>
          <td>${ep.alert_kind === "lifo" ? "LIFO FRIENDLY" : "RANGE CAUTION"}</td>
          <td>${ep.phase || "FINALIZED"}</td>
          <td>${fmtNum(ep.residual_z, 2)}</td>
          <td>${ep.outcome || "—"}</td>
          <td>${fmtNum(ep.net_ticks, 2)}</td>
        </tr>
      `).join("");
    }
  } catch (_err) {
    // ignore log refresh errors; main panels still work
  }
}

function renderPayload(payload) {
  renderDualAxisPanel("panel-p1", payload.panels.p1);
  renderDualAxisPanel("panel-p2", payload.panels.p2);
  renderVolumePanel("panel-p3", payload.panels.p3);
  renderPredictionPanel("panel-p4", payload.panels.p4);
  renderPredictionPanel("panel-p5", payload.panels.p5);

  if (payload.range_caution) {
    renderCaution(payload.range_caution);
    document.getElementById("episode-body").dataset.live = "1";
  }

  const flow = payload.meta?.flow || {};
  const fairValue = payload.meta?.fair_value || {};
  const intraday = fairValue.intraday || {};
  const tenday = fairValue.tenday || {};
  updateFreshnessBanner(payload.meta?.live_status);
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
  const cautionState = payload.range_caution?.context?.state || "—";
  const softState = payload.range_caution?.context?.soft_breakout?.state || "—";
  const lifoState = payload.range_caution?.context?.lifo_friendly?.state || "—";
  META.textContent = `V1.2 | Caution: ${cautionState} | LIFO: ${lifoState} | SoftBO: ${softState} | Trades: ${(flow.trade_count || 0).toLocaleString()} | Aggress final: ${(flow.total_aggress_final || 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} | ${intradayText} | ${tendayText}${alignText}`;
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
document.getElementById("dv01-adjust").addEventListener("change", () => refreshDashboard(false));
document.getElementById("lookback").addEventListener("change", () => refreshDashboard(false));
document.getElementById("price-symbol").addEventListener("change", () => refreshDashboard(false));
document.getElementById("log-scope").addEventListener("change", () => refreshCautionLog());
document.querySelectorAll("#aggression-symbols input").forEach((input) => {
  input.addEventListener("change", () => refreshDashboard(false));
});

updateModeUi();
refreshDashboard(false);
setInterval(refreshCautionLog, 15_000);

