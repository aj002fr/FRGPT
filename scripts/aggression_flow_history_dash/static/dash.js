const LIVE_POLL_MS = 15000;
const state = { rows: [], allRows: [], selected: null, diagnostic: null, boot: null };
let liveTimer = null;

const TAG_COLORS = {
  "AUCTION/SUPPLY": "#d48b11",
  AUCTION: "#d48b11",
  "MONTH-END": "#7b4ea3",
  "TIER-1": "#c23b22",
  NFP: "#1f77b4",
  CPI: "#17becf",
  FOMC: "#e377c2",
  PCE: "#2ca02c",
  GDP: "#8c564b",
};

const DIST_TAGS = {
  all: { label: "All days", color: "#1a1a1a" },
  auction: { label: "Auction/supply", color: "#d48b11" },
  month_end: { label: "Month-end", color: "#7b4ea3" },
  tier1: { label: "Tier-1", color: "#c23b22" },
  Mon: { label: "Mon", color: "#4c78a8" },
  Tue: { label: "Tue", color: "#54a24b" },
  Wed: { label: "Wed", color: "#eeca3b" },
  Thu: { label: "Thu", color: "#b279a2" },
  Fri: { label: "Fri", color: "#ff9da6" },
};

function $(id) {
  return document.getElementById(id);
}

function fmt(value, digits) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function pct(value) {
  if (value === null || value === undefined) return "—";
  return `${(100 * value).toFixed(0)}%`;
}

function units() {
  return $("units").value === "dv01" ? "flow" : "contracts";
}

function buyKey() {
  return units() === "flow" ? "buy_flow" : "buy_contracts";
}

function sellKey() {
  return units() === "flow" ? "sell_flow" : "sell_contracts";
}

function netRisk(row) {
  return (row.buy_flow || 0) - (row.sell_flow || 0);
}

function pointColor(row) {
  const tags = row.tags || [];
  for (const tag of ["AUCTION/SUPPLY", "AUCTION", "TIER-1", "NFP", "CPI", "FOMC", "PCE", "GDP", "MONTH-END"]) {
    if (tags.includes(tag)) return TAG_COLORS[tag];
  }
  return "#8a9199";
}

async function getJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!payload.success) throw new Error(payload.error || "Request failed");
  return payload;
}

function queryString(extra) {
  const params = new URLSearchParams({ symbol: $("symbol").value });
  if ($("start").value) params.set("start", $("start").value);
  if ($("end").value) params.set("end", $("end").value);
  Object.entries(extra || {}).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}

function slopeNote(entry) {
  if (!entry || entry.slope_bps_per_mm === null || entry.slope_bps_per_mm === undefined) {
    return `n=${entry && entry.n ? entry.n : 0}`;
  }
  return `${Number(entry.slope_bps_per_mm).toFixed(2)} bps / $1mm  n=${entry.n}`;
}

function interceptNote(entry, extra) {
  const bits = [];
  if (entry && entry.intercept_bps !== null && entry.intercept_bps !== undefined) {
    bits.push(`intercept ${Number(entry.intercept_bps).toFixed(2)}`);
  }
  if (extra) bits.push(extra);
  return bits.join(" · ");
}

function renderCards(diag) {
  const family = $("family").value;
  const cohort = (diag.cohorts && (diag.cohorts[family] || diag.cohorts.all)) || {};
  const bid = cohort.bid_gt_ask || {};
  const ask = cohort.ask_gt_bid || {};
  const slopes = diag.bps_per_mm_dv01 || {};
  $("cards").innerHTML = [
    card("Days", cohort.n, family === "all" ? "in filter" : family.replace("_", "-")),
    card("All days", slopeNote(slopes.all), interceptNote(slopes.all, "OLS bps per $1mm DV01")),
    card("Auction / supply", slopeNote(slopes.auction), interceptNote(slopes.auction, "coupon 2/3/5/7/10/20/30")),
    card("Month-end", slopeNote(slopes.month_end), interceptNote(slopes.month_end, "last 3 business days")),
    card("Tier-1", slopeNote(slopes.tier1), interceptNote(slopes.tier1, "NFP / CPI / FOMC / PCE / GDP")),
    card("Bid > ask, rallied", pct(bid.rally_share), `${bid.rally_n || 0}/${bid.n || 0} days`),
    card("Ask > bid, rallied", pct(ask.rally_share), `${ask.rally_n || 0}/${ask.n || 0} days`),
  ].join("");
}

