"use strict";

const REFRESH_MS = 20000;
let CONFIG = null;
let LAST_PAYLOAD = null;

function el(id) {
  return document.getElementById(id);
}

function setStatus(text, isError) {
  const node = el("status");
  node.textContent = text;
  node.classList.toggle("error", Boolean(isError));
}

function fmt(value, digits) {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return Number(value).toFixed(digits === undefined ? 2 : digits);
}

function fmtPrice(value) {
  return value === null || value === undefined ? "n/a" : Number(value).toFixed(5);
}

function fmtTicks(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  const num = Number(value);
  return `${num >= 0 ? "+" : ""}${num.toFixed(2)}t`;
}

function signClass(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  return Number(value) > 0 ? "pos" : Number(value) < 0 ? "neg" : "";
}

function etTime(iso) {
  if (!iso) return "n/a";
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour12: false,
    });
  } catch (err) {
    return String(iso);
  }
}

// ----------------------------------------------------------------- rendering

function ticketCell(label, value, cls) {
  return `<div class="${cls || ""}"><span>${label}</span><strong>${value}</strong></div>`;
}

function renderBook(book) {
  const live = book.live || {};
  const stats = book.stats || {};
  const state = String(live.state || "FLAT");
  const sideCls = live.side === "LONG" ? "side-long" : live.side === "SHORT" ? "side-short" : "";

  let ticket = "";
  ticket += ticketCell("SIDE", live.side || "&mdash;", sideCls);
  ticket += ticketCell("ENTRY", fmtPrice(live.entry_price), "");
  ticket += ticketCell("STOP", fmtPrice(live.stop_price), "stop");
  ticket += ticketCell("TARGET", fmtPrice(live.target_price), "target");
  if (state === "IN_TRADE") {
    ticket += ticketCell("TIME STOP", etTime(live.deadline_ts), "");
    ticket += ticketCell("UNREALIZED", fmtTicks(live.unrealized_ticks), "");
    ticket += ticketCell("MAE", fmtTicks(live.mae_ticks), "");
    ticket += ticketCell("MFE", fmtTicks(live.mfe_ticks), "");
  }

  const exits = stats.exit_mix || {};
  const exitText = Object.keys(exits).length
    ? Object.entries(exits).map(([k, v]) => `${k} ${v}`).join(" / ")
    : "none yet";
  const netCls = signClass(stats.mean_net_ticks);
  const totalCls = signClass(stats.total_net_ticks);

  const statsHtml = stats.n_trades
    ? `Closed <b>${stats.n_trades}</b> over <b>${stats.n_dates}</b> sessions &middot;
       mean net <b class="${netCls}">${fmtTicks(stats.mean_net_ticks)}</b>
       (gross ${fmtTicks(stats.mean_gross_ticks)} less ${fmt(stats.cost_ticks, 2)}t cost)<br>
       Total net <b class="${totalCls}">${fmtTicks(stats.total_net_ticks)}</b> &middot;
       win rate <b>${stats.hit_rate === null ? "n/a" : (stats.hit_rate * 100).toFixed(1) + "%"}</b> &middot;
       mean MAE <b>${fmtTicks(stats.mean_mae_ticks)}</b> &middot;
       median hold <b>${fmt(stats.median_minutes_held, 0)}m</b><br>
       Exits: ${exitText}`
    : "No closed paper trades yet.";

  return `
    <div class="book">
      <div class="book-header">
        <h3>${book.name}</h3>
        <span class="state-badge ${state.toLowerCase()}">${state.replace("_", " ")}</span>
      </div>
      <p class="spec-text">${live.spec_text || ""}</p>
      <div class="ticket">${ticket}</div>
      <div class="instruction">${live.instruction || ""}</div>
      <div class="book-stats">${statsHtml}</div>
    </div>`;
}

