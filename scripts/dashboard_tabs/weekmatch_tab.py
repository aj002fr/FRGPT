"""
Week Match Analysis Tab Module
------------------------------
Generates HTML and JavaScript for the week match analysis tab.
Allows users to upload an Excel calendar, view events by tier,
find matching historical weeks, and visualize price paths.
"""


def generate_weekmatch_tab_html(config: dict) -> str:
    """
    Generate HTML content for the week match analysis tab.
    
    Args:
        config: Dictionary containing configuration options
    
    Returns:
        HTML string for the week match tab content
    """
    return '''        <!-- WEEK MATCH ANALYSIS TAB -->
        <div class="tab-content" id="weekmatchTab">
            <!-- File Upload Section -->
            <div class="weekmatch-upload-section">
                <div class="upload-dropzone" id="weekmatchDropzone">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" style="width: 48px; height: 48px; margin-bottom: 12px; color: var(--accent-teal);">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                    </svg>
                    <p style="font-size: 1.1rem; color: var(--text-primary); margin-bottom: 8px;">
                        Drop Excel Calendar Here
                    </p>
                    <p style="font-size: 0.9rem; color: var(--text-secondary);">
                        or <label for="weekmatchFileInput" style="color: var(--accent-teal); cursor: pointer; text-decoration: underline;">browse files</label>
                    </p>
                    <input type="file" id="weekmatchFileInput" accept=".xlsx,.xls" style="display: none;" onchange="handleWeekMatchFileSelect(event)" />
                </div>
                <div id="weekmatchUploadStatus" style="display: none; margin-top: 15px; padding: 12px 20px; background: var(--bg-tertiary); border-radius: 8px;">
                    <span id="weekmatchFileName" style="color: var(--accent-teal);"></span>
                    <button onclick="clearWeekMatchUpload()" style="margin-left: 15px; background: none; border: none; color: var(--text-secondary); cursor: pointer;">✕ Clear</button>
                </div>
                
                <!-- Date Range Filter -->
                <div class="weekmatch-date-filter" style="margin-top: 20px; padding: 15px 20px; background: var(--bg-tertiary); border-radius: 8px;">
                    <div style="display: flex; align-items: center; gap: 20px; flex-wrap: wrap;">
                        <span style="color: var(--text-secondary); font-weight: 500;">Filter Events by Date Range:</span>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <label style="color: var(--text-secondary); font-size: 0.9rem;">From:</label>
                            <input type="date" id="weekmatchDateFrom" style="background: var(--bg-secondary); border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px 12px; border-radius: 6px;" />
                        </div>
                        <div style="display: flex; align-items: center; gap: 10px;">
                            <label style="color: var(--text-secondary); font-size: 0.9rem;">To:</label>
                            <input type="date" id="weekmatchDateTo" style="background: var(--bg-secondary); border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px 12px; border-radius: 6px;" />
                        </div>
                        <button id="weekmatchApplyFilter" onclick="reprocessWeekMatchFile()" style="background: var(--accent-teal); border: none; color: white; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 500;" disabled>
                            Apply Filter
                        </button>
                    </div>
                    <p style="margin-top: 8px; font-size: 0.8rem; color: var(--text-secondary);">
                        Only events within this date range will be analyzed
                    </p>
                </div>
            </div>
            
            <!-- Parsed Events Display -->
            <div class="weekmatch-events-panel" id="weekmatchEventsPanel" style="display: none;">
                <h2>
                    Uploaded Week Events
                    <span class="badge" id="weekmatchEventCount">0 events</span>
                </h2>
                <div class="tier-legend" style="display: flex; gap: 20px; margin-bottom: 15px; padding: 10px 15px; background: var(--bg-tertiary); border-radius: 8px;">
                    <span style="display: flex; align-items: center; gap: 6px;">
                        <span style="width: 14px; height: 14px; background: #fef08a; border-radius: 3px;"></span>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">Tier 1</span>
                    </span>
                    <span style="display: flex; align-items: center; gap: 6px;">
                        <span style="width: 14px; height: 14px; background: #fbcfe8; border-radius: 3px;"></span>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">Tier 2</span>
                    </span>
                    <span style="display: flex; align-items: center; gap: 6px;">
                        <span style="width: 14px; height: 14px; background: #93c5fd; border-radius: 3px;"></span>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">Tier 3</span>
                    </span>
                    <span style="display: flex; align-items: center; gap: 6px;">
                        <span style="width: 14px; height: 14px; background: #e5e7eb; border-radius: 3px;"></span>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">Tier 4</span>
                    </span>
                </div>
                <div id="weekmatchEventsList" class="events-grid" style="max-height: 400px; overflow-y: auto;">
                    <!-- Events will be populated here -->
                </div>
                <div style="margin-top: 20px; text-align: center;">
                    <button class="btn-load" id="findMatchesBtn" onclick="findMatchingWeeks()" disabled>
                        Find Matching Historical Weeks
                    </button>
                </div>
            </div>
            
            <!-- Matching Weeks Results -->
            <div class="weekmatch-results-panel" id="weekmatchResultsPanel" style="display: none;">
                <h2>
                    Matching Historical Weeks
                    <span class="badge" id="weekmatchResultsCount">0 matches</span>
                </h2>
                <div class="weekmatch-results-grid" id="weekmatchResultsGrid">
                    <!-- Results cards will be populated here -->
                </div>
            </div>
            
            <!-- Price Paths Chart -->
            <div class="weekmatch-chart-panel" id="weekmatchChartPanel" style="display: none;">
                <h2>
                    Price Paths (Normalized to Week Start)
                    <span class="badge" style="background: var(--accent-teal);">bps</span>
                </h2>
                <div style="display: flex; gap: 15px; margin-bottom: 10px; padding: 8px 12px; background: var(--bg-tertiary); border-radius: 6px; font-size: 0.85rem; color: var(--text-secondary);">
                    <span>📍 All paths normalized to 0 at Monday 6pm open</span>
                    <span>|</span>
                    <span>🖱️ Click a line to view candlestick chart</span>
                </div>
                <div id="weekmatchPriceChart" style="height: 500px;">
                    <div class="empty-state">
                        <p>Select weeks above to view price paths</p>
                    </div>
                </div>
            </div>
            
            <!-- Candlestick Modal for Selected Week -->
            <div class="weekmatch-candle-panel" id="weekmatchCandlePanel" style="display: none;">
                <h2>
                    Candlestick Chart
                    <span class="badge" id="weekmatchCandleWeek">--</span>
                    <button onclick="closeWeekMatchCandle()" style="margin-left: auto; background: none; border: none; color: var(--text-secondary); cursor: pointer; font-size: 1.2rem;">✕</button>
                </h2>
                <div id="weekmatchCandleChart" style="height: 450px;"></div>
            </div>
        </div>'''


