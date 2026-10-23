#!/usr/bin/env python3
"""
Standalone Pre-Event NFP HTML Builder (Template-Based)
------------------------------------------------------
Generates a standalone pre-event HTML dashboard by injecting fresh data into
the legacy pre-event template HTML (embedded data, no API calls).
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Reuse existing data pipeline
from scripts import live_unified_dashboard as lud


DEFAULT_TEMPLATE = PROJECT_ROOT / "workspace" / "scenarios" / "live_preevent_dashboard_20260108_191400.html"
OUTPUT_DIR = PROJECT_ROOT / "workspace" / "scenarios"


def _compute_chart_window_start(lookback_days: int) -> float:
    """Return start hours from event time for 3pm T-(lookback_days)."""
    # 3pm T-1 = -17.5 hours from 8:30am event time.
    # Each extra day adds 24 hours of lookback.
    return -17.5 - (max(1, lookback_days) - 1) * 24.0


def _sort_event_series(events: list) -> None:
    """Sort time-series fields in-place for each event."""
    for event in events:
        prices = event.get("prices")
        if isinstance(prices, list):
            prices.sort(key=lambda p: p.get("ts", ""))
        candles = event.get("candlestick_5m")
        if isinstance(candles, list):
            candles.sort(key=lambda c: c.get("ts", ""))


def _percentile(sorted_vals: list, pct: float) -> float:
    """Return percentile value from a sorted list."""
    if not sorted_vals:
        return 0.0
    pct = max(0.0, min(100.0, pct))
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def _filter_extreme_cons_std(events: list, pct_min: float, pct_max: float) -> list:
    """Drop events with extreme consensus std dev values using percentile bounds."""
    cons_values = sorted([e.get("cons_std") for e in events if e.get("cons_std") is not None])
    if not cons_values:
        return events
    lower = _percentile(cons_values, pct_min)
    upper = _percentile(cons_values, pct_max)
    return [e for e in events if e.get("cons_std") is not None and lower <= e["cons_std"] <= upper]


def _inject_events(html: str, events: list, cons_std_stats: dict) -> str:
    events_json = json.dumps(events, separators=(",", ":"))
    stats_json = json.dumps(cons_std_stats, separators=(",", ":"))
    html = re.sub(r"const events = .*?;\n", f"const events = {events_json};\n", html, flags=re.DOTALL)
    html = re.sub(r"const consStdStats = .*?;\n", f"const consStdStats = {stats_json};\n", html)
    return html


def _inject_window_constants(html: str, chart_start: float, generated_hours: float) -> str:
    html = re.sub(
        r"const DASHBOARD_GENERATED_HOURS = .*?;",
        f"const DASHBOARD_GENERATED_HOURS = {generated_hours};",
        html,
    )
    html = re.sub(
        r"const CHART_WINDOW_START = .*?;",
        f"const CHART_WINDOW_START = {chart_start};",
        html,
    )
    html = re.sub(r"const CHART_WINDOW_END = .*?;", "const CHART_WINDOW_END = 0;", html)
    # For historical-only mode: stats window = full chart window (not "now")
    # Use 'let' instead of 'const' so recalculateAll can update these dynamically
    html = re.sub(r"const STATS_WINDOW_START = .*?;", "let STATS_WINDOW_START = CHART_WINDOW_START;", html)
    html = re.sub(r"const FIXED_WINDOW_START = .*?;", "let FIXED_WINDOW_START = CHART_WINDOW_START;", html)
    html = re.sub(r"const FIXED_WINDOW_END = .*?;", "let FIXED_WINDOW_END = CHART_WINDOW_END;", html)
    return html


def _inject_expected_rally_selloff(html: str) -> str:
    insert_block = (
        "            const pos = values.filter(v => v > 0);\n"
        "            const neg = values.filter(v => v < 0);\n"
        "            const pRally = pos.length / n;\n"
        "            const pSelloff = neg.length / n;\n"
        "            const expRally = pRally * (pos.length ? (pos.reduce((a, b) => a + b, 0) / pos.length) : 0);\n"
        "            const expSelloff = pSelloff * (neg.length ? (neg.reduce((a, b) => a + b, 0) / neg.length) : 0);\n"
    )
    html = re.sub(
        r"(const stdDev = Math\.sqrt\(variance\);\n)",
        r"\1" + insert_block,
        html,
    )
    html = re.sub(
        r"`Median: \$\{p50\.toFixed\(2\)\}<br>` \+\n"
        r"`75%: \$\{p75\.toFixed\(2\)\}<br>` \+\n",
        "`Median: ${p50.toFixed(2)}<br>` +\n"
        "`Exp Rally: ${expRally.toFixed(2)}<br>` +\n"
        "`Exp Selloff: ${expSelloff.toFixed(2)}<br>` +\n"
        "`75%: ${p75.toFixed(2)}<br>` +\n",
        html,
    )
    return html


def _strip_live_labels(html: str) -> str:
    html = html.replace("Live Pre-Event Analysis", "Pre-Event Analysis")
    html = html.replace("🔴 LIVE", "")
    html = html.replace(" LIVE)", ")")
    return html


def _enable_time_picker(html: str) -> str:
    """Un-hide the time picker so users can adjust the chart window (up to T-5)."""
    html = html.replace(
        '<div class="time-picker-row" style="display: none;">',
        '<div class="time-picker-row">',
    )
    return html


def _add_recalculate_all_button(html: str) -> str:
    """Add a Recalculate All button that updates everything for the selected window."""
    # Add button next to Apply button
    html = html.replace(
        '<button class="btn btn-small" onclick="applyTimeRange()">Apply</button>',
        '<button class="btn btn-small" onclick="applyTimeRange()">Apply</button> '
        '<button class="btn btn-small" style="background:#22c55e;margin-left:8px;" onclick="recalculateAll()">Recalculate All</button>'
    )
    
    # Add the recalculateAll function
    recalc_fn = '''
        // Recalculate everything for the selected window
        function recalculateAll() {
            // Read start window from dropdowns
            const startDay = parseInt(document.getElementById('startDay').value);
            const startTimeVal = document.getElementById('startTime').value;
            const [startHour, startMin] = startTimeVal.split(':').map(Number);
            
            const startHours = dayTimeToHours(startDay, startHour, startMin);
            // Always end at 8:30am event time (0h)
            const endHours = 0;
            
            // console.log('Recalculate All: window=' + startHours.toFixed(2) + 'h to ' + endHours.toFixed(2) + 'h');
            
            // Update global window variables AND the FIXED constants so relayout events don't reset
            currentWindowStart = startHours;
            currentWindowEnd = endHours;
            FIXED_WINDOW_START = startHours;
            FIXED_WINDOW_END = endHours;
            STATS_WINDOW_START = startHours;
            
            // 1. Re-render price paths normalized to new window
            renderNormalizedPaths(startHours, endHours, false);
            
            // 2. Recalculate stats for new window
            recalculateStatsOnly(startHours, endHours);
            
            // 3. Update distribution charts
            updateDistributionCharts(startHours, endHours);
            
            // 4. Update window range display
            document.getElementById('windowRange').textContent = 
                hoursToClockTime(startHours) + ' → 8:30 AM (Event)';
            
            console.log('Recalculate All complete');
        }
        
        // Stats calculation without triggering syncTimeInputs
        function recalculateStatsOnly(startHours, endHours) {
            if (rawPathsData.length === 0) return;
            
            const bpsMoves = [];
            rawPathsData.forEach(event => {
                if (!event.candlestick_5m || event.candlestick_5m.length === 0) return;
                
                const visibleCandles = [];
                event.candlestick_5m.forEach(c => {
                    const hours = tsToHoursFromEvent(c.ts, event.date);
                    if (hours >= startHours && hours <= endHours) {
                        visibleCandles.push({ ...c, hours });
                    }
                });
                
                if (visibleCandles.length < 2) return;
                visibleCandles.sort((a, b) => a.hours - b.hours);
                
                const firstPrice = visibleCandles[0].close;
                const lastPrice = visibleCandles[visibleCandles.length - 1].close;
                const bpsMove = (lastPrice - firstPrice) * 8;
                
                bpsMoves.push({ date: event.date, bps: bpsMove });
            });
            
            if (bpsMoves.length === 0) {
                clearStats();
                return;
            }
            
            const bpsValues = bpsMoves.map(m => m.bps).sort((a, b) => a - b);
            const n = bpsValues.length;
            const mean = bpsValues.reduce((a, b) => a + b, 0) / n;
            const median = bpsValues[Math.floor(n / 2)];
            const maxPos = Math.max(...bpsValues);
            const maxNeg = Math.min(...bpsValues);
            
            const percentile = (arr, p) => {
                const idx = Math.ceil((p / 100) * arr.length) - 1;
                return arr[Math.max(0, Math.min(idx, arr.length - 1))];
            };
            const p25 = percentile(bpsValues, 25);
            const p50 = median;
            const p75 = percentile(bpsValues, 75);
            const p95 = percentile(bpsValues, 95);
            const p99 = percentile(bpsValues, 99);
            
            let zeroPercentile = 0;
            for (let i = 0; i < bpsValues.length; i++) {
                if (bpsValues[i] < 0) zeroPercentile++;
                else break;
            }
            zeroPercentile = (zeroPercentile / n) * 100;
            
            // Update stats display
            document.getElementById('windowRange').textContent = 
                hoursToClockTime(startHours) + ' → ' + hoursToClockTime(endHours);
            document.getElementById('meanMove').textContent = mean.toFixed(3) + ' bps';
            document.getElementById('medianMove').textContent = median.toFixed(3) + ' bps';
            document.getElementById('maxPosMove').textContent = '+' + maxPos.toFixed(3) + ' bps';
            document.getElementById('maxNegMove').textContent = maxNeg.toFixed(3) + ' bps';
            document.getElementById('zeroPercentile').textContent = 'P' + zeroPercentile.toFixed(0);
            document.getElementById('p25Move').textContent = p25.toFixed(3);
            document.getElementById('p50Move').textContent = p50.toFixed(3);
            document.getElementById('p75Move').textContent = p75.toFixed(3);
            document.getElementById('p95Move').textContent = p95.toFixed(3);
            document.getElementById('p99Move').textContent = p99.toFixed(3);
            
            // Color coding
            document.getElementById('meanMove').style.color = mean >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
            document.getElementById('medianMove').style.color = median >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
            document.getElementById('zeroPercentile').style.color = zeroPercentile < 50 ? 'var(--accent-green)' : 'var(--accent-red)';
        }
        
        // Helper to convert hours to clock time string
        function hoursToClockTime(hoursFromEvent) {
            const nfpHour = 8.5;
            let totalHours = nfpHour + hoursFromEvent;
            let dayOffset = 0;
            while (totalHours >= 24) { totalHours -= 24; dayOffset++; }
            while (totalHours < 0) { totalHours += 24; dayOffset--; }
            const hour = Math.floor(totalHours);
            const minutes = Math.round((totalHours - hour) * 60);
            const isPM = hour >= 12;
            const displayHour = hour % 12 || 12;
            const ampm = isPM ? 'PM' : 'AM';
            const timeStr = displayHour + ':' + String(minutes).padStart(2, '0') + ' ' + ampm;
            if (dayOffset === 0) return timeStr;
            else if (dayOffset === -1) return timeStr + ' (T-1)';
            else if (dayOffset === 1) return timeStr + ' (T+1)';
            else return timeStr + ' (T' + (dayOffset > 0 ? '+' : '') + dayOffset + ')';
        }
        
'''
    
    # Insert the function before applyTimeRange
    html = html.replace(
        "        function applyTimeRange() {",
        recalc_fn + "        function applyTimeRange() {"
    )
    
    return html


def _add_dynamic_normalization(html: str) -> str:
    """Modify applyTimeRange to re-render paths normalized to the new window start."""
    # Replace the simple relayout with a full re-render that normalizes to new window start
    old_apply = """            // Update the chart's x-axis range
            const chartDiv = document.getElementById('rawPathsChart');
            Plotly.relayout(chartDiv, {
                'xaxis.range': [startHours, endHours]
            });
            
            // The relayout will trigger the plotly_relayout event which updates stats"""
    
    new_apply = """            // Update current window and re-render with dynamic normalization
            currentWindowStart = startHours;
            currentWindowEnd = endHours;
            renderNormalizedPaths(startHours, endHours, false);
            
            // Stats and distros already updated by renderNormalizedPaths"""
    
    html = html.replace(old_apply, new_apply)
    
    # Fix stats envelope to use dynamic window start instead of FIXED_WINDOW_START
    html = html.replace(
        "const statsStartTime = FIXED_WINDOW_START;  // Always start from 3pm T-1",
        "const statsStartTime = windowStart;  // Use dynamic window start"
    )
    
    # Pass windowStart to calculateEnvelopeStats for normalization
    html = html.replace(
        "const envelopeStats = calculateEnvelopeStats(rawPathsData, windowStart, windowEnd, null);",
        "const envelopeStats = calculateEnvelopeStats(rawPathsData, windowStart, windowEnd, windowStart);"
    )
    
    # Remove the automatic stats recalculation at the end of renderNormalizedPaths
    # since recalculateAll handles this explicitly
    html = html.replace(
        "// Stats always use current time to event, not visible chart range\n                calculateStatsForRange(CHART_WINDOW_START, CHART_WINDOW_END);",
        "// Stats calculation handled by recalculateAll button\n                // calculateStatsForRange skipped here"
    )
    
    return html


