"""
Pre-Event Impulse Tab
---------------------
Relates prior-vs-consensus setup to pre-event TY drift and overlays a
Bloomberg ECOS estimates snapshot for the current event.
"""


def generate_impulse_tab_html(config: dict) -> str:
    return """        <!-- PRE-EVENT IMPULSE TAB -->
        <div class="tab-content" id="impulseTab">
            <div class="controls">
                <div class="control-group" style="grid-column: span 2;">
                    <label>ECOS Estimates File</label>
                    <input type="file" id="impulseEcosFile" accept=".xlsx,.xls" />
                    <div style="display:flex; gap:8px; margin-top:8px; flex-wrap:wrap;">
                        <button class="btn btn-primary" onclick="loadImpulseEcosFile()">Upload ECOS</button>
                        <button class="btn btn-small" onclick="loadImpulseDefaultEcos()">Load Default ECOS</button>
                    </div>
                </div>
                <div class="control-group">
                    <label>Window Start Day</label>
                    <select id="impulseStartDay" onchange="refreshImpulseHistory()">
                        <option value="-4">T-4</option>
                        <option value="-3">T-3</option>
                        <option value="-2" selected>T-2</option>
                        <option value="-1">T-1</option>
                    </select>
                </div>
                <div class="control-group">
                    <label>Start Time (ET)</label>
                    <input type="time" id="impulseStartTime" value="15:00" onchange="refreshImpulseHistory()" />
                </div>
                <div class="control-group">
                    <label>&nbsp;</label>
                    <button class="btn btn-primary" onclick="refreshImpulseHistory()">Refresh History</button>
                </div>
            </div>

            <div class="stats-grid" id="impulseSetupStats">
                <div class="stat-card"><div class="stat-label">Prior</div><div class="stat-value" id="impulsePrior">--</div></div>
                <div class="stat-card"><div class="stat-label">Median</div><div class="stat-value" id="impulseMedian">--</div></div>
                <div class="stat-card"><div class="stat-label">Expected Change</div><div class="stat-value" id="impulseExpectedChange">--</div></div>
                <div class="stat-card"><div class="stat-label">Std Dev</div><div class="stat-value" id="impulseStdDev">--</div></div>
                <div class="stat-card"><div class="stat-label">Prior Percentile</div><div class="stat-value" id="impulsePriorPct">--</div></div>
                <div class="stat-card"><div class="stat-label">Rank / Recency Wtd</div><div class="stat-value" id="impulseWeighted">--</div></div>
            </div>

            <div class="chart-card">
                <div class="chart-header">
                    <h3>What-if Actuals: Surprise Percentile + Bucket Moves</h3>
                    <span class="badge" id="impulseScenariosBadge">bucket stats</span>
                </div>
                <div class="controls" style="margin-bottom: 12px;">
                    <div class="control-group">
                        <label>Consensus</label>
                        <input type="number" id="impulseScenarioConsensus" value="85" step="1">
                    </div>
                    <div class="control-group">
                        <label>Std Dev</label>
                        <input type="number" id="impulseScenarioStd" value="20.51" step="0.01">
                    </div>
                    <div class="control-group">
                        <label>Bucket Width (%ile)</label>
                        <input type="number" id="impulseScenarioBucketWidth" value="20" min="5" max="50" step="5">
                    </div>
                    <div class="control-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary" onclick="computeImpulseScenarios()">Compute What-ifs</button>
                    </div>
                    <div class="control-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-small" onclick="addImpulseScenarioRow()">Add Row</button>
                    </div>
                </div>
                <div style="display:grid; grid-template-columns: minmax(260px, 0.8fr) minmax(520px, 1.6fr); gap:14px;">
                    <div style="max-height: 300px; overflow:auto;">
                        <table class="data-table" id="impulseScenarioInputTable">
                            <thead><tr><th>Label</th><th>Hypothetical Actual</th><th></th></tr></thead>
                            <tbody>
                                <tr><td><input value="bofa"></td><td><input type="number" value="95" step="1"></td><td><button class="btn btn-small" onclick="this.closest('tr').remove()">x</button></td></tr>
                                <tr><td><input value="rbc"></td><td><input type="number" value="99" step="1"></td><td><button class="btn btn-small" onclick="this.closest('tr').remove()">x</button></td></tr>
                                <tr><td><input value="nomura"></td><td><input type="number" value="110" step="1"></td><td><button class="btn btn-small" onclick="this.closest('tr').remove()">x</button></td></tr>
                                <tr><td><input value="marex"></td><td><input type="number" value="110" step="1"></td><td><button class="btn btn-small" onclick="this.closest('tr').remove()">x</button></td></tr>
                                <tr><td><input value="pantheon"></td><td><input type="number" value="50" step="1"></td><td><button class="btn btn-small" onclick="this.closest('tr').remove()">x</button></td></tr>
                            </tbody>
                        </table>
                    </div>
                    <div style="max-height: 300px; overflow:auto;">
                        <table class="data-table" id="impulseScenarioResultsTable">
                            <thead>
                                <tr>
                                    <th>Label</th><th>Actual</th><th>z</th><th>Surp %ile</th>
                                    <th>Pre Exp / Max</th><th>+2h Exp / Max</th><th>EOD Exp / Max</th><th>N</th>
                                </tr>
                            </thead>
                            <tbody></tbody>
                        </table>
                    </div>
                </div>
                <div class="insight-box" id="impulseScenarioReadout">Expected move is mean signed TY bps in the matched surprise-percentile bucket; max move is mean max-extent bps.</div>
            </div>

            <div class="charts-container">
                <div class="chart-card">
                    <div class="chart-header">
                        <h3>Pre-Event TY Drift: Setup-Matched vs All Events</h3>
                        <span class="badge" id="impulsePathBadge">Load ECOS</span>
                    </div>
                    <div id="impulseDriftChart" class="chart"></div>
                    <div class="insight-box" id="impulseReadout">Upload or load the ECOS file to overlay the current NFP setup.</div>
                </div>
                <div class="chart-card">
                    <div class="chart-header">
                        <h3>Expected Change vs Pre-Event Drift</h3>
                        <span class="badge" id="impulseScatterBadge">2023+ regime</span>
                    </div>
                    <div id="impulseScatterChart" class="chart"></div>
                </div>
            </div>

            <div class="chart-card">
                <div class="chart-header">
                    <h3>ECOS Analyst Estimates</h3>
                    <span class="badge" id="impulseEstimatesBadge">--</span>
                </div>
                <div style="max-height: 340px; overflow:auto;">
                    <table class="data-table" id="impulseEstimatesTable">
                        <thead><tr><th>Economist</th><th>Firm</th><th>Estimate</th><th>As Of</th><th>Rank</th></tr></thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>
        </div>
"""