def generate_weekmatch_tab_js() -> str:
    """
    Generate JavaScript functions for the week match analysis tab.
    
    Returns:
        JavaScript string with all week match tab functions
    """
    return '''        // Week Match tab state
        let weekmatchUploadedEvents = [];
        let weekmatchMatchingWeeks = [];
        let weekmatchSelectedWeeks = new Set();
        let weekmatchUploadedFile = null;  // Store file for reprocessing with date range
        
        // Tier color mapping for display
        const TIER_COLORS = {
            1: '#fef08a',  // Yellow
            2: '#fbcfe8',  // Pink
            3: '#93c5fd',  // Light Blue
            4: '#e5e7eb'   // Gray
        };
        
        const TIER_BORDER_COLORS = {
            1: '#eab308',
            2: '#ec4899',
            3: '#3b82f6',
            4: '#9ca3af'
        };
        
        // Initialize dropzone drag-and-drop
        function initWeekMatchDropzone() {
            const dropzone = document.getElementById('weekmatchDropzone');
            if (!dropzone) return;
            
            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
                dropzone.addEventListener(eventName, preventDefaults, false);
            });
            
            function preventDefaults(e) {
                e.preventDefault();
                e.stopPropagation();
            }
            
            ['dragenter', 'dragover'].forEach(eventName => {
                dropzone.addEventListener(eventName, () => {
                    dropzone.style.borderColor = 'var(--accent-teal)';
                    dropzone.style.background = 'rgba(20, 184, 166, 0.1)';
                }, false);
            });
            
            ['dragleave', 'drop'].forEach(eventName => {
                dropzone.addEventListener(eventName, () => {
                    dropzone.style.borderColor = 'var(--border-color)';
                    dropzone.style.background = 'var(--bg-tertiary)';
                }, false);
            });
            
            dropzone.addEventListener('drop', handleWeekMatchDrop, false);
        }
        
        function handleWeekMatchDrop(e) {
            const files = e.dataTransfer.files;
            if (files.length > 0) {
                processWeekMatchFile(files[0]);
            }
        }
        
        function handleWeekMatchFileSelect(e) {
            const files = e.target.files;
            if (files.length > 0) {
                processWeekMatchFile(files[0]);
            }
        }
        
        async function processWeekMatchFile(file, useDateRange = false) {
            if (!file.name.match(/\\.xlsx?$/i)) {
                alert('Please upload an Excel file (.xlsx or .xls)');
                return;
            }
            
            // Store file for reprocessing
            weekmatchUploadedFile = file;
            
            // Show loading state
            const statusDiv = document.getElementById('weekmatchUploadStatus');
            const fileNameSpan = document.getElementById('weekmatchFileName');
            fileNameSpan.textContent = `Processing ${file.name}...`;
            statusDiv.style.display = 'block';
            
            // Read file and send to server for parsing
            const formData = new FormData();
            formData.append('file', file);
            
            // Add date range if specified
            const dateFrom = document.getElementById('weekmatchDateFrom').value;
            const dateTo = document.getElementById('weekmatchDateTo').value;
            if (dateFrom) formData.append('date_from', dateFrom);
            if (dateTo) formData.append('date_to', dateTo);
            
            try {
                const response = await fetch('/api/weekmatch/parse', {
                    method: 'POST',
                    body: formData
                });
                
                const data = await response.json();
                
                if (!data.success) {
                    throw new Error(data.error || 'Failed to parse file');
                }
                
                const dateRangeInfo = (dateFrom || dateTo) ? ` [${dateFrom || '...'} to ${dateTo || '...'}]` : '';
                fileNameSpan.textContent = `${file.name} (${data.events.length} events${dateRangeInfo})`;
                weekmatchUploadedEvents = data.events;
                
                // Display parsed events
                displayWeekMatchEvents(data.events);
                
                // Enable buttons
                document.getElementById('findMatchesBtn').disabled = false;
                document.getElementById('weekmatchApplyFilter').disabled = false;
                
            } catch (err) {
                console.error('Error parsing file:', err);
                alert('Error parsing file: ' + err.message);
                fileNameSpan.textContent = `Error: ${err.message}`;
            }
        }
        
        // Reprocess the uploaded file with new date range
        async function reprocessWeekMatchFile() {
            if (!weekmatchUploadedFile) {
                alert('Please upload a file first');
                return;
            }
            
            // Clear previous results
            weekmatchMatchingWeeks = [];
            weekmatchSelectedWeeks.clear();
            document.getElementById('weekmatchResultsPanel').style.display = 'none';
            document.getElementById('weekmatchChartPanel').style.display = 'none';
            document.getElementById('weekmatchCandlePanel').style.display = 'none';
            
            // Reprocess with date range
            await processWeekMatchFile(weekmatchUploadedFile, true);
        }
        
        function displayWeekMatchEvents(events) {
            const panel = document.getElementById('weekmatchEventsPanel');
            const list = document.getElementById('weekmatchEventsList');
            const countBadge = document.getElementById('weekmatchEventCount');
            
            // Group events by day
            const byDay = {};
            events.forEach(evt => {
                if (!byDay[evt.day]) byDay[evt.day] = [];
                byDay[evt.day].push(evt);
            });
            
            let html = '';
            const dayOrder = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
            
            dayOrder.forEach(day => {
                if (!byDay[day]) return;
                
                html += `<div class="day-group" style="margin-bottom: 20px;">
                    <h4 style="color: var(--accent-teal); margin-bottom: 10px; font-size: 0.95rem; border-bottom: 1px solid var(--border-color); padding-bottom: 5px;">${day}</h4>
                    <div class="day-events" style="display: flex; flex-direction: column; gap: 6px;">`;
                
                byDay[day].sort((a, b) => a.time.localeCompare(b.time)).forEach(evt => {
                    const tierColor = TIER_COLORS[evt.tier] || TIER_COLORS[4];
                    const borderColor = TIER_BORDER_COLORS[evt.tier] || TIER_BORDER_COLORS[4];
                    
                    html += `<div class="event-item" style="
                        display: grid;
                        grid-template-columns: 60px 50px 1fr;
                        gap: 12px;
                        padding: 8px 12px;
                        background: ${tierColor}22;
                        border-left: 3px solid ${borderColor};
                        border-radius: 4px;
                        align-items: center;
                    ">
                        <span style="color: var(--text-secondary); font-size: 0.85rem; font-family: monospace;">${evt.time}</span>
                        <span style="color: var(--text-secondary); font-size: 0.8rem; font-weight: 600;">${evt.country}</span>
                        <span style="color: var(--text-primary); font-size: 0.9rem;">${evt.name}</span>
                    </div>`;
                });
                
                html += '</div></div>';
            });
            
            list.innerHTML = html;
            countBadge.textContent = `${events.length} events`;
            panel.style.display = 'block';
        }
        
        function clearWeekMatchUpload() {
            weekmatchUploadedEvents = [];
            weekmatchMatchingWeeks = [];
            weekmatchSelectedWeeks.clear();
            
            document.getElementById('weekmatchUploadStatus').style.display = 'none';
            document.getElementById('weekmatchEventsPanel').style.display = 'none';
            document.getElementById('weekmatchResultsPanel').style.display = 'none';
            document.getElementById('weekmatchChartPanel').style.display = 'none';
            document.getElementById('weekmatchCandlePanel').style.display = 'none';
            document.getElementById('weekmatchFileInput').value = '';
            document.getElementById('findMatchesBtn').disabled = true;
        }
        
        async function findMatchingWeeks() {
            if (weekmatchUploadedEvents.length === 0) {
                alert('Please upload an Excel calendar first');
                return;
            }
            
            const btn = document.getElementById('findMatchesBtn');
            btn.textContent = 'Searching...';
            btn.disabled = true;
            
            try {
                const response = await fetch('/api/weekmatch/candidates', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ events: weekmatchUploadedEvents })
                });
                
                const data = await response.json();
                
                if (!data.success) {
                    throw new Error(data.error || 'Failed to find matches');
                }
                
                weekmatchMatchingWeeks = data.candidates;
                displayMatchingWeeks(data.candidates);
                
            } catch (err) {
                console.error('Error finding matches:', err);
                alert('Error: ' + err.message);
            } finally {
                btn.textContent = 'Find Matching Historical Weeks';
                btn.disabled = false;
            }
        }
        
        function displayMatchingWeeks(candidates) {
            const panel = document.getElementById('weekmatchResultsPanel');
            const grid = document.getElementById('weekmatchResultsGrid');
            const countBadge = document.getElementById('weekmatchResultsCount');
            
            if (candidates.length === 0) {
                grid.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 40px;">No matching weeks found</div>';
                countBadge.textContent = '0 matches';
                panel.style.display = 'block';
                return;
            }
            
            let html = '';
            candidates.forEach((week, idx) => {
                const isSelected = weekmatchSelectedWeeks.has(week.week_start);
                
                html += `<div class="week-match-card ${isSelected ? 'selected' : ''}" 
                    data-week="${week.week_start}"
                    onclick="toggleWeekSelection('${week.week_start}')"
                    style="
                        background: var(--bg-secondary);
                        border: 2px solid ${isSelected ? 'var(--accent-teal)' : 'var(--border-color)'};
                        border-radius: 10px;
                        padding: 15px;
                        cursor: pointer;
                        transition: all 0.2s;
                    ">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <span style="font-weight: 600; color: var(--accent-teal);">Week of ${week.week_start}</span>
                        <span class="badge" style="background: ${week.match_pct >= 80 ? 'var(--accent-green)' : week.match_pct >= 60 ? 'var(--accent-orange)' : 'var(--accent-red)'};">
                            ${week.total_matched}/${weekmatchUploadedEvents.length} (${week.match_pct.toFixed(0)}%)
                        </span>
                    </div>
                    
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; font-size: 0.8rem;">
                        <div style="text-align: center;">
                            <div style="color: var(--text-secondary);">Matched</div>
                            <div style="display: flex; justify-content: center; gap: 4px; margin-top: 4px;">
                                ${[1,2,3,4].map(t => `<span style="background: ${TIER_COLORS[t]}; color: #333; padding: 2px 6px; border-radius: 3px; font-weight: 600;">${week.matched_by_tier[t] || 0}</span>`).join('')}
                            </div>
                        </div>
                        <div style="text-align: center;">
                            <div style="color: var(--text-secondary);">Missing</div>
                            <div style="display: flex; justify-content: center; gap: 4px; margin-top: 4px;">
                                ${[1,2,3,4].map(t => `<span style="background: ${TIER_COLORS[t]}44; color: var(--text-primary); padding: 2px 6px; border-radius: 3px;">${week.missing_by_tier[t] || 0}</span>`).join('')}
                            </div>
                        </div>
                        <div style="text-align: center;">
                            <div style="color: var(--text-secondary);">Extra</div>
                            <div style="display: flex; justify-content: center; gap: 4px; margin-top: 4px;">
                                ${[1,2,3,4].map(t => `<span style="background: ${TIER_COLORS[t]}44; color: var(--text-primary); padding: 2px 6px; border-radius: 3px;">${week.extra_by_tier[t] || 0}</span>`).join('')}
                            </div>
                        </div>
                    </div>
                </div>`;
            });
            
            grid.innerHTML = html;
            countBadge.textContent = `${candidates.length} matches`;
            panel.style.display = 'block';
            
            // Show chart panel
            document.getElementById('weekmatchChartPanel').style.display = 'block';
        }
        
        function toggleWeekSelection(weekStart) {
            if (weekmatchSelectedWeeks.has(weekStart)) {
                weekmatchSelectedWeeks.delete(weekStart);
            } else {
                weekmatchSelectedWeeks.add(weekStart);
            }
            
            // Update card styling
            document.querySelectorAll('.week-match-card').forEach(card => {
                const isSelected = weekmatchSelectedWeeks.has(card.dataset.week);
                card.classList.toggle('selected', isSelected);
                card.style.borderColor = isSelected ? 'var(--accent-teal)' : 'var(--border-color)';
            });
            
            // Fetch and render price paths for selected weeks
            if (weekmatchSelectedWeeks.size > 0) {
                fetchAndRenderPricePaths();
            } else {
                clearPricePathsChart();
            }

            applyWeekMatchFilterToDashboard();
        }

        function applyWeekMatchFilterToDashboard() {
            if (typeof applyWeekMatchFilter !== 'function') {
                return;
            }
            const weeks = Array.from(weekmatchSelectedWeeks);
            if (!weeks.length) {
                applyWeekMatchFilter([]);
            } else {
                applyWeekMatchFilter(weeks, { mode: 'weekmatch', weeks_matched: weeks.length });
            }
            if (typeof updatePreeventDashboard === 'function') {
                updatePreeventDashboard();
            }
        }
        
        async function fetchAndRenderPricePaths() {
            const weeks = Array.from(weekmatchSelectedWeeks);
            
            try {
                const response = await fetch('/api/weekmatch/pricepaths', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ weeks: weeks })
                });
                
                const data = await response.json();
                
                if (!data.success) {
                    throw new Error(data.error || 'Failed to fetch price paths');
                }
                
                renderPricePathsChart(data.price_paths);
                
            } catch (err) {
                console.error('Error fetching price paths:', err);
            }
        }
        
        // Store raw price data for dynamic normalization
        let weekmatchRawPrices = {};
        let weekmatchSeriesByWeek = {}; // weekStart -> {genericTs, genericIso, close, pDate}
        let weekmatchRelayoutTimer = null;
        let weekmatchIsRelayouting = false;
        
        // BPS conversion function (uses existing constants from main dashboard)
        // TICKS_PER_POINT = 64, TICKS_PER_BPS = 4
        const weekmatchDecimalToBps = (move) => (move * 64) / 4;
        
        function renderPricePathsChart(pricePaths, windowStartIdx = null, windowEndIdx = null) {
            const chartDiv = document.getElementById('weekmatchPriceChart');
            
            if (!pricePaths || Object.keys(pricePaths).length === 0) {
                chartDiv.innerHTML = '<div class="empty-state"><p>No price data available for selected weeks</p></div>';
                return;
            }
            
            // Store raw data for re-normalization
            weekmatchRawPrices = pricePaths;
            weekmatchSeriesByWeek = {};
            
            const traces = [];
            const colors = [
                '#1d9bf0', '#00ba7c', '#f4212e', '#ff7a00', '#9b59b6',
                '#14b8a6', '#e879f9', '#fbbf24', '#64748b', '#ec4899'
            ];
            
            let colorIdx = 0;
            let allTimestamps = [];
            
            for (const [weekStart, data] of Object.entries(pricePaths)) {
                if (!data.prices || data.prices.length === 0) continue;
                
                const prices = data.prices;
                
                // Convert timestamps to "generic week" - all weeks align to same day-of-week
                const weekStartDate = new Date(weekStart + 'T08:00:00');
                const baseWeekStart = new Date('2000-01-03T08:00:00'); // A Monday
                
                // Build generic timestamps for all prices
                const pricesWithGeneric = prices.map(p => {
                    const pDate = new Date(p.ts);
                    const offsetMs = pDate.getTime() - weekStartDate.getTime();
                    const genericDate = new Date(baseWeekStart.getTime() + offsetMs);
                    return { ...p, genericTs: genericDate.getTime(), genericIso: genericDate.toISOString(), pDate };
                });
                weekmatchSeriesByWeek[weekStart] = pricesWithGeneric;
                
                // Filter by window if specified (using generic timestamps)
                let filtered = pricesWithGeneric;
                if (windowStartIdx !== null && windowEndIdx !== null) {
                    filtered = pricesWithGeneric.filter(p => 
                        p.genericTs >= windowStartIdx && p.genericTs <= windowEndIdx
                    );
                }
                
                if (filtered.length === 0) continue;
                
                // Use server-calculated bps, adjusted for window start
                // Server normalizes to Monday 8am, we adjust for window start if needed
                const refBps = filtered[0].bps || 0;  // First point in window becomes 0
                
                const xData = [];
                const yData = [];
                const hoverTexts = [];
                
                filtered.forEach(p => {
                    xData.push(p.genericIso);
                    // Use server bps, offset by reference point
                    const bps = (p.bps || 0) - refBps;
                    yData.push(bps);
                    hoverTexts.push(`<b>${weekStart}</b><br>${p.pDate.toLocaleDateString()} ${p.pDate.toLocaleTimeString()}<br>${bps.toFixed(1)} bps`);
                    
                    allTimestamps.push(p.genericTs);
                });
                
                traces.push({
                    x: xData,
                    y: yData,
                    type: 'scatter',
                    mode: 'lines',
                    name: weekStart,
                    line: { width: 1.5, color: colors[colorIdx % colors.length] },
                    text: hoverTexts,
                    hoverinfo: 'text',
                    customdata: Array(xData.length).fill({ weekStart: weekStart })
                });
                colorIdx++;
            }
            
            if (traces.length === 0) {
                chartDiv.innerHTML = '<div class="empty-state"><p>No price data in selected window</p></div>';
                return;
            }
            
            // Determine x-axis range
            const minTs = Math.min(...allTimestamps);
            const maxTs = Math.max(...allTimestamps);
            
            const layout = {
                ...darkLayout,
                title: 'Normalized Weekly Price Paths (Mon 8am - Fri 6pm)',
                xaxis: {
                    ...darkLayout.xaxis,
                    title: '',
                    type: 'date',
                    tickformat: '%a %H:%M',
                    dtick: 6 * 3600 * 1000,  // Tick every 6 hours
                    tickangle: -45,
                    range: [new Date(minTs).toISOString(), new Date(maxTs).toISOString()],
                },
                yaxis: {
                    ...darkLayout.yaxis,
                    title: 'Move (bps)',
                    zeroline: true,
                    zerolinecolor: '#4b5563',
                },
                showlegend: true,
                legend: {
                    orientation: 'h',
                    y: -0.25,
                    x: 0.5,
                    xanchor: 'center'
                },
                hovermode: 'closest',
                margin: { t: 50, r: 40, b: 80, l: 60 },
            };
            
            const config = {
                responsive: true,
                scrollZoom: true,
                displayModeBar: true,
                modeBarButtonsToAdd: ['pan2d', 'zoom2d', 'zoomIn2d', 'zoomOut2d', 'resetScale2d'],
            };

            renderPathsChart('weekmatchPriceChart', traces, layout, config, {
                click: handlePricePathClick,
                relayout: handleWeekMatchRelayout,
            });
        }

        function handleWeekMatchRelayout(eventData) {
            if (weekmatchIsRelayouting) return;
            if (!weekmatchSeriesByWeek || Object.keys(weekmatchSeriesByWeek).length === 0) return;

            let newStart = null;
            let newEnd = null;
            if (eventData['xaxis.range']) {
                newStart = new Date(eventData['xaxis.range'][0]).getTime();
                newEnd = new Date(eventData['xaxis.range'][1]).getTime();
            } else if (eventData['xaxis.range[0]'] !== undefined) {
                newStart = new Date(eventData['xaxis.range[0]']).getTime();
                newEnd = new Date(eventData['xaxis.range[1]']).getTime();
            } else if (eventData['xaxis.autorange']) {
                // Reset to full window normalization
                newStart = null;
                newEnd = null;
            } else {
                return;
            }

            if (weekmatchRelayoutTimer) {
                clearTimeout(weekmatchRelayoutTimer);
            }

            weekmatchRelayoutTimer = setTimeout(() => {
                weekmatchIsRelayouting = true;
                try {
                    const traceNames = [];
                    const yUpdates = [];
                    const textUpdates = [];

                    Object.keys(weekmatchSeriesByWeek).forEach((weekStart) => {
                        const series = weekmatchSeriesByWeek[weekStart] || [];
                        let filtered = series;
                        if (newStart !== null && newEnd !== null) {
                            filtered = series.filter(p => p.genericTs >= newStart && p.genericTs <= newEnd);
                        }
                        if (!filtered.length) {
                            yUpdates.push([]);
                            textUpdates.push([]);
                            traceNames.push(weekStart);
                            return;
                        }

                        const refBps = filtered[0].bps || 0;
                        const yData = [];
                        const hoverTexts = [];
                        filtered.forEach(p => {
                            const bps = (p.bps || 0) - refBps;
                            yData.push(bps);
                            hoverTexts.push(`<b>${weekStart}</b><br>${p.pDate.toLocaleDateString()} ${p.pDate.toLocaleTimeString()}<br>${bps.toFixed(1)} bps`);
                        });

                        yUpdates.push(yData);
                        textUpdates.push(hoverTexts);
                        traceNames.push(weekStart);
                    });

                    const chartDiv = document.getElementById('weekmatchPriceChart');
                    const traceIndices = chartDiv.data
                        .map((t, idx) => ({ name: t.name, idx }))
                        .filter(t => traceNames.includes(t.name))
                        .map(t => t.idx);

                    // Restyle traces in-place (avoid full replot)
                    Plotly.restyle('weekmatchPriceChart', { y: yUpdates, text: textUpdates }, traceIndices);
                } finally {
                    weekmatchIsRelayouting = false;
                }
            }, 120);
        }
        
        function clearPricePathsChart() {
            weekmatchRawPrices = {};
            document.getElementById('weekmatchPriceChart').innerHTML = '<div class="empty-state"><p>Select weeks above to view price paths</p></div>';
        }
        
        function handlePricePathClick(clickData) {
            if (!clickData.points || clickData.points.length === 0) return;
            
            const traceName = clickData.points[0].data.name;
            if (traceName === 'Reference') return;
            
            // Show candlestick for this week
            showWeekCandlestick(traceName);
        }
        
        async function showWeekCandlestick(weekStart) {
            const panel = document.getElementById('weekmatchCandlePanel');
            const badge = document.getElementById('weekmatchCandleWeek');
            const chartDiv = document.getElementById('weekmatchCandleChart');
            
            badge.textContent = `Week of ${weekStart}`;
            chartDiv.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-secondary);">Loading candlestick data...</div>';
            panel.style.display = 'block';
            
            try {
                const response = await fetch(`/api/weekmatch/candlestick/${weekStart}`);
                const data = await response.json();
                
                if (!data.success) {
                    throw new Error(data.error || 'Failed to fetch candlestick data');
                }
                
                renderWeekCandlestick(data, weekStart);
                
            } catch (err) {
                console.error('Error fetching candlestick:', err);
                chartDiv.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--accent-red);">Error: ${err.message}</div>`;
            }
        }
        
        function renderWeekCandlestick(data, weekStart) {
            const candles = data.candlestick;
            const events = data.events || [];
            
            if (!candles || candles.length === 0) {
                document.getElementById('weekmatchCandleChart').innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-secondary);">No candlestick data available</div>';
                return;
            }
            
            // Add event markers
            const shapes = [];
            const annotations = [];
            
            events.forEach((evt, idx) => {
                const tierColor = TIER_BORDER_COLORS[evt.tier] || TIER_BORDER_COLORS[4];
                
                shapes.push({
                    type: 'line',
                    x0: evt.datetime,
                    x1: evt.datetime,
                    y0: 0, y1: 1, yref: 'paper',
                    line: { color: tierColor, width: 1.5, dash: 'dot' },
                });
                
                if (evt.tier <= 2) {  // Only annotate top 2 tiers
                    annotations.push({
                        x: evt.datetime,
                        y: 1 - (idx % 5) * 0.05,
                        yref: 'paper',
                        text: evt.name.substring(0, 15),
                        showarrow: false,
                        font: { color: tierColor, size: 9 },
                        bgcolor: 'rgba(26, 35, 50, 0.9)',
                        bordercolor: tierColor,
                        borderwidth: 1,
                    });
                }
            });
            
            renderCandlestickChart('weekmatchCandleChart', candles, {
                title: `Week of ${weekStart}`,
                xTitle: 'Time',
                yTitle: 'Price',
                shapes: shapes,
                annotations: annotations,
                showRangeSlider: true,
                height: 450,
            });
        }
        
        function closeWeekMatchCandle() {
            document.getElementById('weekmatchCandlePanel').style.display = 'none';
        }
        
        // Initialize on tab switch
        function initWeekMatchTab() {
            initWeekMatchDropzone();
        }'''


def generate_weekmatch_tab_css() -> str:
    """
    Generate CSS styles specific to the week match tab.
    
    Returns:
        CSS string for week match tab styling
    """
    return '''
        /* Week Match Tab Styles */
        .weekmatch-upload-section {
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 25px;
        }
        
        .upload-dropzone {
            border: 2px dashed var(--border-color);
            border-radius: 12px;
            padding: 40px;
            text-align: center;
            background: var(--bg-tertiary);
            transition: all 0.2s;
            cursor: pointer;
        }
        
        .upload-dropzone:hover {
            border-color: var(--accent-teal);
            background: rgba(20, 184, 166, 0.05);
        }
        
        .weekmatch-events-panel,
        .weekmatch-results-panel,
        .weekmatch-chart-panel,
        .weekmatch-candle-panel {
            background: var(--bg-secondary);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 25px;
        }
        
        .weekmatch-results-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 15px;
            max-height: 400px;
            overflow-y: auto;
            padding: 5px;
        }
        
        .week-match-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }
        
        .week-match-card.selected {
            background: rgba(20, 184, 166, 0.1) !important;
        }
        
        .events-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
'''

