"""
Pre-Event Tab Module
--------------------
Generates HTML and JavaScript for the pre-event analysis tab.
"""


def generate_preevent_tab_html(config: dict) -> str:
    """
    Generate HTML content for the pre-event tab.
    
    Args:
        config: Dictionary containing:
            - event_unit: Unit for event (e.g., 'K')
            - user_cons_std: User's consensus std dev value
            - cons_std_step: Step size for consensus std input
            - cons_std_min: Minimum consensus std value
            - cons_std_max: Maximum consensus std value
            - bucket_width_default: Default bucket width
            - bucket_step: Step size for bucket width
            - bucket_min: Minimum bucket width
            - bucket_max: Maximum bucket width
            - user_fwd_be: User's forward breakeven value
            - current_hours_from_event: Current hours from event
            - event_short_name: Short name for event (e.g., 'NFP')
            - event_time: Event time string (e.g., '08:30')
            - event_time_hours: Event time as decimal hours (e.g., 8.5)
            - event_date: Event date string (e.g., '2025-01-10')
    
    Returns:
        HTML string for the pre-event tab content
    """
    event_unit = config.get('event_unit', 'K')
    user_cons_std = config.get('user_cons_std', 26.1)
    cons_std_step = config.get('cons_std_step', 0.5)
    cons_std_min = config.get('cons_std_min', 10)
    cons_std_max = config.get('cons_std_max', 60)
    bucket_width_default = config.get('bucket_width_default', 7)
    bucket_step = config.get('bucket_step', 1)
    bucket_min = config.get('bucket_min', 1)
    bucket_max = config.get('bucket_max', 20)
    user_fwd_be = config.get('user_fwd_be', 6.384)
    current_hours_from_event = config.get('current_hours_from_event', 0.0)
    event_short_name = config.get('event_short_name', 'NFP')
    event_time = config.get('event_time', '08:30')  # Default to 8:30 if not provided
    event_time_hours = config.get('event_time_hours', 8.5)  # Default to 8.5 if not provided
    event_date = config.get('event_date', '')
    show_live = config.get('show_live', True)
    live_panel_style = '' if show_live else ' style="display:none;"'
    live_badge = '<span style="color: #fbbf24;">🔴 LIVE</span> ' if show_live else ''
    live_stat_style = '' if show_live else ' style="display:none;"'
    
    return f'''        <!-- PRE-EVENT TAB -->
        <div class="tab-content active" id="preeventTab">
            <!-- LIVE STATS PANEL (matches standalone pre-event) -->
            <div id="liveStatsPanel" class="live-data-panel"{live_panel_style}>
                <div class="live-header">
                    <span class="live-indicator"></span>
                    <span class="live-title">LIVE DATA</span>
                    <span id="liveTimestamp" class="live-timestamp">--:--:--</span>
                </div>
                <div class="live-stats-grid">
                    <div class="live-stat">
                        <div class="live-label">TY Price</div>
                        <div id="livePrice" class="live-value live-price">---</div>
                        <div id="livePrice32" class="live-sublabel">---</div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">Reference Price</div>
                        <div id="liveRef" class="live-value" style="color: #8b98a5;">---</div>
                        <div id="liveRefTimestamp" class="live-sublabel">--:--:--</div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">FWD Breakeven</div>
                        <div class="live-value">
                            <input type="number" id="liveFwdInput" value="" placeholder="{user_fwd_be}" step="0.1" min="0.1" max="30"
                                   title="Leave blank to use the default FWD breakeven"
                                   style="width: 70px; background: var(--bg-secondary); border: 1px solid var(--border-color); 
                                          color: var(--text-primary); padding: 4px 8px; border-radius: 4px; font-size: 1.1rem;">
                            <span style="color: var(--text-secondary); font-size: 0.9rem;"> bps</span>
                        </div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">High / Low</div>
                        <div id="liveHighLow" class="live-value" style="font-size: 0.9rem;">--- / ---</div>
                        <div id="liveMoveUpDown" class="live-sublabel">↑-- / ↓--</div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">B/E Realized</div>
                        <div id="liveBeRealized" class="live-value live-be">--- bps</div>
                        <div class="live-sublabel" style="color: #888;">max(high-open, open-low)</div>
                    </div>
                    <div class="live-stat highlight">
                        <div class="live-label">B/E Multiplier</div>
                        <div id="liveBeMultiplier" class="live-value live-multiplier">---x</div>
                    </div>
                </div>
                <div class="live-status">
                    <span id="liveStatusText">Connecting to live data...</span>
                    <button id="refreshBtn" onclick="refreshLiveData()" style="margin-left: 15px; padding: 4px 12px; 
                            background: var(--accent-orange); border: none; border-radius: 4px; color: white; cursor: pointer;">
                        Refresh
                    </button>
                </div>
            </div>
            
            <div class="controls">
                <div style="grid-column: span 2; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent-blue); margin-bottom: -4px;">
                    Analysis Configuration
                </div>
                <!-- Dynamic component std dev inputs - one for each selected component -->
                <div class="control-group" id="componentStdDevSection" style="grid-column: span 2;">
                    <label>Your Consensus Std Dev (per component)</label>
                    <div id="componentStdDevContainer" style="display: flex; flex-wrap: wrap; gap: 20px; padding: 12px; background: var(--bg-tertiary); border-radius: 8px;">
                        <div style="color: var(--text-secondary); font-size: 0.85rem;">Select components above to configure std dev inputs.</div>
                    </div>
                    <div id="bucketModeHint" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 6px;">
                        Buckets update based on selected components
                    </div>
                </div>
                
        <!-- Removed live window label block -->

        <div class="control-group" style="grid-column: span 2;">
            <label>Analyze From Year</label>
            <select id="analysisFromYear" onchange="onConfigChange()" style="
                width: 160px;
                padding: 8px 12px;
                border-radius: 6px;
                border: 1px solid var(--border-color);
                background: var(--bg-tertiary);
                color: var(--text-primary);
                font-size: 0.9rem;
            ">
                <option value="2018">2018</option>
                <option value="2019">2019</option>
                <option value="2020">2020</option>
                <option value="2021" selected>2021</option>
                <option value="2022">2022</option>
                <option value="2023">2023</option>
                <option value="2024">2024</option>
                <option value="2025">2025</option>
                <option value="2026">2026</option>
            </select>
        </div>

        <div class="control-group" style="grid-column: span 2;">
            <label>Pre-Event Window</label>
            <div style="display: grid; grid-template-columns: auto 1fr auto; gap: 10px 12px; align-items: center;">
                <label style="color: var(--text-secondary);">Lookback days (x)</label>
                <input type="number" id="preeventLookbackDays" value="1" min="1" max="5" step="1" style="width: 70px;">
                <div></div>
                <label style="color: var(--text-secondary);">Start slider</label>
                <div>
                    <input type="range" id="preWindowStartSlider" min="0" max="1439" step="1" value="900" style="width: 100%;">
                    <div id="preWindowStartLabel" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 3px;">15:00 (T-x)</div>
                </div>
                <input type="time" id="windowStartInput" style="width: 120px;">
                <label style="color: var(--text-secondary);">End slider</label>
                <div>
                    <input type="range" id="preWindowEndSlider" min="0" max="1439" step="1" value="510" style="width: 100%;">
                    <div id="preWindowEndLabel" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 3px;">08:30 (T)</div>
                </div>
                <input type="time" id="windowEndInput" style="width: 120px;">
                <button class="btn btn-secondary" onclick="applyCustomWindow()">Apply Window</button>
                <span style="color: var(--text-secondary); font-size: 0.85rem;">Zoom or pan updates normalization</span>
            </div>
        </div>
                
                <div style="grid-column: span 2; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent-blue); margin-bottom: -4px;">
                    Boxplot Controls
                </div>
                <!-- Dynamic bucket width - auto-calculated based on component range -->
                <div class="control-group">
                    <label>Bucket Width <span id="bucketWidthUnit" style="color: var(--text-secondary);">({event_unit})</span></label>
                    <input type="number" id="bucketWidthInput" value="{bucket_width_default}" step="{bucket_step}">
                    <div id="bucketWidthHint" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 4px;">Auto-set based on component range</div>
                </div>
                
                <div class="control-group">
                    <label>Bucket Mode</label>
                    <select id="bucketModeInput">
                        <option value="headline">Headline only</option>
                        <option value="combo">Headline + Components</option>
                    </select>
                </div>
                
                <div class="control-group">
                    <label>Y-Axis</label>
                    <select id="preYAxisMode">
                        <option value="be" selected>B/E Multiplier</option>
                        <option value="bps">Bps Move</option>
                    </select>
                </div>
                
                <div class="control-group">
                    <label>Options</label>
                    <div class="checkbox-wrapper">
                        <input type="checkbox" id="includeOutliers" checked>
                        <span>Include outliers (both plots)</span>
                    </div>
                    <div class="checkbox-wrapper" style="margin-top: 8px;">
                        <input type="checkbox" id="noFiltersPreevent">
                        <span>No filters (All events)</span>
                    </div>
                </div>
                
                <div class="control-group">
                    <label>&nbsp;</label>
                    <button class="btn btn-primary" id="updatePreeventBtn" onclick="runPreeventUpdate()">Update Analysis</button>
                </div>
            </div>
            <div id="preeventDataQuality" style="margin: 10px 0 4px; padding: 8px 10px; border-left: 3px solid var(--accent-blue); background: rgba(29,155,240,0.08); color: var(--text-secondary); font-size: 0.82rem;">
                Waiting for analysis update...
            </div>
            
            <div class="charts-container">
                <div class="chart-panel">
                    <h2>
                        {live_badge}B/E by Consensus Std Dev (<span id="preeventBeWindowLabel">Start → Now</span>)
                        <span class="badge" id="boxplotBadge">All Events</span>
                        <span class="badge" id="preeventYAxisBadge" style="background:#334155;">B/E</span>
                        <span class="badge" id="preeventBucketModeBadge" style="background:#0f766e;">Headline</span>
                        <span class="badge" id="preeventSampleWarnBadge" style="display:none; background:#b45309;">Small sample</span>
                    </h2>
                    <div style="margin: -4px 0 10px; color: var(--text-secondary); font-size: 0.82rem;">
                        Bucketized distribution view with deterministic all-buckets rendering.
                    </div>
                    <div id="boxplotChart" style="height: 450px;"></div>
                    <div class="bucket-info" id="userBucketInfo"></div>
                </div>
                
                <div class="chart-panel">
                    <h2 style="display: flex; align-items: center; gap: 15px; flex-wrap: wrap;">
                        <span>Pre-Event Price Paths (<span id="preeventPathsWindowLabel">Start → {event_time}</span>)</span>
                        <span class="badge" id="rawPathsBadge">0 events</span>
                        <label style="display: flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--text-secondary);">
                            <input type="checkbox" id="showStatsLinesPreevent" onchange="updatePreeventPaths()">
                            Show stats lines
                        </label>
                        <button id="renderPreeventPathsBtn" onclick="renderPreeventHeavyPanels()" style="
                            padding: 6px 14px;
                            border-radius: 6px;
                            border: 1px solid var(--accent-orange);
                            background: transparent;
                            color: var(--accent-orange);
                            font-size: 0.85rem;
                            cursor: pointer;
                        ">
                            Render price paths (heavy)
                        </button>
                        <button id="refreshPathsBtn" onclick="refreshPricePaths()" style="
                            padding: 6px 14px;
                            border-radius: 6px;
                            border: 1px solid var(--accent-blue);
                            background: transparent;
                            color: var(--accent-blue);
                            font-size: 0.85rem;
                            cursor: pointer;
                            margin-left: auto;
                            display: flex;
                            align-items: center;
                            gap: 6px;
                        ">
                            <span style="font-size: 1rem;">&#x21bb;</span> Refresh Live Path
                        </button>
                        <button id="resetPreeventViewBtn" onclick="resetPreeventView()" style="
                            padding: 6px 14px;
                            border-radius: 6px;
                            border: 1px solid var(--border-color);
                            background: transparent;
                            color: var(--text-secondary);
                            font-size: 0.85rem;
                            cursor: pointer;
                        ">
                            Reset View
                        </button>
                    </h2>
                    <div id="preeventPathsWarning" style="display:none; margin: 8px 0; padding: 8px 12px; border-radius: 6px; background: rgba(251, 191, 36, 0.12); color: #fbbf24; font-size: 0.85rem;"></div>
                    <div class="raw-paths-stats" id="rawPathsStats">
                        <div class="stats-row">
                            <div class="stat-item">
                                <span class="stat-label">Window (End→Event)</span>
                                <span class="stat-value" id="endToEventRange">--</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Mean (End→Event)</span>
                                <span class="stat-value" id="meanMove">-- bps</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Median (End→Event)</span>
                                <span class="stat-value" id="medianMove">-- bps</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Max+ / Max-</span>
                                <span class="stat-value" id="maxPosNegMove">-- / --</span>
                            </div>
                            <div class="stat-item" style="background: #2d3748; border: 1px solid #ef4444;"{live_stat_style}>
                                <span class="stat-label" style="color: #ef4444;">LIVE Path</span>
                                <span class="stat-value" id="livePathBps" style="color: #fbbf24;">-- bps</span>
                            </div>
                        </div>
                        <div class="stats-row" style="margin-top: 10px;">
                            <div class="stat-item">
                                <span class="stat-label">P25 / P75</span>
                                <span class="stat-value" id="p25p75Move">-- / --</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">P5 / P95</span>
                                <span class="stat-value" id="p5p95Move">-- / --</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">0 bps %</span>
                                <span class="stat-value" id="zeroPctMove">--%</span>
                            </div>
                        </div>
                    </div>
                    <div id="rawPathsChart" style="height: 550px;"></div>
                    <div class="event-details" id="eventDetails">
                        <h3>Selected Event Details</h3>
                        <p style="color: #888;">Click on a price line to see event details</p>
                    </div>
                </div>
            </div>
            
            <!-- Pre-Event Distribution Explorer -->
            <div class="charts-container" style="margin-top: 30px;">
                <div class="chart-panel" style="grid-column: span 2;">
                    <h2>
                        Pre-Event Distribution Explorer
                        <span class="badge" style="background: #8b5cf6;">Return Distribution</span>
                        <span class="badge" id="preeventDistroWindowBadge" style="background: #f59e0b; color: #000;">t-60m → t</span>
                    </h2>
                    
                    <div style="margin-bottom: 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                        <span style="color: var(--text-secondary); margin-right: 10px;">From:</span>
                        <button class="distro-preset-btn" data-from="-1440" onclick="setPreeventDistroFrom(-1440)">t-24h</button>
                        <button class="distro-preset-btn" data-from="-720" onclick="setPreeventDistroFrom(-720)">t-12h</button>
                        <button class="distro-preset-btn" data-from="-480" onclick="setPreeventDistroFrom(-480)">t-8h</button>
                        <button class="distro-preset-btn" data-from="-240" onclick="setPreeventDistroFrom(-240)">t-4h</button>
                        <button class="distro-preset-btn" data-from="-120" onclick="setPreeventDistroFrom(-120)">t-2h</button>
                        <button class="distro-preset-btn" data-from="-60" onclick="setPreeventDistroFrom(-60)">t-1h</button>
                        <button class="distro-preset-btn" data-from="-30" onclick="setPreeventDistroFrom(-30)">t-30m</button>
                        <button class="distro-preset-btn" data-from="-15" onclick="setPreeventDistroFrom(-15)">t-15m</button>
                    </div>
                    <div style="margin-bottom: 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                        <span style="color: var(--text-secondary); margin-right: 10px;">To:</span>
                        <button class="distro-preset-btn" data-to="-120" onclick="setPreeventDistroTo(-120)">t-2h</button>
                        <button class="distro-preset-btn" data-to="-60" onclick="setPreeventDistroTo(-60)">t-1h</button>
                        <button class="distro-preset-btn" data-to="-30" onclick="setPreeventDistroTo(-30)">t-30m</button>
                        <button class="distro-preset-btn" data-to="-15" onclick="setPreeventDistroTo(-15)">t-15m</button>
                        <button class="distro-preset-btn active" data-to="0" onclick="setPreeventDistroTo(0)">t</button>
                    </div>

                    <div style="margin-bottom: 12px; display: flex; gap: 16px; align-items: center;">
                        <label style="display: inline-flex; align-items: center; gap: 6px; color: var(--text-secondary); font-size: 0.9rem;">
                            <input type="checkbox" id="preeventDistroOutliersToggle" checked onchange="updatePreeventDistroWindow()">
                            Include outliers (P5/P95)
                        </label>
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 20px;">
                        <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 15px;">
                            <div id="preeventDistroChart" style="height: 350px;"></div>
                        </div>
                        <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 20px;">
                            <h4 style="color: var(--accent-purple); margin-bottom: 15px;">Window Statistics</h4>
                            <div style="display: flex; flex-direction: column; gap: 12px;">
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Mean:</span>
                                    <span id="preeventDistroStatMean" style="color: var(--text-primary);">-- bps</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Expected Rally:</span>
                                    <span id="preeventDistroStatExpectedRally" style="color: var(--text-primary);">-- bps</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Expected Selloff:</span>
                                    <span id="preeventDistroStatExpectedSelloff" style="color: var(--text-primary);">-- bps</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">P5 / P95:</span>
                                    <span id="preeventDistroStatP5P95" style="color: var(--text-primary);">-- / --</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Min / Max:</span>
                                    <span id="preeventDistroStatMinMax" style="color: var(--text-primary);">-- / --</span>
                                </div>
                                <div class="distro-stat-row" style="margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-color);">
                                    <span style="color: var(--accent-green);">0 bps Percentile / Rally Rate:</span>
                                    <span id="preeventDistroStatZeroPct" style="color: var(--accent-green); font-weight: bold;">--%</span>
                                    <span id="preeventDistroStatRallyRate" style="color: var(--accent-green); font-weight: bold;"> / --%</span>
                                </div>
                            </div>
                            <div id="preeventDistroBucketList" style="margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border-color); color: var(--text-secondary); font-size: 0.85rem;">
                                Click a histogram bar to see dates in that bucket.
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>'''


