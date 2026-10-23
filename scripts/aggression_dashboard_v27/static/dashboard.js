const STATUS = document.getElementById("status");
const MODEL_SELECT = document.getElementById("model-select");
const DATE_SELECT = document.getElementById("date-select");
const REFRESH_BTN = document.getElementById("refresh-btn");
const READOUT = document.getElementById("readout");
const BEST_RULE = document.getElementById("best-rule");
const EXPLAINER = document.getElementById("explainer-text");
const LIVE_BANNER = document.getElementById("live-banner");
const LIVE_STATE = document.getElementById("live-state");
const LIVE_MESSAGE = document.getElementById("live-message");
const LIVE_FEED_STATUS = document.getElementById("live-feed-status");
const LIVE_PRICE = document.getElementById("live-price");
const ALERT_SCOPE = document.getElementById("alert-scope");

let CONFIG = null;

function setStatus(text, kind = "") {
  STATUS.textContent = text;
  STATUS.className = kind;
}

function fmt(value, digits = 3) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}

function fmtPct(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return `${fmt(100 * Number(value), digits)}%`;
}

function decimalTo32nds(price) {
  if (price == null || Number.isNaN(Number(price))) return "n/a";
  let whole = Math.floor(Number(price));
  const fraction = Number(price) - whole;
  let ticks64 = Math.round(fraction * 64);
  if (ticks64 >= 64) {
    whole += 1;
    ticks64 -= 64;
  }
  const thirtySeconds = Math.floor(ticks64 / 2);
  const half = ticks64 % 2 === 1;
  return `${whole}-${String(thirtySeconds).padStart(2, "0")}${half ? "+" : ""}`;
}

function bondTicks(values, targetCount = 6) {
  const finite = (values || []).map(Number).filter((value) => Number.isFinite(value));
  if (!finite.length) return {};
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const tick = 1 / 64;
  const lo = Math.floor(min / tick) * tick;
  const hi = Math.ceil(max / tick) * tick;
  const span = Math.max(hi - lo, tick);
  const stepTicks = Math.max(1, Math.ceil((span / tick) / Math.max(1, targetCount - 1)));
  const tickvals = [];
  for (let value = lo; value <= hi + tick / 2; value += stepTicks * tick) {
    tickvals.push(Number(value.toFixed(6)));
  }
  if (tickvals[tickvals.length - 1] < hi) tickvals.push(Number(hi.toFixed(6)));
  return {
    tickmode: "array",
    tickvals,
    ticktext: tickvals.map(decimalTo32nds),
  };
}

function priceHoverData(rows, key) {
  return (rows || []).map((row) => decimalTo32nds(row[key]));
}

function populateSelect(el, values, selected) {
  el.innerHTML = "";
  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    if (value === selected) option.selected = true;
    el.appendChild(option);
  });
}

