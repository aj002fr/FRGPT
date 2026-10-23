"""
Post-Event Tab Module
---------------------
Generates HTML and JavaScript for the post-event analysis tab.
"""


def generate_postevent_tab_html(config: dict) -> str:
    """
    Generate HTML content for the post-event tab.
    
    Args:
        config: Dictionary containing:
            - user_fwd_be: User's forward breakeven value
            - default_surprise_percentile: Recommended surprise percentile for first load
            - default_post_bucket_width: Recommended percentile bucket width for first load
    
    Returns:
        HTML string for the post-event tab content
    """
    user_fwd_be = config.get('user_fwd_be', 6.384)
    default_surprise_percentile = config.get('default_surprise_percentile', 50.0)
    default_post_bucket_width = config.get('default_post_bucket_width', 20.0)
    
    return f'''        <!-- POST-EVENT TAB -->
        <div class="tab-content" id="posteventTab">
            <!-- LIVE STATS PANEL (matches standalone post-event) -->
            <div id="liveStatsPanelPost" class="live-data-panel post-event">
                <div class="live-header" style="border-bottom-color: var(--accent-purple);">
                    <span class="live-indicator" style="background: var(--accent-purple);"></span>
                    <span class="live-title" style="color: var(--accent-purple);">LIVE DATA (POST-EVENT)</span>
                    <span id="liveTimestampPost" class="live-timestamp">--:--:--</span>
                </div>
                <div class="live-stats-grid">
                    <div class="live-stat">
                        <div class="live-label">TY Price</div>
                        <div id="livePricePost" class="live-value" style="color: var(--accent-purple);">---</div>
                        <div id="livePrice32Post" class="live-sublabel">---</div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">Reference Price</div>
                        <div id="liveRefPost" class="live-value" style="color: #8b98a5;">---</div>
                        <div id="liveRefTimestampPost" class="live-sublabel">Release Price</div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">FWD Breakeven</div>
                        <div class="live-value">
                            <input type="number" id="liveFwdInputPost" value="{user_fwd_be}" step="0.1" min="0.1" max="20" 
                                   style="width: 70px; background: var(--bg-secondary); border: 1px solid var(--accent-purple); 
                                          color: var(--text-primary); padding: 4px 8px; border-radius: 4px; font-size: 1.1rem;">
                            <span style="color: var(--text-secondary); font-size: 0.9rem;"> bps</span>
                        </div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">High / Low</div>
                        <div id="liveHighLowPost" class="live-value" style="font-size: 0.9rem; color: var(--accent-purple);">--- / ---</div>
                        <div id="liveMoveUpDownPost" class="live-sublabel">↑-- / ↓--</div>
                    </div>
                    <div class="live-stat">
                        <div class="live-label">B/E Realized</div>
                        <div id="liveBeRealizedPost" class="live-value" style="color: var(--accent-green);">--- bps</div>
                        <div class="live-sublabel" style="color: #888;">since 8:30am release</div>
                    </div>
                    <div class="live-stat highlight" style="border-color: var(--accent-purple);">
                        <div class="live-label">Post-Event B/E</div>
                        <div id="liveBeMultiplierPost" class="live-value" style="color: var(--accent-purple);">---x</div>
                    </div>
                </div>
                <div class="live-status">
                    <span id="liveStatusTextPost">Connecting to live data...</span>
                    <button id="refreshBtnPost" onclick="refreshLiveData()" style="margin-left: 15px; padding: 4px 12px; 
                            background: var(--accent-purple); border: none; border-radius: 4px; color: white; cursor: pointer;">
                        Refresh
                    </button>
                </div>
            </div>
            
            <div class="controls">
                <div style="grid-column: span 2; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent-purple); margin-bottom: -4px;">
                    Analysis Configuration
                </div>
                <div class="control-group">
                    <label>Your Surprise Percentile</label>
                    <input type="number" id="surprisePercentileInput" value="{default_surprise_percentile}" step="5" min="0" max="100">
                </div>
                
                <div class="control-group">
                    <label>Percentile Bucket Width</label>
                    <input type="number" id="postBucketWidthInput" value="{default_post_bucket_width}" step="5" min="10" max="50">
                </div>
                
                <div class="control-group">
                    <label>Y-Axis</label>
                    <select id="postYAxisMode">
                        <option value="be" selected>B/E Multiplier</option>
                        <option value="bps">Bps Move</option>
                    </select>
                </div>
                
                <div class="control-group">
                    <label>Options</label>
                    <div class="checkbox-wrapper">
                        <input type="checkbox" id="includeOutliersPost" checked>
                        <span>Include outliers</span>
                    </div>
                    <div class="checkbox-wrapper" style="margin-top: 8px;">
                        <input type="checkbox" id="filterByBucketPost">
                        <span>Filter paths by bucket</span>
                    </div>
                    <div class="checkbox-wrapper" style="margin-top: 8px;">
                        <input type="checkbox" id="noFiltersPost">
                        <span>No filters (All events)</span>
                    </div>
                </div>
                
                <div class="control-group">
                    <label>&nbsp;</label>
                    <button class="btn btn-primary" id="updatePosteventBtn" style="background: var(--accent-purple);" onclick="runPosteventUpdate()">Update Analysis</button>
                </div>

                <div class="control-group" style="grid-column: span 2;">
                    <label>Post-Event Window (Time of Day)</label>
                    <div style="display: grid; grid-template-columns: auto 1fr auto; gap: 10px 12px; align-items: center;">
                        <label style="color: var(--text-secondary);">Start slider</label>
                        <div>
                            <input type="range" id="postWindowStartSlider" min="0" max="1439" step="1" value="510" style="width: 100%;">
                            <div id="postWindowStartLabel" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 3px;">08:30 (T)</div>
                        </div>
                        <input type="time" id="windowStartInputPost" style="width: 120px;">
                        <label style="color: var(--text-secondary);">End slider</label>
                        <div>
                            <input type="range" id="postWindowEndSlider" min="0" max="1439" step="1" value="1020" style="width: 100%;">
                            <div id="postWindowEndLabel" style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 3px;">17:00</div>
                        </div>
                        <input type="time" id="windowEndInputPost" style="width: 120px;">
                        <button class="btn btn-secondary" onclick="applyCustomWindowPost()">Apply Window</button>
                        <span style="color: var(--text-secondary); font-size: 0.85rem;">Zoom or pan updates normalization</span>
                    </div>
                </div>
            </div>
            <div id="posteventDataQuality" style="margin: 10px 0 4px; padding: 8px 10px; border-left: 3px solid var(--accent-purple); background: rgba(139,92,246,0.08); color: var(--text-secondary); font-size: 0.82rem;">
                Waiting for analysis update...
            </div>
            
            <div class="charts-container">
                <div class="chart-panel">
                    <h2>
                        <span style="color: #fbbf24;">🔴 LIVE</span> B/E by Surprise Percentile (<span id="posteventBeWindowLabel">Event → Now</span>)
                        <span class="badge" id="boxplotBadgePost" style="background: var(--accent-purple);">All Events</span>
                        <span class="badge" id="boxplotTimeRangePost" style="background: #f59e0b; color: #000;">--</span>
                        <span class="badge" id="liveBeMultiplierBadgePost" style="background: #ef4444; color: #fff; font-size: 1rem; padding: 6px 12px;">LIVE: --x</span>
                        <span class="badge" id="posteventYAxisBadge" style="background:#334155;">B/E</span>
                        <span class="badge" id="posteventSampleWarnBadge" style="display:none; background:#b45309;">Small sample</span>
                    </h2>
                    <div style="margin: -4px 0 10px; color: var(--text-secondary); font-size: 0.82rem;">
                        Percentile buckets remain visible even when empty; your active bucket is highlighted.
                    </div>
                    <div id="boxplotChartPost" style="height: 450px;"></div>
                    <div class="bucket-info" id="userBucketInfoPost" style="border-left-color: var(--accent-purple);"></div>
                </div>
                
                <div class="chart-panel">
                    <h2>
                        Post-Event Price Paths (<span id="posteventPathsWindowLabel">Event → End</span>)
                        <span class="badge" id="rawPathsBadgePost" style="background: var(--accent-purple);">0 events</span>
                        <label style="display: inline-flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--text-secondary); margin-left: 8px;">
                            <input type="checkbox" id="showStatsLinesPost" onchange="updatePosteventPaths()">
                            Show stats lines
                        </label>
                        <button id="resetPosteventViewBtn" onclick="resetPosteventView()" style="
                            margin-left: 10px;
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
                    <div class="raw-paths-stats" id="rawPathsStatsPost">
                        <div class="stats-row">
                            <div class="stat-item">
                                <span class="stat-label">Mean Move</span>
                                <span class="stat-value" id="meanMovePost">-- bps</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Median Move</span>
                                <span class="stat-value" id="medianMovePost">-- bps</span>
                            </div>
                            <div class="stat-item" style="background: #2d3748; border: 1px solid var(--accent-purple);">
                                <span class="stat-label" style="color: var(--accent-purple);">🔴 LIVE Path</span>
                                <span class="stat-value" id="livePathBpsPost" style="color: var(--accent-purple);">-- bps</span>
                            </div>
                        </div>
                    </div>
                    <div id="rawPathsChartPost" style="height: 550px;"></div>
                    <div class="event-details" id="eventDetailsPost">
                        <h3>Selected Event Details</h3>
                        <p style="color: #888;">Click on a price line to see event details</p>
                    </div>
                </div>
            </div>
            
            <!-- Distribution Explorer Section -->
            <div class="charts-container" style="margin-top: 30px;">
                <div class="chart-panel" style="grid-column: span 2;">
                    <h2>
                        Window Distribution Explorer
                        <span class="badge" style="background: #8b5cf6;">Return Distribution</span>
                        <span class="badge" id="distroWindowBadge" style="background: #f59e0b; color: #000;">t → t+30m</span>
                    </h2>
                    
                    <!-- Time Window Presets -->
                    <div style="margin-bottom: 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                        <span style="color: var(--text-secondary); margin-right: 10px;">From:</span>
                        <button class="distro-preset-btn" data-from="-1440" onclick="setDistroFrom(-1440)">t-24h</button>
                        <button class="distro-preset-btn" data-from="-1200" onclick="setDistroFrom(-1200)">t-20h</button>
                        <button class="distro-preset-btn" data-from="-960" onclick="setDistroFrom(-960)">t-16h</button>
                        <button class="distro-preset-btn" data-from="-720" onclick="setDistroFrom(-720)">t-12h</button>
                        <button class="distro-preset-btn" data-from="-480" onclick="setDistroFrom(-480)">t-8h</button>
                        <button class="distro-preset-btn" data-from="-240" onclick="setDistroFrom(-240)">t-4h</button>
                        <button class="distro-preset-btn" data-from="-120" onclick="setDistroFrom(-120)">t-2h</button>
                        <button class="distro-preset-btn" data-from="-60" onclick="setDistroFrom(-60)">t-1h</button>
                        <button class="distro-preset-btn" data-from="-30" onclick="setDistroFrom(-30)">t-30m</button>
                        <button class="distro-preset-btn" data-from="-15" onclick="setDistroFrom(-15)">t-15m</button>
                        <button class="distro-preset-btn active" data-from="0" onclick="setDistroFrom(0)">t</button>
                    </div>
                    <div style="margin-bottom: 20px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center;">
                        <span style="color: var(--text-secondary); margin-right: 10px;">To:</span>
                        <button class="distro-preset-btn" data-to="15" onclick="setDistroTo(15)">t+15m</button>
                        <button class="distro-preset-btn active" data-to="30" onclick="setDistroTo(30)">t+30m</button>
                        <button class="distro-preset-btn" data-to="60" onclick="setDistroTo(60)">t+1h</button>
                        <button class="distro-preset-btn" data-to="120" onclick="setDistroTo(120)">t+2h</button>
                        <button class="distro-preset-btn" data-to="240" onclick="setDistroTo(240)">t+4h</button>
                        <button class="distro-preset-btn" data-to="480" onclick="setDistroTo(480)">t+8h</button>
                        <button class="distro-preset-btn" data-to="510" onclick="setDistroTo(510)">t+8.5h (5pm)</button>
                    </div>

                    <div style="margin-bottom: 12px; display: flex; gap: 16px; align-items: center;">
                        <label style="display: inline-flex; align-items: center; gap: 6px; color: var(--text-secondary); font-size: 0.9rem;">
                            <input type="checkbox" id="distroOutliersToggle" checked onchange="updateDistroWindow()">
                            Include outliers (P5/P95)
                        </label>
                    </div>
                    
                    <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 20px;">
                        <!-- Distribution Chart -->
                        <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 15px;">
                            <div id="postDistroChart" style="height: 350px;"></div>
                        </div>
                        
                        <!-- Stats Panel -->
                        <div style="background: var(--bg-tertiary); border-radius: 10px; padding: 20px;">
                            <h4 style="color: var(--accent-purple); margin-bottom: 15px;">Window Statistics</h4>
                            <div style="display: flex; flex-direction: column; gap: 12px;">
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Mean:</span>
                                    <span id="distroStatMean" style="color: var(--text-primary);">-- bps</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Expected Rally:</span>
                                    <span id="distroStatExpectedRally" style="color: var(--text-primary);">-- bps</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Expected Selloff:</span>
                                    <span id="distroStatExpectedSelloff" style="color: var(--text-primary);">-- bps</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">P5 / P95:</span>
                                    <span id="distroStatP5P95" style="color: var(--text-primary);">-- / --</span>
                                </div>
                                <div class="distro-stat-row">
                                    <span style="color: var(--text-secondary);">Min / Max:</span>
                                    <span id="distroStatMinMax" style="color: var(--text-primary);">-- / --</span>
                                </div>
                                <div class="distro-stat-row" style="margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-color);">
                                    <span style="color: var(--accent-green);">0 bps Percentile / Rally Rate:</span>
                                    <span id="distroStatZeroPct" style="color: var(--accent-green); font-weight: bold;">--%</span>
                                    <span id="distroStatRallyRate" style="color: var(--accent-green); font-weight: bold;"> / --%</span>
                                </div>
                            </div>
                            <div id="distroBucketList" style="margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border-color); color: var(--text-secondary); font-size: 0.85rem;">
                                Click a histogram bar to see dates in that bucket.
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>'''


