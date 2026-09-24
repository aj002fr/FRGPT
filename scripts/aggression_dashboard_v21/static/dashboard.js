const $ = (id) => document.getElementById(id);

function fmt(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "—";
}

function fmtTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function pnlText(outcome) {
  if (!outcome || !outcome.complete) return "Pending";
  const value = Number(outcome.after_cost_ticks);
  return `${value >= 0 ? "+" : ""}${fmt(value)} ticks`;
}

function setHealth(payload) {
  const feed = payload.feed || {};
  const db = payload.events_db || {};
  $("feed-status").textContent = feed.connected ? `LIVE · ${fmt(feed.last_tick_age_sec, 0)}s` : "DISCONNECTED";
  $("feed-status").className = feed.connected ? "good" : "bad";
  $("events-status").textContent = String(db.status || "unknown").toUpperCase();
  $("events-status").className = db.status === "ok" ? "good" : "bad";
}

function renderActive(active) {
  if (!active) {
    $("event-name").textContent = "No active release";
    $("event-phase").textContent = "IDLE";
    ["event-time", "event-flow", "event-progress", "event-lean", "event-lock-price", "event-pre-move"].forEach(id => $(id).textContent = "—");
    $("quality-flags").innerHTML = "";
    renderOutcomes({});
    return;
  }
  $("event-name").textContent = active.event_name || "Scheduled US release";
  $("event-phase").textContent = active.phase || "UNKNOWN";
  $("event-time").textContent = fmtTime(active.event_ts_et || active.event_ts_utc);
  $("event-flow").textContent = active.first5_agg_sum == null ? "Collecting" : fmt(active.first5_agg_sum, 0);
  $("event-progress").textContent = `${active.first5_buckets || 0} / 5 buckets`;
  $("event-lean").textContent = active.lean || "Pending";
  $("event-lean").className = active.lean === "LONG" ? "good" : active.lean === "SHORT" ? "bad" : "";
  $("event-lock-price").textContent = fmt(active.lock_price, 5);
  const preMove = Number(active.release_to_lock_ticks);
  $("event-pre-move").textContent = Number.isFinite(preMove) ? `${preMove >= 0 ? "+" : ""}${fmt(preMove)} ticks` : "—";
  const flags = Object.entries(active.quality || {}).filter(([, value]) => value);
  $("quality-flags").innerHTML = flags.map(([name]) => `<span class="flag">${name.replaceAll("_", " ")}</span>`).join("");
  renderOutcomes(active.outcomes || {});
}

function renderOutcomes(outcomes) {
  $("outcomes").innerHTML = [5, 15, 30].map(horizon => {
    const outcome = outcomes[`${horizon}m`];
    const value = outcome?.after_cost_ticks;
    const cls = Number(value) > 0 ? "good" : Number(value) < 0 ? "bad" : "";
    return `<div><span>+${horizon}m after lock</span><strong class="${cls}">${pnlText(outcome)}</strong><small>Adverse ${fmt(outcome?.adverse_ticks)} ticks</small></div>`;
  }).join("");
}

function renderLists(payload) {
  $("upcoming-list").innerHTML = (payload.upcoming || []).slice(0, 12).map(row =>
    `<div class="list-item"><div>${row.event_name || "US release"}<br><small>${row.component_count || 0} components</small></div><strong>${fmtTime(row.event_ts_et || row.event_ts_utc)}</strong></div>`
  ).join("") || "<small>No releases in the next 48 hours.</small>";
  $("recent-list").innerHTML = (payload.recent || []).slice(0, 12).map(row =>
    `<div class="list-item"><div>${row.event_name || "US release"}<br><small>${row.phase || ""}</small></div><strong>${row.lean || "—"}</strong></div>`
  ).join("") || "<small>No paper episodes yet.</small>";
}

function renderMagnitude(context) {
  const value = context || {};
  const validated = value.validated_context || {};
  const elevated = value.state === "ELEVATED";
  $("magnitude-state").textContent = value.ready ? value.state : "WARMING";
  $("magnitude-state").className = `badge ${elevated ? "magnitude-elevated" : ""}`;
  $("magnitude-vol").textContent = value.prior10_realized_vol == null ? "—" : fmt(value.prior10_realized_vol, 5);
  $("magnitude-range").textContent = value.prior10_range_ticks == null ? "—" : `${fmt(value.prior10_range_ticks, 1)} ticks`;
  const moveBase = Number(validated.big_move_rate_baseline);
  const moveElevated = Number(validated.big_move_rate_elevated);
  const resolutionBase = Number(validated.resolution_rate_baseline);
  const resolutionElevated = Number(validated.resolution_rate_elevated);
  $("magnitude-move-rate").textContent = Number.isFinite(moveElevated)
    ? `${(moveElevated * 100).toFixed(1)}% vs ${(moveBase * 100).toFixed(1)}%`
    : "—";
  $("magnitude-resolution-rate").textContent = Number.isFinite(resolutionElevated)
    ? `${(resolutionElevated * 100).toFixed(1)}% vs ${(resolutionBase * 100).toFixed(1)}%`
    : "—";
  $("magnitude-reasons").innerHTML = (value.reasons || [])
    .map(reason => `<span class="flag magnitude-flag">${reason}</span>`)
    .join("");
}

async function refreshEventFlow() {
  try {
    const response = await fetch("/api/event_flow", { cache: "no-store" });
    const payload = await response.json();
    setHealth(payload);
    renderActive(payload.active);
    renderLists(payload);
  } catch (error) {
    $("feed-status").textContent = "ERROR";
    $("feed-status").className = "bad";
  }
}

async function refreshLive() {
  try {
    const response = await fetch("/api/live_signal", { cache: "no-store" });
    const payload = await response.json();
    if (!payload.success) return;
    $("residual-state").textContent = payload.state || "NONE";
    $("residual-message").textContent = payload.message || "";
    renderMagnitude(payload.magnitude_context);
    const rows = payload.series || [];
    if (window.Plotly && rows.length) {
      Plotly.react("fair-value-chart", [
        { x: rows.map(r => r.x), y: rows.map(r => r.price), name: "Price", mode: "lines" },
        { x: rows.map(r => r.x), y: rows.map(r => r.fair_value), name: "Fair value", mode: "lines" }
      ], {
        margin: { l: 50, r: 20, t: 15, b: 35 },
        paper_bgcolor: "transparent",
        plot_bgcolor: "transparent",
        font: { color: "#8ca1b2" },
        xaxis: { gridcolor: "#263b4b" },
        yaxis: { gridcolor: "#263b4b" },
        legend: { orientation: "h" }
      }, { responsive: true, displaylogo: false });
    }
  } catch (_) {}
}

async function refreshLog() {
  const scope = $("log-scope").value;
  const response = await fetch(`/api/event_log?scope=${scope}`, { cache: "no-store" });
  const payload = await response.json();
  $("log-body").innerHTML = (payload.rows || []).slice().reverse().map(row =>
    `<tr><td>${row.event_name || ""}<br><small>${fmtTime(row.event_ts_utc)}</small></td><td>${row.lean || "—"}</td><td>${fmt(row.lock_price, 5)}</td><td>${fmt(row.after_cost_ticks_5m)}</td><td>${fmt(row.after_cost_ticks_15m)}</td><td>${fmt(row.after_cost_ticks_30m)}</td></tr>`
  ).join("");
}

$("log-scope").addEventListener("change", refreshLog);
Promise.all([refreshEventFlow(), refreshLive(), refreshLog()]);
setInterval(refreshEventFlow, 3000);
setInterval(refreshLive, 3000);
setInterval(refreshLog, 15000);