function renderTable(el, rows, cols) {
  if (!rows?.length) {
    el.innerHTML = "<p>No rows.</p>";
    return;
  }
  const head = cols.map((col) => `<th>${col.label}</th>`).join("");
  const body = rows.map((row) => (
    `<tr>${cols.map((col) => `<td>${col.format ? col.format(row[col.key], row) : row[col.key]}</td>`).join("")}</tr>`
  )).join("");
  el.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function renderMetrics(metrics) {
  const sorted = [...(metrics || [])].sort((a, b) => a.oos_rmse - b.oos_rmse);
  Plotly.react("metrics-chart", [
    {
      x: sorted.map((row) => row.model),
      y: sorted.map((row) => row.oos_r2),
      name: "OOS R2",
      type: "bar",
      marker: { color: "#2f6f9f" },
      yaxis: "y",
    },
    {
      x: sorted.map((row) => row.model),
      y: sorted.map((row) => row.oos_rmse),
      name: "OOS RMSE",
      type: "scatter",
      mode: "lines+markers",
      yaxis: "y2",
      line: { color: "#c24e3a" },
    },
  ], {
    title: "Model Comparison (Walk-Forward OOS)",
    template: "plotly",
    height: 390,
    yaxis: { title: "R2" },
    yaxis2: { title: "RMSE", overlaying: "y", side: "right", showgrid: false },
    legend: { orientation: "h" },
    margin: { l: 60, r: 70, t: 55, b: 70 },
  }, { responsive: true });
}

function renderResearch(payload) {
  const rows = payload.series || [];
  READOUT.textContent = payload.plain_english || "";
  const rule = CONFIG.holdout_rules?.[0];
  BEST_RULE.textContent = rule
    ? `${rule.condition || `|z| >= ${rule.threshold}, ${rule.horizon}m`}: P(5m adv<=3 ticks) ${fmt(100 * rule.p_adverse_le_3_5m, 1)}%, median lean ${fmt(rule.median_lean_duration_min, 1)}m, n=${rule.n}. Not after-cost.`
    : "No holdout rule available.";
  const researchPriceTicks = bondTicks([...rows.map((r) => r.price), ...rows.map((r) => r.fair_value)]);

  Plotly.react("fair-value-chart", [
    {
      x: rows.map((r) => r.x),
      y: rows.map((r) => r.price),
      customdata: priceHoverData(rows, "price"),
      name: "Actual Price",
      mode: "lines",
      type: "scattergl",
      line: { color: "#1f4ed8" },
      hovertemplate: "Actual %{customdata} (%{y:.6f})<extra></extra>",
    },
    {
      x: rows.map((r) => r.x),
      y: rows.map((r) => r.fair_value),
      customdata: priceHoverData(rows, "fair_value"),
      name: "Fair Value",
      mode: "lines",
      type: "scattergl",
      line: { color: "#d62728", dash: "dash" },
      hovertemplate: "Fair value %{customdata} (%{y:.6f})<extra></extra>",
    },
  ], {
    title: `Actual vs Fair Value (${payload.model}, ${payload.date})`,
    template: "plotly",
    height: 430,
    hovermode: "x unified",
    yaxis: { title: "Price (32nds)", ...researchPriceTicks },
    margin: { l: 65, r: 35, t: 55, b: 70 },
    legend: { orientation: "h" },
  }, { responsive: true });

  Plotly.react("residual-chart", [
    { x: rows.map((r) => r.x), y: rows.map((r) => r.residual_z), name: "Residual z", mode: "lines", type: "scattergl", line: { color: "#5f74d8" } },
  ], {
    title: "Residual Z-Score (Research Thresholds)",
    template: "plotly",
    height: 430,
    hovermode: "x unified",
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 2, y1: 2, line: { color: "#b00020", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: -2, y1: -2, line: { color: "#b00020", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: "#777", dash: "dot" } },
    ],
    margin: { l: 65, r: 35, t: 55, b: 70 },
  }, { responsive: true });

  const drivers = payload.drivers || [];
  Plotly.react("drivers-chart", [{
    x: drivers.map((row) => row.mean),
    y: drivers.map((row) => row.feature),
    type: "bar",
    orientation: "h",
    marker: { color: drivers.map((row) => row.mean >= 0 ? "#2ca02c" : "#d62728") },
    name: "Driver",
  }], {
    title: `Feature Drivers (${payload.model})`,
    template: "plotly",
    height: 430,
    margin: { l: 150, r: 35, t: 55, b: 70 },
  }, { responsive: true });
}

function formatTradePrice(price) {
  if (price == null || Number.isNaN(Number(price))) return "n/a";
  return `${decimalTo32nds(price)} (${fmt(price, 5)})`;
}

function signedTicks(value) {
  if (value == null || Number.isNaN(Number(value))) return "0.0";
  const number = Number(value);
  const text = fmt(number, 1);
  return number > 0 ? `+${text}` : text;
}