def generate_preevent_tab_js(config: dict) -> str:
    """
    Generate JavaScript functions for the pre-event tab.
    
    Args:
        config: Dictionary containing:
            - bucket_precision: Precision for bucket labels
            - event_time: Event time string (e.g., '08:30')
            - event_time_hours: Event time as decimal hours (e.g., 8.5)
    
    Returns:
        JavaScript string with pre-event tab functions
    """
    bucket_precision = config.get('bucket_precision', 0)
    event_time = config.get('event_time', '08:30')
    event_time_hours = config.get('event_time_hours', 8.5)
    
    return f'''        // Event time configuration
        let EVENT_TIME = '{event_time}';
        let EVENT_TIME_HOURS = {event_time_hours};

        // Check if UER component is selected (for NFP events)
        function isUerSelected() {{
            return (typeof uerComponentKey !== 'undefined') && uerComponentKey && Array.isArray(selectedComponents) && selectedComponents.includes(uerComponentKey);
        }}

        let preeventSelectedEvent = null;
        let preeventSelectedDate = null;
        let preeventLastBucketEvents = null;
        let preeventLastBucketNote = null;
        let preeventLastDistroEvents = null;
        let preeventHeavyEnabled = false;

        function getActiveEvents() {{
            const base = (typeof getFilteredEvents === 'function') ? getFilteredEvents() : events;
            return Array.isArray(base) ? base : [];
        }}

        function selectHeavyEvents(eventList, maxEvents = 120) {{
            if (!Array.isArray(eventList)) {{
                return {{ events: [], note: null }};
            }}
            if (eventList.length <= maxEvents) {{
                return {{ events: eventList, note: null }};
            }}
            const step = Math.ceil(eventList.length / maxEvents);
            const sampled = eventList.filter((_, idx) => idx % step === 0);
            return {{
                events: sampled,
                note: `Sampled ${{sampled.length}} of ${{eventList.length}} events for performance`
            }};
        }}

        function decimatePrices(prices, maxPoints = 800) {{
            if (!Array.isArray(prices) || prices.length <= maxPoints) return prices;
            const step = Math.ceil(prices.length / maxPoints);
            return prices.filter((_, idx) => idx % step === 0);
        }}

        function pruneHeavyEventDetails(eventList) {{
            if (!Array.isArray(eventList)) return;
            eventList.forEach((evt) => {{
                if (!evt) return;
                delete evt.prices;
                delete evt.postevent_prices;
                delete evt.candlestick_5m;
                delete evt.candlestick;
            }});
        }}
        
        function getActiveComponentKeys() {{
            const keys = Array.isArray(selectedComponents) ? [...selectedComponents] : [];
            if (headlineComponentKey && !keys.includes(headlineComponentKey)) {{
                keys.unshift(headlineComponentKey);
            }}
            return keys;
        }}

        function getComponentStdValue(eventObj, key) {{
            if (!eventObj) return null;
            if (key === headlineComponentKey) return eventObj.cons_std ?? null;
            if (uerComponentKey && key === uerComponentKey) return eventObj.uer_cons_std ?? null;
            const consStdKey = `${{key}}_cons_std`;
            const stdKey = `${{key}}_std`;
            if (eventObj[consStdKey] != null) return eventObj[consStdKey];
            if (eventObj[stdKey] != null) return eventObj[stdKey];
            return null;
        }}

        function computeMean(values) {{
            if (!values.length) return null;
            const sum = values.reduce((a, b) => a + b, 0);
            return sum / values.length;
        }}

        function computeStd(values, mean) {{
            if (!values.length || mean == null) return null;
            const variance = values.reduce((acc, v) => acc + Math.pow(v - mean, 2), 0) / values.length;
            return Math.sqrt(variance);
        }}

        function computeComponentStats(keys, eventsList) {{
            const stats = {{}};
            keys.forEach((key) => {{
                const values = eventsList
                    .map((e) => getComponentStdValue(e, key))
                    .filter((v) => v != null && Number.isFinite(v));
                const mean = computeMean(values);
                let std = null;
                let minZ = null;
                let maxZ = null;
                const epsilon = 1e-6;
                if (values.length > 1 && mean != null) {{
                    std = computeStd(values, mean);
                    const stdAdj = (std != null ? std : 0) + epsilon;
                    values.forEach((v) => {{
                        const z = (v - mean) / stdAdj;
                        if (minZ == null || z < minZ) minZ = z;
                        if (maxZ == null || z > maxZ) maxZ = z;
                    }});
                    std = stdAdj;
                }}
                stats[key] = {{ mean, std, count: values.length, minZ, maxZ }};
            }});
            return stats;
        }}

        function computeZScore(value, stats) {{
            if (value == null || stats?.mean == null || stats?.std == null || stats.std === 0) {{
                return null;
            }}
            let z = (value - stats.mean) / stats.std;
            if (Number.isFinite(stats.minZ) && z < stats.minZ) z = stats.minZ;
            if (Number.isFinite(stats.maxZ) && z > stats.maxZ) z = stats.maxZ;
            return z;
        }}

        function computeTotalScore(eventObj, keys, statsByKey) {{
            let total = 0;
            for (const key of keys) {{
                const value = getComponentStdValue(eventObj, key);
                const z = computeZScore(value, statsByKey[key]);
                if (z == null) return null;
                if (eventType === 'NFP' && uerComponentKey && key === uerComponentKey) {{
                    total += (1 - z);
                }} else {{
                    total += z;
                }}
            }}
            return total;
        }}

        function createScoreBuckets(width, min, max, precision = 2) {{
            if (!Number.isFinite(width) || width <= 0) return [];
            if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
            const range = max - min;
            if (!Number.isFinite(range) || range <= 0) return [];
            const maxBuckets = 500;
            if (range / width > maxBuckets) {{
                return [];
            }}
            const buckets = [];
            const bucketPrecision = Math.max(precision, Math.min(6, Math.ceil(-Math.log10(width)) + 1));
            let start = Math.floor(min / width) * width;
            while (start <= max && buckets.length < maxBuckets) {{
                buckets.push({{
                    min: start,
                    max: start + width,
                    label: `${{start.toFixed(bucketPrecision)}}-${{(start + width).toFixed(bucketPrecision)}}`
                }});
                start += width;
            }}
            return buckets;
        }}
        
        // Pre-event bucket creation (for single component mode)
        function recommendBucketWidth(range, targetBuckets = 5) {{
            if (!Number.isFinite(range) || range <= 0) return null;
            const denom = Math.max(1, targetBuckets - 1);
            return range / denom;
        }}

        function createBuckets(width) {{
            const min = consStdStats.min;
            const max = consStdStats.max;
            const buckets = [];
            const precision = Math.max(bucketPrecision, Math.min(6, Math.ceil(-Math.log10(width)) + 1));
            let start = Math.floor(min / width) * width;
            while (start <= max) {{
                buckets.push({{
                    min: start,
                    max: start + width,
                    label: `${{start.toFixed(precision)}}-${{(start + width).toFixed(precision)}}`
                }});
                start += width;
            }}
            return buckets;
        }}

        function setPreeventBusy(isBusy) {{
            const btn = document.getElementById('updatePreeventBtn');
            if (!btn) return;
            btn.disabled = !!isBusy;
            btn.style.opacity = isBusy ? '0.7' : '1';
            btn.textContent = isBusy ? 'Updating...' : 'Update Analysis';
        }}

        async function runPreeventUpdate() {{
            setPreeventBusy(true);
            try {{
                await updatePreeventDashboard();
            }} finally {{
                setPreeventBusy(false);
            }}
        }}
        
        // Update pre-event dashboard
        async function updatePreeventDashboard() {{
            // Check if required elements exist
            const consStdEl = document.getElementById('consStdInput');
            const bucketWidthEl = document.getElementById('bucketWidthInput');
            const includeOutliersEl = document.getElementById('includeOutliers');
            
            // Exit early if elements not yet in DOM
            if (!consStdEl || !bucketWidthEl || !includeOutliersEl) {{
                return;  // Elements not ready yet
            }}

            const lookbackInput = document.getElementById('preeventLookbackDays');
            const startInput = document.getElementById('windowStartInput');
            const endInput = document.getElementById('windowEndInput');
            if (!window.preeventBucketWidthTouched) {{
                window.preeventBucketWidthTouched = false;
            }}
            if (!bucketWidthEl._bound) {{
                bucketWidthEl.addEventListener('input', () => {{
                    window.preeventBucketWidthTouched = true;
                }});
                bucketWidthEl._bound = true;
            }}
            if (lookbackInput && !lookbackInput._bound) {{
                lookbackInput.addEventListener('change', () => {{
                    if (typeof updatePreEventWindowLabels === 'function') updatePreEventWindowLabels();
                    applyCustomWindow();
                }});
                lookbackInput._bound = true;
            }}
            if (startInput && !startInput._bound) {{
                startInput.addEventListener('change', () => {{
                    if (typeof updatePreEventWindowLabels === 'function') updatePreEventWindowLabels();
                    applyCustomWindow();
                }});
                startInput._bound = true;
            }}
            if (endInput && !endInput._bound) {{
                endInput.addEventListener('change', () => {{
                    if (typeof updatePreEventWindowLabels === 'function') updatePreEventWindowLabels();
                    applyCustomWindow();
                }});
                endInput._bound = true;
            }}
            if (startInput && startInput.dataset.init !== 'true') {{
                startInput.value = '15:00';
                startInput.dataset.init = 'true';
            }}
            if (endInput && endInput.dataset.init !== 'true') {{
                // Default window end to event time (user can override)
                const eventTime = document.getElementById('eventTimeInput')?.value || EVENT_TIME;
                endInput.value = eventTime || '08:30';
                endInput.dataset.init = 'true';
            }}
            if (typeof updatePreEventWindowLabels === 'function') updatePreEventWindowLabels();

            const activeEvents = getActiveEvents();
            const candidateEvents = activeEvents.filter(e => e && e.date);
            if (!preeventHeavyEnabled) {{
                pruneHeavyEventDetails(activeEvents);
            }}
            
            const defaultHeadlineStd = (typeof componentStdDevs !== 'undefined'
                    && typeof headlineComponentKey !== 'undefined'
                    && headlineComponentKey
                    && componentStdDevs[headlineComponentKey] != null)
                ? componentStdDevs[headlineComponentKey]
                : 26;
            const rawHeadlineStd = parseFloat(consStdEl.value);
            const userHeadlineStd = Number.isFinite(rawHeadlineStd) ? rawHeadlineStd : defaultHeadlineStd;

            const uerEl = document.getElementById('uerStdInput');
            const defaultUerStd = (typeof componentStdDevs !== 'undefined'
                    && typeof uerComponentKey !== 'undefined'
                    && uerComponentKey
                    && componentStdDevs[uerComponentKey] != null)
                ? componentStdDevs[uerComponentKey]
                : 0.1;
            const rawUerStd = parseFloat(uerEl?.value || '');
            const userUerStd = Number.isFinite(rawUerStd) ? rawUerStd : defaultUerStd;
            let bucketWidth = parseFloat(bucketWidthEl.value) || 7;
            const includeOutliers = includeOutliersEl.checked;
            const bucketMode = document.getElementById('bucketModeInput')?.value || 'headline';
            const noFilters = document.getElementById('noFiltersPreevent')?.checked || false;
            const yAxisMode = document.getElementById('preYAxisMode')?.value || 'be';
            const activeComponentKeys = getActiveComponentKeys();
            const useComboMode = !noFilters && bucketMode === 'combo' && activeComponentKeys.length > 1;
            if (!useComboMode && !window.preeventBucketWidthTouched) {{
                const range = consStdStats.max - consStdStats.min;
                const recommended = recommendBucketWidth(range);
                if (recommended && Number.isFinite(recommended)) {{
                    bucketWidth = recommended;
                    bucketWidthEl.value = recommended.toFixed(4);
                }}
            }}
            const eventsWithConsStd = activeEvents.filter(e => e && e.cons_std != null).length;
            const eventsWithUerStd = activeEvents.filter(e => e && e.uer_cons_std != null).length;
            const eventsWithBe = activeEvents.filter(e => (e && e.be_by_time && e.be_by_time[EVENT_TIME] > 0)).length;
            const qualityEl = document.getElementById('preeventDataQuality');
            if (qualityEl) {{
                qualityEl.textContent = `Events: ${{activeEvents.length}} | cons std: ${{eventsWithConsStd}} | UER std: ${{eventsWithUerStd}} | realized pre-window: ${{eventsWithBe}}`;
            }}
            const yAxisBadge = document.getElementById('preeventYAxisBadge');
            if (yAxisBadge) yAxisBadge.textContent = yAxisMode === 'bps' ? 'Bps' : 'B/E';
            const modeBadge = document.getElementById('preeventBucketModeBadge');
            if (modeBadge) {{
                if (noFilters) modeBadge.textContent = 'No Filters';
                else modeBadge.textContent = useComboMode ? 'Headline+Components' : 'Headline';
            }}
            
            const boxplotData = [];
            const bucketInfo = document.getElementById('userBucketInfo');
            
            const rawFwdBeInput = parseFloat(document.getElementById('liveFwdInput')?.value || document.getElementById('fwdBeInput')?.value || '');
            const fwdBeInput = (Number.isFinite(rawFwdBeInput) && rawFwdBeInput > 0)
                ? rawFwdBeInput
                : (typeof userFwdBe !== 'undefined' ? userFwdBe : 0);
            const scaleY = (val) => {{
                if (yAxisMode === 'bps') {{
                    return fwdBeInput > 0 ? val * fwdBeInput : val;
                }}
                return val;
            }};
            const yAxisTitle = yAxisMode === 'bps' ? 'Bps Move' : 'B/E Multiplier';

            preeventLastBucketEvents = null;
            preeventLastBucketNote = null;
            preeventLastDistroEvents = null;
            preeventHeavyEnabled = false;

            if (noFilters) {{
                const beValues = activeEvents
                    .map(e => e.be_by_time?.[EVENT_TIME] || 0)
                    .filter(v => v > 0)
                    .map(scaleY);
                if (beValues.length > 0) {{
                    boxplotData.push({{
                        type: 'box',
                        y: beValues,
                        name: 'All Events',
                        boxpoints: includeOutliers ? 'outliers' : false,
                        marker: {{ color: '#1d9bf0' }},
                        line: {{ color: '#1d9bf0' }},
                    }});
                }}
                const layout = {{
                    ...darkLayout,
                    title: 'Pre-Event All Events (at ' + EVENT_TIME + ')',
                    yaxis: {{ ...darkLayout.yaxis, title: yAxisTitle }},
                    showlegend: false,
                }};
                Plotly.newPlot('boxplotChart', boxplotData, layout, {{responsive: true}});
                if (bucketInfo) {{
                    bucketInfo.innerHTML = `
                        <p><strong>No Filters:</strong> using all ${{beValues.length}} events</p>
                    `;
                }}
                const sampleWarnBadge = document.getElementById('preeventSampleWarnBadge');
                if (sampleWarnBadge) sampleWarnBadge.style.display = 'none';
                const badge = document.getElementById('boxplotBadge');
                if (badge) badge.textContent = 'All Events (No filters)';
                preeventLastBucketEvents = activeEvents;
                preeventLastBucketNote = 'All events (no filters)';
                preeventLastDistroEvents = activeEvents;
            }} else if (useComboMode) {{
                // MULTI-COMPONENT MODE: total score = sum of component z-scores
                const componentKeys = activeComponentKeys;
                const componentStats = computeComponentStats(componentKeys, activeEvents);
                const scoredEvents = activeEvents
                    .map((e) => {{
                        const total = computeTotalScore(e, componentKeys, componentStats);
                        return total == null ? null : {{ event: e, total }};
                    }})
                    .filter(Boolean);

                const userComponentValues = {{}};
                componentKeys.forEach((key) => {{
                    if (key === headlineComponentKey) {{
                        userComponentValues[key] = userHeadlineStd;
                    }} else if (uerComponentKey && key === uerComponentKey) {{
                        userComponentValues[key] = userUerStd;
                    }} else {{
                        userComponentValues[key] = componentStdDevs[key];
                    }}
                }});

                const userZScores = {{}};
                let userTotalScore = 0;
                let userScoreValid = true;
                componentKeys.forEach((key) => {{
                    const z = computeZScore(userComponentValues[key], componentStats[key]);
                    userZScores[key] = z;
                    if (z == null) {{
                        userScoreValid = false;
                    }} else if (eventType === 'NFP' && uerComponentKey && key === uerComponentKey) {{
                        userTotalScore += (1 - z);
                    }} else {{
                        userTotalScore += z;
                    }}
                }});

                const totals = scoredEvents.map((s) => s.total);
                if (userScoreValid) totals.push(userTotalScore);
                const minTotal = totals.length ? Math.min(...totals) : 0;
                const maxTotal = totals.length ? Math.max(...totals) : 0;
                if (!window.preeventBucketWidthTouched) {{
                    const range = maxTotal - minTotal;
                    const recommended = recommendBucketWidth(range);
                    if (recommended && Number.isFinite(recommended)) {{
                        bucketWidth = recommended;
                        bucketWidthEl.value = recommended.toFixed(4);
                    }}
                }}
                const scoreBuckets = createScoreBuckets(bucketWidth, minTotal, maxTotal, 2);
                
                const userBucket = userScoreValid
                    ? scoreBuckets.find(b => userTotalScore >= b.min && userTotalScore < b.max)
                    : null;
                const userBucketLabel = userBucket ? userBucket.label : null;

                const scoreCategoryOrder = scoreBuckets.map(b => b.label);
                let selectedBucketEvents = [];
                scoreBuckets.forEach((bucket) => {{
                    const bucketEvents = scoredEvents
                        .filter(s => s.total >= bucket.min && s.total < bucket.max)
                        .map(s => s.event);
                    const beValues = bucketEvents.map(e => e.be_by_time?.[EVENT_TIME] || 0).filter(v => v > 0).map(scaleY);
                    const isUserBucket = userBucketLabel && bucket.label === userBucketLabel;
                    const baseColor = isUserBucket ? '#22c55e' : '#1d9bf0';
                    const trace = {{
                        type: 'box',
                        y: beValues.length ? beValues : [null],
                        name: bucket.label,
                        boxpoints: includeOutliers ? 'outliers' : false,
                        marker: {{ color: baseColor }},
                        line: {{ color: baseColor, width: isUserBucket ? 3 : 1 }},
                        hoverinfo: beValues.length ? 'all' : 'skip',
                    }};
                    boxplotData.push(trace);
                    if (isUserBucket) {{
                        selectedBucketEvents = bucketEvents;
                    }}
                }});

                const layout = {{
                    ...darkLayout,
                    title: 'Pre-Event by Total Component Z-Score (at ' + EVENT_TIME + ')',
                    yaxis: {{ ...darkLayout.yaxis, title: yAxisTitle }},
                    showlegend: false,
                    xaxis: {{ ...darkLayout.xaxis, type: 'category', tickangle: -30, categoryorder: 'array', categoryarray: scoreCategoryOrder }}
                }};

                Plotly.newPlot('boxplotChart', boxplotData, layout, {{responsive: true}});

                const scoreLines = componentKeys.map((key) => {{
                    const label = eventComponentMap?.[key]?.label || key;
                    const z = userZScores[key];
                    const zText = z == null ? 'n/a' : z.toFixed(2);
                    if (eventType === 'NFP' && uerComponentKey && key === uerComponentKey) {{
                        return `<div>${{label}} z-score: <strong>${{zText}}</strong> <span style="color:#fbbf24;">(inverted)</span></div>`;
                    }}
                    return `<div>${{label}} z-score: <strong>${{zText}}</strong></div>`;
                }}).join('');
                const clampedNote = componentKeys.some((k) => Number.isFinite(componentStats[k]?.minZ) || Number.isFinite(componentStats[k]?.maxZ))
                    ? '<p style="font-size: 0.8rem; color: #fbbf24;">Note: z-scores are clipped to the observed range; std uses a small epsilon.</p>'
                    : '';
                const totalText = userScoreValid ? userTotalScore.toFixed(2) : 'n/a';
                bucketInfo.innerHTML = `
                    <p><strong>Multi-Component Score</strong> (sum of component z-scores)</p>
                    <div style="margin: 6px 0;">${{scoreLines}}</div>
                    <p>Total score: <strong>${{totalText}}</strong></p>
                    <p>Your bucket: <span class="highlight">${{userBucketLabel || 'n/a'}}</span></p>
                    <p style="font-size: 0.85rem; color: var(--text-secondary);">
                        z = (component std dev - mean) / std; buckets are based on total score.
                    </p>
                    ${{clampedNote}}
                `;
                if (selectedBucketEvents.length > 0 && selectedBucketEvents.length < 3) {{
                    bucketInfo.innerHTML += `
                        <p style="color: #fbbf24; font-weight: 600;">Warning: only ${{selectedBucketEvents.length}} events in this bucket</p>
                    `;
                }}
                const sampleWarnBadge = document.getElementById('preeventSampleWarnBadge');
                if (sampleWarnBadge) {{
                    sampleWarnBadge.style.display = (selectedBucketEvents.length > 0 && selectedBucketEvents.length < 3) ? 'inline-block' : 'none';
                }}
                preeventLastBucketEvents = selectedBucketEvents.length ? selectedBucketEvents : [];
                preeventLastBucketNote = selectedBucketEvents.length ? 'Selected bucket events' : 'No bucket events';
                preeventLastDistroEvents = preeventLastBucketEvents;

            }} else {{
                // SINGLE COMPONENT MODE (headline only)
                const buckets = createBuckets(bucketWidth);
                const userBucket = buckets.find(b => userHeadlineStd >= b.min && userHeadlineStd < b.max);
                const userBucketLabel = userBucket ? userBucket.label : null;
                const bucketCategoryOrder = buckets.map(b => b.label);

                buckets.forEach((bucket, idx) => {{
                    const bucketEvents = activeEvents.filter(e => 
                        e.cons_std >= bucket.min && e.cons_std < bucket.max
                    );
                    
                    const beValues = bucketEvents.map(e => e.be_by_time?.[EVENT_TIME] || 0).filter(v => v > 0).map(scaleY);
                    const isUserBucket = bucket.label === userBucketLabel;
                    const baseColor = isUserBucket ? '#22c55e' : '#1d9bf0';
                    boxplotData.push({{
                        type: 'box',
                        y: beValues.length ? beValues : [null],
                        name: bucket.label,
                        boxpoints: includeOutliers ? 'outliers' : false,
                        marker: {{ color: baseColor }},
                        line: {{ color: baseColor, width: isUserBucket ? 3 : 1 }},
                        hoverinfo: beValues.length ? 'all' : 'skip',
                    }});
                }});
                
                const layout = {{
                    ...darkLayout,
                    title: 'Pre-Event by Consensus Std Dev (at ' + EVENT_TIME + ')',
                    yaxis: {{ ...darkLayout.yaxis, title: yAxisTitle }},
                    showlegend: false,
                    xaxis: {{ ...darkLayout.xaxis, type: 'category', categoryorder: 'array', categoryarray: bucketCategoryOrder }}
                }};
                
                Plotly.newPlot('boxplotChart', boxplotData, layout, {{responsive: true}});
                
                // Find user's bucket
                if (userBucket) {{
                    const bucketEvents = activeEvents.filter(e => e.cons_std >= userBucket.min && e.cons_std < userBucket.max);
                    const beValues = bucketEvents.map(e => e.be_by_time?.[EVENT_TIME] || 0).filter(v => v > 0).map(scaleY);
                    const avgBe = beValues.length > 0 ? (beValues.reduce((a, b) => a + b, 0) / beValues.length).toFixed(2) : 'N/A';
                    bucketInfo.innerHTML = `
                        <p>Your cons std dev of <strong>${{userHeadlineStd}}</strong> falls in bucket <strong>${{userBucket.label}}</strong></p>
                        <p>Average move in this bucket: <span class="highlight">${{avgBe}}</span> (from ${{beValues.length}} events)</p>
                    `;
                    if (bucketEvents.length > 0 && bucketEvents.length < 3) {{
                        bucketInfo.innerHTML += `
                            <p style="color: #fbbf24; font-weight: 600;">Warning: only ${{bucketEvents.length}} events in this bucket</p>
                        `;
                    }}
                    const sampleWarnBadge = document.getElementById('preeventSampleWarnBadge');
                    if (sampleWarnBadge) {{
                        sampleWarnBadge.style.display = bucketEvents.length < 3 ? 'inline-block' : 'none';
                    }}
                    preeventLastBucketEvents = bucketEvents;
                    preeventLastBucketNote = 'Selected bucket events';
                    preeventLastDistroEvents = bucketEvents;
                }}
            }}

            if (Array.isArray(preeventLastBucketEvents) && preeventLastBucketEvents.length > 0) {{
                // Sample the bucket so large sets stay responsive, then auto-render
                // both the price paths and the distribution explorer (no extra click).
                const heavySelection = selectHeavyEvents(preeventLastBucketEvents);
                const heavyEvents = heavySelection.events;
                const heavyNote = heavySelection.note || preeventLastBucketNote;
                await ensureEventDetailsLoaded(heavyEvents);
                preeventHeavyEnabled = true;
                updatePreeventPaths(heavyEvents, heavyNote);
                if (typeof updatePreeventDistroWindow === 'function') {{
                    updatePreeventDistroWindow(heavyEvents);
                }}

                // Now window.preeventWindow is set by updatePreeventPaths
                const wStart = window.preeventWindow?.start;
                const wEnd = window.preeventWindow?.end;
                if (Number.isFinite(wStart) && Number.isFinite(wEnd)) {{
                    const realized = computeBucketRealizedStats(
                        heavyEvents, wStart, wEnd, fwdBeInput
                    );
                    const nowStr = formatLocalNowTime();
                    let nowMs = timeToSessionMs(nowStr);
                    if (!Number.isFinite(nowMs)) nowMs = wEnd;
                    if (nowMs > wEnd) nowMs = wEnd;
                    if (nowMs < wStart) nowMs = wStart;
                    const realizedNow = computeBucketRealizedStats(
                        heavyEvents, wStart, nowMs, fwdBeInput
                    );
                    if (bucketInfo && realized) {{
                        const startLabel = formatTimeOnly(wStart);
                        const endLabel = formatTimeOnly(wEnd);
                        bucketInfo.innerHTML += `
                            <p style="margin-top: 10px;"><strong>Realized (bucket)</strong> ${{startLabel}} → ${{endLabel}} (n=${{realized.count}})</p>
                            <p>
                              Mult: mean <span class="highlight">${{realized.meanMult.toFixed(2)}}x</span>,
                              med ${{realized.medianMult.toFixed(2)}}x,
                              P25/P75 ${{realized.p25Mult.toFixed(2)}}x / ${{realized.p75Mult.toFixed(2)}}x,
                              P5/P95 ${{realized.p5Mult.toFixed(2)}}x / ${{realized.p95Mult.toFixed(2)}}x
                            </p>
                            <p style="color: var(--text-secondary);">
                              Bps: mean <span class="highlight">${{realized.meanBps.toFixed(1)}}</span>,
                              med ${{realized.medianBps.toFixed(1)}},
                              P25/P75 ${{realized.p25Bps.toFixed(1)}} / ${{realized.p75Bps.toFixed(1)}},
                              P5/P95 ${{realized.p5Bps.toFixed(1)}} / ${{realized.p95Bps.toFixed(1)}}
                            </p>
                        `;
                    }}
                    if (bucketInfo && realizedNow) {{
                        const nowLabel = formatTimeOnly(nowMs);
                        bucketInfo.innerHTML += `
                            <p>Realized so far (${{formatTimeOnly(wStart)}} → ${{nowLabel}}): <span class="highlight">${{realizedNow.avgMult.toFixed(2)}}x</span>
                            (${{realizedNow.avgBps.toFixed(1)}} bps, n=${{realizedNow.count}})</p>
                        `;
                    }}
                }}
            }} else {{
                preeventHeavyEnabled = false;
                const sampleWarnBadge = document.getElementById('preeventSampleWarnBadge');
                if (sampleWarnBadge) sampleWarnBadge.style.display = 'none';
            }}
        }}

        async function renderPreeventHeavyPanels() {{
            const activeEvents = Array.isArray(preeventLastBucketEvents) ? preeventLastBucketEvents : getActiveEvents();
            const selection = selectHeavyEvents(activeEvents);
            await ensureEventDetailsLoaded(selection.events);
            preeventHeavyEnabled = true;
            updatePreeventPaths(selection.events, selection.note || preeventLastBucketNote);
            if (typeof updatePreeventDistroWindow === 'function') {{
                updatePreeventDistroWindow(selection.events);
            }}
        }}
        
        // Update pre-event price paths
        function clearPreeventSelection() {{
            if (!preeventSelectedDate) return;
            preeventSelectedDate = null;
            updatePreeventPaths();
        }}

        function updatePreeventPaths(eventOverride = null, sampleNote = null) {{
            if (typeof updatePreEventWindowLabels === 'function') updatePreEventWindowLabels();
            if (!preeventHeavyEnabled && !eventOverride) {{
                return;
            }}
            const activeEvents = eventOverride || preeventLastBucketEvents || getActiveEvents();
            const traces = [];
            let validEventCount = 0;
            const MAX_EVENTS_PLOTTED = 80;
            const MAX_POINTS_PER_EVENT = 400;
            const MAX_TOTAL_POINTS = 20000;
            const cappedEvents = activeEvents.slice(0, MAX_EVENTS_PLOTTED);
            let warningText = null;
            if (activeEvents.length > MAX_EVENTS_PLOTTED) {{
                warningText = `Capped to ${{MAX_EVENTS_PLOTTED}} events for performance`;
            }}
            const palette = [
                '#1d9bf0', '#22c55e', '#f97316', '#a855f7', '#f43f5e',
                '#14b8a6', '#eab308', '#8b5cf6', '#0ea5e9', '#f59e0b',
                '#10b981', '#ef4444', '#6366f1', '#84cc16', '#06b6d4'
            ];
            const smoothingWindow = 5;
            if (!window.preeventWindow) {{
                window.preeventWindow = {{ start: null, end: null }};
            }}
            window.preeventRawSeries = [];
            window.preeventRelayoutLock = false;

            // smoothSeries/interpolateAt are shared helpers in live_unified_dashboard.py

            function addGapBreaks(tsArr, yArr, maxGapMs = 90 * 60 * 1000) {{
                const xOut = [];
                const yOut = [];
                for (let i = 0; i < tsArr.length; i += 1) {{
                    if (i > 0 && Number.isFinite(tsArr[i]) && Number.isFinite(tsArr[i - 1])) {{
                        if ((tsArr[i] - tsArr[i - 1]) > maxGapMs) {{
                            // Insert null to avoid drawing straight lines across large data gaps.
                            xOut.push(new Date(tsArr[i]));
                            yOut.push(null);
                        }}
                    }}
                    xOut.push(new Date(tsArr[i]));
                    yOut.push(yArr[i]);
                }}
                return {{ x: xOut, y: yOut }};
            }}

            function buildTraces(windowStart, windowEnd) {{
                const out = [];
                const selectedDate = preeventSelectedDate;
                window.preeventRawSeries.forEach((series, idx) => {{
                    const base = interpolateAt(series.timestamps, series.bps, windowStart);
                    const normalized = series.bps.map(v => v - base);
                    const smoothed = smoothSeries(normalized, smoothingWindow);
                    const segmented = addGapBreaks(series.timestamps, smoothed);
                    const isSelected = selectedDate && series.date === selectedDate;
                    const color = isSelected ? '#f59e0b' : palette[idx % palette.length];
                    const width = isSelected ? 4 : 2;
                    const opacity = isSelected ? 1 : (selectedDate ? 0.15 : 0.5);
                    out.push({{
                        x: segmented.x,
                        y: segmented.y,
                        type: 'scattergl',
                        mode: 'lines',
                        name: series.date,
                        line: {{ width: width, color: color }},
                        opacity: opacity,
                        hoverinfo: 'name+y',
                        connectgaps: false,
                    }});
                }});
                return out;
            }}

            // percentile helper is shared in live_unified_dashboard.py

            function buildStatsTraces(windowStart, windowEnd) {{
                const timeSet = new Set();
                const seriesBases = window.preeventRawSeries.map(series => ({{
                    timestamps: series.timestamps,
                    bps: series.bps,
                    base: interpolateAt(series.timestamps, series.bps, windowStart),
                }}));

                seriesBases.forEach(series => {{
                    series.timestamps.forEach(t => {{
                        if (t >= windowStart && t <= windowEnd) timeSet.add(t);
                    }});
                }});

                const times = Array.from(timeSet).sort((a, b) => a - b);
                if (!times.length) return [];

                const mean = [];
                const median = [];
                const p25 = [];
                const p75 = [];
                const p5 = [];
                const p95 = [];

                times.forEach(t => {{
                    const values = seriesBases.map(series => {{
                        const v = interpolateAt(series.timestamps, series.bps, t);
                        return v - series.base;
                    }}).filter(v => Number.isFinite(v));
                    if (!values.length) {{
                        mean.push(null);
                        median.push(null);
                        p25.push(null);
                        p75.push(null);
                        p5.push(null);
                        p95.push(null);
                        return;
                    }}
                    values.sort((a, b) => a - b);
                    const avg = values.reduce((a, b) => a + b, 0) / values.length;
                    mean.push(avg);
                    median.push(percentile(values, 50));
                    p25.push(percentile(values, 25));
                    p75.push(percentile(values, 75));
                    p5.push(percentile(values, 5));
                    p95.push(percentile(values, 95));
                }});

                const x = times.map(t => new Date(t));
                return [
                    {{ x, y: mean, type: 'scatter', mode: 'lines', name: 'Mean', line: {{ color: '#e5e7eb', width: 2 }} }},
                    {{ x, y: median, type: 'scatter', mode: 'lines', name: 'Median', line: {{ color: '#67e8f9', width: 2 }} }},
                    {{ x, y: p25, type: 'scatter', mode: 'lines', name: 'P25', line: {{ color: '#fbbf24', width: 1.5, dash: 'dot' }} }},
                    {{ x, y: p75, type: 'scatter', mode: 'lines', name: 'P75', line: {{ color: '#fbbf24', width: 1.5, dash: 'dot' }} }},
                    {{ x, y: p5, type: 'scatter', mode: 'lines', name: 'P5', line: {{ color: '#ef4444', width: 1, dash: 'dash' }} }},
                    {{ x, y: p95, type: 'scatter', mode: 'lines', name: 'P95', line: {{ color: '#ef4444', width: 1, dash: 'dash' }} }},
                ];
            }}

            const BASE_DATE = new Date('2000-01-01T00:00:00');
            const MS_PER_DAY = 24 * 60 * 60 * 1000;

            function formatTimeOnly(ms) {{
                const d = new Date(ms);
                const pad = (v) => `${{v}}`.padStart(2, '0');
                return `${{pad(d.getHours())}}:${{pad(d.getMinutes())}}`;
            }}

            function timeStrToMinutes(timeStr) {{
                const [h, m] = (timeStr || '').split(':').map(Number);
                if (Number.isNaN(h) || Number.isNaN(m)) return NaN;
                return (h * 60) + m;
            }}

            function sessionMinutesToTime(sessionMinutes) {{
                const minutes = ((sessionMinutes % 1440) + 1440) % 1440;
                const h = Math.floor(minutes / 60);
                const m = minutes % 60;
                const pad = (v) => `${{v}}`.padStart(2, '0');
                const dayOffset = sessionMinutes >= 1440 ? 1 : 0;
                return {{ timeStr: `${{pad(h)}}:${{pad(m)}}`, dayOffset }};
            }}

            function timeToMs(timeStr, dayOffset = 0) {{
                const [h, m] = (timeStr || '').split(':').map(Number);
                if (Number.isNaN(h) || Number.isNaN(m)) return NaN;
                const d = new Date(BASE_DATE.getTime());
                d.setHours(h, m, 0, 0);
                d.setDate(d.getDate() + dayOffset);
                return d.getTime();
            }}

        function timeToSessionMs(timeStr) {{
            const [h, m] = (timeStr || '').split(':').map(Number);
            if (Number.isNaN(h) || Number.isNaN(m)) return NaN;
            const dayOffset = h < 12 ? 1 : 0;
            return timeToMs(timeStr, dayOffset);
        }}

        function getPreEventWindowInputs() {{
            const startInput = document.getElementById('windowStartInput');
            const endInput = document.getElementById('windowEndInput');
            const lookbackInput = document.getElementById('preeventLookbackDays');
            const fallbackStart = document.getElementById('startTimeInput');
            const fallbackEnd = document.getElementById('eventTimeInput');
            return {{ startInput, endInput, lookbackInput, fallbackStart, fallbackEnd }};
        }}

        function hhmmToMinutes(hhmm) {{
            const [h, m] = (hhmm || '').split(':').map(Number);
            if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
            return h * 60 + m;
        }}

        function minutesToHhmm(totalMin) {{
            if (!Number.isFinite(totalMin)) return '00:00';
            const mins = ((Math.round(totalMin) % 1440) + 1440) % 1440;
            const h = Math.floor(mins / 60);
            const m = mins % 60;
            const pad = (v) => `${{v}}`.padStart(2, '0');
            return `${{pad(h)}}:${{pad(m)}}`;
        }}

        function syncPreWindowSliderLabels() {{
            const startInput = document.getElementById('windowStartInput');
            const endInput = document.getElementById('windowEndInput');
            const startLabel = document.getElementById('preWindowStartLabel');
            const endLabel = document.getElementById('preWindowEndLabel');
            if (startLabel && startInput?.value) startLabel.textContent = `${{startInput.value}} (T-x)`;
            if (endLabel && endInput?.value) endLabel.textContent = `${{endInput.value}} (T)`;
        }}

        function bindPreWindowSliders() {{
            const startSlider = document.getElementById('preWindowStartSlider');
            const endSlider = document.getElementById('preWindowEndSlider');
            const startInput = document.getElementById('windowStartInput');
            const endInput = document.getElementById('windowEndInput');
            if (!startSlider || !endSlider || !startInput || !endInput) return;
            if (startSlider.dataset.bound === 'true') return;

            let timer = null;
            const applyDebounced = () => {{
                if (timer) clearTimeout(timer);
                timer = setTimeout(() => applyCustomWindow(), 250);
            }};

            startSlider.addEventListener('input', () => {{
                startInput.value = minutesToHhmm(parseInt(startSlider.value, 10));
                syncPreWindowSliderLabels();
                applyDebounced();
            }});
            endSlider.addEventListener('input', () => {{
                endInput.value = minutesToHhmm(parseInt(endSlider.value, 10));
                syncPreWindowSliderLabels();
                applyDebounced();
            }});
            startInput.addEventListener('change', () => {{
                const mins = hhmmToMinutes(startInput.value);
                if (mins != null) startSlider.value = `${{mins}}`;
                syncPreWindowSliderLabels();
            }});
            endInput.addEventListener('change', () => {{
                const mins = hhmmToMinutes(endInput.value);
                if (mins != null) endSlider.value = `${{mins}}`;
                syncPreWindowSliderLabels();
            }});

            startSlider.dataset.bound = 'true';
            endSlider.dataset.bound = 'true';
            syncPreWindowSliderLabels();
        }}

        function updatePreEventWindowLabels() {{
            const lookbackDays = parseInt(document.getElementById('preeventLookbackDays')?.value || '1', 10);
            const startTimeStr = document.getElementById('windowStartInput')?.value || '15:00';
            const startTime = `${{startTimeStr}} (T-${{Math.max(1, lookbackDays)}})`;
            const eventTime = document.getElementById('eventTimeInput')?.value || EVENT_TIME;
            const liveLabel = document.getElementById('preeventLiveWindowLabel');
            const liveRange = document.getElementById('preeventLiveWindowRange');
            const remaining = document.getElementById('preeventRemainingWindow');
            const fullWindow = document.getElementById('preeventFullWindow');
            const beLabel = document.getElementById('preeventBeWindowLabel');
            const pathsLabel = document.getElementById('preeventPathsWindowLabel');
            const endTimeStr = document.getElementById('windowEndInput')?.value || (document.getElementById('eventTimeInput')?.value || EVENT_TIME);
            const summary = document.getElementById('preeventWindowSummary');
            if (liveLabel) liveLabel.textContent = `${{startTime}} → Now (Realized)`;
            if (liveRange) liveRange.textContent = `${{startTime}} → now`;
            if (remaining) remaining.textContent = `now → ${{eventTime}}`;
            if (fullWindow) fullWindow.textContent = `${{startTime}} → ${{eventTime}}`;
            if (beLabel) beLabel.textContent = `${{startTime}} → Now`;
            if (pathsLabel) pathsLabel.textContent = `${{startTime}} → ${{eventTime}}`;
            if (summary) summary.textContent = `Start ${{startTimeStr}} (T-${{Math.max(1, lookbackDays)}}) → End ${{endTimeStr}} (T)`;
        }}

        function mapPreeventTimestamp(eventObj, ts) {{
            const d = new Date(ts);
            const tsDayUtc = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
            const eventDayUtc = eventObj?.date ? Date.UTC(
                parseInt(eventObj.date.slice(0, 4), 10),
                parseInt(eventObj.date.slice(5, 7), 10) - 1,
                parseInt(eventObj.date.slice(8, 10), 10)
            ) : null;
            const baseOffset = eventDayUtc != null
                ? Math.round((tsDayUtc - eventDayUtc) / MS_PER_DAY) + 1
                : (d.getHours() < 12 ? 1 : 0);
            const mapped = new Date(BASE_DATE.getTime());
            mapped.setHours(d.getHours(), d.getMinutes(), d.getSeconds(), 0);
            mapped.setDate(mapped.getDate() + baseOffset);
            return mapped.getTime();
        }}

        function formatLocalNowTime() {{
            const now = new Date();
            const pad = (v) => `${{v}}`.padStart(2, '0');
            return `${{pad(now.getHours())}}:${{pad(now.getMinutes())}}`;
        }}

        function computeBucketRealizedStats(events, windowStart, windowEnd, fwdFallback) {{
            const multipliers = [];
            const bpsMoves = [];
            (events || []).forEach((e) => {{
                if (!e.prices || !e.prices.length) return;
                const times = e.prices.map(p => mapPreeventTimestamp(e, p.ts));
                const values = e.prices.map(p => p.bps);
                const startVal = interpolateAt(times, values, windowStart);
                const endVal = interpolateAt(times, values, windowEnd);
                const bps = endVal - startVal;
                const fwd = e.fwd_be || e.fwd || fwdFallback;
                if (!Number.isFinite(bps) || !Number.isFinite(fwd) || fwd === 0) return;
                multipliers.push(bps / fwd);
                bpsMoves.push(bps);
            }});
            if (!multipliers.length) return null;
            const sortedMult = [...multipliers].sort((a, b) => a - b);
            const sortedBps = [...bpsMoves].sort((a, b) => a - b);
            const n = sortedMult.length;
            const meanMult = multipliers.reduce((a, b) => a + b, 0) / n;
            const meanBps = bpsMoves.reduce((a, b) => a + b, 0) / n;
            const pct = (arr, p) => {{
                if (!arr.length) return null;
                const idx = Math.min(arr.length - 1, Math.max(0, Math.round((p / 100) * (arr.length - 1))));
                return arr[idx];
            }};
            const medianMult = pct(sortedMult, 50);
            const p25Mult = pct(sortedMult, 25);
            const p75Mult = pct(sortedMult, 75);
            const p5Mult = pct(sortedMult, 5);
            const p95Mult = pct(sortedMult, 95);
            const medianBps = pct(sortedBps, 50);
            const p25Bps = pct(sortedBps, 25);
            const p75Bps = pct(sortedBps, 75);
            const p5Bps = pct(sortedBps, 5);
            const p95Bps = pct(sortedBps, 95);
            // Back-compat names used elsewhere in the UI
            const avgMult = meanMult;
            const avgBps = meanBps;
            return {{
                avgMult, avgBps, count: n,
                meanMult, medianMult, p25Mult, p75Mult, p5Mult, p95Mult,
                meanBps, medianBps, p25Bps, p75Bps, p5Bps, p95Bps,
            }};
        }}
            
            let totalPoints = 0;
            cappedEvents.forEach((event, idx) => {{
                if (event.prices && event.prices.length > 0) {{
                    validEventCount++;
                    const prices = decimatePrices(event.prices, MAX_POINTS_PER_EVENT);
                    totalPoints += prices.length;
                    const timestamps = prices.map(p => mapPreeventTimestamp(event, p.ts));
                    const paired = timestamps
                        .map((ts, i) => ({{ ts, bps: prices[i]?.bps }}))
                        .filter((p) => Number.isFinite(p.ts) && Number.isFinite(p.bps))
                        .sort((a, b) => a.ts - b.ts);
                    const sortedTs = paired.map((p) => p.ts);
                    const sortedBps = paired.map((p) => p.bps);

                    window.preeventRawSeries.push({{
                        date: event.date,
                        timestamps: sortedTs,
                        bps: sortedBps,
                    }});
                }}
            }});

            if (window.preeventRawSeries.length) {{
                let maxTime = null;
                window.preeventRawSeries.forEach(series => {{
                    series.timestamps.forEach((ts) => {{
                        if (maxTime == null || ts > maxTime) {{
                            maxTime = ts;
                        }}
                    }});
                }});
                const {{ startInput, endInput, lookbackInput, fallbackStart, fallbackEnd }} = getPreEventWindowInputs();
                const lookbackDays = Math.min(5, Math.max(1, parseInt(lookbackInput?.value || '1', 10)));
                const startTimeStr = startInput?.value || fallbackStart?.value || '15:00';
                const endTimeStr = endInput?.value || fallbackEnd?.value || EVENT_TIME;
                const endDayOffset = timeStrToMinutes(endTimeStr) < 12 * 60 ? 1 : 0;
                let defaultStart = timeToMs(startTimeStr, 1 - lookbackDays);
                let defaultEnd = timeToMs(endTimeStr, endDayOffset);
                if (Number.isNaN(defaultStart)) defaultStart = maxTime - (2 * 60 * 60 * 1000);
                if (Number.isNaN(defaultEnd)) defaultEnd = maxTime;
                if (defaultEnd <= defaultStart) {{
                    defaultEnd = timeToMs(endTimeStr, 1);
                }}
                if (window.preeventWindow.start == null || window.preeventWindow.end == null) {{
                    window.preeventWindow.start = defaultStart;
                    window.preeventWindow.end = defaultEnd;
                }}
                if (startInput && endInput) {{
                    startInput.value = formatTimeOnly(window.preeventWindow.start);
                    endInput.value = formatTimeOnly(window.preeventWindow.end);
                }}
            }}
            
            const showStatsLines = document.getElementById('showStatsLinesPreevent')?.checked ?? false;
            const eventTimeStr = document.getElementById('eventTimeInput')?.value || EVENT_TIME;
            const eventTimeMs = timeToSessionMs(eventTimeStr);
            const shapes = Number.isFinite(eventTimeMs) ? [{{
                type: 'line',
                x0: eventTimeMs,
                x1: eventTimeMs,
                y0: 0,
                y1: 1,
                yref: 'paper',
                line: {{ color: '#fbbf24', width: 1, dash: 'dot' }},
            }}] : [];
            const layout = {{
                ...darkLayout,
                title: 'Pre-Event Price Paths (normalized to window start)',
                xaxis: {{ 
                    ...darkLayout.xaxis, 
                    title: 'Time of Day',
                    range: [window.preeventWindow.start, window.preeventWindow.end],
                    type: 'date',
                    tickformat: '%H:%M',
                    rangeslider: {{ visible: false }},
                }},
                yaxis: {{ ...darkLayout.yaxis, title: 'Move (bps)' }},
                showlegend: showStatsLines,
                hovermode: 'closest',
                dragmode: 'pan',
                shapes,
            }};

            const primaryTraces = showStatsLines
                ? buildStatsTraces(window.preeventWindow.start, window.preeventWindow.end)
                : buildTraces(window.preeventWindow.start, window.preeventWindow.end);
            traces.push(...(primaryTraces.length ? primaryTraces : buildTraces(window.preeventWindow.start, window.preeventWindow.end)));
            const plotConfig = {{responsive: true, scrollZoom: true}};
            Plotly.react('rawPathsChart', traces, layout, plotConfig).then(() => {{
                const chart = document.getElementById('rawPathsChart');
                if (!chart._preeventRelayoutBound) {{
                    chart.on('plotly_relayout', (evt) => {{
                        if (window.preeventRelayoutLock) return;
                        if (!evt) return;
                        let start = null;
                        let end = null;
                        if (evt['xaxis.range[0]'] != null && evt['xaxis.range[1]'] != null) {{
                            start = Date.parse(evt['xaxis.range[0]']);
                            end = Date.parse(evt['xaxis.range[1]']);
                        }} else if (Array.isArray(evt['xaxis.range'])) {{
                            start = Date.parse(evt['xaxis.range'][0]);
                            end = Date.parse(evt['xaxis.range'][1]);
                        }} else if (evt['xaxis.autorange'] === true && chart.layout?.xaxis?.range) {{
                            start = Date.parse(chart.layout.xaxis.range[0]);
                            end = Date.parse(chart.layout.xaxis.range[1]);
                        }}
                        if (!Number.isNaN(start) && !Number.isNaN(end)) {{
                            window.preeventWindow.start = start;
                            window.preeventWindow.end = end;
                            const startInput = document.getElementById('windowStartInput');
                            const endInput = document.getElementById('windowEndInput');
                            if (startInput) startInput.value = formatTimeOnly(start);
                            if (endInput) endInput.value = formatTimeOnly(end);
                            window.preeventRelayoutLock = true;
                            const nextTraces = showStatsLines
                                ? buildStatsTraces(start, end)
                                : buildTraces(start, end);
                            Plotly.react('rawPathsChart', nextTraces, {{
                                ...layout,
                                xaxis: {{ ...layout.xaxis, range: [start, end] }},
                            }}, plotConfig).finally(() => {{
                                window.preeventRelayoutLock = false;
                            }});
                        }}
                    }});
                    chart._preeventRelayoutBound = true;
                }}
                if (!chart._preeventClickBound) {{
                    chart.on('plotly_click', (clickData) => {{
                        if (showStatsLines) return;
                        if (!clickData.points || clickData.points.length === 0) return;
                        const traceIdx = clickData.points[0].curveNumber;
                        const series = window.preeventRawSeries[traceIdx];
                        if (!series) return;
                        if (preeventSelectedDate && preeventSelectedDate === series.date) {{
                            clearPreeventSelection();
                            return;
                        }}
                        preeventSelectedDate = series.date;
                        const clickedEvent = events.find(e => e.date === series.date);
                        if (clickedEvent) {{
                            showEventDetailsPreevent(clickedEvent);
                        }}
                    }});
                    chart.on('plotly_doubleclick', () => {{
                        clearPreeventSelection();
                    }});
                    chart._preeventClickBound = true;
                }}
            }});
            const rawBadge = document.getElementById('rawPathsBadge');
            if (rawBadge) {{
                rawBadge.textContent = `${{validEventCount}} events${{sampleNote ? ' (sampled)' : ''}}`;
                rawBadge.title = sampleNote || '';
            }}

            const warningEl = document.getElementById('preeventPathsWarning');
            if (warningEl) {{
                if (totalPoints > MAX_TOTAL_POINTS) {{
                    warningEl.textContent = `Skipped render: ${{totalPoints}} points exceeds cap (${{MAX_TOTAL_POINTS}}). Narrow the bucket or reduce events.`;
                    warningEl.style.display = 'block';
                    return;
                }}
                if (warningText) {{
                    warningEl.textContent = warningText;
                    warningEl.style.display = 'block';
                }} else {{
                    warningEl.textContent = '';
                    warningEl.style.display = 'none';
                }}
            }}
            
            // Calculate summary stats: move from window END → event time
            const endMs = window.preeventWindow?.end;
            const remainingMoves = [];
            if (Number.isFinite(endMs) && Number.isFinite(eventTimeMs)) {{
                window.preeventRawSeries.forEach((series) => {{
                    const endVal = interpolateAt(series.timestamps, series.bps, endMs);
                    const eventVal = interpolateAt(series.timestamps, series.bps, eventTimeMs);
                    const move = eventVal - endVal;
                    if (Number.isFinite(move)) remainingMoves.push(move);
                }});
            }}
            if (remainingMoves.length > 0) {{
                const sorted = [...remainingMoves].sort((a, b) => a - b);
                const mean = remainingMoves.reduce((a, b) => a + b, 0) / remainingMoves.length;
                const med = (typeof percentile === 'function') ? percentile(sorted, 50) : sorted[Math.floor(sorted.length / 2)];
                const p25 = (typeof percentile === 'function') ? percentile(sorted, 25) : sorted[Math.floor(sorted.length * 0.25)];
                const p75 = (typeof percentile === 'function') ? percentile(sorted, 75) : sorted[Math.floor(sorted.length * 0.75)];
                const p5 = (typeof percentile === 'function') ? percentile(sorted, 5) : sorted[Math.floor(sorted.length * 0.05)];
                const p95 = (typeof percentile === 'function') ? percentile(sorted, 95) : sorted[Math.floor(sorted.length * 0.95)];
                const zeroPct = (typeof computePercentileOfZero === 'function')
                    ? computePercentileOfZero(sorted)
                    : ((sorted.filter(v => v < 0).length / sorted.length) * 100);
                const maxPos = sorted[sorted.length - 1];
                const maxNeg = sorted[0];
                document.getElementById('meanMove').textContent = `${{mean.toFixed(1)}} bps`;
                document.getElementById('medianMove').textContent = `${{med.toFixed(1)}} bps`;
                const rangeEl = document.getElementById('endToEventRange');
                if (rangeEl && Number.isFinite(endMs)) {{
                    const endLabel = formatTimeOnly(endMs);
                    const evtLabel = eventTimeStr || EVENT_TIME;
                    rangeEl.textContent = `${{endLabel}} → ${{evtLabel}}`;
                }}
                const maxEl = document.getElementById('maxPosNegMove');
                if (maxEl) {{
                    maxEl.textContent = `+${{maxPos.toFixed(1)}} / ${{maxNeg.toFixed(1)}}`;
                }}
                const p25p75 = document.getElementById('p25p75Move');
                if (p25p75) p25p75.textContent = `${{p25.toFixed(1)}} / ${{p75.toFixed(1)}}`;
                const p5p95 = document.getElementById('p5p95Move');
                if (p5p95) p5p95.textContent = `${{p5.toFixed(1)}} / ${{p95.toFixed(1)}}`;
                const zeroEl = document.getElementById('zeroPctMove');
                if (zeroEl) zeroEl.textContent = `${{zeroPct.toFixed(1)}}%`;
            }} else {{
                const rangeEl = document.getElementById('endToEventRange');
                if (rangeEl) rangeEl.textContent = '--';
                const maxEl = document.getElementById('maxPosNegMove');
                if (maxEl) maxEl.textContent = '-- / --';
                const p25p75 = document.getElementById('p25p75Move');
                if (p25p75) p25p75.textContent = '-- / --';
                const p5p95 = document.getElementById('p5p95Move');
                if (p5p95) p5p95.textContent = '-- / --';
                const zeroEl = document.getElementById('zeroPctMove');
                if (zeroEl) zeroEl.textContent = '--%';
            }}
        }}

        function applyCustomWindow() {{
            const {{ startInput, endInput, lookbackInput }} = getPreEventWindowInputs();
            if (!startInput || !endInput || !lookbackInput) return;
            const lookbackDays = Math.min(5, Math.max(1, parseInt(lookbackInput.value || '1', 10)));
            const startTimeStr = startInput.value || '15:00';
            const endTimeStr = endInput.value || (document.getElementById('eventTimeInput')?.value || EVENT_TIME);
            const endDayOffset = timeStrToMinutes(endTimeStr) < 12 * 60 ? 1 : 0;
            let start = timeToMs(startTimeStr, 1 - lookbackDays);
            let end = timeToMs(endTimeStr, endDayOffset);
            if (Number.isNaN(start) || Number.isNaN(end)) return;
            if (end <= start) {{
                end = timeToMs(endTimeStr, 1);
            }}
            window.preeventWindow = {{ start, end }};
            if (typeof updatePreEventWindowLabels === 'function') updatePreEventWindowLabels();
            bindPreWindowSliders();
            const startSlider = document.getElementById('preWindowStartSlider');
            const endSlider = document.getElementById('preWindowEndSlider');
            if (startInput && startSlider && startInput.value) {{
                const mins = hhmmToMinutes(startInput.value);
                if (mins != null) startSlider.value = `${{mins}}`;
            }}
            if (endInput && endSlider && endInput.value) {{
                const mins = hhmmToMinutes(endInput.value);
                if (mins != null) endSlider.value = `${{mins}}`;
            }}
            syncPreWindowSliderLabels();
            updatePreeventPaths();
        }}

        function resetPreeventView() {{
            window.preeventWindow = {{ start: null, end: null }};
            updatePreeventPaths();
        }}

        let preeventDistroFromMin = -60;
        let preeventDistroToMin = 0;
        const PREEVENT_DISTRO_BASE_DATE = new Date('2000-01-01T00:00:00');

        function formatPreeventDistroLabel(minutes) {{
            if (minutes === 0) return 't';
            const abs = Math.abs(minutes);
            if (abs % 60 === 0) return `t-${{abs / 60}}h`;
            return `t-${{abs}}m`;
        }}

        function setPreeventDistroFrom(minutes) {{
            preeventDistroFromMin = minutes;
            document.querySelectorAll('#preeventTab .distro-preset-btn[data-from]').forEach(btn => {{
                btn.classList.toggle('active', parseInt(btn.dataset.from) === minutes);
            }});
            updatePreeventDistroWindow();
        }}

        function setPreeventDistroTo(minutes) {{
            preeventDistroToMin = minutes;
            document.querySelectorAll('#preeventTab .distro-preset-btn[data-to]').forEach(btn => {{
                btn.classList.toggle('active', parseInt(btn.dataset.to) === minutes);
            }});
            updatePreeventDistroWindow();
        }}

        function preeventTimeToSessionMs(timeStr) {{
            const [h, m] = (timeStr || '').split(':').map(Number);
            if (Number.isNaN(h) || Number.isNaN(m)) return NaN;
            const dayOffset = h < 12 ? 1 : 0;
            const d = new Date(PREEVENT_DISTRO_BASE_DATE.getTime());
            d.setHours(h, m, 0, 0);
            d.setDate(d.getDate() + dayOffset);
            return d.getTime();
        }}

        function preeventInterpolateAt(times, values, targetTime) {{
            if (!times.length) return 0;
            if (targetTime <= times[0]) return values[0];
            if (targetTime >= times[times.length - 1]) return values[values.length - 1];
            for (let i = 0; i < times.length - 1; i++) {{
                const t0 = times[i];
                const t1 = times[i + 1];
                if (targetTime >= t0 && targetTime <= t1) {{
                    const v0 = values[i];
                    const v1 = values[i + 1];
                    const t = (targetTime - t0) / (t1 - t0 || 1);
                    return v0 + (v1 - v0) * t;
                }}
            }}
            return values[values.length - 1];
        }}

        function updatePreeventDistroWindow(eventOverride = null) {{
            const badge = document.getElementById('preeventDistroWindowBadge');
            if (badge) {{
                badge.textContent = `${{formatPreeventDistroLabel(preeventDistroFromMin)}} → ${{formatPreeventDistroLabel(preeventDistroToMin)}}`;
            }}
            if (!preeventHeavyEnabled && !eventOverride) {{
                return;
            }}
            const eventTimeStr = document.getElementById('eventTimeInput')?.value || EVENT_TIME;
            const eventTimeMs = preeventTimeToSessionMs(eventTimeStr);
            if (Number.isNaN(eventTimeMs)) return;
            const fromMs = eventTimeMs + preeventDistroFromMin * 60 * 1000;
            const toMs = eventTimeMs + preeventDistroToMin * 60 * 1000;
            if (toMs <= fromMs) return;

            const moveEntries = [];
            const activeEvents = eventOverride || preeventLastDistroEvents || getActiveEvents();
            activeEvents.forEach(event => {{
                const prices = decimatePrices(event.prices || []);
                if (prices.length < 2) return;
                const mappedPrices = prices.map(p => {{
                    const d = new Date(p.ts);
                    const dayOffset = d.getHours() < 12 ? 1 : 0;
                    const mapped = new Date(PREEVENT_DISTRO_BASE_DATE.getTime());
                    mapped.setHours(d.getHours(), d.getMinutes(), d.getSeconds(), 0);
                    mapped.setDate(mapped.getDate() + dayOffset);
                    return {{ ts: mapped.getTime(), bps: p.bps }};
                }});
                const times = mappedPrices.map(p => p.ts);
                const values = mappedPrices.map(p => p.bps);
                const startVal = preeventInterpolateAt(times, values, fromMs);
                const endVal = preeventInterpolateAt(times, values, toMs);
                moveEntries.push({{ date: event.date, move: endVal - startVal }});
            }});

            renderPreeventDistroExplorer(moveEntries);
        }}

        function renderPreeventDistroExplorer(entries) {{
            const chartDiv = document.getElementById('preeventDistroChart');
            if (!entries || entries.length === 0) {{
                chartDiv.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 60px;">No data for this window</div>';
                document.getElementById('preeventDistroStatMean').textContent = '-- bps';
                document.getElementById('preeventDistroStatExpectedRally').textContent = '-- bps';
                document.getElementById('preeventDistroStatExpectedSelloff').textContent = '-- bps';
                document.getElementById('preeventDistroStatP5P95').textContent = '-- / --';
                document.getElementById('preeventDistroStatMinMax').textContent = '-- / --';
                document.getElementById('preeventDistroStatZeroPct').textContent = '--%';
                document.getElementById('preeventDistroStatRallyRate').textContent = ' / --%';
                const bucketList = document.getElementById('preeventDistroBucketList');
                if (bucketList) bucketList.textContent = 'Click a histogram bar to see dates in that bucket.';
                return;
            }}

            const includeOutliers = document.getElementById('preeventDistroOutliersToggle')?.checked ?? true;
            const values = entries.map(e => e.move);
            const sortedAll = [...values].sort((a, b) => a - b);
            const nAll = sortedAll.length;
            const p5Val = sortedAll[Math.floor(nAll * 0.05)];
            const p95Val = sortedAll[Math.min(Math.floor(nAll * 0.95), nAll - 1)];
            const filteredEntries = includeOutliers
                ? entries
                : entries.filter(e => e.move >= p5Val && e.move <= p95Val);
            const filteredValues = filteredEntries.map(e => e.move);
            const sorted = [...filteredValues].sort((a, b) => a - b);
            const n = sorted.length;
            if (n === 0) {{
                chartDiv.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 60px;">No data for this window</div>';
                document.getElementById('preeventDistroStatMean').textContent = '-- bps';
                document.getElementById('preeventDistroStatExpectedRally').textContent = '-- bps';
                document.getElementById('preeventDistroStatExpectedSelloff').textContent = '-- bps';
                document.getElementById('preeventDistroStatP5P95').textContent = '-- / --';
                document.getElementById('preeventDistroStatMinMax').textContent = '-- / --';
                document.getElementById('preeventDistroStatZeroPct').textContent = '--%';
                document.getElementById('preeventDistroStatRallyRate').textContent = ' / --%';
                const bucketList = document.getElementById('preeventDistroBucketList');
                if (bucketList) bucketList.textContent = 'Click a histogram bar to see dates in that bucket.';
                return;
            }}

            const mean = filteredValues.reduce((a, b) => a + b, 0) / n;
            const variance = filteredValues.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (n - 1 || 1);
            const min = sorted[0];
            const max = sorted[n - 1];
            const p5 = sorted[Math.floor(n * 0.05)];
            const p95 = sorted[Math.min(Math.floor(n * 0.95), n - 1)];
            const zeroPct = typeof computePercentileOfZero === 'function' ? computePercentileOfZero(sorted) : null;
            const rallyValues = filteredValues.filter(v => v > 0);
            const selloffValues = filteredValues.filter(v => v < 0);
            const expectedRally = rallyValues.length ? rallyValues.reduce((a, b) => a + b, 0) / rallyValues.length : 0;
            const expectedSelloff = selloffValues.length ? selloffValues.reduce((a, b) => a + b, 0) / selloffValues.length : 0;
            const rallyRate = (rallyValues.length / n) * 100;

            document.getElementById('preeventDistroStatMean').textContent = `${{mean.toFixed(2)}} bps`;
            document.getElementById('preeventDistroStatExpectedRally').textContent = `${{expectedRally.toFixed(2)}} bps`;
            document.getElementById('preeventDistroStatExpectedSelloff').textContent = `${{expectedSelloff.toFixed(2)}} bps`;
            document.getElementById('preeventDistroStatP5P95').textContent = `${{p5.toFixed(1)}} / ${{p95.toFixed(1)}}`;
            document.getElementById('preeventDistroStatMinMax').textContent = `${{min.toFixed(1)}} / ${{max.toFixed(1)}}`;
            document.getElementById('preeventDistroStatZeroPct').textContent = zeroPct !== null ? `${{zeroPct.toFixed(1)}}%` : '--%';
            document.getElementById('preeventDistroStatRallyRate').textContent = ` / ${{rallyRate.toFixed(1)}}%`;

            const range = max - min || 1;
            const stdDev = Math.sqrt(variance);
            let binWidth = 3.49 * stdDev / Math.cbrt(n);
            if (!Number.isFinite(binWidth) || binWidth <= 0) {{
                binWidth = range / 10;
            }}
            let binCount = Math.ceil(range / binWidth);
            binCount = Math.min(50, Math.max(5, binCount));
            const binSize = range / binCount || 1;
            const kMin = Math.floor(min / binSize);
            const kMax = Math.ceil(max / binSize);
            const binStart = kMin * binSize;
            const binEnd = kMax * binSize;
            window.preeventDistroEntries = filteredEntries;
            window.preeventDistroBinSize = binSize;

            const histTrace = {{
                x: filteredValues,
                type: 'histogram',
                histnorm: 'probability density',
                opacity: 0.6,
                name: 'Histogram',
                marker: {{ color: '#9b59b6' }},
                xbins: {{ start: binStart, end: binEnd, size: binSize }},
            }};

            const kde = typeof gaussianKDE === 'function' ? gaussianKDE(filteredValues) : null;
            const xRange = max - min || 1;
            const xPadding = xRange * 0.15;
            const xGrid = [];
            const yGrid = [];
            if (kde) {{
                for (let i = 0; i < 200; i++) {{
                    const x = min - xPadding + (xRange + 2 * xPadding) * i / 199;
                    xGrid.push(x);
                    yGrid.push(kde(x));
                }}
            }}

            const traces = [histTrace];
            if (kde) {{
                traces.push({{
                    x: xGrid,
                    y: yGrid,
                    type: 'scatter',
                    mode: 'lines',
                    name: 'KDE',
                    line: {{ color: '#22c55e', width: 2 }},
                }});
            }}

            const shapes = [{{
                type: 'line',
                x0: 0, x1: 0,
                y0: 0, y1: 1,
                yref: 'paper',
                line: {{ color: '#ef4444', width: 2, dash: 'dash' }},
            }}];

            const annotations = [{{
                x: 0,
                y: 1.02,
                yref: 'paper',
                text: `0 bps @ P${{zeroPct?.toFixed(0) || '--'}}`,
                showarrow: false,
                font: {{ color: '#ef4444', size: 10 }},
            }}];

            // Mark the selected event's move (if any) for this window
            const selectedDate = preeventSelectedDate;
            if (selectedDate) {{
                const selectedEntry = (filteredEntries || []).find(e => e.date === selectedDate);
                if (selectedEntry) {{
                    shapes.push({{
                        type: 'line',
                        x0: selectedEntry.move, x1: selectedEntry.move,
                        y0: 0, y1: 1,
                        yref: 'paper',
                        line: {{ color: '#f59e0b', width: 2, dash: 'dot' }},
                    }});
                    annotations.push({{
                        x: selectedEntry.move,
                        y: 1.02,
                        yref: 'paper',
                        text: `${{selectedDate}}: ${{selectedEntry.move.toFixed(2)}} bps`,
                        showarrow: false,
                        font: {{ color: '#f59e0b', size: 10 }},
                    }});
                }}
            }}

            const layout = {{
                ...darkLayout,
                title: '',
                xaxis: {{ 
                    ...darkLayout.xaxis, 
                    title: 'Move (bps)',
                    zeroline: true,
                    zerolinecolor: '#ef4444',
                }},
                yaxis: {{ 
                    ...darkLayout.yaxis, 
                    title: 'Density',
                }},
                barmode: 'overlay',
                shapes: shapes,
                annotations: annotations,
            }};

            Plotly.react('preeventDistroChart', traces, layout, {{ responsive: true }});
            const bucketList = document.getElementById('preeventDistroBucketList');
            if (bucketList) bucketList.textContent = 'Click a histogram bar to see dates in that bucket.';

            chartDiv.on('plotly_click', (clickData) => {{
                if (!clickData.points || clickData.points.length === 0) return;
                const point = clickData.points[0];
                const binCenter = point.x;
                const size = window.preeventDistroBinSize || binSize;
                const binStart = binCenter - size / 2;
                const binEnd = binCenter + size / 2;
                const entriesInBin = (window.preeventDistroEntries || []).filter(e => e.move >= binStart && e.move < binEnd);
                if (!bucketList) return;
                if (!entriesInBin.length) {{
                    bucketList.textContent = 'No events in this bin.';
                    return;
                }}
                const items = entriesInBin
                    .sort((a, b) => b.move - a.move)
                    .map(e => `
                        <div style="display:flex; justify-content: space-between; align-items: center; gap: 8px;">
                            <button style="background:none; border:none; color: var(--accent-blue); cursor:pointer; padding:0;" onclick="selectPreeventEventDate('${{e.date}}')">${{e.date}}</button>
                            <span>${{e.move.toFixed(2)}} bps</span>
                        </div>
                    `)
                    .join('');
                bucketList.innerHTML = `<div style="margin-bottom:6px; color: var(--text-primary);">Bin ${{binStart.toFixed(2)}} to ${{binEnd.toFixed(2)}} bps (${{entriesInBin.length}})</div>${{items}}`;
            }});
        }}

        function selectPreeventEventDate(date) {{
            if (!date) return;
            const event = getActiveEvents().find(e => e.date === date);
            if (!event) return;
            showEventDetailsPreevent(event);
        }}

        setTimeout(() => {{
            if (typeof events !== 'undefined' && events.length > 0) {{
                updatePreeventDistroWindow();
            }}
        }}, 1000);

        function showEventDetailsPreevent(event) {{
            const panel = document.getElementById('eventDetails');
            if (!panel || !event) return;
            preeventSelectedEvent = event;
            preeventSelectedDate = event.date;
            const consStd = event.cons_std != null ? event.cons_std : 'N/A';
            const beVal = event.be_by_time?.[EVENT_TIME] ? event.be_by_time[EVENT_TIME].toFixed(2) : 'N/A';
            panel.innerHTML = `
                <h3>Selected Event Details</h3>
                <p><strong>Date:</strong> ${{event.date}}</p>
                <p><strong>Cons Std Dev:</strong> ${{consStd}}</p>
                <p><strong>B/E Multiplier (at ${{EVENT_TIME}}):</strong> ${{beVal}}x</p>
                <button class="btn btn-secondary" onclick="openSelectedPreeventCandlestick()">View Candlestick</button>
            `;
            if (typeof updatePreeventPaths === 'function') {{
                updatePreeventPaths();
            }}
        }}

        function openSelectedPreeventCandlestick() {{
            if (!preeventSelectedEvent) return;
            if (typeof openCandlestickModal === 'function') {{
                openCandlestickModal(preeventSelectedEvent);
            }}
        }}
        '''