function renderBooks(paper) {
  const books = (paper && paper.books) || [];
  el("books").innerHTML = books.map(renderBook).join("");
  const select = el("trade-book");
  if (select && !select.options.length && books.length) {
    books.forEach((book) => {
      const option = document.createElement("option");
      option.value = book.name;
      option.textContent = book.name;
      select.appendChild(option);
    });
    loadTradeLog();
  }
}

function renderLiveBanner(payload) {
  const banner = el("live-banner");
  const latest = payload.latest || {};
  const z = latest.residual_z;
  const absZ = z === null || z === undefined ? 0 : Math.abs(Number(z));
  const state = absZ >= 2.0 ? "DISLOCATION" : absZ >= 1.5 ? "WATCH" : "NONE";
  banner.className = `live-banner ${state === "NONE" ? "none" : state.toLowerCase()}`;
  el("live-state").textContent = state === "NONE" ? "NO DISLOCATION" : state;
  const residual = Number(latest.residual || 0);
  el("live-message").textContent =
    state === "NONE"
      ? "Model is monitoring the causal residual z-score. Books stay flat until a signal qualifies."
      : `Residual z=${fmt(z, 2)}; price is ${residual > 0 ? "rich" : "cheap"} vs V2 fair value.`;
  el("live-price").textContent = `Price: ${fmtPrice(latest.price)}`;
  const feed = payload.feed || {};
  el("live-feed-status").textContent = `feed ${feed.state || "unknown"} | rows ${feed.rows || 0}`;
}

function renderFairValueChart(series) {
  if (!series || !series.length) return;
  const x = series.map((row) => row.x);
  Plotly.react(
    "live-fair-value-chart",
    [
      { x, y: series.map((r) => r.price), name: "Price", type: "scatter", mode: "lines" },
      { x, y: series.map((r) => r.fair_value), name: "Fair value", type: "scatter", mode: "lines" },
    ],
    {
      margin: { l: 55, r: 15, t: 40, b: 45 },
      title: { text: "Live price vs V2 fair value", font: { size: 14 } },
      legend: { orientation: "h", y: -0.18 },
    },
    { displayModeBar: false, responsive: true }
  );
}

function renderEquityChart(paper) {
  const books = (paper && paper.books) || [];
  const traces = books
    .filter((book) => (book.equity || []).length)
    .map((book) => ({
      x: book.equity.map((p) => p.x),
      y: book.equity.map((p) => p.cumulative_net_ticks),
      name: book.name,
      type: "scatter",
      mode: "lines+markers",
    }));
  if (!traces.length) {
    Plotly.react(
      "equity-chart",
      [],
      {
        margin: { l: 55, r: 15, t: 40, b: 45 },
        title: { text: "Cumulative paper net ticks (after cost)", font: { size: 14 } },
        annotations: [
          { text: "No closed paper trades yet", showarrow: false, x: 0.5, y: 0.5, xref: "paper", yref: "paper" },
        ],
      },
      { displayModeBar: false, responsive: true }
    );
    return;
  }
  Plotly.react(
    "equity-chart",
    traces,
    {
      margin: { l: 55, r: 15, t: 40, b: 45 },
      title: { text: "Cumulative paper net ticks (after cost)", font: { size: 14 } },
      legend: { orientation: "h", y: -0.18 },
      shapes: [
        {
          type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0,
          line: { color: "#9aa6b4", width: 1, dash: "dot" },
        },
      ],
    },
    { displayModeBar: false, responsive: true }
  );
}

const LOG_COLUMNS = [
  ["entry_ts", "Entry", (v) => etTime(v)],
  ["side", "Side", (v) => v || "n/a"],
  ["signal_z", "Signal z", (v) => fmt(v, 2)],
  ["entry_price", "Entry px", (v) => fmtPrice(v)],
  ["exit_price", "Exit px", (v) => fmtPrice(v)],
  ["exit_reason", "Exit", (v) => v || "n/a"],
  ["minutes_held", "Held", (v) => `${fmt(v, 0)}m`],
  ["mae_ticks", "MAE", (v) => fmtTicks(v)],
  ["gross_ticks", "Gross", (v) => fmtTicks(v)],
  ["net_ticks", "Net", (v) => fmtTicks(v)],
];

