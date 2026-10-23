const HEALTH_STRIP = document.getElementById("health-strip");
const OVERALL_STATUS = document.getElementById("overall-status");
const CYCLE_LABEL = document.getElementById("cycle-label");
const EVENTS_LIST = document.getElementById("events-list");
const HEADLINE_FEED = document.getElementById("headline-feed");
const ADHOC_FORM = document.getElementById("adhoc-form");
const ADHOC_RESULT = document.getElementById("adhoc-result");
const HISTORY_WINDOW = document.getElementById("history-window");
const HISTORY_COVERAGE = document.getElementById("history-coverage");
const TOGGLE_EVENTS = document.getElementById("toggle-events");
const TOGGLE_HEADLINES = document.getElementById("toggle-headlines");
const THEME_FILTER = document.getElementById("theme-filter");
const ANNOTATION_DETAIL = document.getElementById("annotation-detail");
const SIGNALS_LIST = document.getElementById("signals-list");
const SIGNALS_META = document.getElementById("signals-meta");
const FLOW_CONTEXT = document.getElementById("flow-context");
const DISTRIBUTION_EVENT = document.getElementById("distribution-event");
const DISTRIBUTION_WINDOW = document.getElementById("distribution-window");
const DISTRIBUTION_SCENARIO = document.getElementById("distribution-scenario");
const DISTRIBUTION_SUMMARY = document.getElementById("distribution-summary");
const DISTRIBUTION_NOTE = document.getElementById("distribution-note");

let cyclePayload = { cycle: {}, events: [] };
let runwayPayload = { flow: { rows: [] }, price: {} };
let currentHealth = { feeds: {} };
let headlineToggleTouched = false;
let liveAnnotationCache = { loadedAt: 0, start: null, end: null, payload: { rows: [], clusters: [] } };
let distributionBands = { events: [], current_price: null, window: "2h", scenario: "baseline" };

function clsStatus(status) {
  if (status === "ok") return "status-ok";
  if (status === "stale") return "status-stale";
  return "status-down";
}

function fmtAge(value) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  const n = Number(value);
  if (n < 60) return `${n.toFixed(0)}s`;
  if (n < 3600) return `${(n / 60).toFixed(1)}m`;
  return `${(n / 3600).toFixed(1)}h`;
}

