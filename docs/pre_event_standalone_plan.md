# Standalone Pre-Event HTML Dashboard Plan (Current)

## Purpose
Define the current standalone **pre-event** HTML dashboard (NFP-style) so it can be iteratively edited.
This plan assumes a **historical-only** HTML (no live price panel).

## Source of Truth
- Output HTML example: `workspace/scenarios/live_preevent_dashboard_20260108_191400.html`
- Generator: `scripts/live_unified_dashboard.py` (preferred place to change for repeatable output)

## Non-Negotiable Data Contracts
These rules apply to **any processing** derived from raw OHLCV (analytics, candles, price paths, distributions, percentiles, envelope stats).

- **Time ordering (must sort first)**:
  - Before doing *anything* with time-series (candles, paths, returns, resampling, stats), enforce **strict ascending timestamp order**.
  - If data arrives out-of-order: **sort by timestamp** and (in generator code) log/warn.
- **Treasury conversions (must use rules file)**:
  - All **ticks / bps / decimal** conversions and formatting must use `rules/treasury_price_conversion.py` (no ad-hoc multipliers like `*16` sprinkled in JS).

## Fixed Window
- Chart window (start is configurable):
  - Default: **3pm T-1 → 8:30am event time**
  - Optional extended lookback: **3pm T-5 → 3pm T-1** (for viewing up to 5 prior days of pre-event drift)
- Stats window: **current time → 8:30am** at the time the HTML was generated

## Data at the Top (Shared Inputs for All Panels)
These data blocks are embedded near the top of the HTML and referenced by every panel below.

**Primary data**
- `events` array: per-event fields (date, cons_std, surprise, standardized_surprise, pre-event paths, candlestick_5m, other_events).
- `consStdStats`: min/max/mean/median for bucket defaults.

**Derived / filtered data**
- `filtered_events`: the subset of `events` actually used by all panels after applying any filters (especially Week Match).
- `filter_metadata`: a small object describing what filters were applied (so the header/badges can be honest).

**Window constants**
- `CHART_WINDOW_START`, `CHART_WINDOW_END`
  - Default: 3pm T-1 → 8:30am
  - Extended lookback option: 3pm T-5 → 3pm T-1 (start-side lookback)
- `STATS_WINDOW_START` (current time when HTML was generated)

**Core helpers**
- `createBuckets()`, `assignToBucket()` for bucket logic
- `calculateDynamicBE()` for B/E from candlesticks

---

## Panels (What Exists Today)

### 0) Week Match Filter (new: optional, generator-time or UI-time)
Goal: select which **events to match on** (Week Match anchors), compute matching historical weeks, and then run the **entire pre-event dashboard** on the resulting filtered event set.

**User intent**
- “I only want to analyze pre-event drift for NFP events that occurred in weeks that *also* had X/Y/Z macro events.”

**What it does**
- Lets you pick a set of **anchor events** (the events you care about matching on).
- Produces a set of **matched weeks** (historical week-start dates) and therefore a set of **matched event dates**.
- Builds `filtered_events` by keeping only `events` whose dates fall into the matched weeks (or other match criteria you define).
- Every downstream panel (controls, boxplot, price paths, event details, distributions, candlestick) uses **`filtered_events`**, not the full `events`.

**Data it references**
- Raw `events` (the full event universe for the chosen event type)
- Week-match inputs (anchors + tiering rules + date range constraints)

**Implementation reference (existing system)**
- Unified dashboard already has Week Match functionality:
  - Tab module: `scripts/dashboard_tabs/weekmatch_tab.py`
  - Server endpoints in `scripts/live_unified_dashboard.py`: `/api/weekmatch/parse`, `/api/weekmatch/candidates`, `/api/weekmatch/pricepaths`, `/api/weekmatch/candlestick/<week_start>`

**Edit levers**
- **Anchors UI**: which events can be selected, tiers/weights, and “required vs optional” anchors
- **Matching rule**: what constitutes a match (strict all-anchors vs tiered partial match)
- **Filter application**: define the mapping “matched weeks → matched event dates → filtered_events”

**Outputs**
- `filtered_events` (used everywhere below)
- `filter_metadata` for header/badges (e.g., `{"mode":"weekmatch","anchors":["CPI","ISM PMI"],"weeks_matched":12}`)

---

### 1) Header
**What it shows**
- Title: "Pre-Event Analysis" (historical)
- Subtitle: window description + number of historical events