def generate_postevent_tab_js() -> str:
    """
    Generate JavaScript functions for the post-event tab.
    
    Returns:
        JavaScript string with post-event tab functions
    """
    return '''        const POSTEVENT_BASE_DATE = new Date('2000-01-01T00:00:00');

        function formatTimeOnlyPost(ms) {
            const d = new Date(ms);
            const pad = (v) => `${v}`.padStart(2, '0');
            return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
        }

        function timeToMsPost(timeStr, dayOffset = 0) {
            const [h, m] = (timeStr || '').split(':').map(Number);
            if (Number.isNaN(h) || Number.isNaN(m)) return NaN;
            const d = new Date(POSTEVENT_BASE_DATE.getTime());
            d.setHours(h, m, 0, 0);
            d.setDate(d.getDate() + dayOffset);
            return d.getTime();
        }

        function hhmmToMinutesPost(hhmm) {
            const [h, m] = (hhmm || '').split(':').map(Number);
            if (!Number.isFinite(h) || !Number.isFinite(m)) return null;
            return h * 60 + m;
        }

        function minutesToHhmmPost(totalMin) {
            if (!Number.isFinite(totalMin)) return '00:00';
            const mins = ((Math.round(totalMin) % 1440) + 1440) % 1440;
            const h = Math.floor(mins / 60);
            const m = mins % 60;
            const pad = (v) => `${v}`.padStart(2, '0');
            return `${pad(h)}:${pad(m)}`;
        }

        function syncPostWindowSliderLabels() {
            const startInput = document.getElementById('windowStartInputPost');
            const endInput = document.getElementById('windowEndInputPost');
            const startLabel = document.getElementById('postWindowStartLabel');
            const endLabel = document.getElementById('postWindowEndLabel');
            if (startLabel && startInput?.value) startLabel.textContent = `${startInput.value} (T)`;
            if (endLabel && endInput?.value) endLabel.textContent = `${endInput.value}`;
        }

        function bindPostWindowSliders() {
            const startSlider = document.getElementById('postWindowStartSlider');
            const endSlider = document.getElementById('postWindowEndSlider');
            const startInput = document.getElementById('windowStartInputPost');
            const endInput = document.getElementById('windowEndInputPost');
            if (!startSlider || !endSlider || !startInput || !endInput) return;
            if (startSlider.dataset.bound === 'true') return;

            let timer = null;
            const applyDebounced = () => {
                if (timer) clearTimeout(timer);
                timer = setTimeout(() => applyCustomWindowPost(), 250);
            };

            startSlider.addEventListener('input', () => {
                startInput.value = minutesToHhmmPost(parseInt(startSlider.value, 10));
                syncPostWindowSliderLabels();
                applyDebounced();
            });
            endSlider.addEventListener('input', () => {
                endInput.value = minutesToHhmmPost(parseInt(endSlider.value, 10));
                syncPostWindowSliderLabels();
                applyDebounced();
            });
            startInput.addEventListener('change', () => {
                const mins = hhmmToMinutesPost(startInput.value);
                if (mins != null) startSlider.value = `${mins}`;
                syncPostWindowSliderLabels();
            });
            endInput.addEventListener('change', () => {
                const mins = hhmmToMinutesPost(endInput.value);
                if (mins != null) endSlider.value = `${mins}`;
                syncPostWindowSliderLabels();
            });

            startSlider.dataset.bound = 'true';
            endSlider.dataset.bound = 'true';
            syncPostWindowSliderLabels();
        }

        function getPostEventWindowInputs() {
            const startInput = document.getElementById('windowStartInputPost');
            const endInput = document.getElementById('windowEndInputPost');
            const fallbackStart = document.getElementById('eventTimeInput');
            const fallbackEnd = document.getElementById('postEndTimeInput');
            return { startInput, endInput, fallbackStart, fallbackEnd };
        }

        function updatePostEventWindowLabels() {
            const eventTime = document.getElementById('eventTimeInput')?.value || 'Event';
            const endTime = document.getElementById('postEndTimeInput')?.value || 'End';
            const beLabel = document.getElementById('posteventBeWindowLabel');
            const pathsLabel = document.getElementById('posteventPathsWindowLabel');
            if (beLabel) beLabel.textContent = `${eventTime} → ${endTime}`;
            if (pathsLabel) pathsLabel.textContent = `${eventTime} → ${endTime}`;
        }

        function calcPosteventBeAtEnd(event, endMs, fwdOverride) {
            if (!event || !Array.isArray(event.postevent_prices) || !event.postevent_prices.length) return null;
            const fwd = (event.fwd || fwdOverride || 0);
            if (!fwd || fwd <= 0) return null;
            const openPrice = event.postevent_prices[0].price ?? event.postevent_prices[0].close;
            if (!openPrice || !Number.isFinite(openPrice)) return null;
            let high = -Infinity;
            let low = Infinity;
            for (const p of event.postevent_prices) {
                const ts = new Date(p.ts);
                const mapped = new Date(POSTEVENT_BASE_DATE.getTime());
                mapped.setHours(ts.getHours(), ts.getMinutes(), ts.getSeconds(), 0);
                const mappedMs = mapped.getTime();
                if (mappedMs > endMs) continue;
                const highVal = p.high ?? p.price ?? p.close;
                const lowVal = p.low ?? p.price ?? p.close;
                if (Number.isFinite(highVal)) high = Math.max(high, highVal);
                if (Number.isFinite(lowVal)) low = Math.min(low, lowVal);
            }
            if (!Number.isFinite(high) || !Number.isFinite(low)) return null;
            const upMove = Math.abs(high - openPrice);
            const downMove = Math.abs(openPrice - low);
            const movePoints = Math.max(upMove, downMove);
            const moveBps = decimalToBps(movePoints);
            return moveBps / fwd;
        }

        function calcPosteventMoveBpsAtEnd(event, endMs) {
            if (!event || !Array.isArray(event.postevent_prices) || !event.postevent_prices.length) return null;
            const openPrice = event.postevent_prices[0].price ?? event.postevent_prices[0].close;
            if (!openPrice || !Number.isFinite(openPrice)) return null;
            let high = -Infinity;
            let low = Infinity;
            for (const p of event.postevent_prices) {
                const ts = new Date(p.ts);
                const mapped = new Date(POSTEVENT_BASE_DATE.getTime());
                mapped.setHours(ts.getHours(), ts.getMinutes(), ts.getSeconds(), 0);
                const mappedMs = mapped.getTime();
                if (mappedMs > endMs) continue;
                const highVal = p.high ?? p.price ?? p.close;
                const lowVal = p.low ?? p.price ?? p.close;
                if (Number.isFinite(highVal)) high = Math.max(high, highVal);
                if (Number.isFinite(lowVal)) low = Math.min(low, lowVal);
            }
            if (!Number.isFinite(high) || !Number.isFinite(low)) return null;
            const upMove = Math.abs(high - openPrice);
            const downMove = Math.abs(openPrice - low);
            const movePoints = Math.max(upMove, downMove);
            return decimalToBps(movePoints);
        }

        function setPosteventBusy(isBusy) {
            const btn = document.getElementById('updatePosteventBtn');
            if (!btn) return;
            btn.disabled = !!isBusy;
            btn.style.opacity = isBusy ? '0.7' : '1';
            btn.textContent = isBusy ? 'Updating...' : 'Update Analysis';
        }

        async function runPosteventUpdate() {
            setPosteventBusy(true);
            try {
                await updatePosteventDashboard();
            } finally {
                setPosteventBusy(false);
            }
        }

        // Update post-event dashboard
        async function updatePosteventDashboard() {
            updatePostEventWindowLabels();
            bindPostWindowSliders();
            const cfgDefaultPct = Number(window.DASHBOARD_CONFIG?.default_surprise_percentile ?? 50);
            const cfgDefaultBucket = Number(window.DASHBOARD_CONFIG?.default_post_bucket_width ?? 20);
            const pctEl = document.getElementById('surprisePercentileInput');
            const bwEl = document.getElementById('postBucketWidthInput');
            if (pctEl && !Number.isFinite(parseFloat(pctEl.value))) {
                pctEl.value = `${Number.isFinite(cfgDefaultPct) ? cfgDefaultPct : 50}`;
            }
            if (bwEl && !Number.isFinite(parseFloat(bwEl.value))) {
                bwEl.value = `${Number.isFinite(cfgDefaultBucket) ? cfgDefaultBucket : 20}`;
            }
            const bucketWidthRaw = parseFloat(document.getElementById('postBucketWidthInput').value);
            const bucketWidth = Number.isFinite(bucketWidthRaw) && bucketWidthRaw > 0 ? bucketWidthRaw : 20;
            const includeOutliers = document.getElementById('includeOutliersPost').checked;
            const yAxisMode = document.getElementById('postYAxisMode')?.value || 'be';
            const noFilters = document.getElementById('noFiltersPost')?.checked || false;
            const { startInput, endInput, fallbackStart, fallbackEnd } = getPostEventWindowInputs();
            const endStr = endInput?.value || fallbackEnd?.value;
            const endMs = timeToMsPost(endStr, 0);
            const startSlider = document.getElementById('postWindowStartSlider');
            const endSlider = document.getElementById('postWindowEndSlider');
            if (startInput && startSlider && startInput.value) {
                const mins = hhmmToMinutesPost(startInput.value);
                if (mins != null) startSlider.value = `${mins}`;
            }
            if (endInput && endSlider && endInput.value) {
                const mins = hhmmToMinutesPost(endInput.value);
                if (mins != null) endSlider.value = `${mins}`;
            }
            syncPostWindowSliderLabels();
            const yAxisBadge = document.getElementById('posteventYAxisBadge');
            if (yAxisBadge) yAxisBadge.textContent = yAxisMode === 'bps' ? 'Bps' : 'B/E';

            const candidateEvents = Array.isArray(events) ? events.filter(e => e && e.date) : [];
            await ensureEventDetailsLoaded(candidateEvents);
            const detailCount = candidateEvents.filter(e => Array.isArray(e.postevent_prices) && e.postevent_prices.length > 0).length;
            const pctCount = candidateEvents.filter(e => Number.isFinite((e.surp_pct || 0) * 100)).length;
            const qualityEl = document.getElementById('posteventDataQuality');
            if (qualityEl) {
                qualityEl.textContent = `Events: ${candidateEvents.length} | with percentile: ${pctCount} | with post-event paths: ${detailCount}`;
            }
            
            const getYValue = (event) => {
                return yAxisMode === 'bps'
                    ? calcPosteventMoveBpsAtEnd(event, endMs)
                    : calcPosteventBeAtEnd(event, endMs, userFwdBe);
            };
            const yAxisTitle = yAxisMode === 'bps' ? 'Bps Move' : 'B/E Multiplier';

            // Create percentile buckets (0-25, 25-50, 50-75, 75-100)
            const buckets = [];
            if (noFilters) {
                buckets.push({ min: 0, max: 100, label: 'All Events' });
            } else {
                for (let i = 0; i < 100; i += bucketWidth) {
                    buckets.push({
                        min: i,
                        max: Math.min(i + bucketWidth, 100),
                        label: `P${i}-${Math.min(i + bucketWidth, 100)}`
                    });
                }
            }
            
            // Get user's surprise percentile to determine current bucket
            const userSurpPctRaw = parseFloat(document.getElementById('surprisePercentileInput')?.value || `${cfgDefaultPct}`);
            const userSurpPct = Number.isFinite(userSurpPctRaw) ? userSurpPctRaw : 50;
            const userBucketIdx = noFilters ? -1 : buckets.findIndex(b => userSurpPct >= b.min && userSurpPct < b.max);
            
            // Group events by surprise percentile bucket
            const boxplotData = [];
            const annotations = [];
            let boxIdx = 0;
            
            const bucketCategoryOrder = buckets.map((b) => b.label);
            buckets.forEach((bucket, bucketIdx) => {
                const bucketEvents = events.filter(e => {
                    const pct = (e.surp_pct || 0) * 100;
                    return pct >= bucket.min && pct < bucket.max;
                });
                
                const beValues = bucketEvents
                    .map(getYValue)
                    .filter(v => v != null && v > 0);
                
                const isUserBucket = bucketIdx === userBucketIdx;
                const hasSmallSample = beValues.length > 0 && beValues.length < 3;
                
                // Add warning annotation for small samples
                if (hasSmallSample) {
                    annotations.push({
                        x: bucket.label,
                        y: Math.max(...beValues) * 1.1,
                        text: `⚠️ n=${beValues.length}`,
                        showarrow: false,
                        font: { color: '#fbbf24', size: 10 },
                        yanchor: 'bottom',
                    });
                }
                
                // Always render the bucket (even if empty) so all buckets show
                const baseColor = isUserBucket ? '#22c55e' : '#9b59b6';
                const borderWidth = isUserBucket ? 3 : 1;
                boxplotData.push({
                    type: 'box',
                    y: beValues.length ? beValues : [null],
                    name: bucket.label,
                    boxpoints: includeOutliers ? 'outliers' : false,
                    marker: {
                        color: baseColor,
                        size: isUserBucket ? 8 : 6,
                    },
                    line: {
                        color: baseColor,
                        width: borderWidth,
                    },
                    fillcolor: isUserBucket ? 'rgba(34, 197, 94, 0.3)' : 'rgba(155, 89, 182, 0.3)',
                    hoverinfo: beValues.length ? 'all' : 'skip',
                });
                if (beValues.length > 0) {
                    boxIdx++;
                }
            });
            
            const boxplotLayout = {
                ...darkLayout,
                title: noFilters ? 'Post-Event All Events' : 'Post-Event B/E by Surprise Percentile',
                yaxis: { ...darkLayout.yaxis, title: yAxisTitle },
                showlegend: false,
                xaxis: { ...darkLayout.xaxis, type: 'category', categoryorder: 'array', categoryarray: bucketCategoryOrder },
                annotations,
            };
            Plotly.newPlot('boxplotChartPost', boxplotData, boxplotLayout, {responsive: true});

            const boxplotBadge = document.getElementById('boxplotBadgePost');
            if (boxplotBadge) {
                if (noFilters) {
                    boxplotBadge.textContent = 'All Events (No filters)';
                } else {
                    boxplotBadge.textContent = yAxisMode === 'bps' ? 'Buckets (Bps)' : 'Buckets (B/E)';
                }
            }
            
            // Update user bucket info panel
            const userBucketInfo = document.getElementById('userBucketInfoPost');
            const sampleWarnBadge = document.getElementById('posteventSampleWarnBadge');
            if (userBucketInfo && userBucketIdx >= 0) {
                const bucket = buckets[userBucketIdx];
                const bucketEvents = events.filter(e => {
                    const pct = (e.surp_pct || 0) * 100;
                    return pct >= bucket.min && pct < bucket.max;
                });
                const beValues = bucketEvents
                    .map(getYValue)
                    .filter(v => v != null && v > 0);
                
                if (beValues.length > 0) {
                    const mean = (beValues.reduce((a, b) => a + b, 0) / beValues.length).toFixed(2);
                    const sorted = [...beValues].sort((a, b) => a - b);
                    const median = sorted[Math.floor(sorted.length / 2)].toFixed(2);
                    const warning = beValues.length < 3 ? ' <span style="color:#fbbf24;">⚠️ Small sample</span>' : '';
                    if (sampleWarnBadge) sampleWarnBadge.style.display = beValues.length < 3 ? 'inline-block' : 'none';
                    const suffix = yAxisMode === 'bps' ? ' bps' : 'x';
                    userBucketInfo.innerHTML = `
                        <strong style="color:#22c55e;">Your Bucket: ${bucket.label}</strong> (n=${beValues.length}${warning})<br>
                        Mean: ${mean}${suffix} | Median: ${median}${suffix}
                    `;
                } else {
                    userBucketInfo.innerHTML = `<strong style="color:#22c55e;">Your Bucket: ${bucket.label}</strong> — No data`;
                    if (sampleWarnBadge) sampleWarnBadge.style.display = 'inline-block';
                }
            } else if (userBucketInfo && noFilters) {
                const allValues = events.map(getYValue).filter(v => v != null && v > 0);
                userBucketInfo.innerHTML = `<strong>All Events</strong> (n=${allValues.length})`;
                if (sampleWarnBadge) sampleWarnBadge.style.display = 'none';
            }
            
            // Update post-event paths chart
            updatePosteventPaths();
            
            // Update distribution explorer
            if (typeof updateDistroWindow === 'function') {
                updateDistroWindow();
            }
        }
        
        let posteventSelectedEvent = null;
        let posteventSelectedDate = null;

        // Update post-event price paths
        function clearPosteventSelection() {
            if (!posteventSelectedDate) return;
            posteventSelectedDate = null;
            updatePosteventPaths();
        }

        function updatePosteventPaths() {
            updatePostEventWindowLabels();
            const traces = [];
            let validEventCount = 0;
            const palette = [
                '#9b59b6', '#1d9bf0', '#22c55e', '#f97316', '#f43f5e',
                '#14b8a6', '#eab308', '#8b5cf6', '#0ea5e9', '#f59e0b'
            ];
            const smoothingWindow = 5;
            if (!window.posteventWindow) {
                window.posteventWindow = { start: null, end: null };
            }
            window.posteventRawSeries = [];
            window.posteventRelayoutLock = false;

            // Get bucket filter settings
            const cfgDefaultPct = Number(window.DASHBOARD_CONFIG?.default_surprise_percentile ?? 50);
            const cfgDefaultBucket = Number(window.DASHBOARD_CONFIG?.default_post_bucket_width ?? 20);
            const bucketWidthRaw = parseFloat(document.getElementById('postBucketWidthInput')?.value || `${cfgDefaultBucket}`);
            const bucketWidth = Number.isFinite(bucketWidthRaw) && bucketWidthRaw > 0 ? bucketWidthRaw : 20;
            const userSurpPctRaw = parseFloat(document.getElementById('surprisePercentileInput')?.value || `${cfgDefaultPct}`);
            const userSurpPct = Number.isFinite(userSurpPctRaw) ? userSurpPctRaw : 50;
            const bucketMin = Math.floor(userSurpPct / bucketWidth) * bucketWidth;
            const bucketMax = bucketMin + bucketWidth;
            const noFilters = document.getElementById('noFiltersPost')?.checked ?? false;
            const filterByBucket = !noFilters && (document.getElementById('filterByBucketPost')?.checked ?? false);

            // smoothSeries/interpolateAt/percentile are shared helpers in live_unified_dashboard.py

            function addGapBreaks(tsArr, yArr, maxGapMs = 90 * 60 * 1000) {
                const xOut = [];
                const yOut = [];
                for (let i = 0; i < tsArr.length; i += 1) {
                    if (i > 0 && Number.isFinite(tsArr[i]) && Number.isFinite(tsArr[i - 1])) {
                        if ((tsArr[i] - tsArr[i - 1]) > maxGapMs) {
                            xOut.push(new Date(tsArr[i]));
                            yOut.push(null);
                        }
                    }
                    xOut.push(new Date(tsArr[i]));
                    yOut.push(yArr[i]);
                }
                return { x: xOut, y: yOut };
            }

            function buildTraces(windowStart, windowEnd) {
                const out = [];
                const selectedDate = posteventSelectedDate;
                window.posteventRawSeries.forEach((series, idx) => {
                    const base = interpolateAt(series.timestamps, series.bps, windowStart);
                    const normalized = series.bps.map(v => v - base);
                    const smoothed = smoothSeries(normalized, smoothingWindow);
                    const segmented = addGapBreaks(series.timestamps, smoothed);
                    const isHighlighted = series.isUserBucket;
                    const isSelected = selectedDate && series.date === selectedDate;
                    const color = isSelected ? '#f59e0b' : (isHighlighted ? '#22c55e' : palette[idx % palette.length]);
                    const width = isSelected ? 4 : (isHighlighted ? 3 : 2);
                    const opacity = isSelected ? 1 : (selectedDate ? 0.15 : (isHighlighted ? 0.7 : 0.5));
                    out.push({
                        x: segmented.x,
                        y: segmented.y,
                        type: 'scatter',
                        mode: 'lines',
                        name: series.date,
                        line: { width: width, color: color },
                        opacity: opacity,
                        hoverinfo: 'name+y',
                        connectgaps: false,
                    });
                });
                // Compute window stats and update display
                updateWindowStats(windowStart, windowEnd);
                return out;
            }

            function buildStatsTraces(windowStart, windowEnd) {
                const timeSet = new Set();
                const seriesBases = window.posteventRawSeries.map(series => ({
                    timestamps: series.timestamps,
                    bps: series.bps,
                    base: interpolateAt(series.timestamps, series.bps, windowStart),
                }));

                seriesBases.forEach(series => {
                    series.timestamps.forEach(t => {
                        if (t >= windowStart && t <= windowEnd) timeSet.add(t);
                    });
                });

                const times = Array.from(timeSet).sort((a, b) => a - b);
                if (!times.length) return [];

                const mean = [];
                const median = [];
                const p25 = [];
                const p75 = [];
                const p5 = [];
                const p95 = [];

                times.forEach(t => {
                    const values = seriesBases.map(series => {
                        const v = interpolateAt(series.timestamps, series.bps, t);
                        return v - series.base;
                    }).filter(v => Number.isFinite(v));
                    if (!values.length) {
                        mean.push(null);
                        median.push(null);
                        p25.push(null);
                        p75.push(null);
                        p5.push(null);
                        p95.push(null);
                        return;
                    }
                    values.sort((a, b) => a - b);
                    const avg = values.reduce((a, b) => a + b, 0) / values.length;
                    mean.push(avg);
                    median.push(percentile(values, 50));
                    p25.push(percentile(values, 25));
                    p75.push(percentile(values, 75));
                    p5.push(percentile(values, 5));
                    p95.push(percentile(values, 95));
                });

                const x = times.map(t => new Date(t));
                return [
                    { x, y: mean, type: 'scatter', mode: 'lines', name: 'Mean', line: { color: '#e5e7eb', width: 2 } },
                    { x, y: median, type: 'scatter', mode: 'lines', name: 'Median', line: { color: '#67e8f9', width: 2 } },
                    { x, y: p25, type: 'scatter', mode: 'lines', name: 'P25', line: { color: '#fbbf24', width: 1.5, dash: 'dot' } },
                    { x, y: p75, type: 'scatter', mode: 'lines', name: 'P75', line: { color: '#fbbf24', width: 1.5, dash: 'dot' } },
                    { x, y: p5, type: 'scatter', mode: 'lines', name: 'P5', line: { color: '#ef4444', width: 1, dash: 'dash' } },
                    { x, y: p95, type: 'scatter', mode: 'lines', name: 'P95', line: { color: '#ef4444', width: 1, dash: 'dash' } },
                ];
            }

            // Compute and display stats for visible window
            function updateWindowStats(windowStart, windowEnd) {
                const endMoves = window.posteventRawSeries.map(series => {
                    const baseVal = interpolateAt(series.timestamps, series.bps, windowStart);
                    const endVal = interpolateAt(series.timestamps, series.bps, windowEnd);
                    return endVal - baseVal;
                });
                if (endMoves.length > 0) {
                    const mean = (endMoves.reduce((a, b) => a + b, 0) / endMoves.length).toFixed(1);
                    const sorted = [...endMoves].sort((a, b) => a - b);
                    const median = sorted[Math.floor(sorted.length / 2)].toFixed(1);
                    document.getElementById('meanMovePost').textContent = `${mean} bps`;
                    document.getElementById('medianMovePost').textContent = `${median} bps`;
                }
            }

            events.forEach((event, idx) => {
                if (event.postevent_prices && event.postevent_prices.length > 0) {
                    // Check bucket filter
                    const evtPct = (event.surp_pct || 0) * 100;
                    const isUserBucket = evtPct >= bucketMin && evtPct < bucketMax;
                    if (filterByBucket && !isUserBucket) return;  // Skip if filtering
                    
                    validEventCount++;
                    const timestamps = event.postevent_prices.map(p => {
                        const d = new Date(p.ts);
                        const mapped = new Date(POSTEVENT_BASE_DATE.getTime());
                        mapped.setHours(d.getHours(), d.getMinutes(), d.getSeconds(), 0);
                        return mapped.getTime();
                    });
                    const paired = timestamps
                        .map((ts, i) => ({ ts, bps: event.postevent_prices[i]?.bps }))
                        .filter((p) => Number.isFinite(p.ts) && Number.isFinite(p.bps))
                        .sort((a, b) => a.ts - b.ts);
                    window.posteventRawSeries.push({
                        date: event.date,
                        timestamps: paired.map((p) => p.ts),
                        bps: paired.map((p) => p.bps),
                        isUserBucket: isUserBucket,
                    });
                }
            });

            if (window.posteventRawSeries.length) {
                const allTimes = window.posteventRawSeries.flatMap(s => s.timestamps);
                const maxTime = Math.max(...allTimes);
                const { startInput, endInput, fallbackStart, fallbackEnd } = getPostEventWindowInputs();
                const startStr = startInput?.value || fallbackStart?.value;
                const endStr = endInput?.value || fallbackEnd?.value;
                let defaultStart = timeToMsPost(startStr, 0);
                let defaultEnd = timeToMsPost(endStr, 0);
                if (Number.isNaN(defaultStart)) defaultStart = maxTime - (2 * 60 * 60 * 1000);
                if (Number.isNaN(defaultEnd)) defaultEnd = maxTime;
                if (defaultEnd <= defaultStart) {
                    defaultEnd = timeToMsPost(endStr, 1);
                }
                if (window.posteventWindow.start == null || window.posteventWindow.end == null) {
                    window.posteventWindow.start = defaultStart;
                    window.posteventWindow.end = defaultEnd;
                }
                if (startInput && endInput) {
                    startInput.value = formatTimeOnlyPost(window.posteventWindow.start);
                    endInput.value = formatTimeOnlyPost(window.posteventWindow.end);
                }
            }

            const showStatsLines = document.getElementById('showStatsLinesPost')?.checked ?? false;
            const eventTimeStr = document.getElementById('eventTimeInput')?.value || EVENT_TIME;
            const eventTimeMs = timeToMsPost(eventTimeStr, 0);
            const shapes = Number.isFinite(eventTimeMs) ? [{
                type: 'line',
                x0: eventTimeMs,
                x1: eventTimeMs,
                y0: 0,
                y1: 1,
                yref: 'paper',
                line: { color: '#fbbf24', width: 1, dash: 'dot' },
            }] : [];
            const layout = {
                ...darkLayout,
                title: 'Post-Event Price Paths (normalized to window start)',
                xaxis: { 
                    ...darkLayout.xaxis, 
                    title: 'Time of Day',
                    range: [window.posteventWindow.start, window.posteventWindow.end],
                    type: 'date',
                    tickformat: '%H:%M',
                    rangeslider: { visible: true, thickness: 0.08 },
                },
                yaxis: { ...darkLayout.yaxis, title: 'Move (bps)' },
                showlegend: showStatsLines,
                hovermode: 'closest',
                dragmode: 'pan',
                shapes,
            };

            const plotConfig = {responsive: true, scrollZoom: true};
            const primaryTraces = showStatsLines
                ? buildStatsTraces(window.posteventWindow.start, window.posteventWindow.end)
                : buildTraces(window.posteventWindow.start, window.posteventWindow.end);
            traces.push(...(primaryTraces.length ? primaryTraces : buildTraces(window.posteventWindow.start, window.posteventWindow.end)));
            if (showStatsLines) {
                updateWindowStats(window.posteventWindow.start, window.posteventWindow.end);
            }
            Plotly.newPlot('rawPathsChartPost', traces, layout, plotConfig).then(() => {
                const chart = document.getElementById('rawPathsChartPost');
                if (!chart._posteventRelayoutBound) {
                    chart.on('plotly_relayout', (evt) => {
                        if (window.posteventRelayoutLock) return;
                        const startKey = 'xaxis.range[0]';
                        const endKey = 'xaxis.range[1]';
                        if (!evt) return;
                        let start = null;
                        let end = null;
                        if (evt[startKey] != null && evt[endKey] != null) {
                            start = Date.parse(evt[startKey]);
                            end = Date.parse(evt[endKey]);
                        } else if (Array.isArray(evt['xaxis.range'])) {
                            start = Date.parse(evt['xaxis.range'][0]);
                            end = Date.parse(evt['xaxis.range'][1]);
                        } else if (evt['xaxis.autorange'] === true && chart.layout?.xaxis?.range) {
                            start = Date.parse(chart.layout.xaxis.range[0]);
                            end = Date.parse(chart.layout.xaxis.range[1]);
                        }
                        if (!Number.isNaN(start) && !Number.isNaN(end)) {
                            window.posteventWindow.start = start;
                            window.posteventWindow.end = end;
                            const startInput = document.getElementById('windowStartInputPost');
                            const endInput = document.getElementById('windowEndInputPost');
                            if (startInput) startInput.value = formatTimeOnlyPost(start);
                            if (endInput) endInput.value = formatTimeOnlyPost(end);
                            window.posteventRelayoutLock = true;
                            const nextTraces = showStatsLines
                                ? buildStatsTraces(start, end)
                                : buildTraces(start, end);
                            Plotly.react('rawPathsChartPost', nextTraces, {
                                ...layout,
                                xaxis: { ...layout.xaxis, range: [start, end] },
                            }, plotConfig).finally(() => {
                                window.posteventRelayoutLock = false;
                            });
                            if (showStatsLines) {
                                updateWindowStats(start, end);
                            }
                        }
                    });
                    
                    // Click handler for showing event details and candlestick
                    chart.on('plotly_click', (clickData) => {
                        if (showStatsLines) return;
                        if (!clickData.points || clickData.points.length === 0) return;
                        const traceIdx = clickData.points[0].curveNumber;
                        const series = window.posteventRawSeries[traceIdx];
                        if (!series) return;
                        if (posteventSelectedDate && posteventSelectedDate === series.date) {
                            clearPosteventSelection();
                            return;
                        }
                        posteventSelectedDate = series.date;
                        // Find the event matching this date
                        const clickedEvent = events.find(e => e.date === series.date);
                        if (clickedEvent && typeof showEventDetailsPost === 'function') {
                            showEventDetailsPost(clickedEvent);
                        }
                    });
                    chart.on('plotly_doubleclick', () => {
                        clearPosteventSelection();
                    });
                    
                    chart._posteventRelayoutBound = true;
                }
            });
            document.getElementById('rawPathsBadgePost').textContent = `${validEventCount} events`;
            
            // Stats are now calculated in buildTraces -> updateWindowStats
        }

        function applyCustomWindowPost() {
            const startInput = document.getElementById('windowStartInputPost');
            const endInput = document.getElementById('windowEndInputPost');
            if (!startInput || !endInput) return;
            let start = timeToMsPost(startInput.value, 0);
            let end = timeToMsPost(endInput.value, 0);
            if (Number.isNaN(start) || Number.isNaN(end)) return;
            if (end <= start) {
                end = timeToMsPost(endInput.value, 1);
            }
            window.posteventWindow = { start, end };
            updatePostEventWindowLabels();
            syncPostWindowSliderLabels();
            updatePosteventPaths();
        }

        function resetPosteventView() {
            window.posteventWindow = { start: null, end: null };
            updatePosteventPaths();
        }

        // ============================================================================
        // DISTRIBUTION EXPLORER
        // ============================================================================
        
        // Distro window state (minutes from event time t=0)
        let distroFromMin = 0;   // t-0 = event time
        let distroToMin = 30;    // t+30 = 30 min after event
        
        function setDistroFrom(minutes) {
            distroFromMin = minutes;
            // Update active state
            document.querySelectorAll('.distro-preset-btn[data-from]').forEach(btn => {
                btn.classList.toggle('active', parseInt(btn.dataset.from) === minutes);
            });
            updateDistroWindow();
        }
        
        function setDistroTo(minutes) {
            distroToMin = minutes;
            // Update active state
            document.querySelectorAll('.distro-preset-btn[data-to]').forEach(btn => {
                btn.classList.toggle('active', parseInt(btn.dataset.to) === minutes);
            });
            updateDistroWindow();
        }
        
        function formatDistroLabel(minutes) {
            if (minutes === 0) return 't';
            const sign = minutes >= 0 ? '+' : '';
            if (Math.abs(minutes) >= 60) {
                const hours = minutes / 60;
                return `t${sign}${hours}h`;
            }
            return `t${sign}${minutes}m`;
        }
        
        // gaussianKDE/computePercentileOfZero are shared helpers in live_unified_dashboard.py
        
        function updateDistroWindow() {
            const badge = document.getElementById('distroWindowBadge');
            if (badge) {
                badge.textContent = `${formatDistroLabel(distroFromMin)} → ${formatDistroLabel(distroToMin)}`;
            }
            // Get event time in hours (default 8:30 = 8.5)
            const eventTimeStr = document.getElementById('eventTimeInput')?.value || '08:30';
            const [evtH, evtM] = eventTimeStr.split(':').map(Number);
            const eventTimeHours = evtH + evtM / 60;
            
            // Convert distro window to milliseconds on base date
            const fromHours = eventTimeHours + distroFromMin / 60;
            const toHours = eventTimeHours + distroToMin / 60;
            const fromMs = POSTEVENT_BASE_DATE.getTime() + fromHours * 60 * 60 * 1000;
            const toMs = POSTEVENT_BASE_DATE.getTime() + toHours * 60 * 60 * 1000;
            
            // Collect moves for all events in the distro window
            const moveEntries = [];
            
            (events || []).forEach(event => {
                const prices = event.postevent_prices || [];
                if (prices.length < 2) return;
                
                // Convert event prices to base date timestamps
                const mappedPrices = prices.map(p => {
                    const d = new Date(p.ts);
                    const mapped = new Date(POSTEVENT_BASE_DATE.getTime());
                    mapped.setHours(d.getHours(), d.getMinutes(), d.getSeconds(), 0);
                    return { ts: mapped.getTime(), bps: p.bps };
                });
                
                // Find bps at window start and end
                const startPt = mappedPrices.find(p => p.ts >= fromMs);
                const endPt = [...mappedPrices].reverse().find(p => p.ts <= toMs);
                
                if (startPt && endPt) {
                    const move = endPt.bps - startPt.bps;
                    moveEntries.push({ date: event.date, move });
                }
            });
            
            // Render distribution
            renderDistroExplorer(moveEntries);
        }

        function showEventDetailsPost(event) {
            const panel = document.getElementById('eventDetailsPost');
            if (!panel || !event) return;
            posteventSelectedEvent = event;
            posteventSelectedDate = event.date;
            const surpPct = event.surp_pct != null ? (event.surp_pct * 100).toFixed(1) : 'N/A';
            panel.innerHTML = `
                <h3>Selected Event Details</h3>
                <p><strong>Date:</strong> ${event.date}</p>
                <p><strong>Surprise Percentile:</strong> ${surpPct}</p>
                <button class="btn btn-secondary" onclick="openSelectedPosteventCandlestick()">View Candlestick</button>
            `;
            if (typeof updatePosteventPaths === 'function') {
                updatePosteventPaths();
            }
        }

        function openSelectedPosteventCandlestick() {
            if (!posteventSelectedEvent) return;
            if (typeof openCandlestickModal === 'function') {
                openCandlestickModal(posteventSelectedEvent);
            }
        }

        function selectPosteventEventDate(date) {
            if (!date) return;
            const event = (events || []).find(e => e.date === date);
            if (!event) return;
            showEventDetailsPost(event);
        }
        
        function renderDistroExplorer(entries) {
            const chartDiv = document.getElementById('postDistroChart');
            
            if (!entries || entries.length === 0) {
                chartDiv.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 60px;">No data for this window</div>';
                document.getElementById('distroStatMean').textContent = '-- bps';
                document.getElementById('distroStatExpectedRally').textContent = '-- bps';
                document.getElementById('distroStatExpectedSelloff').textContent = '-- bps';
                document.getElementById('distroStatP5P95').textContent = '-- / --';
                document.getElementById('distroStatMinMax').textContent = '-- / --';
                document.getElementById('distroStatZeroPct').textContent = '--%';
                document.getElementById('distroStatRallyRate').textContent = ' / --%';
                const bucketList = document.getElementById('distroBucketList');
                if (bucketList) bucketList.textContent = 'Click a histogram bar to see dates in that bucket.';
                return;
            }
            
            const includeOutliers = document.getElementById('distroOutliersToggle')?.checked ?? true;
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
            if (n === 0) {
                chartDiv.innerHTML = '<div style="text-align: center; color: var(--text-secondary); padding: 60px;">No data for this window</div>';
                document.getElementById('distroStatMean').textContent = '-- bps';
                document.getElementById('distroStatExpectedRally').textContent = '-- bps';
                document.getElementById('distroStatExpectedSelloff').textContent = '-- bps';
                document.getElementById('distroStatP5P95').textContent = '-- / --';
                document.getElementById('distroStatMinMax').textContent = '-- / --';
                document.getElementById('distroStatZeroPct').textContent = '--%';
                document.getElementById('distroStatRallyRate').textContent = ' / --%';
                const bucketList = document.getElementById('distroBucketList');
                if (bucketList) bucketList.textContent = 'Click a histogram bar to see dates in that bucket.';
                return;
            }
            
            // Stats
            const mean = filteredValues.reduce((a, b) => a + b, 0) / n;
            const median = n % 2 === 0 ? (sorted[n/2 - 1] + sorted[n/2]) / 2 : sorted[Math.floor(n/2)];
            const variance = filteredValues.reduce((sum, v) => sum + (v - mean) ** 2, 0) / (n - 1 || 1);
            const min = sorted[0];
            const max = sorted[n - 1];
            const p5 = sorted[Math.floor(n * 0.05)];
            const p95 = sorted[Math.min(Math.floor(n * 0.95), n - 1)];
            const zeroPct = computePercentileOfZero(sorted);
            const rallyValues = filteredValues.filter(v => v > 0);
            const selloffValues = filteredValues.filter(v => v < 0);
            const expectedRally = rallyValues.length ? rallyValues.reduce((a, b) => a + b, 0) / rallyValues.length : 0;
            const expectedSelloff = selloffValues.length ? selloffValues.reduce((a, b) => a + b, 0) / selloffValues.length : 0;
            const rallyRate = (rallyValues.length / n) * 100;
            
            // Update stats panel
            document.getElementById('distroStatMean').textContent = `${mean.toFixed(2)} bps`;
            document.getElementById('distroStatExpectedRally').textContent = `${expectedRally.toFixed(2)} bps`;
            document.getElementById('distroStatExpectedSelloff').textContent = `${expectedSelloff.toFixed(2)} bps`;
            document.getElementById('distroStatP5P95').textContent = `${p5.toFixed(1)} / ${p95.toFixed(1)}`;
            document.getElementById('distroStatMinMax').textContent = `${min.toFixed(1)} / ${max.toFixed(1)}`;
            document.getElementById('distroStatZeroPct').textContent = zeroPct !== null ? `${zeroPct.toFixed(1)}%` : '--%';
            document.getElementById('distroStatRallyRate').textContent = ` / ${rallyRate.toFixed(1)}%`;
            
            const range = max - min || 1;
            const stdDev = Math.sqrt(variance);
            let binWidth = 3.49 * stdDev / Math.cbrt(n);
            if (!Number.isFinite(binWidth) || binWidth <= 0) {
                binWidth = range / 10;
            }
            let binCount = Math.ceil(range / binWidth);
            binCount = Math.min(50, Math.max(5, binCount));
            const binSize = range / binCount || 1;
            const kMin = Math.floor(min / binSize);
            const kMax = Math.ceil(max / binSize);
            const binStart = kMin * binSize;
            const binEnd = kMax * binSize;
            window.postDistroEntries = filteredEntries;
            window.postDistroBinSize = binSize;

            // Histogram trace
            const histTrace = {
                x: filteredValues,
                type: 'histogram',
                histnorm: 'probability density',
                opacity: 0.6,
                name: 'Histogram',
                marker: { color: '#9b59b6' },
                xbins: { start: binStart, end: binEnd, size: binSize },
            };
            
            // KDE trace
            const kde = gaussianKDE(filteredValues);
            const xRange = max - min || 1;
            const xPadding = xRange * 0.15;
            const xGrid = [];
            const yGrid = [];
            for (let i = 0; i < 200; i++) {
                const x = min - xPadding + (xRange + 2 * xPadding) * i / 199;
                xGrid.push(x);
                yGrid.push(kde(x));
            }
            
            const kdeTrace = {
                x: xGrid,
                y: yGrid,
                type: 'scatter',
                mode: 'lines',
                name: 'KDE',
                line: { color: '#22c55e', width: 2 },
            };
            
            // Zero line shape
            const shapes = [{
                type: 'line',
                x0: 0, x1: 0,
                y0: 0, y1: 1,
                yref: 'paper',
                line: { color: '#ef4444', width: 2, dash: 'dash' },
            }];
            
            const layout = {
                ...darkLayout,
                title: '',
                xaxis: { 
                    ...darkLayout.xaxis, 
                    title: 'Move (bps)',
                    zeroline: true,
                    zerolinecolor: '#ef4444',
                },
                yaxis: { 
                    ...darkLayout.yaxis, 
                    title: 'Density',
                },
                barmode: 'overlay',
                shapes: shapes,
                annotations: [{
                    x: 0,
                    y: 1.02,
                    yref: 'paper',
                    text: `0 bps @ P${zeroPct?.toFixed(0) || '--'}`,
                    showarrow: false,
                    font: { color: '#ef4444', size: 10 },
                }],
            };
            
            Plotly.react('postDistroChart', [histTrace, kdeTrace], layout, { responsive: true });
            const bucketList = document.getElementById('distroBucketList');
            if (bucketList) bucketList.textContent = 'Click a histogram bar to see dates in that bucket.';

            chartDiv.on('plotly_click', (clickData) => {
                if (!clickData.points || clickData.points.length === 0) return;
                const point = clickData.points[0];
                const binCenter = point.x;
                const size = window.postDistroBinSize || binSize;
                const binStart = binCenter - size / 2;
                const binEnd = binCenter + size / 2;
                const entriesInBin = (window.postDistroEntries || []).filter(e => e.move >= binStart && e.move < binEnd);
                if (!bucketList) return;
                if (!entriesInBin.length) {
                    bucketList.textContent = 'No events in this bin.';
                    return;
                }
                const items = entriesInBin
                    .sort((a, b) => b.move - a.move)
                    .map(e => `
                        <div style="display:flex; justify-content: space-between; align-items: center; gap: 8px;">
                            <button style="background:none; border:none; color: var(--accent-blue); cursor:pointer; padding:0;" onclick="selectPosteventEventDate('${e.date}')">${e.date}</button>
                            <span>${e.move.toFixed(2)} bps</span>
                        </div>
                    `)
                    .join('');
                bucketList.innerHTML = `<div style="margin-bottom:6px; color: var(--text-primary);">Bin ${binStart.toFixed(2)} to ${binEnd.toFixed(2)} bps (${entriesInBin.length})</div>${items}`;
            });
        }
        
        // Initialize distro explorer after dashboard loads
        setTimeout(() => {
            if (typeof events !== 'undefined' && events.length > 0) {
                updateDistroWindow();
            }
        }, 1000);'''