function fmtDate(value) {
  if (!value) return "n/a";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
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

function bondTicks(values, targetCount = 7) {
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

function candleTrace(rows, name) {
  return {
    type: "candlestick",
    name,
    x: rows.map((row) => row.t),
    open: rows.map((row) => row.o),
    high: rows.map((row) => row.h),
    low: rows.map((row) => row.l),
    close: rows.map((row) => row.c),
    increasing: { line: { color: "#22c55e" } },
    decreasing: { line: { color: "#ef4444" } },
    hovertext: rows.map((row) => (
      `O ${decimalTo32nds(row.o)}<br>H ${decimalTo32nds(row.h)}<br>L ${decimalTo32nds(row.l)}<br>C ${decimalTo32nds(row.c)}`
    )),
    hoverinfo: "x+text",
  };
}

function fmtNum(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "n/a";
  return Number(value).toFixed(digits);
}

function chartLayout(title, height = 560, rangeslider = false) {
  return {
    title,
    template: "plotly_dark",
    paper_bgcolor: "#111827",
    plot_bgcolor: "#0b1220",
    height,
    margin: { l: 70, r: 25, t: 40, b: 55 },
    xaxis: {
      title: "Time",
      rangeslider: { visible: rangeslider },
      showspikes: true,
      spikemode: "across",
      spikesnap: "cursor",
    },
    yaxis: {
      title: "TY price",
      domain: [0.22, 1.0],
      showspikes: true,
      spikemode: "across",
      spikesnap: "cursor",
    },
    yaxis2: {
      domain: [0.0, 0.16],
      range: [0, 2],
      tickmode: "array",
      tickvals: [1.45, 0.55],
      ticktext: ["Events", "Headlines"],
      fixedrange: true,
      showgrid: false,
      zeroline: false,
    },
    hovermode: "x unified",
  };
}

function themeColor(theme) {
  if (theme === "inflation") return "#ef4444";
  if (theme === "labor") return "#3b82f6";
  if (theme === "growth") return "#22c55e";
  return "#a855f7";
}

function eventSymbol(row) {
  if (row.surprise_status === "full-z") return "diamond";
  if (row.surprise_status === "proxy-z") return "circle";
  return "circle-open";
}

function eventSize(row) {
  const tier = Number(row.tier || 0);
  if (tier >= 1) return 12;
  if (Math.abs(Number(row.z || 0)) >= 1) return 12;
  return 8;
}

function isMajorEvent(row) {
  return Number(row.tier || 0) >= 1 || Math.abs(Number(row.z || 0)) >= 1;
}

function truncateText(text, max = 120) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

function selectedTheme() {
  return THEME_FILTER?.value || "all";
}

function filterEventsForRail(events, startMs, endMs) {
  const theme = selectedTheme();
  return (events || []).filter((row) => {
    const ts = Date.parse(row.ts);
    if (!Number.isFinite(ts) || ts < startMs || ts > endMs) return false;
    if (theme !== "all" && (row.theme || "other") !== theme) return false;
    return true;
  });
}

function headlineBucketForWindow(windowValue) {
  if (windowValue === "1D") return null;
  if (windowValue === "5D") return "1h";
  if (windowValue === "1M") return "6h";
  if (windowValue === "3M") return "1D";
  return "1W";
}

async function fetchHeadlines(startIso, endIso, windowValue = "1D") {
  const bucket = headlineBucketForWindow(windowValue);
  const params = new URLSearchParams({ start: startIso, end: endIso, limit: "2000" });
  if (bucket) params.set("bucket", bucket);
  const response = await fetch(`/api/headlines?${params.toString()}`);
  const payload = await response.json();
  return response.ok ? payload : { rows: [], clusters: [], error: payload.error };
}

async function fetchEventsRange(startIso, endIso) {
  const params = new URLSearchParams({ start: startIso, end: endIso });
  const response = await fetch(`/api/events_range?${params.toString()}`);
  const payload = await response.json();
  return response.ok ? (payload.events || []) : [];
}

function railTraces({ events = [], headlines = [], clusters = [], showHeadlines = true }) {
  const traces = [];
  if (TOGGLE_EVENTS.checked && events.length) {
    const majors = events.filter(isMajorEvent).slice(0, 8);
    const labelSet = new Set(majors.map((row) => row.id || `${row.name}:${row.ts}`));
    traces.push({
      x: events.map((row) => row.ts),
      y: events.map(() => 1.45),
      yaxis: "y2",
      mode: "markers+text",
      name: "Events",
      text: events.map((row) => labelSet.has(row.id || `${row.name}:${row.ts}`) ? row.name : ""),
      textposition: events.map((_, idx) => idx % 2 === 0 ? "top center" : "bottom center"),
      marker: {
        size: events.map(eventSize),
        color: events.map((row) => themeColor(row.theme || "other")),
        symbol: events.map(eventSymbol),
        line: { width: 1, color: "#e5e7eb" },
      },
      customdata: events.map((row) => [
        row.name,
        row.theme || "other",
        row.z == null ? "n/a" : Number(row.z).toFixed(2),
        row.surprise_status || "n/a",
      ]),
      hovertemplate: "<b>%{customdata[0]}</b><br>%{x}<br>%{customdata[1]} | z %{customdata[2]} | %{customdata[3]}<extra></extra>",
    });
  }
  if (showHeadlines && TOGGLE_HEADLINES.checked) {
    if (clusters.length) {
      traces.push({
        x: clusters.map((row) => row.ts),
        y: clusters.map(() => 0.55),
        yaxis: "y2",
        mode: "markers",
        name: "Headline clusters",
        marker: {
          size: clusters.map((row) => Math.min(26, 7 + Math.sqrt(row.count) * 4)),
          color: "#f59e0b",
          opacity: 0.85,
        },
        customdata: clusters.map((row) => [
          row.count,
          (row.samples || []).map((s) => `${s.source}: ${truncateText(s.text, 80)}`).join("<br>"),
        ]),
        hovertemplate: "<b>%{customdata[0]} headlines</b><br>%{x}<br>%{customdata[1]}<extra></extra>",
      });
    } else if (headlines.length) {
      traces.push({
        x: headlines.map((row) => row.ts),
        y: headlines.map(() => 0.55),
        yaxis: "y2",
        mode: "markers",
        name: "Headlines",
        marker: { size: 7, color: "#f59e0b", symbol: "triangle-up", opacity: 0.85 },
        customdata: headlines.map((row) => [row.source, truncateText(row.text)]),
        hovertemplate: "<b>%{customdata[0]}</b><br>%{x}<br>%{customdata[1]}<extra></extra>",
      });
    }
  }
  return traces;
}

function guideShapes(events, layoutShapes = []) {
  const majors = events.filter(isMajorEvent).slice(0, 8);
  return [
    ...layoutShapes,
    ...majors.map((row) => ({
      type: "line",
      x0: row.ts,
      x1: row.ts,
      y0: 0.22,
      y1: 1.0,
      yref: "paper",
      line: { color: themeColor(row.theme || "other"), width: 1, dash: "dot" },
    })),
  ];
}

function wireAnnotationClick(chartId) {
  const el = document.getElementById(chartId);
  if (!el || el._annotationClickWired) return;
  el.on("plotly_click", (data) => {
    const point = data.points?.[0];
    if (!point) return;
    if ((point.data?.name || "").startsWith("Projected")) {
      const cd = point.customdata || [];
      ANNOTATION_DETAIL.innerHTML = `<b>Projected distribution:</b> ${cd[1] || cd[0]}<br>${point.x}<br>N_eff ${cd[2] || "n/a"} | ${cd[3] || ""}`;
      if (cd[0]) selectDistributionEvent(cd[0]);
    } else if (point.data?.name === "Events") {
      const cd = point.customdata || [];
      ANNOTATION_DETAIL.innerHTML = `<b>Event:</b> ${cd[0]}<br>${point.x}<br>${cd[1]} | z ${cd[2]} | ${cd[3]}`;
    } else if ((point.data?.name || "").includes("Headline")) {
      const cd = point.customdata || [];
      ANNOTATION_DETAIL.innerHTML = `<b>${point.data.name}:</b> ${cd[0]}<br>${point.x}<br>${cd[1] || ""}`;
    }
  });
  el._annotationClickWired = true;
}

function projectedBandTraces() {
  const traces = [];
  (distributionBands.events || []).forEach((event, idx) => {
    const pp = event.projected_price || {};
    if (pp.p5 == null || pp.p95 == null) return;
    const color = themeColor(event.theme || "other");
    const opacity = Math.max(0.35, 0.9 - idx * 0.08);
    const x = event.ts;
    const name = event.name || event.canonical_name;
    const custom = [event.id, name, event.n_eff, event.conditioning?.description || "baseline"];
    traces.push({
      x: [x, x],
      y: [pp.p5, pp.p95],
      mode: "lines",
      name: `Projected p5-p95 ${name}`,
      line: { color, width: 8 },
      opacity,
      customdata: [custom, custom],
      hovertemplate: `<b>${name}</b><br>%{x}<br>p5 ${decimalTo32nds(pp.p5)} / p95 ${decimalTo32nds(pp.p95)}<br>N_eff ${event.n_eff}<extra></extra>`,
      showlegend: false,
    });
    if (pp.p25 != null && pp.p75 != null) {
      traces.push({
        x: [x, x],
        y: [pp.p25, pp.p75],
        mode: "lines",
        name: `Projected p25-p75 ${name}`,
        line: { color, width: 14 },
        opacity: Math.min(1, opacity + 0.1),
        customdata: [custom, custom],
        hoverinfo: "skip",
        showlegend: false,
      });
    }
    if (pp.p50 != null) {
      traces.push({
        x: [x],
        y: [pp.p50],
        mode: "markers",
        name: `Projected median ${name}`,
        marker: { color, size: 9, symbol: "line-ew", line: { color: "#e5e7eb", width: 1 } },
        customdata: [custom],
        hovertemplate: `<b>${name}</b><br>%{x}<br>median ${decimalTo32nds(pp.p50)}<br>N_eff ${event.n_eff}<extra></extra>`,
        showlegend: false,
      });
    }
  });
  return traces;
}

function renderHealth(health) {
  currentHealth = health || { feeds: {} };
  const status = health.status || "down";
  OVERALL_STATUS.textContent = status;
  OVERALL_STATUS.className = `status-pill ${clsStatus(status)}`;
  const feeds = health.feeds || {};
  HEALTH_STRIP.innerHTML = Object.entries(feeds).map(([name, feed]) => {
    const s = feed.status || "down";
    return `<div class="feed-pill feed-${s}" title="${feed.error || ""}">
      ${name.replaceAll("_", " ")}: ${s} (${fmtAge(feed.age_seconds)})
    </div>`;
  }).join("");
}

function renderEvents(payload) {
  const cycle = payload.cycle || {};
  CYCLE_LABEL.textContent = `${fmtDate(cycle.start)} -> ${fmtDate(cycle.end)}`;
  const now = Date.now();
  const events = (payload.events || []).filter((row) => {
    const ts = Date.parse(row.ts);
    return Number.isFinite(ts) && ts >= now - 24 * 3600 * 1000;
  }).slice(0, 25);
  EVENTS_LIST.innerHTML = events.map((row) => {
    const z = row.z == null ? "n/a" : Number(row.z).toFixed(2);
    const status = row.surprise_status || (row.assumption ? "assumption" : "n/a");
    return `<div class="event-card ${row.theme || "other"}">
      <b>${row.name}</b>
      <div class="small-note">${fmtDate(row.ts)} | ${row.type || "event"} | ${row.theme || "other"}</div>
      <div class="small-note">z ${z} | ${status}${row.assumption ? " | user assumption" : ""}</div>
    </div>`;
  }).join("") || "<p class='small-note'>No upcoming events in the current cycle.</p>";
}

function renderHeadlineFeed(health) {
  const iran = health.feeds?.iran_headlines || {};
  const text = iran.latest_text || "No latest headline available.";
  HEADLINE_FEED.innerHTML = `<div class="event-card other">
    <b>Iran/geopolitics feed</b>
    <div class="small-note">Status: ${iran.status || "down"} | Age: ${fmtAge(iran.age_seconds)}</div>
    <p>${text}</p>
  </div>`;
}

function renderSignals(payload) {
  const signals = payload?.signals || [];
  const flow = payload?.flow_context || {};
  FLOW_CONTEXT.textContent = `${flow.label || "flow n/a"}${flow.imbalance == null ? "" : ` ${(100 * flow.imbalance).toFixed(0)}%`}`;
  const hw = payload?.headline_window || {};
  SIGNALS_META.textContent = `${signals.length} active watches | ${hw.high_signal_count ?? 0} high-signal headlines in last 6h | generated ${fmtDate(payload?.generated_at)}`;
  if (!signals.length) {
    SIGNALS_LIST.innerHTML = `<div class="signal-card"><h3>No active signal</h3><p class="small-note">No high-signal headline/event watch met the first-pass rules. This does not mean no risk; it means the current rules are quiet.</p></div>`;
    return;
  }
  SIGNALS_LIST.innerHTML = signals.map((signal) => {
    const reasons = (signal.reasons || []).map((r) => `<li>${truncateText(r, 90)}</li>`).join("");
    return `<div class="signal-card ${signal.severity || signal.kind}">
      <h3>${signal.title}</h3>
      <p>${truncateText(signal.summary, 220)}</p>
      <div class="small-note">${fmtDate(signal.timestamp)} | confidence ${signal.confidence || "n/a"} | ${signal.source || signal.kind}</div>
      <ul class="signal-reasons">${reasons}</ul>
      <p class="small-note"><b>Caveat:</b> ${signal.assumption || "watch signal only"}</p>
    </div>`;
  }).join("");
}

function populateDistributionEventSelect(events) {
  const current = DISTRIBUTION_EVENT.value;
  DISTRIBUTION_EVENT.innerHTML = "";
  (events || []).forEach((event) => {
    const opt = document.createElement("option");
    opt.value = event.id;
    opt.textContent = `${event.name || event.canonical_name} (${fmtDate(event.ts)})`;
    DISTRIBUTION_EVENT.appendChild(opt);
  });
  if (current && [...DISTRIBUTION_EVENT.options].some((opt) => opt.value === current)) {
    DISTRIBUTION_EVENT.value = current;
  }
}

function selectDistributionEvent(eventId) {
  if (!eventId) return;
  if ([...DISTRIBUTION_EVENT.options].some((opt) => opt.value === eventId)) {
    DISTRIBUTION_EVENT.value = eventId;
    loadSelectedDistribution();
  }
}

async function loadDistributionBands() {
  const params = new URLSearchParams({
    window: DISTRIBUTION_WINDOW.value || "2h",
    scenario: DISTRIBUTION_SCENARIO.value || "baseline",
    limit: "6",
  });
  const response = await fetch(`/api/distributions?${params.toString()}`);
  distributionBands = await response.json();
  populateDistributionEventSelect(distributionBands.events || []);
  if (!DISTRIBUTION_EVENT.value && distributionBands.events?.length) {
    DISTRIBUTION_EVENT.value = distributionBands.events[0].id;
  }
  await loadSelectedDistribution();
  await renderRunway();
}

function renderDistributionSummary(payload) {
  const stats = payload.stats || {};
  const projected = payload.projected_price || {};
  const pills = [
    `N ${payload.n ?? 0}`,
    `N_eff ${payload.n_eff ?? 0}`,
    `mean ${fmtNum(stats.mean)} bps`,
    `p5/p95 ${fmtNum(stats.p5)} / ${fmtNum(stats.p95)} bps`,
    `rally ${fmtNum((stats.rally_prob ?? 0) * 100, 1)}%`,
    `selloff ${fmtNum((stats.selloff_prob ?? 0) * 100, 1)}%`,
    `median px ${projected.p50 == null ? "n/a" : decimalTo32nds(projected.p50)}`,
    `status ${(payload.surprise_statuses || []).join(",") || "n/a"}`,
  ];
  DISTRIBUTION_SUMMARY.innerHTML = pills.map((p) => `<span class="dist-pill">${p}</span>`).join("");
  DISTRIBUTION_NOTE.textContent = `${payload.event_name || "Event"} | ${payload.conditioning?.description || "baseline"} | projection centered on current price and ignores pre-event drift.`;
}

async function loadSelectedDistribution() {
  const eventId = DISTRIBUTION_EVENT.value;
  if (!eventId) {
    DISTRIBUTION_SUMMARY.innerHTML = "<span class='dist-pill'>No upcoming distribution event.</span>";
    Plotly.react("distribution-chart", [], chartLayout("Event Distribution", 360, false), { responsive: true });
    return;
  }
  const params = new URLSearchParams({
    event_id: eventId,
    window: DISTRIBUTION_WINDOW.value || "2h",
    scenario: DISTRIBUTION_SCENARIO.value || "baseline",
  });
  const response = await fetch(`/api/distribution?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok || payload.error) {
    DISTRIBUTION_SUMMARY.innerHTML = `<span class="dist-pill">Distribution unavailable: ${payload.error || response.status}</span>`;
    Plotly.react("distribution-chart", [], chartLayout("Event Distribution", 360, false), { responsive: true });
    return;
  }
  renderDistributionSummary(payload);
  const values = payload.values_bps || [];
  const stats = payload.stats || {};
  const traces = [{
    type: "histogram",
    x: values,
    nbinsx: Math.min(30, Math.max(8, Math.round(Math.sqrt(values.length || 1)))),
    marker: { color: "#60a5fa", opacity: 0.75 },
    name: "Historical bps",
  }];
  const layout = chartLayout("Historical Reaction Distribution (bps)", 360, false);
  layout.xaxis = { title: "TY move (bps)", zeroline: true };
  layout.yaxis = { title: "Count", domain: [0, 1] };
  layout.yaxis2 = undefined;
  layout.shapes = [];
  ["p5", "p50", "p95"].forEach((key) => {
    if (stats[key] != null) {
      layout.shapes.push({
        type: "line",
        x0: stats[key],
        x1: stats[key],
        y0: 0,
        y1: 1,
        yref: "paper",
        line: { color: key === "p50" ? "#fbbf24" : "#94a3b8", width: key === "p50" ? 2 : 1, dash: "dot" },
      });
    }
  });
  Plotly.react("distribution-chart", traces, layout, { responsive: true });
}

async function liveAnnotations(startIso, endIso) {
  const now = Date.now();
  if (
    liveAnnotationCache.start === startIso
    && liveAnnotationCache.end === endIso
    && now - liveAnnotationCache.loadedAt < 30000
  ) {
    return liveAnnotationCache.payload;
  }
  const payload = await fetchHeadlines(startIso, endIso, "1D");
  liveAnnotationCache = { loadedAt: now, start: startIso, end: endIso, payload };
  return payload;
}

async function renderRunway() {
  const candles = runwayPayload.live_candles?.rows || [];
  const candleMode = runwayPayload.live_candles?.mode || "none";
  const rawRows = runwayPayload.flow?.rows || [];
  const tyRows = rawRows.filter((row) => row.price != null);
  const now = new Date();
  const events = cyclePayload.events || [];
  const startMs = candles.length ? Date.parse(candles[0].t) : now.getTime() - 24 * 3600 * 1000;
  const endMs = Math.max(now.getTime(), candles.length ? Date.parse(candles[candles.length - 1].t) : now.getTime());
  const railEvents = filterEventsForRail(events, startMs, endMs);
  const futureEvents = events.filter((row) => Date.parse(row.ts) >= now.getTime()).slice(0, 12);
  const startIso = new Date(startMs).toISOString();
  const endIso = new Date(endMs).toISOString();
  const headlinesOk = currentHealth.feeds?.iran_headlines?.status === "live";
  const headlinePayload = headlinesOk ? await liveAnnotations(startIso, endIso) : { rows: [], clusters: [] };

  const traces = [];
  if (candleMode === "candles" && candles.length) {
    traces.push(candleTrace(candles, "TY live 1m"));
  } else if (tyRows.length) {
    traces.push({
      x: tyRows.map((row) => row.ts),
      y: tyRows.map((row) => row.price),
      customdata: tyRows.map((row) => decimalTo32nds(row.price)),
      mode: "lines",
      name: "TY live (TBBO)",
      line: { color: "#60a5fa", width: 2 },
      hovertemplate: "TY %{customdata}<br>%{x}<extra></extra>",
    });
  }
  if (futureEvents.length) {
    traces.push({
      x: futureEvents.map((row) => row.ts),
      y: futureEvents.map(() => runwayPayload.current_price || null),
      mode: "markers+text",
      name: "Upcoming events",
      text: futureEvents.map((row) => row.name),
      textposition: "top center",
      marker: { size: 10, color: futureEvents.map((row) => row.theme === "inflation" ? "#ef4444" : row.theme === "labor" ? "#3b82f6" : row.theme === "growth" ? "#22c55e" : "#a855f7") },
      hovertemplate: "%{text}<br>%{x}<extra></extra>",
    });
  }
  traces.push(...projectedBandTraces());
  traces.push(...railTraces({
    events: railEvents,
    headlines: headlinePayload.rows || [],
    clusters: headlinePayload.clusters || [],
    showHeadlines: headlinesOk,
  }));

  const priceValues = candles.length
    ? candles.flatMap((row) => [row.o, row.h, row.l, row.c])
    : tyRows.map((row) => row.price);
  (distributionBands.events || []).forEach((event) => {
    const pp = event.projected_price || {};
    ["p5", "p25", "p50", "p75", "p95"].forEach((key) => {
      if (pp[key] != null) priceValues.push(pp[key]);
    });
  });
  const baseLayout = chartLayout("Live session (~1 day)", 560, false);
  const layout = {
    ...baseLayout,
    yaxis: { ...chartLayout("", 560, false).yaxis, ...bondTicks(priceValues) },
    shapes: [{
      type: "line",
      x0: now.toISOString(),
      x1: now.toISOString(),
      y0: 0,
      y1: 1,
      yref: "paper",
      line: { color: "#fbbf24", width: 2, dash: "dot" },
    }],
    annotations: [{
      x: now.toISOString(),
      y: 1,
      yref: "paper",
      text: "now",
      showarrow: false,
      font: { color: "#fbbf24" },
    }],
  };
  layout.shapes = guideShapes(railEvents, layout.shapes);
  if (!headlinesOk) {
    layout.annotations.push({
      x: 0.01,
      xref: "paper",
      y: 0.08,
      yref: "paper",
      text: "Headlines stale/unavailable",
      showarrow: false,
      font: { color: "#fbbf24", size: 11 },
      xanchor: "left",
    });
  }
  Plotly.react("runway-chart", traces, layout, { responsive: true });
  wireAnnotationClick("runway-chart");
}

async function loadHistory() {
  const windowValue = HISTORY_WINDOW.value || "1M";
  HISTORY_COVERAGE.textContent = "Loading historical candles...";
  const response = await fetch(`/api/price_history?window=${encodeURIComponent(windowValue)}`);
  const payload = await response.json();
  if (!response.ok || payload.error) {
    HISTORY_COVERAGE.textContent = `History unavailable: ${payload.error || response.status}`;
    Plotly.react("history-chart", [], chartLayout("Historical TY", 620, true), { responsive: true });
    return;
  }
  const rows = payload.rows || [];
  HISTORY_COVERAGE.textContent = `Window ${payload.window} | ${payload.interval} | ${rows.length} candles | data through ${fmtDate(payload.coverage_end)}`;
  const values = rows.flatMap((row) => [row.o, row.h, row.l, row.c]);
  const startIso = rows.length ? rows[0].t : null;
  const endIso = rows.length ? rows[rows.length - 1].t : payload.coverage_end;
  const histEvents = startIso && endIso ? await fetchEventsRange(startIso, endIso) : [];
  const smallWindow = ["1D", "5D"].includes(payload.window);
  const showHistHeadlines = TOGGLE_HEADLINES.checked && (smallWindow || headlineToggleTouched);
  const headlinePayload = (showHistHeadlines && startIso && endIso)
    ? await fetchHeadlines(startIso, endIso, payload.window)
    : { rows: [], clusters: [] };
  const layout = chartLayout("Historical TY Candlesticks", 620, true);
  layout.yaxis = { ...layout.yaxis, ...bondTicks(values) };
  layout.xaxis.rangeselector = {
    buttons: [
      { count: 1, label: "1D", step: "day", stepmode: "backward" },
      { count: 5, label: "5D", step: "day", stepmode: "backward" },
      { count: 1, label: "1M", step: "month", stepmode: "backward" },
      { count: 3, label: "3M", step: "month", stepmode: "backward" },
      { count: 6, label: "6M", step: "month", stepmode: "backward" },
      { count: 1, label: "1Y", step: "year", stepmode: "backward" },
      { label: "All", step: "all" },
    ],
  };
  const startMs = startIso ? Date.parse(startIso) : 0;
  const endMs = endIso ? Date.parse(endIso) : 0;
  const railEvents = filterEventsForRail(histEvents, startMs, endMs);
  const traces = rows.length ? [candleTrace(rows, "TY OHLCV")] : [];
  traces.push(...railTraces({
    events: railEvents,
    headlines: headlinePayload.rows || [],
    clusters: headlinePayload.clusters || [],
    showHeadlines: showHistHeadlines,
  }));
  layout.shapes = guideShapes(railEvents, layout.shapes || []);
  Plotly.react("history-chart", traces, layout, { responsive: true });
  wireAnnotationClick("history-chart");
}

async function loadCycle() {
  const response = await fetch("/api/cycle");
  cyclePayload = await response.json();
  renderEvents(cyclePayload);
}

async function loadRunway() {
  const response = await fetch("/api/runway");
  runwayPayload = await response.json();
  await renderRunway();
}

async function loadHealth() {
  const response = await fetch("/api/health");
  const health = await response.json();
  renderHealth(health);
  renderHeadlineFeed(health);
}

async function loadSignals() {
  const response = await fetch("/api/signals");
  const payload = await response.json();
  renderSignals(payload);
}

async function refreshAll() {
  await Promise.all([loadCycle(), loadRunway(), loadHealth(), loadHistory(), loadSignals(), loadDistributionBands()]);
}

ADHOC_FORM.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(ADHOC_FORM);
  const payload = Object.fromEntries(form.entries());
  if (payload.ts) payload.ts = new Date(payload.ts).toISOString();
  const response = await fetch("/api/adhoc_events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  ADHOC_RESULT.textContent = result.ok ? "Added event." : `Error: ${result.error}`;
  await loadCycle();
  await renderRunway();
});

["w-inflation", "w-labor", "w-growth", "w-alpha"].forEach((id) => {
  const el = document.getElementById(id);
  const out = document.getElementById(`${id}-v`);
  const badge = document.getElementById("assumption-badge");
  el.addEventListener("input", () => {
    out.textContent = el.value;
    const alpha = Number(document.getElementById("w-alpha").value);
    badge.classList.toggle("hidden", alpha === 100);
  });
});

HISTORY_WINDOW.addEventListener("change", loadHistory);
TOGGLE_EVENTS.addEventListener("change", () => { renderRunway(); loadHistory(); });
TOGGLE_HEADLINES.addEventListener("change", () => {
  headlineToggleTouched = true;
  renderRunway();
  loadHistory();
});
THEME_FILTER.addEventListener("change", () => { renderRunway(); loadHistory(); });
DISTRIBUTION_EVENT.addEventListener("change", loadSelectedDistribution);
DISTRIBUTION_WINDOW.addEventListener("change", async () => {
  await loadDistributionBands();
  await renderRunway();
});
DISTRIBUTION_SCENARIO.addEventListener("change", async () => {
  await loadDistributionBands();
  await renderRunway();
});

refreshAll();
setInterval(loadRunway, 3000);
setInterval(loadHealth, 5000);
setInterval(loadSignals, 15000);
setInterval(loadCycle, 60000);
setInterval(loadDistributionBands, 60000);

if (window.EventSource) {
  const source = new EventSource("/api/stream");
  source.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.health) {
        renderHealth(payload.health);
        renderHeadlineFeed(payload.health);
      }
      if (payload.runway) {
        runwayPayload = payload.runway;
        renderRunway();
      }
      if (payload.signals) {
        renderSignals(payload.signals);
      }
    } catch {
      // Fall back to polling.
    }
  };
}