def _strip_live_panel(html: str) -> str:
    pattern = r"<!-- LIVE STATS PANEL -->.*?<div class=\"controls\">"
    return re.sub(pattern, "<div class=\"controls\">", html, flags=re.DOTALL)


def _force_historical_stats(html: str) -> str:
    """Use full pre-event window for stats (no 'now'/'live' calculations)."""
    # Update stats window label from "Now→NFP" to "Full Window"
    html = html.replace("Stats Window (Now→NFP)", "Stats Window (Full)")
    html = html.replace("Now→NFP", "3pm T-1 → 8:30am")
    # Initial stats calculation uses the chart window constants (this is fine for initial load)
    # Update the stats window display to show full window instead of "now → NFP"
    html = re.sub(
        r"const currentHoursForStats = getCurrentHoursFromNFP\(\);",
        "const currentHoursForStats = CHART_WINDOW_START;",
        html,
    )
    html = re.sub(
        r"statsWindowElem\.textContent = `\$\{nowStr\} → 8:30am \(\$\{hoursRemaining\}h\)`;",
        "statsWindowElem.textContent = '3pm T-1 → 8:30am (17.5h)';",
        html,
    )
    # Disable live polling completely (causes window reset in standalone mode)
    html = html.replace(
        "Live polling started (10s interval)",
        "Live polling disabled (standalone mode)"
    )
    html = html.replace(
        "setInterval(fetchLiveData, 10000);",
        "// setInterval(fetchLiveData, 10000); // Disabled for standalone"
    )
    # Disable initial fetch too
    html = html.replace(
        "// Initial fetch\n            fetchLiveData();",
        "// Initial fetch disabled for standalone\n            // fetchLiveData();"
    )
    # Fix the renderNormalizedPaths call in updateLivePanel to use currentWindowStart/End
    html = html.replace(
        "renderNormalizedPaths(FIXED_WINDOW_START, FIXED_WINDOW_END, false);",
        "renderNormalizedPaths(currentWindowStart, currentWindowEnd, false);"
    )
    # Also disable syncTimeInputsFromRange to prevent dropdown reset
    html = html.replace(
        "syncTimeInputsFromRange(startHours, endHours);",
        "// syncTimeInputsFromRange(startHours, endHours); // Disabled to preserve user selection"
    )
    return html


