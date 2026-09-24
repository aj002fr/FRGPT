const LIVE_POLL_MS = 15000;
const BPS_PER_POINT = 8;
const WINDOW_NAMES = ["pre_2y", "2y_to_5y", "5y_to_7y", "post_7y"];
const WINDOW_LABELS = {
  pre_2y: "Pre-2Y",
  "2y_to_5y": "After 2Y",
  "5y_to_7y": "After 2Y+5Y / before 7Y",
  post_7y: "Post-7Y",
};

const COMPARE_SLICES = [
  ["2Y", "from_8", "8:00 to auction"],
  ["2Y", "into", "Into auction"],
  ["2Y", "after", "Auction to 17:00"],
  ["5Y", "into", "Into auction"],
  ["5Y", "after", "Auction to 17:00"],
  ["7Y", "into", "Into auction"],
  ["7Y", "after", "Auction to 17:00"],
];

const DISTRO_GROUPS = [
  {
    dateKey: "2y_date",
    color: "#14b8a6",
    marker: "#5eead4",
    categories: [
      ["from_8", "8:00 to auction", 8, 13],
      ["into", "10:00 to auction", 10, 13],
      ["after", "Auction to 17:00", 13, 17],
    ],
  },
  {
    dateKey: "5y_date",
    color: "#38bdf8",
    marker: "#7dd3fc",
    categories: [
      ["5y-from_8", "8:00 to auction", 8, 13],
      ["5y-into", "10:00 to auction", 10, 13],
      ["5y-after", "Auction to 17:00", 13, 17],
    ],
  },
];

const state = { weeks: [], windows: {}, current: null, boot: null, compare: {} };
let liveTimer = null;

function $(id) {
  return document.getElementById(id);
}

async function getJson(url) {
  const response = await fetch(url);
  const payload = await response.json();
  if (!payload.success) throw new Error(payload.error || "Request failed");
  return payload;
}

function monthEndParam() {
  return $("type-filter").value || "all";
}

function matchesTypeFilter(week, filterValue) {
  const info = (week && week.week_info) || {};
  if (filterValue === "month_end") return !!info.is_month_end;
  if (filterValue === "non_month_end") return !info.is_month_end;
  return true;
}

function visibleWeeks() {
  const filter = monthEndParam();
  return (state.weeks || []).filter((week) => matchesTypeFilter(week, filter));
}

function pointsToBps(points) {
  return points * BPS_PER_POINT;
}

function auctionClockHour(week, dateKey, hour) {
  const info = week.week_info || {};
  const dateStr = info[dateKey];
  if (!dateStr || !info.week_start) return null;
  const weekStart = new Date(`${info.week_start}T00:00:00`);
  const aucDate = new Date(`${String(dateStr).split(" ")[0]}T00:00:00`);
  if (Number.isNaN(weekStart.getTime()) || Number.isNaN(aucDate.getTime())) return null;
  const dayOffset = Math.round((aucDate - weekStart) / (24 * 60 * 60 * 1000));
  return 6 + dayOffset * 24 + hour;
}

function renderFeed(feed) {
  const node = $("feed-status");
  const stateName = (feed && feed.state) || "NOT_LIVE";
  const session = feed && feed.session_date ? `session ${feed.session_date}` : "no session";
  const last = feed && feed.last_bar_et ? `last 1m ${feed.last_bar_et}` : "no bars";
  const provisional = Boolean(feed && feed.provisional);
  node.className = `feed ${stateName}${provisional ? " provisional" : ""}`;
  node.textContent = `${provisional ? "PROVISIONAL · " : ""}${stateName.replace("_", " ")} · ${session} · ${last}`;
  node.title = provisional ? "Latest minutes come from the Databento backup during an AccessAPI outage; they are replaced when AccessAPI data arrives." : "";
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}mm`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}k`;
  return `${sign}$${abs.toFixed(0)}`;
}

function renderHeader() {
  const weeks = visibleWeeks();
  $("total-weeks").textContent = String(weeks.length);
  $("month-end-weeks").textContent = String(weeks.filter((week) => week.week_info && week.week_info.is_month_end).length);
  const years = weeks.map((week) => week.week_info && week.week_info.year).filter(Boolean);
  $("data-range").textContent = years.length ? `${Math.min(...years)}–${Math.max(...years)}` : "—";
  const current = state.current;
  if (current && current.week_info) {
    $("current-week").textContent = `${current.week_key} · ${WINDOW_LABELS[current.current_window] || current.current_window || "—"}`;
  } else {
    $("current-week").textContent = "not in a 2/5/7 week";
  }
}

