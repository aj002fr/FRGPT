const byId = (id) => document.getElementById(id);
const STATUS = byId("status");
const MODEL_SELECT = byId("model-select");
const DATE_SELECT = byId("date-select");
const REFRESH_BTN = byId("refresh-btn");
const READOUT = byId("readout");
const BEST_RULE = byId("best-rule");
const EXPLAINER = byId("explainer-text");
const LIVE_BANNER = byId("live-banner");
const LIVE_STATE = byId("live-state");
const LIVE_MESSAGE = byId("live-message");
const LIVE_FEED_STATUS = byId("live-feed-status");
const LIVE_PRICE = byId("live-price");
const ALERT_SCOPE = byId("alert-scope");
const GRADE_DIALOG = byId("grade-dialog");
const GRADE_NOTE = byId("grade-note");

let CONFIG = null;
let LATEST_FEEDBACK = null;
let ACTIVE_GRADE_EPISODE = null;
let FEEDBACK_BUSY = false;

function setStatus(text, kind = "") {
  STATUS.textContent = text;
  STATUS.className = kind;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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
  const finite = (values || []).map(Number).filter(Number.isFinite);
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
  return { tickmode: "array", tickvals, ticktext: tickvals.map(decimalTo32nds) };
}

function priceHoverData(rows, key) {
  return (rows || []).map((row) => decimalTo32nds(row[key]));
}

function populateSelect(el, values, selected) {
  el.innerHTML = "";
  (values || []).forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    option.selected = value === selected;
    el.appendChild(option);
  });
}