function renderSessionDay(payload) {
  const day = payload.alerts?.session_stats || {};
  const startEl = document.getElementById("session-day-start");
  const ticksEl = document.getElementById("session-ticks");
  const countsEl = document.getElementById("session-counts");
  const closedEl = document.getElementById("session-closed");
  if (startEl) startEl.textContent = day.session_start_et || "18:00 ET";
  if (ticksEl) {
    ticksEl.textContent = signedTicks(day.ticks_captured);
    const ticks = Number(day.ticks_captured || 0);
    ticksEl.classList.toggle("positive", ticks > 0);
    ticksEl.classList.toggle("negative", ticks < 0);
  }
  if (countsEl) {
    countsEl.textContent =
      `${day.n_issued || 0} issued · ${day.n_closed || 0} closed · ${day.n_open || 0} open`;
  }
  if (closedEl) {
    closedEl.textContent =
      `${day.n_win || 0} win · ${day.n_lose || 0} lose · ${day.n_flat || 0} flat`;
  }
}

function renderTradeSignal(payload) {
  const box = document.getElementById("trade-signal");
  const entryEl = document.getElementById("trade-entry");
  const exitEl = document.getElementById("trade-exit");
  const nowEl = document.getElementById("trade-now");
  if (!box || !entryEl || !exitEl || !nowEl) return;
  const signal = payload.alerts?.trade_signal || {};
  const phase = String(signal.phase || "FLAT").toLowerCase();
  box.className = `trade-signal ${phase}`;
  if (signal.entry_action && signal.entry_ts_et) {
    entryEl.textContent = `${signal.entry_action}  ${formatTradePrice(signal.entry_price)}  ${signal.entry_ts_et}  z=${fmt(signal.entry_z, 2)}`;
  } else {
    entryEl.textContent = signal.entry_text || "FLAT — wait for |z| ≥ 1.5, session 2–6 ticks, same sign, new session-sign after exit";
  }
  exitEl.textContent = signal.exit_text || "Then EXIT when the fade side dies or flips. 10m is a mark, not a flatten";
  nowEl.textContent = signal.now_text || "No open lean";
}

function signalTraces(marks, yKey) {
  const entries = (marks || []).filter((m) => m.kind === "entry" && m[yKey] != null);
  const exits = (marks || []).filter((m) => m.kind === "exit" && m[yKey] != null);
  const deadlines = (marks || []).filter((m) => m.kind === "deadline");
  const traces = [];
  if (entries.length) {
    traces.push({
      x: entries.map((m) => m.x),
      y: entries.map((m) => m[yKey]),
      name: "ENTRY",
      mode: "markers",
      type: "scatter",
      customdata: entries.map((m) => m.label),
      marker: { color: "#1f4ed8", size: 9, symbol: "triangle-up" },
      hovertemplate: "%{customdata}<extra></extra>",
    });
  }
  if (exits.length) {
    traces.push({
      x: exits.map((m) => m.x),
      y: exits.map((m) => m[yKey]),
      name: "EXIT",
      mode: "markers",
      type: "scatter",
      customdata: exits.map((m) => m.label),
      marker: { color: "#2e7d4f", size: 9, symbol: "triangle-down" },
      hovertemplate: "%{customdata}<extra></extra>",
    });
  }
  return { traces, deadlineXs: deadlines.map((m) => m.x).filter(Boolean) };
}

function deadlineShapes(xs, color = "#888") {
  return xs.map((x) => ({
    type: "line",
    x0: x,
    x1: x,
    yref: "paper",
    y0: 0,
    y1: 1,
    line: { color, dash: "dash", width: 1 },
  }));
}