function windowSamples(name) {
  const xs = [];
  const ys = [];
  visibleWeeks().forEach((week) => {
    const row = week.flow && week.flow.windows && week.flow.windows[name];
    if (!row || (row.n_bars || 0) <= 0 || row.window_bps == null) return;
    xs.push(Number(row.net_flow || 0) / 1e6);
    ys.push(Number(row.window_bps));
  });
  return { xs, ys };
}

function renderHeatmaps() {
  WINDOW_NAMES.forEach((name) => renderHeatmap(name));
}

function renderHeatmap(name) {
  const { xs, ys } = windowSamples(name);
  $(`heat-badge-${name}`).textContent = `${xs.length} weeks`;
  const fit = (state.windows || {})[name] || {};
  const beta = fit.slope_bps_per_mm == null ? "—" : Number(fit.slope_bps_per_mm).toFixed(2);
  $(`heat-beta-${name}`).textContent = `beta ${beta} bps / $1mm`;
  const traces = [];
  if (xs.length) {
    const bins = Math.min(16, Math.max(6, Math.ceil(Math.sqrt(xs.length))));
    traces.push({
      x: xs,
      y: ys,
      type: "histogram2d",
      colorscale: [[0, "#0f172a"], [0.35, "#134e4a"], [0.7, "#14b8a6"], [1, "#ccfbf1"]],
      nbinsx: bins,
      nbinsy: bins,
      hovertemplate: "%{x:.2f} mm<br>%{y:.2f} bps<br>%{z} weeks<extra></extra>",
      colorbar: { title: "n", thickness: 10, len: 0.65, outlinewidth: 0 },
    });
  }
  if (fit.slope_bps_per_mm != null && fit.intercept_bps != null && xs.length >= 2) {
    const xmin = Math.min(...xs);
    const xmax = Math.max(...xs);
    const pad = (xmax - xmin) * 0.08 || 0.15;
    const x0 = xmin - pad;
    const x1 = xmax + pad;
    traces.push({
      x: [x0, x1],
      y: [
        Number(fit.intercept_bps) + Number(fit.slope_bps_per_mm) * x0,
        Number(fit.intercept_bps) + Number(fit.slope_bps_per_mm) * x1,
      ],
      type: "scatter",
      mode: "lines",
      line: { color: "#f9fafb", width: 2 },
      hovertemplate: "OLS %{y:.2f} bps<extra></extra>",
    });
  }
  const curRow = state.current && state.current.flow && state.current.flow.windows
    && state.current.flow.windows[name];
  if (curRow && (curRow.n_bars || 0) > 0 && curRow.window_bps != null) {
    traces.push({
      x: [Number(curRow.net_flow || 0) / 1e6],
      y: [Number(curRow.window_bps)],
      type: "scatter",
      mode: "markers",
      marker: { size: 11, color: "#fbbf24", line: { color: "#111827", width: 1 } },
      hovertemplate: "this week<br>%{x:.2f} mm<br>%{y:.2f} bps<extra></extra>",
    });
  }
  Plotly.react(`heat-${name}`, traces, {
    paper_bgcolor: "#111827",
    plot_bgcolor: "#111827",
    font: { color: "#f9fafb", size: 11 },
    xaxis: { title: "window net ($mm DV01)", gridcolor: "#374151", zeroline: true, zerolinecolor: "#6b7c8a" },
    yaxis: { title: "window bps", gridcolor: "#374151", zeroline: true, zerolinecolor: "#6b7c8a" },
    margin: { t: 8, r: 46, b: 46, l: 48 },
    showlegend: false,
  }, { responsive: true });
}

function liveQuery() {
  const params = new URLSearchParams();
  params.set("month_end", monthEndParam());
  if ($("drop-tier1") && $("drop-tier1").checked) params.set("drop_tier1", "1");
  if ($("drop-month-end") && $("drop-month-end").checked) params.set("drop_month_end", "1");
  return params.toString();
}

function compareCell(block, tenor, sliceName) {
  const row = (((block || {}).rows || {})[tenor] || {})[sliceName] || {};
  return {
    supply: row.supply || {},
    control: row.control || {},
    slope: row.slope_bps_per_mm,
    intercept: row.intercept_bps,
    implied: row.flow_implied_bps,
  };
}

function impliedBps(slope, intercept, net) {
  if (slope == null || intercept == null || net == null || Number.isNaN(Number(net))) return null;
  return Number(intercept) + Number(slope) * Number(net) / 1e6;
}

function signedMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const text = money(Number(value));
  return Number(value) > 0 ? `+${text}` : text;
}

function signedBps(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  const body = `${Math.abs(n).toFixed(2)} bps`;
  if (n > 0) return `+${body}`;
  if (n < 0) return `-${body}`;
  return body;
}

function tone(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value)) || Number(value) === 0) return "";
  return Number(value) > 0 ? "up" : "down";
}

function gap(auction, normal) {
  if (auction === null || auction === undefined || normal === null || normal === undefined) return null;
  if (Number.isNaN(Number(auction)) || Number.isNaN(Number(normal))) return null;
  return Number(auction) - Number(normal);
}

function liveSlice(tenor, sliceName) {
  return ((((state.compare || {}).live || {}).slices || {})[tenor] || {})[sliceName] || null;
}

function liveMarkup(slice) {
  if (!slice) return `<div class="sub">This week is not a 2/5/7 week</div>`;
  if (slice.status === "upcoming") {
    const open = String(slice.start_et || "").slice(11, 16);
    return `<div class="compare-live"><span class="sub">This week · opens ${open || "later"}</span></div>`;
  }
  const label = slice.status === "open" ? "This week · so far" : "This week";
  if (!slice.n_bars) return `<div class="compare-live"><span class="sub">${label} · no prints yet</span></div>`;
  return `<div class="compare-live"><span class="sub">${label}</span><strong class="${tone(slice.net_flow)}">${signedMoney(slice.net_flow)} net</strong><span class="sub">buy ${money(slice.buy_flow)} · sell ${money(slice.sell_flow)} · ${signedBps(slice.window_bps)}</span></div>`;
}

function renderCompare() {
  const clocks = (state.compare || {}).clocks || {};
  const live = (state.compare || {}).live || {};
  const rows = COMPARE_SLICES.map(([tenor, sliceName, title]) => {
    const cell = compareCell(state.compare, tenor, sliceName);
    const auction = cell.supply || {};
    const normal = cell.control || {};
    const clock = clocks[tenor] || {};
    const nowSlice = liveSlice(tenor, sliceName);
    return {
      tenor,
      title,
      when: `${clock.weekday || ""} ${clock[sliceName] || ""}`.trim(),
      auctionN: auction.n || 0,
      normalN: normal.n || 0,
      extraBuy: gap(auction.median_buy, normal.median_buy),
      extraSell: gap(auction.median_sell, normal.median_sell),
      extraNet: gap(auction.median_net, normal.median_net),
      auctionBps: auction.median_bps,
      normalBps: normal.median_bps,
      auctionBuy: auction.median_buy,
      normalBuy: normal.median_buy,
      auctionSell: auction.median_sell,
      normalSell: normal.median_sell,
      auctionNet: auction.median_net,
      normalNet: normal.median_net,
      implied: cell.implied,
      slope: cell.slope,
      intercept: cell.intercept,
      live: nowSlice,
    };
  });
  const maxNet = Math.max(...rows.map((row) => Math.abs(row.extraNet || 0)), 1);
  $("compare-board").innerHTML = rows.map((row) => {
    const width = Math.abs(row.extraNet || 0) / maxNet * 50;
    const left = (row.extraNet || 0) >= 0 ? 50 : 50 - width;
    const open = row.live && row.live.status === "open" ? " open" : "";
    return `<article class="compare-row${open}">
      <div>
        <h3>${row.tenor} ${row.title.toLowerCase()}</h3>
        <div class="when">${row.when}</div>
        <div class="sub">${row.auctionN} auction days · ${row.normalN} normal days</div>
      </div>
      <div class="compare-flow">
        ${liveMarkup(row.live)}
        <div>Extra buy <strong class="${tone(row.extraBuy)}">${signedMoney(row.extraBuy)}</strong> <span class="sub">auction ${money(row.auctionBuy)} · normal ${money(row.normalBuy)}</span></div>
        <div>Extra sell <strong class="${tone(row.extraSell)}">${signedMoney(row.extraSell)}</strong> <span class="sub">auction ${money(row.auctionSell)} · normal ${money(row.normalSell)}</span></div>
        <div>Extra net <strong class="${tone(row.extraNet)}">${signedMoney(row.extraNet)}</strong> <span class="sub">auction ${money(row.auctionNet)} · normal ${money(row.normalNet)}</span></div>
        <div class="delta-track"><div class="delta-zero"></div><div class="delta-fill ${tone(row.extraNet)}" style="left:${left}%;width:${width}%"></div></div>
      </div>
      <div class="compare-move">
        <span class="sub">Auction-day move</span>
        <strong class="${tone(row.auctionBps)}">${signedBps(row.auctionBps)}</strong>
        <span class="sub">Normal day ${signedBps(row.normalBps)}</span>
        <span class="sub">Implied by auction net</span>
        <strong class="${tone(row.implied)}">${signedBps(row.implied)}</strong>
        <span class="sub">${row.slope == null ? "no normal-day fit" : `${Number(row.slope).toFixed(2)} bps / $1mm on normal days`}</span>
        ${row.live && row.live.n_bars ? `<span class="sub">So far implied ${signedBps(impliedBps(row.slope, row.intercept, row.live.net_flow))}</span>` : ""}
      </div>
    </article>`;
  }).join("");
  $("compare-badge").textContent = live.week_key ? `${live.week_key} live` : "no 2/5/7 week";
}