function card(title, value, note) {
  return `<article class="card"><h3>${title}</h3><strong>${value ?? "—"}</strong><span>${note}</span></article>`;
}

function renderFeed(payload) {
  const feed = (payload && payload.feed) || {};
  const node = $("feed-status");
  const state = feed.state || "NOT_LIVE";
  const session = feed.session_date ? `session ${feed.session_date}` : "no session";
  const last = feed.last_bar_et ? `last 1m ${feed.last_bar_et}` : "no bars";
  node.className = `feed ${state}`;
  node.textContent = `${state.replace("_", " ")} · ${session} · ${last}`;
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  const signed = value < 0 ? "-" : "";
  if (abs >= 1e6) return `${signed}$${(abs / 1e6).toFixed(2)}mm`;
  if (abs >= 1e3) return `${signed}$${(abs / 1e3).toFixed(0)}k`;
  return `${signed}$${abs.toFixed(0)}`;
}

function renderToday(payload) {
  const today = payload.today || {};
  const expected = payload.expected || {};
  const soFar = payload.so_far || {};
  const analog = payload.analog || {};
  $("today-title").textContent = `${today.date || "Today"}  ${today.weekday || ""}`;
  $("today-tags").innerHTML = (today.tags || []).map((tag) => `<span class="tag">${tag}</span>`).join("");
  const considered = today.considered || [];
  $("today-considered").innerHTML = considered.length
    ? `Considering today: ${considered.map((item) => {
        const mark = item.on ? "YES" : "no";
        const detail = item.on && item.detail ? ` (${item.detail})` : "";
        return `<span class="tag ${item.on ? "on" : "off"}">${item.label} ${mark}${detail}</span>`;
      }).join(" ")}`
    : "";
  const analogLabel = (analog.keys || []).join(" ∩ ") || "all";
  $("today-analog").textContent = `${payload.label || ""} · analog ${analogLabel}  n=${analog.n || 0}`;
  $("today-cards").innerHTML = [
    card("Typical bid $DV01", money(expected.buy_flow), "median analog day"),
    card("Typical ask $DV01", money(expected.sell_flow), "median analog day"),
    card("Typical net $DV01", money(expected.net_flow), "bid − ask"),
    card("Expected bps from that net", expected.implied_bps_from_net == null ? "—" : Number(expected.implied_bps_from_net).toFixed(2), `OLS analog ${expected.slope_bps_per_mm == null ? "—" : Number(expected.slope_bps_per_mm).toFixed(2)} bps/$1mm`),
    card("Analog median day bps", expected.median_day_bps == null ? "—" : Number(expected.median_day_bps).toFixed(2), "same analog set, actual closes"),
    card("Flow so far", money(soFar.total_flow), `${pct(soFar.share_of_typical_total)} of typical total`),
    card("Net so far", money(soFar.net_flow), `bid ${money(soFar.buy_flow)} · ask ${money(soFar.sell_flow)}`),
    card("Bps implied so far", soFar.implied_bps_from_net == null ? "—" : Number(soFar.implied_bps_from_net).toFixed(2), soFar.day_bps == null ? "open-to-last not yet" : `open-to-last ${Number(soFar.day_bps).toFixed(2)} bps`),
  ].join("");
}

function selectedDistTags() {
  return Array.from(document.querySelectorAll("#dist-tags input:checked")).map((box) => box.value);
}

function rowMatchesTag(row, tag) {
  if (tag === "all") return true;
  if (DIST_TAGS[tag] && ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(tag)) {
    return row.weekday === tag;
  }
  const flags = {
    auction: "is_auction",
    month_end: "is_month_end",
    tier1: "is_tier1",
  };
  return Boolean(row[flags[tag]]);
}

