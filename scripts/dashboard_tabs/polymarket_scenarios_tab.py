"""
Polymarket Scenarios Tab
------------------------
Renders NFP + UER scenario tables with DB-backed Polymarket odds and
historical bucket statistics.
"""


def generate_polymarket_scenarios_tab_html(config: dict) -> str:
    event_type = str(config.get("event_type", "NFP")).upper()
    is_cpi_mode = event_type == "CPI"
    enabled_components = ["cpi"] if is_cpi_mode else ["nfp", "uer"]
    enabled_components_js = "[" + ", ".join(f"'{c}'" for c in enabled_components) + "]"
    tab_title = "Polymarket Scenarios (CPI)" if is_cpi_mode else "Polymarket Scenarios (NFP + UER)"
    nfp_card_extra = "display: none;" if is_cpi_mode else ""
    uer_card_extra = "display: none;" if is_cpi_mode else ""
    cpi_card_extra = "" if is_cpi_mode else "display: none;"

    default_nfp_consensus = config.get("default_nfp_consensus", 150.0)
    default_nfp_std = config.get("default_nfp_std", 40.0)
    default_uer_consensus = config.get("default_uer_consensus", 4.0)
    default_uer_std = config.get("default_uer_std", 0.1)
    default_cpi_consensus = config.get("default_cpi_consensus", 0.3)
    default_cpi_std = config.get("default_cpi_std", 0.05)

    return f"""        <!-- POLYMARKET SCENARIOS TAB -->
        <div class="tab-content" id="polymarketScenariosTab">
            <style>
                #polymarketScenariosTab .card {{
                    margin-bottom: 14px;
                    border-radius: 12px;
                    border: 1px solid var(--border-color);
                    background: linear-gradient(180deg, rgba(15, 23, 42, 0.9), rgba(17, 24, 39, 0.95));
                    box-shadow: 0 8px 20px rgba(2, 6, 23, 0.35);
                }}
                #polymarketScenariosTab h3,
                #polymarketScenariosTab h4 {{
                    margin-top: 0;
                    margin-bottom: 10px;
                    letter-spacing: 0.01em;
                }}
                #polymarketScenariosTab .pm-subtle {{
                    color: var(--text-secondary);
                    font-size: 0.82rem;
                }}
                #polymarketScenariosTab .pm-live-strip {{
                    display: grid;
                    grid-template-columns: repeat(5, minmax(150px, 1fr));
                    gap: 8px;
                    margin-bottom: 10px;
                }}
                #polymarketScenariosTab .pm-live-cell {{
                    background: #0f1a31;
                    border: 1px solid var(--border-color);
                    border-radius: 10px;
                    padding: 8px 10px;
                }}
                #polymarketScenariosTab .pm-live-label {{
                    font-size: 0.75rem;
                    color: var(--text-secondary);
                }}
                #polymarketScenariosTab .pm-live-value {{
                    font-size: 0.95rem;
                    color: var(--text-primary);
                    font-weight: 700;
                }}
                #polymarketScenariosTab .pm-controls-grid {{
                    display: grid;
                    grid-template-columns: repeat(3, minmax(160px, 240px));
                    gap: 10px;
                }}
                #polymarketScenariosTab .pm-table-wrap {{
                    overflow-x: auto;
                    margin-top: 10px;
                    border: 1px solid #253247;
                    border-radius: 10px;
                }}
                #polymarketScenariosTab .data-table {{
                    width: 100%;
                    border-collapse: collapse;
                }}
                #polymarketScenariosTab .data-table thead th {{
                    position: sticky;
                    top: 0;
                    z-index: 1;
                    background: #111b2f;
                    color: #dbeafe;
                    font-size: 0.79rem;
                    white-space: nowrap;
                }}
                #polymarketScenariosTab .data-table tbody td {{
                    font-size: 0.82rem;
                }}
                #polymarketScenariosTab .data-table tbody tr:nth-child(odd) {{
                    background: rgba(17, 24, 39, 0.55);
                }}
                #polymarketScenariosTab .data-table tbody tr:hover {{
                    background: rgba(29, 155, 240, 0.12);
                }}
                #polymarketScenariosTab .data-table tfoot tr {{
                    background: rgba(245, 158, 11, 0.18);
                }}
                #polymarketScenariosTab .data-table tfoot td {{
                    font-weight: 700;
                    border-top: 1px solid rgba(245, 158, 11, 0.45);
                }}
                #pmNfpTable tbody tr.pm-row-selected,
                #pmUerTable tbody tr.pm-row-selected,
                #pmCpiTable tbody tr.pm-row-selected {{
                    background-color: rgba(59, 130, 246, 0.22);
                    outline: 1px solid rgba(59, 130, 246, 0.55);
                }}
                #pmPriceLadder .pm-ladder-table {{
                    width: 100%;
                    border-collapse: collapse;
                    table-layout: fixed;
                    font-size: 0.74rem;
                }}
                #pmPriceLadder .pm-ladder-table th,
                #pmPriceLadder .pm-ladder-table td {{
                    border: 1px solid var(--border-color);
                    padding: 3px 4px;
                    vertical-align: top;
                }}
                #pmPriceLadder .pm-ladder-table th {{
                    background: #111827;
                    color: #cbd5e1;
                    font-weight: 600;
                    letter-spacing: 0.2px;
                }}
                #pmPriceLadder .pm-ladder-price {{
                    white-space: nowrap;
                    font-weight: 700;
                    color: #dbeafe;
                    background: #0f172a;
                    text-align: center;
                }}
                #pmPriceLadder .pm-ladder-cell {{
                    min-height: 18px;
                    background: #0b1220;
                }}
                #pmPriceLadder .pm-tag {{
                    display: inline-flex;
                    align-items: center;
                    margin: 1px 2px 1px 0;
                    padding: 1px 5px;
                    border-radius: 4px;
                    border: 1px solid var(--border-color);
                    background: var(--bg-tertiary);
                    color: var(--text-primary);
                    font-size: 0.66rem;
                    line-height: 1.1;
                }}
                #pmPriceLadder .pm-tag-up {{
                    border-color: rgba(34,197,94,0.6);
                    color: #22c55e;
                }}
                #pmPriceLadder .pm-tag-down {{
                    border-color: rgba(239,68,68,0.6);
                    color: #ef4444;
                }}
                #pmPriceLadder .pm-ladder-wrap {{
                    max-width: 760px;
                }}
                #pmHistModal .modal-content {{
                    border: 1px solid #334155;
                    border-radius: 12px;
                    background: #0b1220;
                    color: #e5e7eb;
                }}
                #pmHistModal .close {{
                    color: #94a3b8;
                }}
                #pmHistModal .close:hover {{
                    color: #e5e7eb;
                }}
                @media (max-width: 1200px) {{
                    #polymarketScenariosTab .pm-live-strip {{
                        grid-template-columns: repeat(2, minmax(150px, 1fr));
                    }}
                }}
                @media (max-width: 820px) {{
                    #polymarketScenariosTab .pm-controls-grid {{
                        grid-template-columns: 1fr;
                    }}
                    #polymarketScenariosTab .pm-live-strip {{
                        grid-template-columns: 1fr;
                    }}
                }}
            </style>
            <div class="card">
                <h3 style="margin-bottom: 8px;">{tab_title}</h3>
                <div id="pmLiveStrip" class="pm-live-strip">
                    <div class="pm-live-cell">
                        <div class="pm-live-label">Status</div>
                        <div id="pmLiveStatus" class="pm-live-value" style="color: #22c55e;">--</div>
                    </div>
                    <div class="pm-live-cell">
                        <div class="pm-live-label">Current TY (Databento)</div>
                        <div id="pmLivePrice" class="pm-live-value">--</div>
                    </div>
                    <div class="pm-live-cell">
                        <div id="pmRefLabel" class="pm-live-label">Reference (3pm)</div>
                        <div id="pmLiveRef" class="pm-live-value">--</div>
                    </div>
                    <div class="pm-live-cell">
                        <div class="pm-live-label">High/Low since ref</div>
                        <div id="pmLiveHighLow" class="pm-live-value" style="font-size: 0.9rem;">-- / --</div>
                    </div>
                    <div class="pm-live-cell">
                        <div class="pm-live-label">B/E Realized (current)</div>
                        <div id="pmLiveBe" class="pm-live-value" style="color: #f59e0b;">--</div>
                    </div>
                </div>
                <div id="pmPriceLadder" style="margin: 10px 0 8px 0;"></div>
                <div id="pmResolvedMarkets" style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 10px;">
                    Resolving tracked markets...
                </div>
                <div class="pm-subtle">
                    Odds source: <b>polymarket_markets.db</b> (latest midpoint per token).
                </div>
            </div>

            <div class="card" id="pmNfpCard" style="margin-top: 14px; {nfp_card_extra}">
                <h4>NFP: Jobs Added in January</h4>
                <div class="controls pm-controls-grid">
                    <div class="control-group">
                        <label>Consensus</label>
                        <input id="pmNfpConsensus" type="number" step="1" value="{default_nfp_consensus}">
                    </div>
                    <div class="control-group">
                        <label>Consensus Std Dev</label>
                        <input id="pmNfpStd" type="number" step="0.1" value="{default_nfp_std}">
                    </div>
                    <div class="control-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary" onclick="pmRefreshTable('nfp')">Refresh NFP</button>
                    </div>
                </div>
                <div class="pm-table-wrap">
                    <table id="pmNfpTable" class="data-table">
                        <thead>
                            <tr>
                                <th>Actual Range</th>
                                <th>Polymarket Odds</th>
                                <th>Surprise Percentile</th>
                                <th>Pre Event B/E (bps)</th>
                                <th>Post Event B/E (bps)</th>
                                <th>Projected @8:30 (R/S)</th>
                                <th>Projected @3pm (R/S)</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                        <tfoot>
                            <tr id="pmNfpExpected">
                                <td><b>Expected (probability-weighted)</b></td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                            </tr>
                        </tfoot>
                    </table>
                </div>
            </div>

            <div class="card" id="pmUerCard" style="margin-top: 14px; {uer_card_extra}">
                <h4>UER: January Unemployment Rate</h4>
                <div class="controls pm-controls-grid">
                    <div class="control-group">
                        <label>Consensus</label>
                        <input id="pmUerConsensus" type="number" step="0.01" value="{default_uer_consensus}">
                    </div>
                    <div class="control-group">
                        <label>Consensus Std Dev</label>
                        <input id="pmUerStd" type="number" step="0.01" value="{default_uer_std}">
                    </div>
                    <div class="control-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary" onclick="pmRefreshTable('uer')">Refresh UER</button>
                    </div>
                </div>
                <div class="pm-table-wrap">
                    <table id="pmUerTable" class="data-table">
                        <thead>
                            <tr>
                                <th>Actual Range</th>
                                <th>Polymarket Odds</th>
                                <th>Surprise Percentile</th>
                                <th>Pre Event B/E (bps)</th>
                                <th>Post Event B/E (bps)</th>
                                <th>Projected @8:30 (R/S)</th>
                                <th>Projected @3pm (R/S)</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                        <tfoot>
                            <tr id="pmUerExpected">
                                <td><b>Expected (probability-weighted)</b></td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                            </tr>
                        </tfoot>
                    </table>
                </div>
            </div>

            <div class="card" id="pmCpiCard" style="margin-top: 14px; {cpi_card_extra}">
                <h4>CPI: Inflation in January</h4>
                <div class="controls pm-controls-grid">
                    <div class="control-group">
                        <label>Headline MoM Consensus</label>
                        <input id="pmCpiConsensus" type="number" step="0.01" value="{default_cpi_consensus}">
                    </div>
                    <div class="control-group">
                        <label>Headline MoM Consensus Std Dev</label>
                        <input id="pmCpiStd" type="number" step="0.01" value="{default_cpi_std}">
                    </div>
                    <div class="control-group">
                        <label>&nbsp;</label>
                        <button class="btn btn-primary" onclick="pmRefreshCpiTables()">Refresh CPI Tables</button>
                    </div>
                </div>
                <div class="pm-table-wrap">
                    <table id="pmCpiTable" class="data-table">
                        <caption style="caption-side: top; text-align: left; color: var(--text-secondary); padding: 6px 2px;">Headline MoM (with surprise percentile + pre/post B/E)</caption>
                        <thead>
                            <tr>
                                <th>Actual Range</th>
                                <th>Polymarket Odds</th>
                                <th>Surprise Percentile</th>
                                <th>Pre Event B/E (bps)</th>
                                <th>Post Event B/E (bps)</th>
                                <th>Projected @8:30 (R/S)</th>
                                <th>Projected @3pm (R/S)</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                        <tfoot>
                            <tr id="pmCpiExpected">
                                <td><b>Expected (probability-weighted)</b></td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                                <td>--</td>
                            </tr>
                        </tfoot>
                    </table>
                </div>
                <div class="pm-table-wrap" style="margin-top: 12px;">
                    <table id="pmCpiYoyTable" class="data-table">
                        <caption style="caption-side: top; text-align: left; color: var(--text-secondary); padding: 6px 2px;">Headline YoY (odds only)</caption>
                        <thead>
                            <tr>
                                <th>Actual Range</th>
                                <th>Polymarket Odds</th>
                            </tr>
                        </thead>
                        <tbody></tbody>
                    </table>
                </div>
            </div>

            <div id="pmHistModal" class="modal" style="display:none;">
                <div class="modal-content" style="max-width: 1000px; width: 96vw;">
                    <span class="close" onclick="pmCloseHistModal()">&times;</span>
                    <h4 id="pmHistTitle">Historical Returns Distribution</h4>
                    <div style="display: flex; gap: 8px; align-items: center; margin: 8px 0 10px 0;">
                        <label for="pmHistStartH">Window start (h)</label>
                        <input id="pmHistStartH" type="number" value="-12" step="1" min="-12" max="12" style="width:80px;">
                        <label for="pmHistEndH">Window end (h)</label>
                        <input id="pmHistEndH" type="number" value="12" step="1" min="-12" max="12" style="width:80px;">
                        <button class="btn btn-secondary" onclick="pmReloadHist()">Reload</button>
                    </div>
                    <div id="pmHistStats" style="font-size: 0.84rem; color: #111827; background: #ffffff; border: 1px solid #d1d5db; border-radius: 6px; padding: 8px 10px; margin: 8px 0 10px 0; white-space: pre-line;"></div>
                    <div id="pmHistChart" style="height: 420px;"></div>
                    <div id="pmHistBinHint" style="font-size: 0.84rem; color: var(--text-secondary); margin: 8px 0 6px 0;">
                        Click a histogram bin to load price paths for events in that bin.
                    </div>
                    <div id="pmHistPathsChart" style="height: 360px;"></div>
                </div>
            </div>
        </div>

        <script>
        const pmEnabledComponents = {enabled_components_js};
        const pmState = {{
            nfp: {{ rowMeta: [], expected: null }},
            uer: {{ rowMeta: [], expected: null }},
            cpi: {{ rowMeta: [], expected: null }},
            cpiYoyRows: [],
            activeHist: null,
            liveTimer: null,
            live: null,
        }};

        function pmToBond32(px) {{
            const v = Number(px);
            if (!Number.isFinite(v)) return '--';
            const whole = Math.floor(v);
            const frac32 = (v - whole) * 32.0;
            const thirtySeconds = Math.floor(frac32 + 1e-9);
            const half = (frac32 - thirtySeconds) >= 0.5;
            const ts = String(Math.max(0, Math.min(31, thirtySeconds))).padStart(2, '0');
            return `${{whole}}-${{ts}}${{half ? '+' : ''}}`;
        }}

        async function pmRefreshLiveStrip() {{
            const statusEl = document.getElementById('pmLiveStatus');
            const priceEl = document.getElementById('pmLivePrice');
            const refEl = document.getElementById('pmLiveRef');
            const highLowEl = document.getElementById('pmLiveHighLow');
            const beEl = document.getElementById('pmLiveBe');
            const refLabelEl = document.getElementById('pmRefLabel');
            try {{
                const live = await fetch('/api/live').then(r => r.json());
                const src = (live.source || 'unknown').toString();
                const tsIso = live.timestamp_iso || null;
                const tsTxt = tsIso ? new Date(tsIso).toLocaleTimeString() : '--';
                if (statusEl) statusEl.textContent = `LIVE (${{src}}) @ ${{tsTxt}}`;
                pmState.live = live;

                const ty = Number(live.ty_price);
                if (priceEl) {{
                    priceEl.textContent = Number.isFinite(ty)
                        ? `${{ty.toFixed(6)}} (${{pmToBond32(ty)}})`
                        : '--';
                }}

                const ref = Number(live.ty_price_3pm);
                const refTs = live.ty_price_3pm_timestamp ? String(live.ty_price_3pm_timestamp) : '';
                const refDay = refTs ? refTs.split('T')[0] : '--';
                if (refLabelEl) refLabelEl.textContent = `Reference (3pm, ${{refDay}})`;
                if (refEl) {{
                    refEl.textContent = Number.isFinite(ref)
                        ? `${{ref.toFixed(6)}} (${{pmToBond32(ref)}})`
                        : '--';
                }}

                const hi = Number(live.high_since_3pm);
                const lo = Number(live.low_since_3pm);
                if (highLowEl) {{
                    const hiTxt = Number.isFinite(hi) ? pmToBond32(hi) : '--';
                    const loTxt = Number.isFinite(lo) ? pmToBond32(lo) : '--';
                    highLowEl.textContent = `${{hiTxt}} / ${{loTxt}}`;
                }}

                const be = Number(live.be_realized_bps);
                const up = Number(live.move_up_bps);
                const downRaw = Number(live.move_down_bps);
                let signedBe = null;
                if (Number.isFinite(up) && Number.isFinite(downRaw)) {{
                    const upMag = Math.max(0, up);
                    const downMag = Math.abs(downRaw);
                    signedBe = upMag >= downMag ? upMag : -downMag;
                }} else if (Number.isFinite(be)) {{
                    signedBe = be;
                }}
                if (beEl) {{
                    beEl.textContent = Number.isFinite(signedBe)
                        ? `${{signedBe >= 0 ? '+' : ''}}${{signedBe.toFixed(2)}} bps`
                        : '--';
                    beEl.style.color = Number.isFinite(signedBe)
                        ? (signedBe >= 0 ? '#22c55e' : '#ef4444')
                        : '#f59e0b';
                }}
                for (const comp of pmEnabledComponents) {{
                    if ((pmState[comp] && pmState[comp].rowMeta || []).length) {{
                        pmRenderRows(comp, pmState[comp].rowMeta || []);
                        pmSetExpectedRow(comp, pmState[comp].expected || null);
                    }}
                }}
                if (pmState.activeHist && pmState.activeHist.row) {{
                    const comp = pmState.activeHist.component;
                    const tokenId = pmState.activeHist.row.token_id;
                    const rowsNow = (pmState[comp] && pmState[comp].rowMeta) ? pmState[comp].rowMeta : [];
                    const idxNow = rowsNow.findIndex(r => String(r.token_id || '') === String(tokenId || ''));
                    if (idxNow >= 0) pmSetSelectedRow(comp, idxNow);
                }}
                pmRenderPriceLadder();
            }} catch (_e) {{
                if (statusEl) statusEl.textContent = 'LIVE status unavailable';
            }}
        }}

        function pmSetSelectedRow(component, idx) {{
            document.querySelectorAll('#pmNfpTable tbody tr.pm-row-selected, #pmUerTable tbody tr.pm-row-selected, #pmCpiTable tbody tr.pm-row-selected, #pmCpiYoyTable tbody tr.pm-row-selected')
                .forEach((tr) => tr.classList.remove('pm-row-selected'));
            const tableId = component === 'nfp' ? 'pmNfpTable' : (component === 'uer' ? 'pmUerTable' : 'pmCpiTable');
            const tbody = document.querySelector(`#${{tableId}} tbody`);
            if (!tbody) return;
            const rows = tbody.querySelectorAll('tr');
            if (idx >= 0 && idx < rows.length) rows[idx].classList.add('pm-row-selected');
        }}

        async function pmResolveMarkets() {{
            const lines = [];
            const labels = {{ nfp: 'NFP', uer: 'UER', cpi: 'CPI' }};
            for (const kind of pmEnabledComponents) {{
                const resp = await fetch(`/api/polymarket/resolve?kind=${{encodeURIComponent(kind)}}`).then(r => r.json()).catch(() => ({{success:false}}));
                if (resp.success) lines.push(`${{labels[kind] || kind.toUpperCase()}}: ${{resp.market_title || resp.market_slug || 'resolved'}}`);
            }}
            if (!lines.length) lines.push('Could not resolve tracked markets (check polymarket DB).');
            const el = document.getElementById('pmResolvedMarkets');
            if (el) el.textContent = lines.join(' | ');
        }}

        function pmSetExpectedRow(component, expected) {{
            const tr = document.getElementById(component === 'nfp' ? 'pmNfpExpected' : (component === 'uer' ? 'pmUerExpected' : 'pmCpiExpected'));
            if (!tr) return;
            const tds = tr.querySelectorAll('td');
            if (tds.length < 7) return;
            tds[1].textContent = '--';
            tds[2].textContent = '--';
            tds[3].textContent = expected && expected.pre_event_be_bps != null ? expected.pre_event_be_bps.toFixed(2) : '--';
            tds[4].textContent = expected && expected.post_event_be_bps != null ? expected.post_event_be_bps.toFixed(2) : '--';
            const preExp = expected && expected.pre_event_be_bps != null ? expected.pre_event_be_bps : null;
            const postExp = expected && expected.post_event_be_bps != null ? expected.post_event_be_bps : null;
            tds[5].innerHTML = pmProjectedHtml(preExp);
            tds[6].innerHTML = pmProjectedHtml(pmAddBps(preExp, postExp));
        }}

        function pmProjectedHtml(expectedBps) {{
            const pair = pmProjectedPair(expectedBps);
            if (!pair) {{
                return '--';
            }}
            const upTxt = pmToBond32(pair.rally);
            const dnTxt = pmToBond32(pair.selloff);
            return `<span style="color:#22c55e; font-weight:700;">${{upTxt}}</span>/<span style="color:#ef4444; font-weight:700;">${{dnTxt}}</span>`;
        }}

        function pmProjectedPair(expectedBps) {{
            const live = pmState.live || {{}};
            const ref = Number(live.ty_price_3pm);
            if (!Number.isFinite(expectedBps) || !Number.isFinite(ref)) return null;
            const bps = Math.max(Number(expectedBps), 0);
            return {{
                rally: ref + (bps / 16.0),
                selloff: ref - (bps / 16.0),
            }};
        }}

        function pmAddBps(a, b) {{
            const aa = Number(a);
            const bb = Number(b);
            const hasA = Number.isFinite(aa);
            const hasB = Number.isFinite(bb);
            if (!hasA && !hasB) return null;
            return (hasA ? aa : 0) + (hasB ? bb : 0);
        }}

        function pmRoundTo32(px) {{
            const v = Number(px);
            if (!Number.isFinite(v)) return null;
            return Math.round(v * 32) / 32;
        }}

        function pmLabelShort(component, label) {{
            const pref = component === 'nfp' ? 'NFP' : (component === 'uer' ? 'UER' : 'CPI');
            const raw = String(label || '').toLowerCase();
            const nums = (raw.match(/-?\\d+(?:\\.\\d+)?/g) || []).map(Number).filter(n => Number.isFinite(n));

            if (component === 'nfp') {{
                if (raw.includes('lose jobs') || raw.includes('fewer than 0') || raw.includes('below 0')) {{
                    return `${{pref}}: (<0)`;
                }}
                if ((raw.includes('more than') || raw.includes('over') || raw.includes('above')) && nums.length) {{
                    return `${{pref}}: (>${{nums[nums.length - 1]}}k)`;
                }}
                if ((raw.includes('between') || raw.includes('to')) && nums.length >= 2) {{
                    const [a, b] = nums.slice(0, 2);
                    return `${{pref}}: (${{a}}k-${{b}}k)`;
                }}
                if (nums.length) return `${{pref}}: (${{nums[0]}}k)`;
                return `${{pref}}: (--)`;
            }}

            // UER/CPI labels are usually exact values; keep concise.
            const vals = nums.filter(n => n <= 20);
            if ((raw.includes('more than') || raw.includes('over') || raw.includes('above')) && vals.length) {{
                return `${{pref}}: (>${{vals[vals.length - 1].toFixed(1)}}%)`;
            }}
            if ((raw.includes('less than') || raw.includes('below') || raw.includes('under')) && vals.length) {{
                return `${{pref}}: (<${{vals[vals.length - 1].toFixed(1)}}%)`;
            }}
            if ((raw.includes('between') || raw.includes('to')) && vals.length >= 2) {{
                const [a, b] = vals.slice(0, 2);
                return `${{pref}}: (${{a.toFixed(1)}}%-${{b.toFixed(1)}}%)`;
            }}
            if (vals.length) return `${{pref}}: (${{vals[vals.length - 1].toFixed(1)}}%)`;
            return `${{pref}}: (--)`;
        }}

        function pmRenderPriceLadder() {{
            const el = document.getElementById('pmPriceLadder');
            if (!el) return;
            const ref = Number((pmState.live || {{}}).ty_price_3pm);
            if (!Number.isFinite(ref)) {{
                el.innerHTML = `<div style="font-size:0.82rem; color: var(--text-secondary);">Price ladder will populate once 3pm reference price is available.</div>`;
                return;
            }}

            const entries = [];
            for (const component of pmEnabledComponents) {{
                const rows = (pmState[component] && pmState[component].rowMeta) ? pmState[component].rowMeta : [];
                for (const row of rows) {{
                    const pre = pmProjectedPair(row.pre_event_be_bps);
                    const post = pmProjectedPair(pmAddBps(row.pre_event_be_bps, row.post_event_be_bps));
                    const shortLabel = pmLabelShort(component, row.actual_range_label);
                    if (pre) {{
                        entries.push({{ t: '830', dir: 'up', price: pmRoundTo32(pre.rally), label: shortLabel }});
                        entries.push({{ t: '830', dir: 'down', price: pmRoundTo32(pre.selloff), label: shortLabel }});
                    }}
                    if (post) {{
                        entries.push({{ t: '1500', dir: 'up', price: pmRoundTo32(post.rally), label: shortLabel }});
                        entries.push({{ t: '1500', dir: 'down', price: pmRoundTo32(post.selloff), label: shortLabel }});
                    }}
                }}
            }}
            const levels = Array.from(new Set(entries.map(e => e.price).filter(v => v != null))).sort((a, b) => b - a);
            if (!levels.length) {{
                el.innerHTML = `<div style="font-size:0.82rem; color: var(--text-secondary);">No projected levels yet.</div>`;
                return;
            }}
            const byKey = new Map();
            for (const e of entries) {{
                const k = `${{e.t}}|${{e.price}}`;
                if (!byKey.has(k)) byKey.set(k, []);
                byKey.get(k).push(e);
            }}
            const rowsHtml = levels.map((lvl) => {{
                const left = byKey.get(`830|${{lvl}}`) || [];
                const right = byKey.get(`1500|${{lvl}}`) || [];
                const mk = (arr) => arr.length
                    ? arr.map(it => `<span class="pm-tag ${{it.dir === 'up' ? 'pm-tag-up' : 'pm-tag-down'}}">${{it.dir === 'up' ? '↑' : '↓'}} ${{it.label}}</span>`).join('')
                    : '<span style="color:var(--text-secondary)">--</span>';
                return `<tr>
                    <td class="pm-ladder-price">${{pmToBond32(lvl)}}</td>
                    <td class="pm-ladder-cell">${{mk(left)}}</td>
                    <td class="pm-ladder-cell">${{mk(right)}}</td>
                </tr>`;
            }}).join('');
            el.innerHTML = `
                <div class="pm-ladder-wrap">
                    <table class="pm-ladder-table">
                        <thead>
                            <tr>
                                <th style="width:92px;">Price</th>
                                <th style="width:300px;">@8:30</th>
                                <th style="width:300px;">@3pm</th>
                            </tr>
                        </thead>
                        <tbody>${{rowsHtml}}</tbody>
                    </table>
                </div>
            `;
        }}

        function pmRenderRows(component, rows) {{
            const tableId = component === 'nfp' ? 'pmNfpTable' : (component === 'uer' ? 'pmUerTable' : 'pmCpiTable');
            const tbody = document.querySelector(`#${{tableId}} tbody`);
            if (!tbody) return;
            tbody.innerHTML = '';
            pmState[component].rowMeta = rows || [];
            (rows || []).forEach((row, idx) => {{
                const tr = document.createElement('tr');
                tr.style.cursor = 'pointer';
                tr.onclick = () => {{
                    pmSetSelectedRow(component, idx);
                    pmOpenHist(component, idx);
                }};
                tr.innerHTML = `
                    <td>${{row.actual_range_label || '--'}}</td>
                    <td>${{row.polymarket_odds != null ? (row.polymarket_odds * 100).toFixed(2) + '%' : '--'}}</td>
                    <td>${{row.surprise_percentile != null ? row.surprise_percentile.toFixed(1) : '--'}}</td>
                    <td>${{row.pre_event_be_bps != null ? row.pre_event_be_bps.toFixed(2) : '--'}}</td>
                    <td>${{row.post_event_be_bps != null ? row.post_event_be_bps.toFixed(2) : '--'}}</td>
                    <td>${{pmProjectedHtml(row.pre_event_be_bps)}}</td>
                    <td>${{pmProjectedHtml(pmAddBps(row.pre_event_be_bps, row.post_event_be_bps))}}</td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        function pmRenderCpiYoyRows(rows) {{
            const tbody = document.querySelector('#pmCpiYoyTable tbody');
            if (!tbody) return;
            tbody.innerHTML = '';
            pmState.cpiYoyRows = rows || [];
            (rows || []).forEach((row) => {{
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${{row.actual_range_label || '--'}}</td>
                    <td>${{row.polymarket_odds != null ? (row.polymarket_odds * 100).toFixed(2) + '%' : '--'}}</td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        async function pmRefreshTable(component) {{
            const consensus = component === 'nfp'
                ? Number(document.getElementById('pmNfpConsensus')?.value || '0')
                : (component === 'uer'
                    ? Number(document.getElementById('pmUerConsensus')?.value || '0')
                    : Number(document.getElementById('pmCpiConsensus')?.value || '0'));
            const consStd = component === 'nfp'
                ? Number(document.getElementById('pmNfpStd')?.value || '0')
                : (component === 'uer'
                    ? Number(document.getElementById('pmUerStd')?.value || '0')
                    : Number(document.getElementById('pmCpiStd')?.value || '0'));

            const url = `/api/scenarios/table?component=${{encodeURIComponent(component)}}&consensus=${{encodeURIComponent(consensus)}}&cons_std=${{encodeURIComponent(consStd)}}`;
            const resp = await fetch(url).then(r => r.json()).catch(() => ({{success:false, error:'Request failed'}}));
            if (!resp.success) {{
                pmRenderRows(component, []);
                pmSetExpectedRow(component, null);
                pmState[component].expected = null;
                pmRenderPriceLadder();
                return;
            }}
            pmRenderRows(component, resp.rows || []);
            pmState[component].expected = resp.expected || null;
            pmSetExpectedRow(component, pmState[component].expected);
            pmRenderPriceLadder();
        }}

        async function pmRefreshCpiYoyTable() {{
            const resp = await fetch('/api/scenarios/cpi_yoy_odds').then(r => r.json()).catch(() => ({{ success:false }}));
            if (!resp.success) {{
                pmRenderCpiYoyRows([]);
                return;
            }}
            pmRenderCpiYoyRows(resp.rows || []);
        }}

        async function pmRefreshCpiTables() {{
            await pmRefreshTable('cpi');
            await pmRefreshCpiYoyTable();
        }}

        async function pmOpenHist(component, idx) {{
            const row = pmState[component].rowMeta[idx];
            if (!row) return;
            pmState.activeHist = {{ component, row }};
            const title = document.getElementById('pmHistTitle');
            if (title) title.textContent = 'Historical Returns Distribution';
            const modal = document.getElementById('pmHistModal');
            if (modal) modal.style.display = 'block';
            await pmReloadHist();
        }}

        function pmCloseHistModal() {{
            const modal = document.getElementById('pmHistModal');
            if (modal) modal.style.display = 'none';
        }}

        function pmKernelDensityEstimate(values, xGrid) {{
            if (!values || values.length < 2 || !xGrid || !xGrid.length) return [];
            const n = values.length;
            const mean = values.reduce((a, b) => a + b, 0) / n;
            const variance = values.reduce((acc, v) => acc + Math.pow(v - mean, 2), 0) / Math.max(1, n - 1);
            const sigma = Math.sqrt(Math.max(variance, 1e-9));
            const bandwidth = 1.06 * sigma * Math.pow(n, -0.2);
            if (!isFinite(bandwidth) || bandwidth <= 0) return [];
            const invSqrt2Pi = 1 / Math.sqrt(2 * Math.PI);
            return xGrid.map((x) => {{
                let sum = 0;
                for (const v of values) {{
                    const z = (x - v) / bandwidth;
                    sum += Math.exp(-0.5 * z * z) * invSqrt2Pi;
                }}
                return sum / (n * bandwidth);
            }});
        }}

        function pmLinspace(minV, maxV, points) {{
            if (!isFinite(minV) || !isFinite(maxV) || points <= 1) return [minV, maxV];
            const out = [];
            const step = (maxV - minV) / (points - 1);
            for (let i = 0; i < points; i++) out.push(minV + i * step);
            return out;
        }}

        async function pmLoadBinPaths(binStart, binEnd) {{
            if (!pmState.activeHist) return;
            const {{ component, row }} = pmState.activeHist;
            const startH = Number(document.getElementById('pmHistStartH')?.value || '-12');
            const endH = Number(document.getElementById('pmHistEndH')?.value || '12');
            const hintEl = document.getElementById('pmHistBinHint');
            if (hintEl) hintEl.textContent = `Loading paths for bin ${{binStart.toFixed(2)}} to ${{binEnd.toFixed(2)}} bps...`;
            const url = `/api/scenarios/bin_paths?component=${{encodeURIComponent(component)}}&bucket_min=${{encodeURIComponent(row.bucket_min)}}&bucket_max=${{encodeURIComponent(row.bucket_max)}}&start_h=${{encodeURIComponent(startH)}}&end_h=${{encodeURIComponent(endH)}}&bin_start=${{encodeURIComponent(binStart)}}&bin_end=${{encodeURIComponent(binEnd)}}`;
            const resp = await fetch(url).then(r => r.json()).catch(() => ({{ success: false, error: 'Request failed' }}));
            if (!resp.success) {{
                if (hintEl) hintEl.textContent = 'Failed to load bin paths.';
                return;
            }}
            const paths = Array.isArray(resp.paths) ? resp.paths : [];
            if (!window.Plotly || !document.getElementById('pmHistPathsChart')) return;
            if (!paths.length) {{
                Plotly.react('pmHistPathsChart', [], {{
                    title: 'Price paths in selected bin',
                    xaxis: {{ title: 'Time of Day', type: 'date', tickformat: '%H:%M' }},
                    yaxis: {{ title: 'Move (bps)' }},
                    paper_bgcolor: 'transparent',
                    plot_bgcolor: 'transparent',
                    font: {{ color: '#d1d5db' }},
                }}, {{ responsive: true }});
                if (hintEl) hintEl.textContent = `No events in bin ${{binStart.toFixed(2)}} to ${{binEnd.toFixed(2)}} bps.`;
                return;
            }}

            const traces = paths.map((p) => {{
                const pts = Array.isArray(p.points) ? p.points : [];
                const timeOnly = pts.map(v => {{
                    if (v.ts) {{
                        const d = new Date(v.ts);
                        const hh = String(d.getHours()).padStart(2, '0');
                        const mm = String(d.getMinutes()).padStart(2, '0');
                        const ss = String(d.getSeconds()).padStart(2, '0');
                        return `2000-01-01 ${{hh}}:${{mm}}:${{ss}}`;
                    }}
                    return v.t_min;
                }});
                const hasTs = pts.length && pts.some(v => !!v.ts);
                return {{
                    x: timeOnly,
                    y: pts.map(v => v.bps),
                    type: 'scatter',
                    mode: 'lines',
                    name: `${{p.date || 'event'}}${{p.value != null ? ` (${{Number(p.value).toFixed(2)}} bps)` : ''}}`,
                    line: {{ width: 1.8 }},
                    hovertemplate: hasTs
                        ? '%{{fullData.name}}<br>%{{x}}<br>%{{y:.2f}} bps<extra></extra>'
                        : '%{{fullData.name}}<br>t=%{{x:.1f}}m<br>%{{y:.2f}} bps<extra></extra>',
                }};
            }});
            const layout = {{
                title: `Price paths in bin (${{binStart.toFixed(2)}} to ${{binEnd.toFixed(2)}} bps)`,
                xaxis: {{ title: 'Time of Day', type: 'date', tickformat: '%H:%M' }},
                yaxis: {{ title: 'Move (bps)' }},
                paper_bgcolor: 'transparent',
                plot_bgcolor: 'transparent',
                font: {{ color: '#d1d5db' }},
                legend: {{ orientation: 'v' }},
                margin: {{ l: 56, r: 20, t: 44, b: 42 }},
            }};
            Plotly.react('pmHistPathsChart', traces, layout, {{ responsive: true }});
            if (hintEl) hintEl.textContent = `Loaded ${{paths.length}} event paths for bin ${{binStart.toFixed(2)}} to ${{binEnd.toFixed(2)}} bps.`;
        }}

        async function pmReloadHist() {{
            if (!pmState.activeHist) return;
            const {{ component, row }} = pmState.activeHist;
            const startH = Number(document.getElementById('pmHistStartH')?.value || '-12');
            const endH = Number(document.getElementById('pmHistEndH')?.value || '12');
            const url = `/api/scenarios/hist?component=${{encodeURIComponent(component)}}&bucket_min=${{encodeURIComponent(row.bucket_min)}}&bucket_max=${{encodeURIComponent(row.bucket_max)}}&start_h=${{encodeURIComponent(startH)}}&end_h=${{encodeURIComponent(endH)}}`;
            const resp = await fetch(url).then(r => r.json()).catch(() => ({{success:false, error:'Request failed'}}));
            if (!resp.success) return;
            const statsEl = document.getElementById('pmHistStats');
            if (statsEl) {{
                const lines = Array.isArray(resp.stats_lines) ? resp.stats_lines : [];
                statsEl.textContent = lines.length ? lines.join('\\n') : 'No stats available.';
            }}
            const hintEl = document.getElementById('pmHistBinHint');
            if (hintEl) hintEl.textContent = 'Click a histogram bin to load price paths for events in that bin.';
            if (window.Plotly && document.getElementById('pmHistPathsChart')) {{
                Plotly.react('pmHistPathsChart', [], {{
                    title: 'Price paths in selected bin',
                    xaxis: {{ title: 'Time of Day', type: 'date', tickformat: '%H:%M' }},
                    yaxis: {{ title: 'Move (bps)' }},
                    paper_bgcolor: 'transparent',
                    plot_bgcolor: 'transparent',
                    font: {{ color: '#d1d5db' }},
                }}, {{ responsive: true }});
            }}
            const values = resp.values || [];
            if (window.Plotly && document.getElementById('pmHistChart')) {{
                const minV = values.length ? Math.min(...values) : -1;
                const maxV = values.length ? Math.max(...values) : 1;
                const range = Math.max(1e-9, (maxV - minV));
                const pad = Math.max(1, range * 0.1);
                const xGrid = pmLinspace(minV - pad, maxV + pad, 240);
                const kdeY = pmKernelDensityEstimate(values, xGrid);
                const binCount = Math.min(50, Math.max(8, Math.round(Math.sqrt(Math.max(1, values.length)) * 3)));
                const binSize = range / binCount || 1;
                const kMin = Math.floor(minV / binSize);
                const kMax = Math.ceil(maxV / binSize);
                const binStartAll = kMin * binSize;
                const binEndAll = kMax * binSize;
                const histTrace = {{
                    x: values,
                    type: 'histogram',
                    histnorm: 'probability density',
                    marker: {{ color: '#3b82f6', opacity: 0.75, line: {{ color: '#1f2937', width: 0.5 }} }},
                    xbins: {{ start: binStartAll, end: binEndAll, size: binSize }},
                    name: 'Distribution',
                }};
                const kdeTrace = {{
                    x: xGrid,
                    y: kdeY,
                    type: 'scatter',
                    mode: 'lines',
                    line: {{ color: '#f59e0b', width: 2 }},
                    name: 'KDE',
                }};
                const traces = kdeY.length ? [histTrace, kdeTrace] : [histTrace];
                const shapes = [];
                const ann = [];
                const markers = Array.isArray(resp.last_two) ? resp.last_two : [];
                for (let i = 0; i < markers.length; i++) {{
                    const m = markers[i] || {{}};
                    if (m.value == null) continue;
                    const color = i === markers.length - 1 ? '#ef4444' : '#10b981';
                    shapes.push({{
                        type: 'line',
                        x0: m.value, x1: m.value,
                        y0: 0, y1: 1, yref: 'paper',
                        line: {{ color, width: 2, dash: 'dash' }},
                    }});
                    ann.push({{
                        x: m.value,
                        y: 0.03 + i * 0.06,
                        yref: 'paper',
                        text: m.label || `Event ${{i+1}}`,
                        showarrow: false,
                        xanchor: 'center',
                        bgcolor: 'rgba(17,24,39,0.85)',
                        bordercolor: color,
                        borderwidth: 1,
                        font: {{ size: 10, color: '#e5e7eb' }},
                    }});
                }}
                const layout = {{
                    title: 'Historical Returns Distribution',
                    xaxis: {{ title: 'Returns (bps)' }},
                    yaxis: {{ title: 'Density' }},
                    paper_bgcolor: 'transparent',
                    plot_bgcolor: 'transparent',
                    font: {{ color: '#d1d5db' }},
                    shapes: shapes,
                    annotations: ann,
                    legend: {{ orientation: 'h', y: 1.08, x: 0 }},
                    margin: {{ l: 56, r: 20, t: 44, b: 42 }},
                }};
                const chartDiv = document.getElementById('pmHistChart');
                if (!chartDiv) return;
                Plotly.react('pmHistChart', traces, layout, {{responsive:true}});
                chartDiv.on('plotly_click', (clickData) => {{
                    if (!clickData || !clickData.points || !clickData.points.length) return;
                    const pt = clickData.points[0];
                    if (!pt || pt.curveNumber !== 0) return;
                    const center = Number(pt.x);
                    if (!Number.isFinite(center)) return;
                    const bs = binSize || 1;
                    const b0 = center - bs / 2;
                    const b1 = center + bs / 2;
                    pmLoadBinPaths(b0, b1);
                }});
            }}
        }}

        setTimeout(() => {{
            pmRefreshLiveStrip();
            if (pmState.liveTimer) clearInterval(pmState.liveTimer);
            pmState.liveTimer = setInterval(pmRefreshLiveStrip, 5000);
            pmResolveMarkets();
            for (const comp of pmEnabledComponents) {{
                if (comp === 'cpi') {{
                    pmRefreshCpiTables();
                }} else {{
                    pmRefreshTable(comp);
                }}
            }}
        }}, 0);
        </script>
"""