function renderTable(el, rows, cols) {
  if (!rows?.length) {
    el.innerHTML = "<p class=\"small-note\">No rows.</p>";
    return;
  }
  const head = cols.map((col) => `<th>${escapeHtml(col.label)}</th>`).join("");
  const body = rows.map((row) => {
    const cells = cols.map((col) => {
      const rendered = col.format ? col.format(row[col.key], row) : row[col.key];
      return `<td>${col.raw ? rendered : escapeHtml(rendered)}</td>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
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
    ? `|z| >= ${rule.threshold}, ${rule.horizon}m: hit ${fmtPct(rule.hit_rate)}, avg move ${fmt(rule.avg_directional_pnl_points, 4)} pts, n=${rule.n}.`
    : "No holdout rule available.";
  const priceTicks = bondTicks([...rows.map((row) => row.price), ...rows.map((row) => row.fair_value)]);

  Plotly.react("fair-value-chart", [
    {
      x: rows.map((row) => row.x),
      y: rows.map((row) => row.price),
      customdata: priceHoverData(rows, "price"),
      name: "Actual Price",
      mode: "lines",
      type: "scattergl",
      line: { color: "#1f4ed8" },
      hovertemplate: "Actual %{customdata} (%{y:.6f})<extra></extra>",
    },
    {
      x: rows.map((row) => row.x),
      y: rows.map((row) => row.fair_value),
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
    yaxis: { title: "Price (32nds)", ...priceTicks },
    margin: { l: 65, r: 35, t: 55, b: 70 },
    legend: { orientation: "h" },
  }, { responsive: true });

  Plotly.react("residual-chart", [{
    x: rows.map((row) => row.x),
    y: rows.map((row) => row.residual_z),
    name: "Residual z",
    mode: "lines",
    type: "scattergl",
    line: { color: "#5f74d8" },
  }], {
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

function renderFeedbackMetrics(targetId, stats) {
  const values = [
    ["Issued", stats.issued || 0],
    ["Pending", stats.pending_grade || 0],
    ["Graded", stats.graded || 0],
    ["Correct", stats.marked_correct || 0],
    ["Wrong", stats.marked_wrong || 0],
    ["Hit Rate", fmtPct(stats.hit_rate_graded)],
  ];
  byId(targetId).innerHTML = values.map(([label, value]) => (
    `<div class="feedback-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
  )).join("");
}

function gradeText(row) {
  if (row.human_correct === true) return '<span class="grade-correct">Correct</span>';
  if (row.human_correct === false) return '<span class="grade-wrong">Wrong</span>';
  return `<span class="grade-pending">${row.human_feedback_status === "skipped" ? "Pending (skipped)" : "Pending"}</span>`;
}

function episodeDescription(row) {
  if (row.alert_kind === "lifo") return "LIFO FRIENDLY";
  return `${row.fade_lean || "C018 fade"} after ${row.move_direction || "move"}`;
}

function renderFeedback(payload) {
  if (!payload) return;
  LATEST_FEEDBACK = payload;
  byId("feedback-date").textContent =
    `${payload.date_et || "Today"} ET · Right = ${payload.right_definition || "human grade only"}.`;
  renderFeedbackMetrics("lifo-feedback-stats", payload.stats?.lifo || {});
  renderFeedbackMetrics("c018-feedback-stats", payload.stats?.c018 || {});
  renderTable(byId("feedback-table"), payload.recent_episodes || [], [
    { key: "lock_ts_utc", label: "locked (UTC)" },
    { key: "alert_kind", label: "kind" },
    { key: "description", label: "alert", format: (_value, row) => episodeDescription(row) },
    { key: "human_correct", label: "human grade", format: (_value, row) => gradeText(row), raw: true },
    { key: "human_feedback_note", label: "note" },
    {
      key: "episode_id",
      label: "action",
      raw: true,
      format: (_value, row) => (
        `<button class="grade-btn" data-episode-id="${escapeHtml(row.episode_id)}" ` +
        `data-alert-kind="${escapeHtml(row.alert_kind)}">Grade</button>`
      ),
    },
  ]);
  maybePromptForFeedback();
}

function openGradeDialog(episode) {
  if (!episode || FEEDBACK_BUSY || GRADE_DIALOG.open) return;
  ACTIVE_GRADE_EPISODE = episode;
  const previousFocus = document.activeElement;
  byId("grade-episode-summary").textContent =
    `${episodeDescription(episode)} · locked ${episode.lock_ts_utc || "n/a"} · ` +
    `price ${decimalTo32nds(episode.lock_price)}.`;
  GRADE_NOTE.value = episode.human_feedback_note || "";
  GRADE_DIALOG.setAttribute("open", "");
  if (previousFocus instanceof HTMLElement) {
    previousFocus.focus({ preventScroll: true });
  }
}

function maybePromptForFeedback() {
  if (GRADE_DIALOG.open || FEEDBACK_BUSY) return;
  const next = LATEST_FEEDBACK?.pending_prompts?.[0];
  if (next) openGradeDialog(next);
}

async function submitFeedback(correct) {
  if (!ACTIVE_GRADE_EPISODE || FEEDBACK_BUSY) return;
  FEEDBACK_BUSY = true;
  byId("feedback-status").className = "";
  byId("feedback-status").textContent = "Saving grade...";
  GRADE_DIALOG.querySelectorAll("button").forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        episode_id: ACTIVE_GRADE_EPISODE.episode_id,
        alert_kind: ACTIVE_GRADE_EPISODE.alert_kind,
        correct,
        note: GRADE_NOTE.value || null,
      }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    LATEST_FEEDBACK = payload.feedback;
    GRADE_DIALOG.close();
    ACTIVE_GRADE_EPISODE = null;
    renderFeedback(payload.feedback);
    byId("feedback-status").className = "";
    byId("feedback-status").textContent =
      correct == null ? "Skipped; grade remains pending." : "Grade saved. Last grade wins.";
  } catch (error) {
    byId("feedback-status").textContent = `Feedback error: ${error.message}`;
    byId("feedback-status").className = "error";
  } finally {
    FEEDBACK_BUSY = false;
    GRADE_DIALOG.querySelectorAll("button").forEach((button) => { button.disabled = false; });
    window.setTimeout(maybePromptForFeedback, 150);
  }
}