function renderWindowCards() {
  $("window-cards").innerHTML = WINDOW_NAMES.map((name) => {
    const row = (state.windows || {})[name] || {};
    const slope = row.slope_bps_per_mm == null ? "—" : `${Number(row.slope_bps_per_mm).toFixed(2)} bps / $1mm`;
    const intercept = row.intercept_bps == null ? "—" : Number(row.intercept_bps).toFixed(2);
    const implied = row.implied_bps_at_median_net == null ? "—" : Number(row.implied_bps_at_median_net).toFixed(2);
    const medianBps = row.median_bps == null ? "—" : Number(row.median_bps).toFixed(2);
    return `<article class="card">
      <h3>${WINDOW_LABELS[name]}</h3>
      <strong>${slope}</strong>
      <span>n=${row.n || 0} · intercept ${intercept}</span>
      <span>median buy ${money(row.median_buy)} · median sell ${money(row.median_sell)}</span>
      <span>median window bps ${medianBps} · median net ${money(row.median_net)}</span>
      <span>expected at median net ${implied}</span>
    </article>`;
  }).join("");
}

function calculateMove(week, bounds, measureType) {
  const pd = week.price_data;
  if (!pd || !pd.hours) return null;
  const arr = pd.hours.map((h, i) => ({ h, c: pd.closes[i] })).filter((d) => d.h >= bounds.start && d.h <= bounds.end);
  if (arr.length < 2) return null;
  const start = arr[0].c;
  let maxUp = 0;
  let maxDown = 0;
  let high = start;
  let low = start;
  arr.forEach((p) => {
    const move = pointsToBps(p.c - start);
    if (move > maxUp) maxUp = move;
    if (move < maxDown) maxDown = move;
    if (p.c > high) high = p.c;
    if (p.c < low) low = p.c;
  });
  const endMove = pointsToBps(arr[arr.length - 1].c - start);
  switch (measureType) {
    case "end_move": return endMove;
    case "max_up": return maxUp;
    case "max_down": return Math.abs(maxDown);
    case "range": return pointsToBps(high - low);
    default: return Math.max(Math.abs(maxUp), Math.abs(maxDown));
  }
}