def _normalize_bps_conversions(html: str) -> str:
    # Convert legacy TY conversion (*32/2 or *16) to rules-based 1pt=8bps.
    html = html.replace("* 32 / 2", "* 8")
    html = re.sub(r"\*\s*16", "* 8", html)
    return html


def _add_distro_debug(html: str) -> str:
    """Add debugging to updateDistributionCharts (disabled for production)."""
    # Debug disabled - just return html unchanged
    return html


def _add_all_events_option(html: str) -> str:
    """Add 'Show All Events' checkbox to bypass bucket filtering."""
    # Add checkbox after the bucket width control
    checkbox_html = '''
            <div class="control-group">
                <label>Filter</label>
                <label class="checkbox-label" style="margin-top: 5px;">
                    <input type="checkbox" id="showAllEvents" onchange="updateDashboard()">
                    <span>All Events (No Bucket Filter)</span>
                </label>
            </div>'''
    
    # Insert before the "Update Analysis" button
    html = html.replace(
        '''<div class="control-group">
                <label>&nbsp;</label>
                <button class="btn btn-primary" onclick="updateDashboard()">Update Analysis</button>
            </div>''',
        checkbox_html + '''
            <div class="control-group">
                <label>&nbsp;</label>
                <button class="btn btn-primary" onclick="updateDashboard()">Update Analysis</button>
            </div>'''
    )
    
    # Modify updateDashboard to check for "All Events" mode
    old_bucket_logic = '''            // Find user's bucket
            const userBucketIdx = assignToBucket(userConsStd, buckets);
            const userBucket = buckets[userBucketIdx];'''
    
    new_bucket_logic = '''            // Check if "All Events" mode is enabled
            const showAllEvents = document.getElementById('showAllEvents')?.checked ?? false;
            
            // Find user's bucket (or use all events if showAllEvents is checked)
            const userBucketIdx = assignToBucket(userConsStd, buckets);
            let userBucket;
            
            if (showAllEvents) {
                // Create a synthetic bucket with ALL events
                const allEvents = [];
                buckets.forEach(b => allEvents.push(...b.events));
                userBucket = {
                    label: 'All Events',
                    min: consStdStats.min,
                    max: consStdStats.max,
                    events: allEvents
                };
            } else {
                userBucket = buckets[userBucketIdx];
            }'''
    
    html = html.replace(old_bucket_logic, new_bucket_logic)
    
    # Update the bucket info display to show "All Events" when in that mode
    old_bucket_info = '''<p><strong>Your bucket:</strong> ${userBucket.label} (Cons Std Dev = ${userConsStd})</p>'''
    new_bucket_info = '''<p><strong>${showAllEvents ? 'Mode: All Events' : 'Your bucket: ' + userBucket.label}</strong> ${showAllEvents ? '' : '(Cons Std Dev = ' + userConsStd + ')'}</p>'''
    
    html = html.replace(old_bucket_info, new_bucket_info)
    
    return html