function renderTradeTable(rows) {
  if (!rows || !rows.length) {
    el("trade-log-table").innerHTML = "<p class='small-note'>No closed paper trades for this scope.</p>";
    return;
  }
  const head = LOG_COLUMNS.map(([, label]) => `<th>${label}</th>`).join("");
  const body = rows
    .slice()
    .reverse()
    .map((row) => {
      const cells = LOG_COLUMNS.map(([key, , format]) => {
        const cls = key === "net_ticks" || key === "gross_ticks" ? signClass(row[key]) : "";
        return `<td class="${cls}">${format(row[key])}</td>`;
      }).join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  el("trade-log-table").innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

async function loadTradeLog() {
  const book = el("trade-book").value;
  const scope = el("trade-scope").value;
  if (!book) return;
  try {
    const res = await fetch(`/api/paper?book=${encodeURIComponent(book)}&scope=${scope}`);
    const data = await res.json();
    const rows = data.log_rows || [];
    renderTradeTable(rows);
    const match = (data.books || []).find((b) => b.name === book);
    const stats = (match && match.stats) || {};
    el("trade-log-stats").textContent = stats.n_trades
      ? `${book}: ${rows.length} shown | lifetime ${stats.n_trades} trades, mean net ${fmtTicks(stats.mean_net_ticks)} after ${fmt(stats.cost_ticks, 2)}t cost.`
      : `${book}: no closed paper trades yet.`;
  } catch (err) {
    el("trade-log-stats").textContent = `Trade log unavailable: ${err}`;
  }
}

// -------------------------------------------------------------------- wiring

async function loadConfig() {
  const res = await fetch("/api/config");
  CONFIG = await res.json();
  el("strategy-status").textContent = CONFIG.strategy_status;
  const ctx = CONFIG.research_context || {};
  el("research-note").textContent = ctx.note || "";
  el("evidence-text").innerHTML =
    `Offline gate verdict for this spec family: <b>${ctx.verdict}</b>. ` +
    `${ctx.cells_passed} of ${ctx.cells_scored} scored cells passed. ` +
    `Specs positive after cost in both explore and holdout: <b>${ctx.specs_positive_after_cost_both_samples}</b>. ` +
    `Explore/holdout correlation ${ctx.explore_holdout_corr}. ` +
    `Live V2 alerts average ${ctx.live_v2_gross_ticks_30m} gross ticks at 30m against a ~0.5t cost.`;
}

async function refresh() {
  try {
    const res = await fetch("/api/live_signal");
    const payload = await res.json();
    LAST_PAYLOAD = payload;
    if (!payload.success) {
      setStatus(payload.error || "Waiting for live data...", false);
      renderBooks(payload.paper);
      return;
    }
    renderLiveBanner(payload);
    renderBooks(payload.paper);
    renderFairValueChart(payload.series);
    renderEquityChart(payload.paper);
    setStatus(`Updated ${new Date().toLocaleTimeString()} | port ${CONFIG?.isolation?.port ?? ""} | paper only`, false);
  } catch (err) {
    setStatus(`Refresh failed: ${err}`, true);
  }
}

async function init() {
  try {
    await loadConfig();
  } catch (err) {
    setStatus(`Config failed: ${err}`, true);
  }
  el("trade-book").addEventListener("change", loadTradeLog);
  el("trade-scope").addEventListener("change", loadTradeLog);
  await refresh();
  await loadTradeLog();
  setInterval(refresh, REFRESH_MS);
  setInterval(loadTradeLog, REFRESH_MS * 3);
}

document.addEventListener("DOMContentLoaded", init);
