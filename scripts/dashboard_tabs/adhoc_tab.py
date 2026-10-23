"""
Adhoc Analysis Tab Module
-------------------------
Generates HTML and JavaScript for the adhoc analysis tab.
"""


def generate_adhoc_tab_html(config: dict) -> str:
    """
    Generate HTML content for the adhoc analysis tab.
    
    Args:
        config: Dictionary containing:
            - event_short_name: Short name for event (e.g., 'NFP')
    
    Returns:
        HTML string for the adhoc tab content
    """
    event_short_name = config.get('event_short_name', 'NFP')
    
    return f'''        <!-- AD-HOC ANALYSIS TAB -->
        <div class="tab-content" id="adhocTab">
            <div class="adhoc-controls">
                <!-- Individual Date Selection -->
                <div class="date-selector">
                    <h3>Select Individual Dates</h3>
                    <div class="date-input-row">
                        <input type="date" id="adhocDateInput" />
                        <button class="btn-add" onclick="addSelectedDate()">+ Add</button>
                    </div>
                    <div class="selected-dates-list" id="selectedDatesList">
                        <!-- Date tags will be added here dynamically -->
                    </div>
                </div>
                
                <!-- Date Range Selection -->
                <div class="date-selector">
                    <h3>Or Select Date Range</h3>
                    <div class="date-range-row">
                        <input type="date" id="adhocDateFrom" />
                        <span>to</span>
                        <input type="date" id="adhocDateTo" />
                        <button class="btn-add" onclick="addDateRange()">+ Add Range</button>
                    </div>
                    <div style="margin-top: 15px;">
                        <label style="display: flex; align-items: center; gap: 10px; cursor: pointer; color: var(--text-secondary);">
                            <input type="checkbox" id="adhocEventDaysOnly" checked style="width: 18px; height: 18px;">
                            <span>Only include {event_short_name} event days</span>
                        </label>
                    </div>
                </div>
            </div>
            
            <div class="adhoc-action-row" style="background: var(--bg-secondary); border-radius: 12px; padding: 15px 20px; margin-bottom: 25px; border: 1px solid var(--border-color);">
                <button class="btn-load" id="loadAdhocBtn" onclick="loadAdhocData()" disabled>
                    Load & Analyze Selected Dates
                </button>
                <span id="adhocDateCount" style="color: var(--text-secondary);">0 dates selected</span>
                <button class="btn-add" onclick="clearAllDates()" style="margin-left: auto; background: var(--bg-tertiary); color: var(--text-secondary);">
                    Clear All
                </button>
            </div>
            
            <!-- Statistics Panel (hidden until data loaded) -->
            <div class="adhoc-stats-panel" id="adhocStatsPanel" style="display: none;">
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 15px;">
                    <h3 style="margin: 0;">Move Statistics (bps)</h3>
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <label style="color: var(--text-secondary); font-size: 0.9rem;">Duration:</label>
                        <select id="adhocDurationSelect" onchange="updateAdhocStatsForDuration()" 
                                style="background: var(--bg-tertiary); border: 1px solid var(--border-color); color: var(--text-primary); 
                                       padding: 8px 16px; border-radius: 6px; font-size: 0.95rem; cursor: pointer;">
                            <option value="5m">5 minutes</option>
                            <option value="1h" selected>1 hour</option>
                            <option value="1d">Full Session (23h)</option>
                        </select>
                        <span id="adhocDurationInfo" style="color: var(--accent-teal); font-size: 0.85rem; font-weight: 500;">
                            Max move in any 1h window
                        </span>
                    </div>
                </div>
                <div class="stats-grid">
                    <div class="stat-card" style="border-left: 3px solid #ef4444;">
                        <div class="stat-label">Max + Move</div>
                        <div class="stat-value" id="adhocStatMaxUp" style="color: #22c55e;">--</div>
                    </div>
                    <div class="stat-card" style="border-left: 3px solid #ef4444;">
                        <div class="stat-label">Max - Move</div>
                        <div class="stat-value" id="adhocStatMaxDown" style="color: #ef4444;">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">Mean</div>
                        <div class="stat-value" id="adhocStatMean">--</div>
                    </div>
                    <div class="stat-card highlight">
                        <div class="stat-label">Median</div>
                        <div class="stat-value" id="adhocStatMedian">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">P5</div>
                        <div class="stat-value" id="adhocStatP5">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">P25</div>
                        <div class="stat-value" id="adhocStatP25">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">P75</div>
                        <div class="stat-value" id="adhocStatP75">--</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-label">P95</div>
                        <div class="stat-value" id="adhocStatP95">--</div>
                    </div>
                </div>
                <div id="adhocStatsNote" style="margin-top: 12px; padding: 10px 15px; background: var(--bg-tertiary); 
                                                 border-radius: 6px; color: var(--text-secondary); font-size: 0.85rem;">
                    Stats based on <span id="adhocStatsCount">0</span> trading sessions. 
                    Shows max bps move within any <span id="adhocStatsDuration">1 hour</span> window during the session.
                </div>
            </div>
            
            <!-- Charts -->
            <div class="adhoc-charts">
                <!-- Price Paths Chart -->
                <div class="adhoc-panel">
                    <h2>
                        Normalized Price Paths (6pm prior day → 5pm selected day)
                        <span class="badge" id="adhocPathsBadge">0 sessions</span>
                        <span class="badge" id="adhocWindowBadge" style="background: #f59e0b; color: #000; margin-left: 8px;">Full Session</span>
                    </h2>
                    <div style="display: flex; gap: 15px; margin-bottom: 10px; padding: 8px 12px; background: var(--bg-tertiary); border-radius: 6px; font-size: 0.85rem; color: var(--text-secondary);">
                        <span>📍 All paths normalized to 0 at visible window start</span>
                        <span>|</span>
                        <span>🖱️ Click a line to view candlestick chart</span>
                        <span>|</span>
                        <span>🔍 Zoom to re-normalize</span>
                    </div>
                    <div id="adhocPathsChart" style="height: 500px;">
                        <div class="empty-state">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
                            </svg>
                            <p>No data loaded yet</p>
                            <span class="hint">Select dates above and click "Load & Analyze" to view price paths</span>
                        </div>
                    </div>
                </div>
                
                <!-- Selected Session Details (click on a path to see) -->
                <div class="adhoc-panel" id="adhocSessionDetails" style="display: none;">
                    <h2>
                        Selected Session Details
                        <span class="badge" id="adhocSelectedSessionBadge">--</span>
                    </h2>
                    <div id="adhocSessionInfo" style="padding: 20px;">
                        <p style="color: var(--text-secondary);">Click on a price path to view session details and candlestick chart</p>
                    </div>
                    <div style="text-align: center; padding-bottom: 20px;">
                        <button id="adhocViewCandleBtn" class="btn btn-primary" onclick="openAdhocCandlestickForSelected()" 
                                style="background: linear-gradient(135deg, #14b8a6, #0d9488); padding: 14px 28px; font-size: 1rem;" disabled>
                            View Candlestick Chart
                        </button>
                    </div>
                </div>
                
                <!-- Distribution Plots Section -->
                <div class="adhoc-panel" id="adhocDistributionPanel" style="display: none;">
                    <h2>
                        Return Distributions
                        <span class="badge" style="background: #8b5cf6;">Histograms</span>
                    </h2>
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;">
                        <!-- Returns Distribution -->
                        <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 15px;">
                            <h4 style="color: var(--accent-teal); margin-bottom: 10px; font-size: 0.95rem;">Session Returns (bps)</h4>
                            <div id="adhocReturnsHist" style="height: 280px;"></div>
                            <div id="adhocReturnsStats" class="distro-stats-box"></div>
                        </div>
                        
                        <!-- Absolute Returns Distribution -->
                        <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 15px;">
                            <h4 style="color: #f59e0b; margin-bottom: 10px; font-size: 0.95rem;">Absolute Returns (bps)</h4>
                            <div id="adhocAbsReturnsHist" style="height: 280px;"></div>
                            <div id="adhocAbsReturnsStats" class="distro-stats-box"></div>
                        </div>
                        
                        <!-- Volatility Distribution -->
                        <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 15px;">
                            <h4 style="color: #e879f9; margin-bottom: 10px; font-size: 0.95rem;">Session Volatility (bps)</h4>
                            <div id="adhocVolatilityHist" style="height: 280px;"></div>
                            <div id="adhocVolatilityStats" class="distro-stats-box"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>'''