function ratioPoints(rows) {
  return (rows || []).filter((row) => row.bps_per_mm !== null && row.bps_per_mm !== undefined);
}

function summarize(values) {
  if (!values.length) return { n: 0, mean: null, median: null, p25: null, p75: null };
  const sorted = values.slice().sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const quantile = (q) => {
    const idx = (sorted.length - 1) * q;
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
  };
  return {
    n: sorted.length,
    mean: values.reduce((a, b) => a + b, 0) / values.length,
    median: sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2,
    p25: quantile(0.25),
    p75: quantile(0.75),
  };
}

function renderDist(_rows) {}

function renderTable(rows) {
  const buy = buyKey();
  const sell = sellKey();
  const body = $("days").querySelector("tbody");
  body.innerHTML = rows.map((row) => `
    <tr data-date="${row.date}">
      <td>${row.date}</td>
      <td>${row.weekday || ""}</td>
      <td>${fmt(row[buy], 0)}</td>
      <td>${fmt(row[sell], 0)}</td>
      <td>${fmt(row[buy] - row[sell], 0)}</td>
      <td>${fmt(row.day_ticks, 1)}</td>
      <td>${fmt(row.bps_per_mm, 2)}</td>
      <td>${(row.tags || []).map((tag) => `<span class="tag">${tag}</span>`).join("")}</td>
    </tr>
  `).join("");
  body.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => selectDay(tr.dataset.date));
  });
}

function renderDailyChart(rows) {
  const buy = buyKey();
  const sell = sellKey();
  const x = rows.map((row) => row.date);
  const traces = [
    { x, y: rows.map((row) => row[buy]), name: "Bid aggress", type: "bar", marker: { color: "#2ca02c" } },
    { x, y: rows.map((row) => -Math.abs(row[sell])), name: "Ask aggress", type: "bar", marker: { color: "#d62728" } },
    {
      x,
      y: rows.map((row) => row.day_ticks),
      name: "Day ticks",
      type: "scatter",
      yaxis: "y2",
      line: { color: "#1a1a1a", width: 1.5 },
    },
  ];
  const node = $("daily-chart");
  Plotly.react(node, traces, {
    barmode: "relative",
    margin: { t: 28, r: 48, b: 40, l: 56 },
    legend: { orientation: "h" },
    yaxis: { title: units() === "flow" ? "$DV01" : "Contracts" },
    yaxis2: { title: "Ticks", overlaying: "y", side: "right" },
    hovermode: "x unified",
  }, { responsive: true });
  if (!node.dataset.clickBound) {
    node.on("plotly_click", (event) => {
      const day = event.points && event.points[0] && event.points[0].x;
      if (day) selectDay(day);
    });
    node.dataset.clickBound = "1";
  }
}

function renderScatter(_rows) {}

async function selectDay(day) {
  state.selected = day;
  $("days").querySelectorAll("tr").forEach((tr) => {
    tr.classList.toggle("active", tr.dataset.date === day);
  });
  $("status").textContent = `Intraday ${day}`;
  const payload = await getJson(`/api/intraday?${queryString({ date: day })}`);
  const buy = units() === "flow" ? "buy_flow" : "buy_contracts";
  const sell = units() === "flow" ? "sell_flow" : "sell_contracts";
  const x = payload.rows.map((row) => row.ts_et);
  const shapes = (payload.events || []).map((event) => ({
    type: "line",
    x0: event.ts_et,
    x1: event.ts_et,
    y0: 0,
    y1: 1,
    yref: "paper",
    line: { color: "#6b7c8a", dash: "dot" },
  }));
  Plotly.react("intraday-chart", [
    { x, y: payload.rows.map((row) => row[buy]), name: "Bid aggress", type: "bar", marker: { color: "#2ca02c" } },
    { x, y: payload.rows.map((row) => -Math.abs(row[sell])), name: "Ask aggress", type: "bar", marker: { color: "#d62728" } },
  ], {
    title: `${payload.symbol} ${day}`,
    barmode: "relative",
    margin: { t: 36, r: 16, b: 40, l: 56 },
    legend: { orientation: "h" },
    shapes,
    hovermode: "x unified",
  }, { responsive: true });
}