**Data it references (from top)**
- Uses computed event count from `filtered_events` (or `events` if no filter is applied)
- Uses window wording aligned to `CHART_WINDOW_START` / `CHART_WINDOW_END`
  - If Week Match is applied, subtitle/badges must mention it (via `filter_metadata`)

**Edit levers**
- Change text in the `<header>` block

---

### 2) Controls Row
**What it shows**
- Consensus std dev input
- Window description (current time → event time)
- Bucket width input
- Outlier toggle
- Update Analysis button

**Data it references (from top)**
- `consStdStats` to inform bucket defaults/limits
- `CHART_WINDOW_START`, `CHART_WINDOW_END`, `STATS_WINDOW_START` for window text
  - If controls depend on sample size (e.g., disable KDE for small N), use `filtered_events.length`

**Edit levers**
- HTML inputs: `#consStdInput`, `#bucketWidthInput`, `#includeOutliers`
- JS: `updateDashboard()` for behavior

---

### 3) Boxplot Panel (B/E by bucket)
**What it shows**
- Boxplots by consensus std dev bucket
- Badges: All Events, time range

**Data it references (from top)**
- `filtered_events` (cons_std + pre-event B/E per event)
- `consStdStats` (bucket range)
- `CHART_WINDOW_START` / `STATS_WINDOW_START` for time-range badges

**Edit levers**
- Bucket creation: `createBuckets()`
- Bucket assignment: `assignToBucket()`
- Boxplot rendering block in `updateDashboard()`
- Badge labels in the HTML

---

### 4) Price Paths + Statistical Envelope
**What it shows**
- Normalized price paths
- Envelope lines (mean/median/percentiles)
- Stats grid:
  - Chart window
  - Stats window (Now → event)
  - Mean/median
  - Max + / Max -
  - 0 bps percentile
  - P25/P50/P75/P95/P99
- Toggles: show paths / show stats lines

**Data it references (from top)**
- `filtered_events` (pre-event path series + candlestick_5m for normalization)
- `CHART_WINDOW_START` / `CHART_WINDOW_END` for plotting bounds
- `STATS_WINDOW_START` for stats-window calculations

**Edit levers**
- Window constants: `CHART_WINDOW_START`, `CHART_WINDOW_END`, `STATS_WINDOW_START`
- Envelope logic: `calculateEnvelopeStats()`
- Toggles: `togglePricePaths()`, `toggleStatsLines()`
- Stats grid HTML IDs: `#meanMove`, `#medianMove`, etc.

---

### 5) Selected Event Details
**What it shows**
- Per-event stats after clicking a price path
- Other events on the same day (if present)

**Data it references (from top)**
- `filtered_events` (selected event fields, surprise, candles, other_events)

**Edit levers**
- JS: `showEventDetails(event)`
- HTML: `#eventDetails`, `#eventStatsGrid`

---

### 6) Bucket Distribution Section
**What it shows**
- Return distribution
- Absolute return distribution
- Volatility distribution
- Stats blocks for each
  - Must include **expected rally** and **expected selloff** in the stats box (in addition to other summary stats like N/mean/percentiles).

**Data it references (from top)**
- `filtered_events` further filtered to current bucket
- Window bounds (`CHART_WINDOW_START`/`STATS_WINDOW_START`) for return windows

**Edit levers**
- HTML: `#returnDistroChart`, `#absReturnDistroChart`, `#volatilityDistroChart`
- JS: distribution render functions in `updateDashboard()` (and helpers)

---

### 7) Candlestick Modal
**What it shows**
- Candlestick chart for selected event
- Duration slider (±1 to ±5 days)

**Data it references (from top)**
- Selected event’s `candlestick_5m` from `filtered_events`

**Edit levers**
- HTML: `#candlestickModal`, `#candleDurationSlider`
- JS: candlestick render and slider handlers

## Data & Logic (Embedded in HTML)
**What exists**
- `events` array with:
  - date, cons_std, surprise, standardized_surprise
  - pre-event B/E stats by time
  - candlestick_5m series
  - other_events metadata
- `consStdStats` used for bucket defaults
- Dynamic B/E calculation from candles (`calculateDynamicBE`)

**Edit levers**
- Replace `events = [...]` for new datasets
- Adjust bucket defaults or dynamic B/E logic in JS

## Iteration Workflow
1. Decide change area (controls, buckets, envelope, distributions, modal).
2. Update generator (`scripts/live_unified_dashboard.py`) if you need repeatability.
3. Regenerate HTML.
4. Validate: charts render, stats update, click-to-details works, modal opens.