def generate_impulse_tab_js(config: dict) -> str:
    return r"""
        let impulseSetup = null;
        let impulseHistory = null;

        function impulseFmt(value, suffix = '') {
            const n = Number(value);
            return Number.isFinite(n) ? `${n.toFixed(1)}${suffix}` : '--';
        }

        function impulseSetText(id, text) {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
        }

        async function loadImpulseDefaultEcos() {
            const fd = new FormData();
            fd.append('use_default', '1');
            await loadImpulseEcos(fd);
        }

        async function loadImpulseEcosFile() {
            const input = document.getElementById('impulseEcosFile');
            if (!input || !input.files || !input.files.length) {
                alert('Select an ECOS .xlsx file first.');
                return;
            }
            const fd = new FormData();
            fd.append('file', input.files[0]);
            await loadImpulseEcos(fd);
        }

        async function loadImpulseEcos(formData) {
            impulseSetText('impulsePathBadge', 'Loading ECOS...');
            const resp = await fetch('/api/impulse/ecos', { method: 'POST', body: formData }).then(r => r.json());
            if (!resp.success) {
                impulseSetText('impulsePathBadge', 'ECOS error');
                document.getElementById('impulseReadout').textContent = resp.error || 'Failed to parse ECOS file';
                return;
            }
            impulseSetup = resp;
            renderImpulseSetup(resp);
            await refreshImpulseHistory();
            await computeImpulseScenarios();
        }

        function renderImpulseSetup(data) {
            const s = data.summary || {};
            impulseSetText('impulsePrior', impulseFmt(s.prior, 'k'));
            impulseSetText('impulseMedian', impulseFmt(s.median, 'k'));
            impulseSetText('impulseExpectedChange', impulseFmt(s.expected_change, 'k'));
            impulseSetText('impulseStdDev', impulseFmt(s.std_dev, 'k'));
            impulseSetText('impulsePriorPct', impulseFmt(s.prior_percentile, '%'));
            impulseSetText('impulseWeighted', `${impulseFmt(s.rank_weighted_consensus, 'k')} / ${impulseFmt(s.recency_weighted_consensus, 'k')}`);
            const consInput = document.getElementById('impulseScenarioConsensus');
            const stdInput = document.getElementById('impulseScenarioStd');
            if (consInput && Number.isFinite(Number(s.median))) consInput.value = Number(s.median).toFixed(2);
            if (stdInput && Number.isFinite(Number(s.std_dev))) stdInput.value = Number(s.std_dev).toFixed(2);

            const estimates = Array.isArray(data.estimates) ? data.estimates : [];
            impulseSetText('impulseEstimatesBadge', `${estimates.length} estimates`);
            const tbody = document.querySelector('#impulseEstimatesTable tbody');
            if (tbody) {
                tbody.innerHTML = estimates.map(e => `
                    <tr>
                        <td>${e.economist || ''}</td>
                        <td>${e.firm || ''}</td>
                        <td>${impulseFmt(e.estimate, 'k')}</td>
                        <td>${e.as_of || ''}</td>
                        <td>${e.rank || ''}</td>
                    </tr>
                `).join('');
            }
        }

        function addImpulseScenarioRow(label = '', actual = '') {
            const tbody = document.querySelector('#impulseScenarioInputTable tbody');
            if (!tbody) return;
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><input value="${label}"></td>
                <td><input type="number" value="${actual}" step="1"></td>
                <td><button class="btn btn-small" onclick="this.closest('tr').remove()">x</button></td>
            `;
            tbody.appendChild(tr);
        }

        function collectImpulseScenarioRows() {
            const rows = [];
            document.querySelectorAll('#impulseScenarioInputTable tbody tr').forEach((tr, idx) => {
                const inputs = tr.querySelectorAll('input');
                const label = (inputs[0]?.value || `row_${idx + 1}`).trim();
                const actual = Number(inputs[1]?.value);
                if (Number.isFinite(actual)) rows.push({ label, actual });
            });
            return rows;
        }

        function impulseMovePair(stats) {
            if (!stats) return '-- / --';
            const signed = stats.signed?.mean;
            const max = stats.max?.mean;
            const signedText = impulseFmt(signed, '');
            const maxText = impulseFmt(max, '');
            const color = Number(signed) > 0 ? 'var(--accent-green)' : (Number(signed) < 0 ? 'var(--accent-red)' : 'var(--text-primary)');
            return `<span style="color:${color}; font-weight:700;">${signedText}</span> / ${maxText}`;
        }

        async function computeImpulseScenarios() {
            const consensus = Number(document.getElementById('impulseScenarioConsensus')?.value);
            const std = Number(document.getElementById('impulseScenarioStd')?.value);
            const bucketWidth = Number(document.getElementById('impulseScenarioBucketWidth')?.value || 20);
            const startDay = Number(document.getElementById('impulseStartDay')?.value || -2);
            const startTime = document.getElementById('impulseStartTime')?.value || '15:00';
            const actuals = collectImpulseScenarioRows();
            impulseSetText('impulseScenariosBadge', 'Computing...');
            const resp = await fetch('/api/impulse/scenarios', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    event_type: window.eventType || 'NFP',
                    consensus,
                    std,
                    bucket_width: bucketWidth,
                    start_day: startDay,
                    start_hhmm: startTime,
                    actuals
                })
            }).then(r => r.json());
            if (!resp.success) {
                impulseSetText('impulseScenariosBadge', 'Error');
                document.getElementById('impulseScenarioReadout').textContent = resp.error || 'Failed to compute scenario table';
                return;
            }
            renderImpulseScenarioResults(resp);
        }

        function renderImpulseScenarioResults(resp) {
            const tbody = document.querySelector('#impulseScenarioResultsTable tbody');
            const rows = resp.rows || [];
            if (!tbody) return;
            tbody.innerHTML = rows.map(r => {
                const n = r.bucket_n || 0;
                const warn = n < 5 ? ' <span style="color:#fbbf24;">low N</span>' : '';
                return `
                    <tr>
                        <td>${r.label}</td>
                        <td>${impulseFmt(r.actual, 'k')}</td>
                        <td>${impulseFmt(r.z, 'σ')}</td>
                        <td>${impulseFmt(r.surprise_pct, '%')}</td>
                        <td>${impulseMovePair(r.pre)}</td>
                        <td>${impulseMovePair(r.post_2h)}</td>
                        <td>${impulseMovePair(r.post_eod)}</td>
                        <td>${n}${warn}</td>
                    </tr>
                `;
            }).join('');
            impulseSetText('impulseScenariosBadge', `${rows.length} scenarios`);
            document.getElementById('impulseScenarioReadout').innerHTML = `
                Consensus ${impulseFmt(resp.consensus, 'k')}, std ${impulseFmt(resp.std, 'k')}, bucket width ${impulseFmt(resp.bucket_width, '%ile')}.
                Pre-window starts T${resp.pre_start_day} ${resp.pre_start_hhmm}; post windows are 8:30→+2h and 8:30→17:00.
            `;
        }

        async function refreshImpulseHistory() {
            const startDay = Number(document.getElementById('impulseStartDay')?.value || -2);
            const startTime = document.getElementById('impulseStartTime')?.value || '15:00';
            impulseSetText('impulsePathBadge', 'Loading history...');
            const params = new URLSearchParams({
                event_type: window.eventType || 'NFP',
                start_day: String(startDay),
                start_hhmm: startTime
            });
            const resp = await fetch(`/api/impulse/history?${params.toString()}`).then(r => r.json());
            if (!resp.success) {
                impulseSetText('impulsePathBadge', 'History error');
                document.getElementById('impulseReadout').textContent = resp.error || 'Failed to load impulse history';
                return;
            }
            impulseHistory = resp;
            renderImpulseCharts();
        }

        function aggregateImpulseRecords(records, stepMin = 30) {
            if (!Array.isArray(records) || !records.length) return [];
            const maxT = Math.max(...records.flatMap(r => (r.path || []).map(p => p.t_min || 0)));
            const out = [];
            for (let t = 0; t <= maxT; t += stepMin) {
                const vals = [];
                records.forEach(r => {
                    const pts = r.path || [];
                    const xs = pts.map(p => p.t_min);
                    const ys = pts.map(p => p.bps);
                    const v = interpolateAt(xs, ys, t);
                    if (Number.isFinite(v)) vals.push(v);
                });
                vals.sort((a,b) => a-b);
                if (vals.length) {
                    out.push({ t_min: t, mean: vals.reduce((a,b)=>a+b,0)/vals.length, p25: percentile(vals,25), p75: percentile(vals,75), count: vals.length });
                }
            }
            return out;
        }

        function selectImpulseMatchedRecords(records, expectedChange) {
            if (!Number.isFinite(expectedChange)) return [];
            const sameSign = records.filter(r => Number.isFinite(r.expected_change) && Math.sign(r.expected_change) === Math.sign(expectedChange));
            if (sameSign.length >= 5) return sameSign;
            const sorted = [...records].filter(r => Number.isFinite(r.expected_change)).sort((a,b) => Math.abs(a.expected_change - expectedChange) - Math.abs(b.expected_change - expectedChange));
            return sorted.slice(0, Math.min(8, sorted.length));
        }

        function renderImpulseCharts() {
            const records = impulseHistory?.records || [];
            const setup = impulseSetup?.summary || {};
            const expectedChange = Number(setup.expected_change);
            const matched = selectImpulseMatchedRecords(records, expectedChange);
            const allAgg = impulseHistory?.aggregate_path || aggregateImpulseRecords(records);
            const matchedAgg = aggregateImpulseRecords(matched);

            const allTrace = { x: allAgg.map(p => p.t_min / 60), y: allAgg.map(p => p.mean), mode: 'lines', name: 'All events mean', line: { color: '#8b98a5', width: 2 } };
            const p25 = { x: allAgg.map(p => p.t_min / 60), y: allAgg.map(p => p.p25), mode: 'lines', name: 'All P25', line: { color: '#374151', width: 1, dash: 'dot' } };
            const p75 = { x: allAgg.map(p => p.t_min / 60), y: allAgg.map(p => p.p75), mode: 'lines', name: 'All P75', line: { color: '#374151', width: 1, dash: 'dot' }, fill: 'tonexty', fillcolor: 'rgba(148,163,184,0.12)' };
            const matchTrace = { x: matchedAgg.map(p => p.t_min / 60), y: matchedAgg.map(p => p.mean), mode: 'lines', name: 'Setup-matched mean', line: { color: '#22c55e', width: 3 } };
            Plotly.newPlot('impulseDriftChart', [p25, p75, allTrace, matchTrace], {
                ...darkLayout,
                xaxis: { ...darkLayout.xaxis, title: 'Hours from window start' },
                yaxis: { ...darkLayout.yaxis, title: 'TY drift (bps)' },
                legend: { orientation: 'h', y: -0.2 }
            }, { responsive: true });

            const x = records.map(r => r.expected_change);
            const y = records.map(r => r.drift_bps);
            const scatter = {
                x, y, type: 'scatter', mode: 'markers', name: 'Historical NFP',
                text: records.map(r => `${r.date}<br>Expected: ${impulseFmt(r.expected_change,'k')}<br>Drift: ${impulseFmt(r.drift_bps,' bps')}`),
                marker: { color: '#1d9bf0', size: 8 }
            };
            const traces = [scatter];
            if (Number.isFinite(expectedChange)) {
                traces.push({ x: [expectedChange], y: [0], type: 'scatter', mode: 'markers', name: 'Current setup (x only)', marker: { color: '#fbbf24', size: 14, symbol: 'star' } });
            }
            Plotly.newPlot('impulseScatterChart', traces, {
                ...darkLayout,
                xaxis: { ...darkLayout.xaxis, title: 'Expected change: consensus - prior (k)' },
                yaxis: { ...darkLayout.yaxis, title: 'Pre-event TY drift (bps)' },
                legend: { orientation: 'h', y: -0.2 }
            }, { responsive: true });

            const driftVals = matched.map(r => r.drift_bps).filter(Number.isFinite);
            const mean = driftVals.length ? driftVals.reduce((a,b)=>a+b,0)/driftVals.length : null;
            const rallyRate = driftVals.length ? driftVals.filter(v => v > 0).length / driftVals.length : null;
            const fit = impulseHistory?.fit || {};
            impulseSetText('impulsePathBadge', `${matched.length} matched / ${records.length} events`);
            document.getElementById('impulseReadout').innerHTML = `
                <strong>Current setup:</strong> expected change ${impulseFmt(expectedChange, 'k')} (median - prior).
                Matched historical bucket: ${matched.length} events. Mean pre-event drift:
                <span class="highlight">${impulseFmt(mean, ' bps')}</span>; rally rate:
                <span class="highlight">${rallyRate == null ? '--' : (rallyRate * 100).toFixed(0) + '%'}</span>.
                Relationship fit: corr ${fit.corr == null ? '--' : fit.corr.toFixed(2)}, slope ${fit.slope == null ? '--' : fit.slope.toFixed(3)} bps/k.
            `;
        }
"""