def generate_adhoc_tab_js() -> str:
    """
    Generate JavaScript functions for the adhoc analysis tab.
    
    Returns:
        JavaScript string with all adhoc tab functions
    """
    # This is a large JavaScript block - reading from the unified dashboard source
    # and formatting it properly
    return '''        // Adhoc tab state variables
        let adhocSelectedDates = [];
        let adhocLoadedData = [];
        let adhocCurrentWindowStart = null;
        let adhocCurrentWindowEnd = null;
        let adhocSelectedSession = null;
        const ADHOC_BASE_DATE = new Date('2000-01-01T18:00:00'); // 6pm session start

        function formatTimeOnly(ms) {
            const d = new Date(ms);
            const pad = (v) => `${v}`.padStart(2, '0');
            return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
        }

        function hoursToMs(hoursFromOpen) {
            return ADHOC_BASE_DATE.getTime() + (hoursFromOpen * 60 * 60 * 1000);
        }
        
        // Get list of event dates from the embedded events data
        function getEventDates() {
            return events.map(e => e.date);
        }
        
        // Get all available trading dates (from adhocTradingDays)
        function getAllTradingDates() {
            return adhocTradingDays.map(d => d.date);
        }
        
        // Add a selected date
        function addSelectedDate() {
            const input = document.getElementById('adhocDateInput');
            const dateStr = input.value;
            
            if (!dateStr) {
                alert('Please select a date');
                return;
            }
            
            if (adhocSelectedDates.includes(dateStr)) {
                alert('Date already selected');
                return;
            }
            
            adhocSelectedDates.push(dateStr);
            adhocSelectedDates.sort();
            input.value = '';
            
            updateSelectedDatesUI();
        }
        
        // Add a date range
        function addDateRange() {
            const fromInput = document.getElementById('adhocDateFrom');
            const toInput = document.getElementById('adhocDateTo');
            const eventDaysOnly = document.getElementById('adhocEventDaysOnly').checked;
            
            const fromDate = fromInput.value;
            const toDate = toInput.value;
            
            if (!fromDate || !toDate) {
                alert('Please select both start and end dates');
                return;
            }
            
            if (fromDate > toDate) {
                alert('Start date must be before end date');
                return;
            }
            
            const eventDates = getEventDates();
            const allTradingDates = getAllTradingDates();
            let current = new Date(fromDate);
            const end = new Date(toDate);
            let addedCount = 0;
            let noDataCount = 0;
            
            while (current <= end) {
                const dateStr = current.toISOString().split('T')[0];
                
                // If filtering to event days only, check if this is an event date
                if (eventDaysOnly) {
                    if (eventDates.includes(dateStr) && !adhocSelectedDates.includes(dateStr)) {
                        adhocSelectedDates.push(dateStr);
                        addedCount++;
                    }
                } else {
                    // Add all trading days (weekdays) - API will fetch data from database
                    // Database has data from 2010-06-06 to present
                    // Use getUTCDay() to match the UTC-based dateStr from toISOString()
                    const dayOfWeek = current.getUTCDay();
                    if (dayOfWeek !== 0 && dayOfWeek !== 6 && !adhocSelectedDates.includes(dateStr)) {
                        // Add any weekday - the API will fetch from database (2010-06-06 to present)
                        adhocSelectedDates.push(dateStr);
                        addedCount++;
                    }
                }
                
                current.setDate(current.getDate() + 1);
            }
            
            adhocSelectedDates.sort();
            fromInput.value = '';
            toInput.value = '';
            
            if (addedCount === 0) {
                if (eventDaysOnly) {
                    alert('No event days found in the selected range');
                } else {
                    alert('No weekdays found in the selected range');
                }
            }
            
            updateSelectedDatesUI();
        }
        
        // Remove a date from selection
        function removeDate(dateStr) {
            adhocSelectedDates = adhocSelectedDates.filter(d => d !== dateStr);
            updateSelectedDatesUI();
        }
        
        // Clear all dates
        function clearAllDates() {
            adhocSelectedDates = [];
            adhocLoadedData = [];
            adhocSelectedSession = null;
            updateSelectedDatesUI();
            
            // Reset charts to empty state
            document.getElementById('adhocStatsPanel').style.display = 'none';
            document.getElementById('adhocSessionDetails').style.display = 'none';
            document.getElementById('adhocDistributionPanel').style.display = 'none';
            document.getElementById('adhocPathsChart').innerHTML = `
                <div class="empty-state">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
                    </svg>
                    <p>No data loaded yet</p>
                    <span class="hint">Select dates above and click "Load & Analyze" to view price paths</span>
                </div>
            `;
            document.getElementById('adhocPathsBadge').textContent = '0 sessions';
        }
        
        // Update the UI showing selected dates
        function updateSelectedDatesUI() {
            const listDiv = document.getElementById('selectedDatesList');
            const countSpan = document.getElementById('adhocDateCount');
            const loadBtn = document.getElementById('loadAdhocBtn');
            
            if (adhocSelectedDates.length === 0) {
                listDiv.innerHTML = '<span style="color: var(--text-secondary); font-style: italic;">No dates selected</span>';
                countSpan.textContent = '0 dates selected';
                loadBtn.disabled = true;
            } else {
                listDiv.innerHTML = adhocSelectedDates.map(d => `
                    <span class="date-tag">
                        ${d}
                        <button class="remove-btn" onclick="removeDate('${d}')">&times;</button>
                    </span>
                `).join('');
                countSpan.textContent = `${adhocSelectedDates.length} date${adhocSelectedDates.length === 1 ? '' : 's'} selected`;
                loadBtn.disabled = false;
            }
        }
        
        // Load and analyze data for selected dates
        async function loadAdhocData() {
            if (adhocSelectedDates.length === 0) {
                alert('Please select at least one date');
                return;
            }
            
            console.log('Selected dates:', adhocSelectedDates);
            console.log('Available pre-loaded trading days:', adhocTradingDays.length);
            
            // Show loading indicator
            const loadBtn = document.querySelector('button[onclick="loadAdhocData()"]');
            const originalText = loadBtn ? loadBtn.textContent : '';
            if (loadBtn) {
                loadBtn.textContent = 'Loading...';
                loadBtn.disabled = true;
            }
            
            // Look up dates - first in pre-loaded data, then fetch from API
            const allData = [];
            const toFetch = [];
            
            adhocSelectedDates.forEach(dateStr => {
                // First check pre-loaded adhocTradingDays (full 6pm->5pm session structure)
                const tradingDayMatch = adhocTradingDays.find(d => d.date === dateStr);
                if (tradingDayMatch) {
                    allData.push(tradingDayMatch);
                    console.log(`Date ${dateStr} found in pre-loaded data (${tradingDayMatch.prices?.length || 0} price points)`);
                } else {
                    // Always fetch from API for ad-hoc analysis (full trading session data)
                    // Events array uses pre-event window data and lacks hours/close fields needed here.
                    toFetch.push(dateStr);
                }
            });
            
            // Fetch missing dates from API using batch endpoint (much faster)
            if (toFetch.length > 0) {
                console.log(`Fetching ${toFetch.length} dates from batch API:`, toFetch);
                
                try {
                    const batchUrl = `/api/adhoc_sessions_batch?dates=${toFetch.join(',')}&skip_candles=1`;
                    const resp = await fetch(batchUrl);
                    
                    if (!resp.ok) {
                        console.error(`Batch API returned ${resp.status}`);
                    } else {
                        const batchData = await resp.json();
                        
                        if (batchData.success && batchData.sessions) {
                            console.log(`Batch API returned ${batchData.total_success}/${batchData.total_requested} sessions`);
                            
                            batchData.sessions.forEach(session => {
                                console.log(`Fetched ${session.date} (${session.prices?.length || 0} price points)`);
                                allData.push(session);
                            });
                            
                            // Log any errors
                            if (Object.keys(batchData.errors || {}).length > 0) {
                                console.warn('Some dates had errors:', batchData.errors);
                            }
                        } else {
                            console.error('Batch API failed:', batchData.error);
                        }
                    }
                } catch (err) {
                    console.error('Batch fetch error:', err);
                }
            }
            
            // Restore button
            if (loadBtn) {
                loadBtn.textContent = originalText;
                loadBtn.disabled = false;
            }
            
            console.log('Total dates with data:', allData.length);
            
            if (allData.length === 0) {
                alert('No price data found for selected dates.\\n\\nThe database contains TY futures data from 2010-06-06 to present.\\n\\nNote: Weekends and holidays have no trading data.');
                return;
            }
            
            // Sort by date
            allData.sort((a, b) => a.date.localeCompare(b.date));
            
            adhocLoadedData = allData;
            
            // Render charts - start with smaller window (8am-12pm = hours 14-18) for less visual noise
            // Users can zoom out to see full session
            renderAdhocPaths(allData, hoursToMs(12), hoursToMs(20), true);  // 6am to 2pm - covers key market hours
            
            // Calculate and display statistics
            calculateAdhocStats(allData);
            
            // Hide session details panel until user clicks a path
            document.getElementById('adhocSessionDetails').style.display = 'none';
            adhocSelectedSession = null;
            
            // Update badges
            document.getElementById('adhocPathsBadge').textContent = `${allData.length} session${allData.length === 1 ? '' : 's'}`;
        }
        
        // Calculate and display statistics for selected duration
        function calculateAdhocStats(data) {
            // Initial calculation with default duration (1 hour)
            updateAdhocStatsForDuration();
            document.getElementById('adhocStatsPanel').style.display = 'block';
            document.getElementById('adhocStatsCount').textContent = data.length;
            
            // Render distribution plots
            renderDistributionPlots(data);
        }
        
        // Update stats based on selected duration
        function updateAdhocStatsForDuration() {
            if (!adhocLoadedData || adhocLoadedData.length === 0) return;
            
            const duration = document.getElementById('adhocDurationSelect').value;
            let durationHours;
            let durationLabel;
            
            switch(duration) {
                case '5m':
                    durationHours = 5 / 60;  // 5 minutes = 0.0833 hours
                    durationLabel = '5 minutes';
                    break;
                case '1h':
                    durationHours = 1;
                    durationLabel = '1 hour';
                    break;
                case '1d':
                default:
                    durationHours = 23;  // Full session
                    durationLabel = 'full session';
                    break;
            }
            
            // Update info text
            document.getElementById('adhocDurationInfo').textContent = `Max move in any ${durationLabel} window`;
            document.getElementById('adhocStatsDuration').textContent = durationLabel;
            
            // Calculate max moves within the duration window for each session
            const allMaxMoves = [];       // Max abs move in any duration window
            const allMaxUpMoves = [];     // Max positive move
            const allMaxDownMoves = [];   // Max negative move
            
            adhocLoadedData.forEach(session => {
                if (!session.prices || session.prices.length < 2) return;
                
                const prices = session.prices;
                let sessionMaxUp = 0;
                let sessionMaxDown = 0;
                
                // For each starting point, find the max move within the duration window
                for (let i = 0; i < prices.length; i++) {
                    const startPrice = prices[i].close;
                    const startHour = prices[i].hours;
                    
                    // Look at all points within the duration window
                    for (let j = i + 1; j < prices.length; j++) {
                        const hourDiff = prices[j].hours - startHour;
                        if (hourDiff > durationHours) break;  // Past the duration window
                        
                        const move = decimalToBps(prices[j].close - startPrice);
                        if (move > sessionMaxUp) sessionMaxUp = move;
                        if (move < sessionMaxDown) sessionMaxDown = move;
                    }
                }
                
                allMaxUpMoves.push(sessionMaxUp);
                allMaxDownMoves.push(Math.abs(sessionMaxDown));
                allMaxMoves.push(Math.max(sessionMaxUp, Math.abs(sessionMaxDown)));
            });
            
            if (allMaxMoves.length === 0) {
                clearAdhocStats();
                return;
            }
            
            // Sort for percentile calculations
            const sortedMoves = [...allMaxMoves].sort((a, b) => a - b);
            const sortedUp = [...allMaxUpMoves].sort((a, b) => a - b);
            const sortedDown = [...allMaxDownMoves].sort((a, b) => a - b);
            
            // percentile helper is shared in live_unified_dashboard.py
            
            // Calculate statistics
            const mean = sortedMoves.reduce((a, b) => a + b, 0) / sortedMoves.length;
            const median = percentile(sortedMoves, 50);
            const maxUp = Math.max(...allMaxUpMoves);
            const maxDown = Math.max(...allMaxDownMoves);
            
            const p5 = percentile(sortedMoves, 5);
            const p25 = percentile(sortedMoves, 25);
            const p75 = percentile(sortedMoves, 75);
            const p95 = percentile(sortedMoves, 95);
            
            // Update UI
            document.getElementById('adhocStatMaxUp').textContent = '+' + maxUp.toFixed(2) + ' bps';
            document.getElementById('adhocStatMaxDown').textContent = '-' + maxDown.toFixed(2) + ' bps';
            document.getElementById('adhocStatMean').textContent = mean.toFixed(2) + ' bps';
            document.getElementById('adhocStatMedian').textContent = median.toFixed(2) + ' bps';
            document.getElementById('adhocStatP5').textContent = p5.toFixed(2) + ' bps';
            document.getElementById('adhocStatP25').textContent = p25.toFixed(2) + ' bps';
            document.getElementById('adhocStatP75').textContent = p75.toFixed(2) + ' bps';
            document.getElementById('adhocStatP95').textContent = p95.toFixed(2) + ' bps';
        }
        
        // Clear stats display
        function clearAdhocStats() {
            document.getElementById('adhocStatMaxUp').textContent = '--';
            document.getElementById('adhocStatMaxDown').textContent = '--';
            document.getElementById('adhocStatMean').textContent = '--';
            document.getElementById('adhocStatMedian').textContent = '--';
            document.getElementById('adhocStatP5').textContent = '--';
            document.getElementById('adhocStatP25').textContent = '--';
            document.getElementById('adhocStatP75').textContent = '--';
            document.getElementById('adhocStatP95').textContent = '--';
            
            // Hide distribution panel
            document.getElementById('adhocDistributionPanel').style.display = 'none';
        }
        
        // Gaussian KDE implementation (matches DistroDashboard scipy.stats.gaussian_kde)
        function adhocGaussianKDE(data) {
            const n = data.length;
            // Scott's rule for bandwidth
            const mean = data.reduce((a, b) => a + b, 0) / n;
            const std = Math.sqrt(data.reduce((sum, v) => sum + (v - mean) ** 2, 0) / n);
            const bandwidth = std * Math.pow(n, -0.2);
            
            return function(x) {
                let sum = 0;
                for (let i = 0; i < n; i++) {
                    const z = (x - data[i]) / bandwidth;
                    sum += Math.exp(-0.5 * z * z) / Math.sqrt(2 * Math.PI);
                }
                return sum / (n * bandwidth);
            };
        }
        
        // Render distribution plots (histograms with stats)
        function renderDistributionPlots(data) {
            if (!data || data.length === 0) {
                document.getElementById('adhocDistributionPanel').style.display = 'none';
                return;
            }
            
            // Calculate returns, absolute returns, and volatility for each session
            const returns = [];      // Close - Open in bps
            const absReturns = [];   // |Close - Open| in bps
            const volatilities = []; // Standard deviation of bps during session
            
            data.forEach(session => {
                const prices = session.prices || [];
                if (prices.length < 2) return;
                
                const bpsValues = prices.map(p => p.bps || 0);
                const openBps = bpsValues[0];
                const closeBps = bpsValues[bpsValues.length - 1];
                
                // Session return (close - open)
                const sessionReturn = closeBps - openBps;
                returns.push(sessionReturn);
                absReturns.push(Math.abs(sessionReturn));
                
                // Volatility (std dev of bps during session)
                if (bpsValues.length > 1) {
                    const mean = bpsValues.reduce((a, b) => a + b, 0) / bpsValues.length;
                    const variance = bpsValues.reduce((acc, v) => acc + Math.pow(v - mean, 2), 0) / bpsValues.length;
                    volatilities.push(Math.sqrt(variance));
                }
            });
            
            // Show the panel
            document.getElementById('adhocDistributionPanel').style.display = 'block';
            
            // Render each distribution (DistroDashboard style)
            renderAdhocDistribution('adhocReturnsHist', 'adhocReturnsStats', returns, 'Session Returns (bps)', true);
            renderAdhocDistribution('adhocAbsReturnsHist', 'adhocAbsReturnsStats', absReturns, 'Absolute Returns (bps)', false);
            renderAdhocDistribution('adhocVolatilityHist', 'adhocVolatilityStats', volatilities, 'Session Volatility (bps)', false);
        }
        
        // DistroDashboard-style distribution plotting (exact match)
        function renderAdhocDistribution(chartId, statsId, values, xlabel, showZeroLine) {
            const chartDiv = document.getElementById(chartId);
            const statsDiv = document.getElementById(statsId);
            
            if (!values || values.length === 0) {
                chartDiv.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 40px;">No data</div>';
                statsDiv.innerHTML = '';
                return;
            }
            
            const sorted = [...values].sort((a, b) => a - b);
            const n = sorted.length;
            
            // Calculate statistics (matching DistroDashboard describe())
            const mean = values.reduce((a, b) => a + b, 0) / n;
            const median = n % 2 === 0 ? (sorted[n/2 - 1] + sorted[n/2]) / 2 : sorted[Math.floor(n/2)];
            const min = sorted[0];
            const max = sorted[n - 1];
            const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (n - 1 || 1);
            const stdDev = Math.sqrt(variance);
            
            // Percentiles (matching DistroDashboard quantile())
            const p25 = sorted[Math.floor(n * 0.25)];
            const p50 = median;
            const p75 = sorted[Math.floor(n * 0.75)];
            const p95 = sorted[Math.floor(n * 0.95)];
            const p99 = sorted[Math.min(Math.floor(n * 0.99), n - 1)];
            
            // Create histogram trace (DistroDashboard: skyblue, opacity 0.6)
            const histTrace = {
                x: values,
                type: 'histogram',
                histnorm: 'probability density',
                opacity: 0.6,
                name: 'Histogram',
                marker: {
                    color: 'skyblue'
                },
                nbinsx: Math.min(20, Math.max(5, Math.floor(n / 2))),  // Adaptive bins
                hovertemplate: '%{x:.2f}<br>Density: %{y:.4f}<extra></extra>'
            };
            
            // Generate KDE curve (DistroDashboard: darkblue, width 2)
            const kde = adhocGaussianKDE(values);
            const xRange = max - min || 1;
            const xPadding = xRange * 0.15;
            const xGrid = [];
            const yGrid = [];
            const numPoints = 300;
            for (let i = 0; i < numPoints; i++) {
                const x = min - xPadding + (xRange + 2 * xPadding) * i / (numPoints - 1);
                xGrid.push(x);
                yGrid.push(kde(x));
            }
            
            const kdeTrace = {
                x: xGrid,
                y: yGrid,
                type: 'scatter',
                mode: 'lines',
                name: 'KDE',
                line: { color: 'darkblue', width: 2 },
                hoverinfo: 'skip'
            };
            
            const traces = [histTrace, kdeTrace];
            
            // Stats box annotation (DistroDashboard style: white bg, black border)
            const statsBox = 
                `N: ${n}<br>` +
                `Mean: ${mean.toFixed(2)}<br>` +
                `Std: ${stdDev.toFixed(2)}<br>` +
                `Min: ${min.toFixed(2)}<br>` +
                `25%: ${p25.toFixed(2)}<br>` +
                `Median: ${p50.toFixed(2)}<br>` +
                `75%: ${p75.toFixed(2)}<br>` +
                `95%: ${p95.toFixed(2)}<br>` +
                `Max: ${max.toFixed(2)}`;
            
            // DistroDashboard layout: plotly_white template
            const layout = {
                title: { text: xlabel, font: { size: 13, color: '#333' } },
                xaxis: { 
                    title: { text: 'Value (bps)', font: { size: 11 } },
                    gridcolor: '#e0e0e0',
                    zerolinecolor: '#888',
                    zeroline: showZeroLine
                },
                yaxis: { 
                    title: { text: 'Density', font: { size: 11 } },
                    gridcolor: '#e0e0e0'
                },
                barmode: 'overlay',
                height: 320,
                margin: { t: 35, r: 30, b: 45, l: 55 },
                paper_bgcolor: 'white',
                plot_bgcolor: 'white',
                showlegend: false,
                annotations: [
                    {
                        xref: 'paper', yref: 'paper',
                        x: 0.98, y: 0.98,
                        xanchor: 'right', yanchor: 'top',
                        text: statsBox,
                        showarrow: false,
                        align: 'left',
                        font: { size: 10, color: 'black' },
                        bgcolor: 'white',
                        bordercolor: 'black',
                        borderwidth: 1,
                        opacity: 0.9
                    }
                ]
            };
            
            Plotly.react(chartId, traces, layout, { responsive: true, displayModeBar: false });
            
            // Hide the separate stats panel since stats are now in the chart
            statsDiv.innerHTML = '';
        }
        
        // Render normalized price paths with dynamic normalization
        function renderAdhocPaths(data, windowStart = null, windowEnd = null, isInitial = true) {
            adhocLoadedData = data;
            adhocCurrentWindowStart = windowStart;
            adhocCurrentWindowEnd = windowEnd;
            
            const traces = [];
            const colors = [
                '#1d9bf0', '#00ba7c', '#f4212e', '#ff7a00', '#9b59b6',
                '#14b8a6', '#e879f9', '#fbbf24', '#64748b', '#ec4899',
                '#a78bfa', '#34d399', '#fb7185', '#fcd34d', '#60a5fa'
            ];
            
            data.forEach((session, idx) => {
                if (!session.prices || session.prices.length === 0) return;
                
                const cleaned = (session.prices || [])
                    .map(p => {
                        const hours = Number(p.hours);
                        const close = Number(p.close);
                        if (!Number.isFinite(hours) || !Number.isFinite(close)) return null;
                        const tsMs = hoursToMs(hours);
                        return { ...p, hours, close, tsMs };
                    })
                    .filter(Boolean)
                    .sort((a, b) => a.tsMs - b.tsMs);
                
                if (cleaned.length === 0) {
                    console.warn(`No valid hours/close data for session ${session.date}`);
                    return;
                }
                
                
                const color = colors[idx % colors.length];
                
                if (adhocCurrentWindowStart == null || adhocCurrentWindowEnd == null) {
                    const allTimes = cleaned.map(p => p.tsMs);
                    const maxTime = Math.max(...allTimes);
                    const defaultEnd = maxTime;
                    const defaultStart = maxTime - (2 * 60 * 60 * 1000);
                    adhocCurrentWindowStart = defaultStart;
                    adhocCurrentWindowEnd = defaultEnd;
                }

                // Find data points within the visible window
                let windowData = cleaned.filter(p => p.tsMs >= adhocCurrentWindowStart && p.tsMs <= adhocCurrentWindowEnd);
                if (windowData.length === 0 && isInitial) {
                    const fullStart = hoursToMs(0);
                    const fullEnd = hoursToMs(23);
                    if (adhocCurrentWindowStart !== fullStart || adhocCurrentWindowEnd !== fullEnd) {
                        adhocCurrentWindowStart = fullStart;
                        adhocCurrentWindowEnd = fullEnd;
                        windowData = cleaned.filter(p => p.tsMs >= adhocCurrentWindowStart && p.tsMs <= adhocCurrentWindowEnd);
                    }
                }
                if (windowData.length === 0) return;
                
                // DEBUG MODE: Plot raw close prices (no normalization) to test for horizontal line artifacts
                // Use ALL windowData points, no filtering
                const baseBps = windowData[0]?.bps ?? 0;
                const yRaw = windowData.map(p => (p.bps ?? 0) - baseBps);
                const segmented = (() => {
                    const xOut = [];
                    const yOut = [];
                    const maxGapMs = 90 * 60 * 1000;
                    for (let i = 0; i < windowData.length; i += 1) {
                        if (i > 0 && Number.isFinite(windowData[i].tsMs) && Number.isFinite(windowData[i - 1].tsMs)) {
                            if ((windowData[i].tsMs - windowData[i - 1].tsMs) > maxGapMs) {
                                xOut.push(new Date(windowData[i].tsMs));
                                yOut.push(null);
                            }
                        }
                        xOut.push(new Date(windowData[i].tsMs));
                        yOut.push(yRaw[i]);
                    }
                    return { x: xOut, y: yOut };
                })();
                const hoverTexts = windowData.map(p => 
                    `${session.session_label || session.date}<br>Time: ${formatTimeOnly(p.tsMs)}<br>Move: ${((p.bps ?? 0) - baseBps).toFixed(2)} bps`
                );
                
                
                // For instrumentation compatibility, create filtered = windowData
                const filtered = windowData.map(p => ({ ...p, adjustedBps: (p.bps ?? 0) - baseBps }));
                const windowStartBps = windowData[0]?.bps ?? 0;
                
                
                
                
                
                
                
                // Use 'scatter' type like pre-event tab with same line styling
                traces.push({
                    x: segmented.x,
                    y: segmented.y,
                    type: 'scatter',
                    mode: 'lines',
                    name: session.date,
                    line: { 
                        width: 1.5, 
                        color: color + '99'  // Add alpha to hex color (99 = 60% opacity)
                    },
                    text: hoverTexts,
                    hoverinfo: 'text',
                    customdata: [session],
                    connectgaps: false,
                });
            });
            
            if (traces.length === 0) {
                document.getElementById('adhocPathsChart').innerHTML = `
                    <div class="empty-state">
                        <p>No price path data available for selected dates</p>
                    </div>
                `;
                return;
            }
            
            // NOTE: Temporarily removing reference line to isolate horizontal artifacts
            
            const layout = {
                ...darkLayout,
                title: 'Normalized Price Paths (bps from visible window start)',
                xaxis: {
                    ...darkLayout.xaxis,
                    title: 'Time of Day',
                    range: [adhocCurrentWindowStart, adhocCurrentWindowEnd],
                    type: 'date',
                    tickformat: '%H:%M',
                    fixedrange: false,
                    // rangeslider disabled - use zoom/pan instead
                    rangeslider: { visible: false },
                    showgrid: false,
                },
                yaxis: {
                    ...darkLayout.yaxis,
                    title: 'Move (bps)',
                    fixedrange: false,
                    zeroline: false,
                    showgrid: false,
                },
                showlegend: true,
                legend: {
                    orientation: 'h',
                    y: -0.2,
                    x: 0.5,
                    xanchor: 'center',
                    font: { size: 10 },
                },
                hovermode: 'closest',
                dragmode: 'zoom',
            };
            
            const config = {
                responsive: true,
                scrollZoom: true,
                displayModeBar: true,
                modeBarButtonsToAdd: ['pan2d', 'zoom2d', 'zoomIn2d', 'zoomOut2d', 'resetScale2d'],
            };
            
            // Debug: log trace info (guard against Date/String x-values)
            const formatTraceX = (value) => {
                if (value === null || value === undefined) return 'n/a';
                if (typeof value === 'number') return value.toFixed(2);
                if (value instanceof Date) return value.toISOString();
                return String(value);
            };
            console.log('Rendering adhoc paths with', traces.length - 1, 'session traces + 1 zero-line');
            traces.forEach((t, i) => {
                if (t.name !== 'Reference') {
                    const xStart = t.x?.[0];
                    const xEnd = t.x?.[t.x?.length - 1];
                    console.log(`  Trace ${i} (${t.name}): ${t.x?.length || 0} points, x range: ${formatTraceX(xStart)}-${formatTraceX(xEnd)}`);
                }
            });
            
            const handlers = isInitial ? {
                relayout: handleAdhocRangeChange,
                click: handleAdhocPathClick,
            } : {};

            renderPathsChart('adhocPathsChart', traces, layout, config, handlers).then(() => {
                updateAdhocWindowBadge(adhocCurrentWindowStart, adhocCurrentWindowEnd);
            });
        }
        
        // Handle range changes (zoom/pan) and re-normalize
        function handleAdhocRangeChange(eventData) {
            let newStart = adhocCurrentWindowStart;
            let newEnd = adhocCurrentWindowEnd;
            
            if (eventData['xaxis.range']) {
                newStart = Date.parse(eventData['xaxis.range'][0]);
                newEnd = Date.parse(eventData['xaxis.range'][1]);
            } else if (eventData['xaxis.range[0]'] !== undefined) {
                newStart = Date.parse(eventData['xaxis.range[0]']);
                newEnd = Date.parse(eventData['xaxis.range[1]']);
            } else if (eventData['xaxis.autorange']) {
                // Reset to full session
                newStart = hoursToMs(0);
                newEnd = hoursToMs(23);
            } else {
                return;
            }
            
            // Clamp to valid session range
            newStart = Math.max(hoursToMs(0), newStart);
            newEnd = Math.min(hoursToMs(23), newEnd);
            
            // Ensure minimum 30-minute window
            if (newEnd - newStart < 30 * 60 * 1000) {
                newStart = hoursToMs(0);
                newEnd = hoursToMs(23);
            }
            
            
            // Only re-render if window changed significantly
            if (Math.abs(newStart - adhocCurrentWindowStart) > 60 * 1000 || Math.abs(newEnd - adhocCurrentWindowEnd) > 60 * 1000) {
                renderAdhocPaths(adhocLoadedData, newStart, newEnd, false);
            }
        }
        
        // Update window badge with current range
        function updateAdhocWindowBadge(start, end) {
            const badge = document.getElementById('adhocWindowBadge');
            if (!badge || start == null || end == null) return;
            const fullStart = hoursToMs(0);
            const fullEnd = hoursToMs(23);
            if (start <= fullStart + (5 * 60 * 1000) && end >= fullEnd - (5 * 60 * 1000)) {
                badge.textContent = 'Full Session';
            } else {
                badge.textContent = `${formatTimeOnly(start)} → ${formatTimeOnly(end)}`;
            }
        }
        
        // Handle click on price path to show session details
        function handleAdhocPathClick(clickData) {
            if (!clickData.points || clickData.points.length === 0) return;
            
            const traceIndex = clickData.points[0].curveNumber;
            
            // Skip if clicked on reference line (last trace)
            if (traceIndex >= adhocLoadedData.length) return;
            
            const clickedSession = adhocLoadedData[traceIndex];
            if (clickedSession) {
                showAdhocSessionDetails(clickedSession);
            }
        }
        
        // Show session details panel (like pre-event tab's showEventDetails)
        function showAdhocSessionDetails(session) {
            adhocSelectedSession = session;
            
            const detailsPanel = document.getElementById('adhocSessionDetails');
            const sessionInfo = document.getElementById('adhocSessionInfo');
            const badge = document.getElementById('adhocSelectedSessionBadge');
            const candleBtn = document.getElementById('adhocViewCandleBtn');
            
            badge.textContent = session.session_label || session.date;
            
            // Calculate session stats
            const prices = session.prices || [];
            let openBps = 0, closeBps = 0, highBps = 0, lowBps = 0;
            if (prices.length > 0) {
                openBps = prices[0].bps || 0;
                closeBps = prices[prices.length - 1].bps || 0;
                highBps = Math.max(...prices.map(p => p.bps || 0));
                lowBps = Math.min(...prices.map(p => p.bps || 0));
            }
            
            const hasCandleData = session.candlestick_5m && session.candlestick_5m.length > 0;
            
            sessionInfo.innerHTML = `
                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px;">
                    <div style="background: var(--bg-tertiary); padding: 15px; border-radius: 8px; text-align: center;">
                        <div style="color: var(--text-secondary); font-size: 0.85rem;">Session</div>
                        <div style="color: var(--accent-teal); font-size: 1.1rem; font-weight: 600;">${session.session_label || session.date}</div>
                    </div>
                    <div style="background: var(--bg-tertiary); padding: 15px; border-radius: 8px; text-align: center;">
                        <div style="color: var(--text-secondary); font-size: 0.85rem;">Session Move</div>
                        <div style="color: ${closeBps >= 0 ? '#22c55e' : '#ef4444'}; font-size: 1.1rem; font-weight: 600;">${closeBps >= 0 ? '+' : ''}${closeBps.toFixed(2)} bps</div>
                    </div>
                    <div style="background: var(--bg-tertiary); padding: 15px; border-radius: 8px; text-align: center;">
                        <div style="color: var(--text-secondary); font-size: 0.85rem;">High</div>
                        <div style="color: #22c55e; font-size: 1.1rem; font-weight: 600;">+${highBps.toFixed(2)} bps</div>
                    </div>
                    <div style="background: var(--bg-tertiary); padding: 15px; border-radius: 8px; text-align: center;">
                        <div style="color: var(--text-secondary); font-size: 0.85rem;">Low</div>
                        <div style="color: #ef4444; font-size: 1.1rem; font-weight: 600;">${lowBps.toFixed(2)} bps</div>
                    </div>
                </div>
                <div style="color: var(--text-secondary); font-size: 0.9rem; text-align: center;">
                    ${hasCandleData ? 'Candlestick data available (' + session.candlestick_5m.length + ' bars)' : 'No candlestick data for this session'}
                </div>
            `;
            
            candleBtn.disabled = !hasCandleData;
            candleBtn.style.opacity = hasCandleData ? '1' : '0.5';
            
            detailsPanel.style.display = 'block';
        }
        
        // Open candlestick modal for selected session (called from button)
        function openAdhocCandlestickForSelected() {
            if (adhocSelectedSession) {
                openAdhocCandlestickModal(adhocSelectedSession);
            }
        }
        
        // Open candlestick modal for a specific trading session
        function openAdhocCandlestickModal(session) {
            const modal = document.getElementById('candlestickModal');
            document.getElementById('modalEventDate').textContent = session.session_label || session.date;
            
            // Render candlestick chart for this session
            const candles = session.candlestick_5m;
            if (!candles || candles.length === 0) {
                document.getElementById('candlestickChart').innerHTML = '<div style="padding: 40px; text-align: center; color: #888;">No candlestick data available for this session</div>';
                modal.classList.add('visible');
                return;
            }
            
            // Add markers for session boundaries
            const sessionDate = session.date;
            const nextDay = new Date(sessionDate);
            nextDay.setDate(nextDay.getDate() + 1);
            const nextDayStr = nextDay.toISOString().split('T')[0];
            
            const shapes = [
                // Session open (6pm)
                {
                    type: 'line',
                    x0: `${sessionDate}T18:00:00`,
                    x1: `${sessionDate}T18:00:00`,
                    y0: 0, y1: 1, yref: 'paper',
                    line: { color: '#14b8a6', width: 2, dash: 'dot' },
                },
                // Midnight
                {
                    type: 'line',
                    x0: `${nextDayStr}T00:00:00`,
                    x1: `${nextDayStr}T00:00:00`,
                    y0: 0, y1: 1, yref: 'paper',
                    line: { color: '#6b7280', width: 1, dash: 'dash' },
                },
                // 8:30 AM (common economic release time)
                {
                    type: 'line',
                    x0: `${nextDayStr}T08:30:00`,
                    x1: `${nextDayStr}T08:30:00`,
                    y0: 0, y1: 1, yref: 'paper',
                    line: { color: '#f59e0b', width: 2, dash: 'dot' },
                },
                // Session close (5pm)
                {
                    type: 'line',
                    x0: `${nextDayStr}T17:00:00`,
                    x1: `${nextDayStr}T17:00:00`,
                    y0: 0, y1: 1, yref: 'paper',
                    line: { color: '#ef4444', width: 2, dash: 'dot' },
                },
            ];
            
            const annotations = [
                { x: `${sessionDate}T18:00:00`, y: 1.02, yref: 'paper', text: 'Open (6pm)', showarrow: false, font: { color: '#14b8a6', size: 10 } },
                { x: `${nextDayStr}T00:00:00`, y: 1.02, yref: 'paper', text: 'Midnight', showarrow: false, font: { color: '#6b7280', size: 10 } },
                { x: `${nextDayStr}T08:30:00`, y: 1.02, yref: 'paper', text: '8:30am', showarrow: false, font: { color: '#f59e0b', size: 10 } },
                { x: `${nextDayStr}T17:00:00`, y: 1.02, yref: 'paper', text: 'Close (5pm)', showarrow: false, font: { color: '#ef4444', size: 10 } },
            ];
            
            // Add event markers if available
            const sessionEvents = session.events || [];
            if (sessionEvents.length > 0) {
                console.log('Adding event markers:', sessionEvents.length, 'events');
                
                // Color scheme for events based on importance
                const eventColors = {
                    'high': '#e11d48',    // Rose red for high importance
                    'medium': '#8b5cf6',  // Purple for medium importance
                };
                
                sessionEvents.forEach((evt, idx) => {
                    const eventDateTime = evt.datetime || `${evt.date}T08:30:00`;
                    const importance = (evt.importance || 'medium').toLowerCase();
                    const color = eventColors[importance] || eventColors['medium'];
                    
                    // Add vertical line for event
                    shapes.push({
                        type: 'line',
                        x0: eventDateTime,
                        x1: eventDateTime,
                        y0: 0, y1: 1, yref: 'paper',
                        line: { color: color, width: 2 },
                    });
                    
                    // Add annotation for event
                    const shortName = evt.name.length > 25 ? evt.name.substring(0, 22) + '...' : evt.name;
                    const actualStr = evt.actual ? ` (${evt.actual})` : '';
                    annotations.push({
                        x: eventDateTime,
                        y: 0.95 - (idx * 0.05),  // Stack annotations to avoid overlap
                        yref: 'paper',
                        text: `<b>${shortName}</b>${actualStr}`,
                        showarrow: true,
                        arrowhead: 2,
                        arrowsize: 0.5,
                        arrowwidth: 1,
                        arrowcolor: color,
                        ax: 30,
                        ay: 0,
                        font: { color: color, size: 9 },
                        bgcolor: 'rgba(26, 26, 46, 0.9)',
                        bordercolor: color,
                        borderwidth: 1,
                        borderpad: 3,
                    });
                });
            }
            
            renderCandlestickChart('candlestickChart', candles, {
                title: `Trading Session: ${session.session_label || session.date}`,
                xTitle: 'Time',
                yTitle: 'Price',
                shapes: shapes,
                annotations: annotations,
                showRangeSlider: true,
                height: 500,
            });
            
            modal.classList.add('visible');
        }
        
        // Initialize ad-hoc UI on page load
        function initAdhocTab() {
            updateSelectedDatesUI();
            
            // Set default date range to recent dates
            const today = new Date();
            const threeMonthsAgo = new Date();
            threeMonthsAgo.setMonth(threeMonthsAgo.getMonth() - 3);
            
            document.getElementById('adhocDateTo').value = today.toISOString().split('T')[0];
            document.getElementById('adhocDateFrom').value = threeMonthsAgo.toISOString().split('T')[0];
        }'''