function updateDistribution() {
  const fromYear = Number($("distro-year").value || 2015);
  const measure = $("distro-measure").value;
  const annotateMonthEnd = $("annotate-month-end").checked;
  let total = 0;
  DISTRO_GROUPS.forEach((group) => {
    group.categories.forEach(([key, label, startHour, endHour]) => {
      const moves = [];
      const monthEndMarkers = [];
      visibleWeeks().forEach((week) => {
        if ((week.week_info.year || 0) < fromYear) return;
        const start = auctionClockHour(week, group.dateKey, startHour);
        const end = auctionClockHour(week, group.dateKey, endHour);
        if (start == null || end == null) return;
        const move = calculateMove(week, { start, end }, measure);
        if (move === null || Number.isNaN(move)) return;
        moves.push(move);
        if (annotateMonthEnd && week.week_info.is_month_end) {
          monthEndMarkers.push(move);
        }
      });
      total += moves.length;
      const badge = $(`distro-badge-${key}`);
      const chart = $(`distro-${key}`);
      const stats = $(`distro-stats-${key}`);
      if (!badge || !chart || !stats) return;
      if (!moves.length) {
        badge.textContent = "0 weeks";
        chart.innerHTML = '<div style="text-align:center;padding:48px;color:#9ca3af;">No weeks in this window</div>';
        stats.textContent = "";
        return;
      }
      const sorted = [...moves].sort((a, b) => a - b);
      const n = sorted.length;
      const mean = moves.reduce((a, b) => a + b, 0) / n;
      const median = n % 2 ? sorted[Math.floor(n / 2)] : (sorted[n / 2 - 1] + sorted[n / 2]) / 2;
      const p25 = sorted[Math.floor(n * 0.25)] ?? sorted[0];
      const p75 = sorted[Math.floor(n * 0.75)] ?? sorted[n - 1];
      badge.textContent = `${n} weeks`;
      stats.textContent = `median ${median.toFixed(2)} · mean ${mean.toFixed(2)} · p25 ${p25.toFixed(2)} · p75 ${p75.toFixed(2)} bps`;
      const min = sorted[0];
      const max = sorted[n - 1];
      const bins = 20;
      const width = (max - min) / bins || 1;
      const counts = Array(bins).fill(0);
      moves.forEach((value) => {
        const idx = Math.min(bins - 1, Math.floor((value - min) / width));
        counts[idx] += 1;
      });
      const x = counts.map((_, i) => min + (i + 0.5) * width);
      Plotly.react(chart, [{
        x,
        y: counts,
        type: "bar",
        marker: { color: group.color },
        name: label,
      }], {
        paper_bgcolor: "#111827",
        plot_bgcolor: "#111827",
        font: { color: "#f9fafb" },
        xaxis: { title: "bps", gridcolor: "#374151" },
        yaxis: { title: "weeks", gridcolor: "#374151" },
        shapes: monthEndMarkers.map((move) => ({
          type: "line",
          x0: move,
          x1: move,
          y0: 0,
          y1: 1,
          yref: "paper",
          line: { color: group.marker, dash: "dot" },
        })),
        margin: { t: 16, r: 12, b: 42, l: 42 },
        showlegend: false,
      }, { responsive: true });
    });
  });
  $("distro-badge").textContent = `${total} samples`;
}

function fillYears() {
  const years = [...new Set((state.weeks || []).map((week) => week.week_info && week.week_info.year).filter(Boolean))].sort();
  const node = $("distro-year");
  node.innerHTML = years.map((year) => `<option value="${year}">${year}</option>`).join("");
  if (years.length) node.value = String(years[0]);
}

function renderAll() {
  renderHeader();
  renderCompare();
  renderHeatmaps();
  renderWindowCards();
  updateDistribution();
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
    const live = await getJson(`/api/live?${liveQuery()}`);
    state.windows = live.windows || {};
    state.compare = live.compare || {};
    state.current = live.current;
    if (live.current && live.current.week_key) {
      const idx = state.weeks.findIndex((week) => week.week_key === live.current.week_key);
      if (idx >= 0) {
        const nextFlow = live.current.flow || {};
        state.weeks[idx].flow = {
          has_flow: nextFlow.has_flow,
          windows: nextFlow.windows || {},
        };
        if (live.current.price_data) state.weeks[idx].price_data = live.current.price_data;
        state.weeks[idx].is_current = true;
        state.weeks[idx].current_window = live.current.current_window;
      }
    }
    renderFeed(live.feed);
    renderHeader();
    renderWindowCards();
    renderCompare();
    renderHeatmaps();
    updateDistribution();
  } catch (_error) {
    /* keep last view */
  }
}

async function reload() {
  stopLive();
  $("status").textContent = "Loading…";
  try {
    const boot = await getJson(`/api/bootstrap?${liveQuery()}`);
    state.boot = boot;
    state.weeks = boot.weeks || [];
    state.windows = boot.windows || {};
    state.compare = boot.compare || {};
    state.current = boot.current;
    fillYears();
    renderFeed(boot.feed);
    renderAll();
    $("status").textContent = `${state.weeks.length} weeks`;
    startLive();
  } catch (error) {
    $("status").textContent = error.message;
  }
}

$("drop-tier1").addEventListener("change", refreshLive);
$("drop-month-end").addEventListener("change", refreshLive);
$("type-filter").addEventListener("change", async () => {
  try {
    const live = await getJson(`/api/live?${liveQuery()}`);
    state.windows = live.windows || {};
    state.compare = live.compare || state.compare || {};
  } catch (_error) {
    /* keep last window cards */
  }
  renderAll();
});
["distro-year", "distro-measure", "annotate-month-end"].forEach((id) => {
  $(id).addEventListener("change", updateDistribution);
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshLive();
});

reload();