function renderLive(payload) {
  LIVE_BANNER.className = `live-banner ${(payload.state || "none").toLowerCase()}`;
  LIVE_STATE.textContent = payload.state || "NONE";
  LIVE_MESSAGE.textContent = payload.message || "";
  const feed = payload.feed || {};
  const feedSource = feed.source === "databento_backup" ? "Databento backup" : "AccessAPI";
  LIVE_FEED_STATUS.textContent = feed.connected
    ? `${feedSource} live, last tick ${fmt(feed.last_tick_age_sec, 1)}s ago`
    : `${feedSource} down: ${feed.error || "unknown"}`;
  const latest = payload.latest || {};
  LIVE_PRICE.textContent = latest.price == null
    ? "Price: n/a"
    : `Price: ${decimalTo32nds(latest.price)} (${fmt(latest.price, 6)})`;

  const stats = payload.alerts?.stats || {};
  const watch = stats.watch_stats || {};
  const dislocation = stats.dislocation_stats || {};
  byId("alert-issued").textContent =
    `Total WATCH ${stats.watch_issued || 0} | DISLOCATION ${stats.dislocation_issued || 0}`;
  byId("watch-stats").textContent =
    `${watch.issued || 0} issued | ${fmtPct(watch.resolution_rate)} resolved | 30m hit ${fmtPct(watch.directional_hit_rate_30m)}`;
  byId("dislocation-stats").textContent =
    `${dislocation.issued || 0} issued | ${fmtPct(dislocation.resolution_rate)} resolved | 30m hit ${fmtPct(dislocation.directional_hit_rate_30m)}`;
  byId("hold-expectations").textContent =
    `Median resolve ${fmt(stats.median_minutes_to_close, 1)}m | ` +
    `adverse ${fmt(stats.median_mae_to_resolve_ticks, 1)} ticks | ` +
    `price share ${fmtPct(stats.median_price_share_of_reversion)}`;

  const lifoPayload = payload.lifo || {};
  const lifoContext = lifoPayload.context || {};
  const lifoStats = lifoPayload.lifo_stats || {};
  const lifoActive = Boolean(lifoContext.active);
  byId("lifo-state").textContent = lifoActive ? "LIFO FRIENDLY" : "NORMAL";
  byId("lifo-stats").textContent =
    `finalized ${lifoStats.n_finalized || 0} | RANGE ${fmtPct(lifoStats.range_rate)} | ` +
    `TREND ${fmtPct(lifoStats.trend_rate)} | amb ${fmtPct(lifoStats.ambiguous_rate)} | ` +
    `active ${lifoStats.n_active || 0}`;
  byId("lifo-alert").classList.toggle("hidden", !lifoActive);

  const c018Context = payload.c018?.context || {};
  const c018Active = Boolean(c018Context.active);
  byId("c018-state").textContent = c018Active ? c018Context.fade_lean : (c018Context.state || "NORMAL");
  byId("c018-stats").textContent =
    `range ${fmt(c018Context.range_30m_ticks, 1)} ticks | ` +
    `T3 ≥ ${fmt(payload.c018?.range_cutoff_ticks, 1)} | last 30 completed bars`;
  byId("c018-fade-lean").textContent = c018Context.fade_lean || "n/a";
  byId("c018-event-detail").textContent =
    `${c018Context.move_direction || ""} move · range ${fmt(c018Context.range_30m_ticks, 1)} ticks`;
  byId("c018-alert").classList.toggle("hidden", !c018Active);

  renderFeedback(payload.feedback);

  const rows = payload.series || [];
  const priceTicks = bondTicks([...rows.map((row) => row.price), ...rows.map((row) => row.fair_value)]);
  Plotly.react("live-fair-value-chart", [
    {
      x: rows.map((row) => row.x),
      y: rows.map((row) => row.price),
      customdata: priceHoverData(rows, "price"),
      name: "Actual Price",
      mode: "lines",
      type: "scattergl",
      line: { color: "#1f4ed8" },
      hovertemplate: "Actual %{customdata} (%{y:.6f})<extra></extra>",
    },
    {
      x: rows.map((row) => row.x),
      y: rows.map((row) => row.fair_value),
      customdata: priceHoverData(rows, "fair_value"),
      name: "Live Fair Value",
      mode: "lines",
      type: "scattergl",
      line: { color: "#d62728", dash: "dash" },
      hovertemplate: "Fair value %{customdata} (%{y:.6f})<extra></extra>",
    },
  ], {
    title: "Live V2.6 Actual vs Fair Value",
    template: "plotly",
    height: 390,
    hovermode: "x unified",
    yaxis: { title: "Price (32nds)", ...priceTicks },
    margin: { l: 65, r: 35, t: 55, b: 70 },
    legend: { orientation: "h" },
  }, { responsive: true });

  Plotly.react("live-residual-chart", [{
    x: rows.map((row) => row.x),
    y: rows.map((row) => row.residual_z),
    name: "Causal residual z",
    mode: "lines",
    type: "scattergl",
    line: { color: "#5f74d8" },
  }], {
    title: "Live Causal Residual Z",
    template: "plotly",
    height: 390,
    hovermode: "x unified",
    shapes: [
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 2, y1: 2, line: { color: "#b00020", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: -2, y1: -2, line: { color: "#b00020", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 1.5, y1: 1.5, line: { color: "#d89614", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: -1.5, y1: -1.5, line: { color: "#d89614", dash: "dot" } },
      { type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: "#777", dash: "dot" } },
    ],
    margin: { l: 65, r: 35, t: 55, b: 70 },
  }, { responsive: true });
}

function renderAlertLog(payload) {
  const stats = payload.stats || {};
  byId("alert-log-stats").textContent =
    `Issued WATCH ${stats.watch_issued || 0}, DISLOCATION ${stats.dislocation_issued || 0} | ` +
    `resolved ${stats.resolved || 0}, expired ${stats.expired || 0}, finalized ${stats.finalized || 0} | ` +
    `30m hit ${fmtPct(stats.directional_hit_rate_30m)}, avg pnl ${fmt(stats.avg_dir_pnl_30m, 4)}`;
  renderTable(byId("alert-log-table"), (payload.rows || []).slice().reverse(), [
    { key: "opened_ts", label: "opened" },
    { key: "state", label: "state" },
    { key: "direction", label: "direction" },
    { key: "open_residual_z", label: "open z", format: (value) => fmt(value, 2) },
    { key: "reversion_status", label: "reversion" },
    { key: "minutes_to_close", label: "min close", format: (value) => fmt(value, 1) },
    { key: "dir_pnl_30m", label: "30m pnl", format: (value) => fmt(value, 4) },
    { key: "status", label: "status" },
  ]);
}

async function refreshAlertLog() {
  try {
    const scope = ALERT_SCOPE?.value || "today";
    const response = await fetch(`/api/alert_log?scope=${encodeURIComponent(scope)}`);
    const payload = await response.json();
    if (response.ok && payload.success) renderAlertLog(payload);
  } catch (_error) {
    // Keep chart polling independent from table rendering.
  }
}

async function refreshLive() {
  try {
    const response = await fetch("/api/live");
    const payload = await response.json();
    if (payload.feedback) renderFeedback(payload.feedback);
    if (response.status === 202) {
      LIVE_MESSAGE.textContent = payload.error || "Warming up live signal...";
      const source = payload.feed?.source === "databento_backup" ? "Databento backup" : "AccessAPI";
      LIVE_FEED_STATUS.textContent = payload.feed?.connected ? `${source} connected` : `${source} waiting`;
      return;
    }
    if (!response.ok || !payload.success) return;
    renderLive(payload);
    refreshAlertLog();
  } catch (error) {
    LIVE_MESSAGE.textContent = `Live signal error: ${error.message}`;
  }
}

async function refreshResearch() {
  setStatus("Loading research view...", "loading");
  try {
    const url =
      `/api/research?model=${encodeURIComponent(MODEL_SELECT.value)}` +
      `&date=${encodeURIComponent(DATE_SELECT.value)}`;
    const response = await fetch(url);
    const payload = await response.json();
    if (!response.ok || !payload.success) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    renderResearch(payload);
    setStatus("Ready.", "");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

async function init() {
  const response = await fetch("/api/config");
  CONFIG = await response.json();
  populateSelect(MODEL_SELECT, CONFIG.models, CONFIG.default_model);
  populateSelect(DATE_SELECT, CONFIG.dates, CONFIG.default_date);
  EXPLAINER.textContent = CONFIG.explainer || "";
  renderMetrics(CONFIG.metrics);
  renderTable(byId("holdout-table"), CONFIG.holdout_rules || [], [
    { key: "threshold", label: "z" },
    { key: "horizon", label: "horizon" },
    { key: "condition", label: "condition" },
    { key: "n", label: "n" },
    { key: "hit_rate", label: "hit %", format: fmtPct },
    { key: "avg_directional_pnl_points", label: "avg pts", format: (value) => fmt(value, 4) },
  ]);
  renderTable(byId("conditional-table"), CONFIG.conditional_rules || [], [
    { key: "condition", label: "condition" },
    { key: "horizon", label: "horizon" },
    { key: "n", label: "n" },
    { key: "mean_reversion_hit_rate", label: "hit %", format: fmtPct },
    { key: "mean_directional_pnl_points", label: "avg pts", format: (value) => fmt(value, 4) },
  ]);
  await refreshResearch();
  await refreshLive();
  window.setInterval(refreshLive, 3000);
}

REFRESH_BTN.addEventListener("click", refreshResearch);
MODEL_SELECT.addEventListener("change", refreshResearch);
DATE_SELECT.addEventListener("change", refreshResearch);
ALERT_SCOPE.addEventListener("change", refreshAlertLog);

byId("feedback-table").addEventListener("click", (event) => {
  const button = event.target.closest(".grade-btn");
  if (!button) return;
  const episode = (LATEST_FEEDBACK?.recent_episodes || []).find(
    (row) => row.episode_id === button.dataset.episodeId
      && row.alert_kind === button.dataset.alertKind,
  );
  openGradeDialog(episode);
});

GRADE_DIALOG.querySelectorAll("[data-correct]").forEach((button) => {
  button.addEventListener("click", () => {
    const value = button.dataset.correct;
    submitFeedback(value === "null" ? null : value === "true");
  });
});

GRADE_DIALOG.addEventListener("cancel", (event) => {
  event.preventDefault();
  submitFeedback(null);
});

init();