def _fix_boxplot_category_order(html: str) -> str:
    """Ensure boxplot x-axis category order matches bucket labels for aligned annotations."""
    pattern = r"xaxis:\s*\{([^}]*title:\s*'Consensus Std Dev Bucket'[^}]*)\}"
    replacement = (
        "xaxis: {\\1, categoryorder: 'array', categoryarray: buckets.map(b => b.label) }"
    )
    return re.sub(pattern, replacement, html)


def build_standalone_html(
    template_path: Path,
    output_path: Path,
    lookback_days: int,
    min_year: int,
    cons_std_pct_min: float,
    cons_std_pct_max: float,
) -> Path:
    event_data = lud.load_event_data("NFP")
    dashboard_data = lud.build_dashboard_data(event_data, event_type="NFP")
    events = dashboard_data.get("events", [])
    if min_year:
        cutoff = f"{min_year:04d}-01-01"
        events = [e for e in events if e.get("date", "") >= cutoff]
    events = _filter_extreme_cons_std(events, cons_std_pct_min, cons_std_pct_max)
    _sort_event_series(events)

    cons_std_stats = lud.compute_cons_std_stats(events) if events else {"min": 0, "max": 0, "mean": 0, "median": 0}

    chart_start = _compute_chart_window_start(lookback_days)
    generated_hours = round(lud.get_current_hours_from_event(), 6)

    html = template_path.read_text(encoding="utf-8")
    html = _inject_events(html, events, cons_std_stats)
    html = _inject_window_constants(html, chart_start, generated_hours)
    html = _inject_expected_rally_selloff(html)
    html = _normalize_bps_conversions(html)
    html = _fix_boxplot_category_order(html)
    html = _strip_live_panel(html)
    html = _strip_live_labels(html)
    html = _force_historical_stats(html)
    html = _enable_time_picker(html)
    html = _add_dynamic_normalization(html)
    html = _add_distro_debug(html)
    html = _add_recalculate_all_button(html)
    html = _add_all_events_option(html)

    output_path.write_text(html, encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build standalone pre-event NFP HTML from legacy template.")
    parser.add_argument("--template", type=str, default=str(DEFAULT_TEMPLATE), help="Path to legacy HTML template")
    parser.add_argument("--lookback-days", type=int, default=1, help="Window start at 3pm T-(days)")
    parser.add_argument("--min-year", type=int, default=2022, help="Minimum event year to include")
    parser.add_argument("--cons-std-pct-min", type=float, default=10.0, help="Lower percentile cutoff for cons std")
    parser.add_argument("--cons-std-pct-max", type=float, default=90.0, help="Upper percentile cutoff for cons std")
    parser.add_argument("--out", type=str, default=None, help="Output HTML path")
    args = parser.parse_args()

    template_path = Path(args.template)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        output_path = Path(args.out)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"preevent_nfp_dashboard_{ts}.html"

    out = build_standalone_html(
        template_path,
        output_path,
        args.lookback_days,
        args.min_year,
        args.cons_std_pct_min,
        args.cons_std_pct_max,
    )
    print(f"Standalone pre-event HTML saved to: {out}")


if __name__ == "__main__":
    main()