function rowMatchesFamily(row, family) {
  if (!family || family === "all") return true;
  if (["mon", "tue", "wed", "thu", "fri", "sat", "sun"].includes(family)) {
    return row.weekday === family.slice(0, 1).toUpperCase() + family.slice(1, 3);
  }
  const flags = {
    auction: "is_auction",
    supply: "is_auction",
    month_end: "is_month_end",
    tier1: "is_tier1",
    nfp: "is_nfp",
    cpi: "is_cpi",
    fomc: "is_fomc",
  };
  return Boolean(row[flags[family]]);
}

function applyViewFilters(rows) {
  const start = $("start").value;
  const end = $("end").value;
  const family = $("family").value;
  return (rows || []).filter((row) => {
    if (start && row.date < start) return false;
    if (end && row.date > end) return false;
    return rowMatchesFamily(row, family);
  });
}

function renderLoaded() {
  state.rows = applyViewFilters(state.allRows);
  renderFeed(state.boot || {});
  renderToday(state.boot || {});
  renderCards(state.diagnostic || {});
  renderDist(state.allRows);
  renderScatter(state.allRows);
  renderDailyChart(state.rows);
  renderTable(state.rows);
  $("status").textContent = `${state.rows.length} days`;
}

function stopLive() {
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
}

function startLive() {
  stopLive();
  liveTimer = setInterval(refreshLive, LIVE_POLL_MS);
}

async function refreshLive() {
  if (!state.boot || document.visibilityState === "hidden") return;
  try {
    const live = await getJson(`/api/today?symbol=${encodeURIComponent($("symbol").value)}`);
    state.boot = { ...state.boot, ...live };
    renderFeed(live);
    renderToday(live);
    if (live.row && live.row.date) {
      const idx = state.allRows.findIndex((row) => row.date === live.row.date);
      if (idx >= 0) state.allRows[idx] = live.row;
      else state.allRows.push(live.row);
      state.rows = applyViewFilters(state.allRows);
    }
    if (state.selected && live.today && state.selected === live.today.date) {
      await selectDay(state.selected);
    }
  } catch (_error) {
    /* keep the last so-far cards */
  }
}

async function reload() {
  stopLive();
  $("status").textContent = "Loading…";
  $("status").classList.remove("error");
  try {
    const boot = await getJson(`/api/bootstrap?symbol=${encodeURIComponent($("symbol").value)}`);
    state.boot = boot;
    state.allRows = boot.rows || [];
    state.diagnostic = boot.diagnostic || {};
    renderLoaded();
    if (state.rows.length) {
      await selectDay(state.selected && state.rows.some((row) => row.date === state.selected)
        ? state.selected
        : state.rows[state.rows.length - 1].date);
    }
    startLive();
  } catch (error) {
    $("status").textContent = error.message;
    $("status").classList.add("error");
  }
}

$("reload").addEventListener("click", reload);
$("units").addEventListener("change", () => {
  if (state.rows.length) {
    renderScatter(state.allRows.length ? state.allRows : state.rows);
    renderDailyChart(state.rows);
    renderTable(state.rows);
    if (state.selected) selectDay(state.selected);
  }
});
$("dist-mode").addEventListener("change", () => {
  renderDist(state.allRows.length ? state.allRows : state.rows);
});
document.querySelectorAll("#dist-tags input").forEach((box) => {
  box.addEventListener("change", () => {
    renderDist(state.allRows.length ? state.allRows : state.rows);
  });
});
$("symbol").addEventListener("change", reload);
["start", "end", "family"].forEach((id) => {
  $(id).addEventListener("change", () => {
    if (state.allRows.length) renderLoaded();
    else reload();
  });
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshLive();
});

reload();