function renderLive(payload) {
  LIVE_BANNER.className = `live-banner ${(payload.state || "none").toLowerCase()}`;
  LIVE_STATE.textContent = payload.state || "NONE";
  LIVE_MESSAGE.textContent = payload.message || "";
  renderSessionDay(payload);
  renderTradeSignal(payload);
  const feed = payload.feed || {};
  const feedSource = feed.source === "databento_backup"
    ? "Databento backup"
    : "AccessAPI";
  LIVE_FEED_STATUS.textContent = feed.connected
    ? `${feedSource} live, last tick ${fmt(feed.last_tick_age_sec, 1)}s ago`
    : `${feedSource} down: ${feed.error || "unknown"}`;
  const latest = payload.latest || {};
  if (LIVE_PRICE) {
    LIVE_PRICE.textContent = latest.price == null
      ? "Price: n/a"
      : `Price: ${decimalTo32nds(latest.price)} (${fmt(latest.price, 6)})`;
  }
  const stats = payload.alerts?.stats || {};
  const watch = stats.watch_stats || {};
  const dislocation = stats.dislocation_stats || {};
  document.getElementById("alert-issued").textContent = `Total WATCH ${stats.watch_issued || 0} | DISLOCATION ${stats.dislocation_issued || 0}`;
  document.getElementById("watch-stats").textContent = `${watch.issued || 0} trades | ${fmt(100 * (watch.resolution_rate || 0), 1)}% z-exit | trade hit ${fmt(100 * (watch.directional_hit_rate_trade || 0), 1)}%`;
  document.getElementById("dislocation-stats").textContent = `${dislocation.issued || 0} trades | ${fmt(100 * (dislocation.resolution_rate || 0), 1)}% z-exit | trade hit ${fmt(100 * (dislocation.directional_hit_rate_trade || 0), 1)}%`;
  document.getElementById("hold-expectations").textContent =
    `Median resolve ${fmt(stats.median_minutes_to_close, 1)}m | ` +
    `adverse ${fmt(stats.median_mae_to_resolve_ticks, 1)} ticks | ` +
    `price share ${fmtPct(stats.median_price_share_of_reversion, 1)}`;

  const lifoPayload = payload.lifo || {};
  const lifoCtx = lifoPayload.context || {};
  const lifoStats = lifoPayload.lifo_stats || {};
  const lifoActive = !!lifoCtx.active;
  const lifoStateEl = document.getElementById("lifo-state");
  if (lifoStateEl) {
    lifoStateEl.textContent = lifoActive ? "LIFO FRIENDLY" : "NORMAL";
  }
  const lifoStatsEl = document.getElementById("lifo-stats");
  if (lifoStatsEl) {
    lifoStatsEl.textContent =
      `finalized ${lifoStats.n_finalized || 0} | ` +
      `RANGE ${fmtPct(lifoStats.range_rate, 1)} | ` +
      `TREND ${fmtPct(lifoStats.trend_rate, 1)} | ` +
      `amb ${fmtPct(lifoStats.ambiguous_rate, 1)} | ` +
      `active ${lifoStats.n_active || 0}`;
  }
  const lifoBanner = document.getElementById("lifo-alert");
  if (lifoBanner) {
    const z = Number(lifoCtx.residual_z);
    const leanLong = lifoActive && Number.isFinite(z) && z < 0;
    const leanShort = lifoActive && Number.isFinite(z) && z > 0;
    lifoBanner.classList.toggle("hidden", !lifoActive);
    lifoBanner.classList.toggle("long", leanLong);
    lifoBanner.classList.toggle("short", leanShort);
  }

  const rows = payload.series || [];
  const marks = payload.alerts?.chart_marks || [];
  const priceMarks = signalTraces(marks, "price");
  const livePriceTicks = bondTicks([...rows.map((r) => r.price), ...rows.map((r) => r.fair_value)]);
  Plotly.react("live-fair-value-chart", [
    {
      x: rows.map((r) => r.x),
      y: rows.map((r) => r.price),
      customdata: priceHoverData(rows, "price"),
      name: "Actual Price",
      mode: "lines",
      type: "scattergl",
      line: { color: "#1f4ed8" },
      hovertemplate: "Actual %{customdata} (%{y:.6f})<extra></extra>",
    },
    {
      x: rows.map((r) => r.x),
      y: rows.map((r) => r.fair_value),
      customdata: priceHoverData(rows, "fair_value"),
      name: "Live Fair Value",
      mode: "lines",
      type: "scattergl",
      line: { color: "#d62728", dash: "dash" },
      hovertemplate: "Fair value %{customdata} (%{y:.6f})<extra></extra>",
    },
    ...priceMarks.traces,
  ], {
    title: "Live V2.7 Actual vs 10m Local Fair Value",
    template: "plotly",
    height: 390,
    hovermode: "x unified",
    yaxis: { title: "Price (32nds)", ...livePriceTicks },
    shapes: deadlineShapes(priceMarks.deadlineXs),
    margin: { l: 65, r: 35, t: 55, b: 70 },
    legend: { orientation: "h" },
  }, { responsive: true });

  const zMarks = signalTraces(marks.map((m) => ({ ...m, residual_z: m.z })), "residual_z");
  Plotly.react("live-residual-chart", [
    { x: rows.map((r) => r.x), y: rows.map((r) => r.residual_z), name: "Causal residual z", mode: "lines", type: "scattergl", line: { color: "#5f74d8" } },
    ...zMarks.traces,
  ], {
    title: "Live Causal Residual Z",
    template: "plotly",
    height: 390,
    hovermode: "x unified",
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 2, y1: 2, line: { color: "#b00020", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: -2, y1: -2, line: { color: "#b00020", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 1.5, y1: 1.5, line: { color: "#d89614", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: -1.5, y1: -1.5, line: { color: "#d89614", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 0.5, y1: 0.5, line: { color: "#888", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: -0.5, y1: -0.5, line: { color: "#888", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: "#777", dash: "dot" } },
      ...deadlineShapes(priceMarks.deadlineXs, "#888"),
    ],
    margin: { l: 65, r: 35, t: 55, b: 70 },
  }, { responsive: true });
}

function renderAlertLog(payload) {
  const stats = payload.stats || {};
  const session = payload.session_stats || {};
  const sessionBit = session.session_start_et
    ? `Session since ${session.session_start_et}: ${signedTicks(session.ticks_captured)} ticks, ${session.n_issued || 0} issued, ${session.n_closed || 0} closed, ${session.n_open || 0} open | `
    : "";
  document.getElementById("alert-log-stats").textContent =
    sessionBit +
    `Issued: WATCH ${stats.watch_issued || 0}, DISLOCATION ${stats.dislocation_issued || 0} | ` +
    `Resolved ${stats.resolved || 0}, expired ${stats.expired || 0}, finalized ${stats.finalized || 0} | ` +
    `Trade hit ${fmt(100 * (stats.directional_hit_rate_trade || 0), 1)}%, avg trade pnl ${fmt(stats.avg_dir_pnl_trade, 4)} (${fmt(stats.avg_dir_pnl_trade_ticks, 2)} ticks), avg adverse ${fmt(stats.avg_adverse_trade, 4)} | ` +
    `WATCH resolved ${fmt(100 * ((stats.watch_stats || {}).resolution_rate || 0), 1)}%, DISLOCATION resolved ${fmt(100 * ((stats.dislocation_stats || {}).resolution_rate || 0), 1)}% | ` +
    `Median resolve ${fmt(stats.median_minutes_to_close, 1)}m, adverse ${fmt(stats.median_mae_to_resolve_ticks, 1)} ticks, price share ${fmtPct(stats.median_price_share_of_reversion, 1)}`;
  renderTable(document.getElementById("alert-log-table"), (payload.rows || []).slice().reverse(), [
    { key: "opened_ts", label: "entry time" },
    { key: "direction", label: "entry", format: (v) => (v === "SHORT_RICH" ? "ENTER SHORT" : v === "LONG_CHEAP" ? "ENTER LONG" : v || "") },
    { key: "open_price", label: "entry px", format: (v) => fmt(v, 5) },
    { key: "open_residual_z", label: "entry z", format: (v) => fmt(v, 2) },
    { key: "exit_reason", label: "exit", format: (v, row) => {
      if (v === "FLIP") return "EXIT FLIP";
      if (v === "Z_RESET") return "EXIT Z-RESET";
      if (v === "CLOCK_10M") return "EXIT 10M CLOCK";
      if (row?.reversion_status === "resolved") return "EXIT Z-RESET";
      if (row?.reversion_status === "expired") return "EXIT 10M CLOCK";
      if (row?.reversion_status === "pending") return "HOLDING";
      return v || "";
    } },
    { key: "minutes_held", label: "min held", format: (v, row) => fmt(v ?? row?.minutes_to_close, 1) },
    { key: "close_price", label: "exit px", format: (v) => fmt(v, 5) },
    { key: "dir_pnl_trade", label: "trade pnl", format: (v) => fmt(v, 4) },
    { key: "dir_pnl_trade_ticks", label: "trade ticks", format: (v) => fmt(v, 2) },
    { key: "adverse_trade", label: "trade adverse", format: (v) => fmt(v, 4) },
    { key: "mae_to_resolve_ticks", label: "adverse ticks", format: (v) => fmt(v, 1) },
    { key: "price_share_of_reversion", label: "% via price", format: (v) => fmtPct(v, 1) },
    { key: "status", label: "trade", format: (v) => (v === "open" ? "OPEN" : v === "finalized" ? "CLOSED" : v || "") },
  ]);
}

async function refreshAlertLog() {
  try {
    const scope = ALERT_SCOPE?.value || "today";
    const resp = await fetch(`/api/alert_log?scope=${encodeURIComponent(scope)}`);
    const payload = await resp.json();
    if (resp.ok && payload.success) renderAlertLog(payload);
  } catch (error) {
    // Keep live chart updates independent from table rendering failures.
  }
}

async function refreshLive() {
  try {
    const resp = await fetch("/api/live_signal");
    const payload = await resp.json();
    if (resp.status === 202) {
      LIVE_MESSAGE.textContent = payload.error || "Warming up live signal...";
      const source = payload.feed?.source === "databento_backup"
        ? "Databento backup"
        : "AccessAPI";
      LIVE_FEED_STATUS.textContent = payload.feed?.connected
        ? `${source} connected`
        : `${source} waiting`;
      renderSessionDay(payload);
      return;
    }
    if (!resp.ok || !payload.success) return;
    renderLive(payload);
    refreshAlertLog();
  } catch (error) {
    LIVE_MESSAGE.textContent = `Live signal error: ${error.message}`;
  }
}

async function refreshResearch() {
  setStatus("Loading research view...", "loading");
  const url = `/api/research?model=${encodeURIComponent(MODEL_SELECT.value)}&date=${encodeURIComponent(DATE_SELECT.value)}`;
  const resp = await fetch(url);
  const payload = await resp.json();
  if (!resp.ok || !payload.success) {
    setStatus(payload.error || `HTTP ${resp.status}`, "error");
    return;
  }
  renderResearch(payload);
  setStatus("Ready.", "");
}

async function init() {
  const resp = await fetch("/api/config");
  CONFIG = await resp.json();
  populateSelect(MODEL_SELECT, CONFIG.models, CONFIG.default_model);
  populateSelect(DATE_SELECT, CONFIG.dates, CONFIG.default_date);
  EXPLAINER.textContent = CONFIG.explainer || "";
  renderMetrics(CONFIG.metrics);
  renderTable(document.getElementById("holdout-table"), CONFIG.holdout_rules || [], [
    { key: "threshold", label: "z" },
    { key: "horizon", label: "horizon" },
    { key: "condition", label: "condition" },
    { key: "n", label: "n" },
    { key: "hit_rate", label: "hit %", format: (v) => fmt(100 * v, 1) },
    { key: "avg_directional_pnl_points", label: "avg pts", format: (v) => fmt(v, 4) },
    { key: "corr_residual_future_change", label: "corr", format: (v) => fmt(v, 3) },
  ]);
  renderTable(document.getElementById("conditional-table"), CONFIG.conditional_rules || [], [
    { key: "condition", label: "condition" },
    { key: "horizon", label: "horizon" },
    { key: "n", label: "n" },
    { key: "mean_reversion_hit_rate", label: "hit %", format: (v) => fmt(100 * v, 1) },
    { key: "mean_directional_pnl_points", label: "avg pts", format: (v) => fmt(v, 4) },
  ]);
  await refreshResearch();
  await refreshLive();
  setInterval(refreshLive, 3000);
}

REFRESH_BTN.addEventListener("click", refreshResearch);
MODEL_SELECT.addEventListener("change", refreshResearch);
DATE_SELECT.addEventListener("change", refreshResearch);
ALERT_SCOPE.addEventListener("change", refreshAlertLog);
init();
