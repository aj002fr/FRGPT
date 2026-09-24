"use strict";

const state = {
  evidence: null,
  config: null,
  sandboxMeta: null,
  preregResult: null,
  selectedExperiment: "E001",
  jobPoll: null,
};

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
    button.classList.add("active");
    document.getElementById(`tab-${button.dataset.tab}`).classList.add("active");
  });
});

async function getJson(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok || body.success === false) {
    throw new Error(body.error || `Request failed (${response.status})`);
  }
  return body;
}

function esc(value) {
  return String(value ?? "—").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function num(value, digits = 4) {
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
}

function pct(value, digits = 1) {
  return Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(digits)}%` : "—";
}

function chip(text, tone = "") {
  return `<span class="chip ${tone}">${esc(text)}</span>`;
}

function renderSummary(data) {
  const branches = data.branching.branches;
  const cohorts = data.cohorts;
  document.getElementById("campaign-summary").innerHTML = `
    <article class="summary-card primary">
      <span class="eyebrow">CAMPAIGN</span>
      <strong>${esc(data.branching.status)}</strong>
      <small>${cohorts.discovery} discovery · ${cohorts.validation} validation · ${cohorts.quarantine} quarantine dates</small>
    </article>
    <article class="summary-card">
      <span class="eyebrow">E001</span><strong>${esc(branches.E001.decision)}</strong>
      <small>Δ ${num(branches.E001.headline_effect, 6)} · confirmation closed</small>
    </article>
    <article class="summary-card">
      <span class="eyebrow">E002</span><strong>${esc(branches.E002.decision)}</strong>
      <small>Δ ${num(branches.E002.headline_effect, 6)} · economics negative</small>
    </article>
    <article class="summary-card">
      <span class="eyebrow">D001</span><strong>${branches.D001.proposals.length} proposals</strong>
      <small>Descriptive and non-evidentiary</small>
    </article>`;
}

function renderIntegrity(data) {
  const integrity = data.integrity;
  const finding = integrity.ledger_path_finding;
  const failures = integrity.checks.filter((check) => check.status !== "pass");
  const detailRows = integrity.checks.map((check) => `
    <tr><td>${esc(check.category)}</td><td>${esc(check.name)}</td>
    <td>${chip(check.status.toUpperCase(), check.status)}</td>
    <td class="mono">${esc((check.actual || "").slice(0, 12))}</td></tr>`).join("");
  document.getElementById("integrity-panel").innerHTML = `
    <div class="panel-title">
      <div><span class="eyebrow">PROVENANCE</span><h2>Integrity chain</h2></div>
      ${chip(`${integrity.passed} PASS / ${integrity.failed} FAIL`, integrity.status)}
    </div>
    <div class="notice warning"><strong>Ledger path mismatch:</strong> ${esc(finding.message)}
      The lab reads <span class="mono">campaign.sqlite</span>; it never falls back to the empty stub.</div>
    <details><summary>Show ${integrity.checks.length} digest checks</summary>
      <div class="table-wrap"><table><thead><tr><th>Category</th><th>Artifact</th><th>Status</th><th>SHA-256</th></tr></thead>
      <tbody>${detailRows || `<tr><td colspan="4">${failures.length} failures</td></tr>`}</tbody></table></div>
    </details>`;
}

function gateCells(gates) {
  return gates.map((gate) => `
    <td title="${esc(gate.detail)}">${chip(gate.passes ? "PASS" : "FAIL", gate.passes ? "pass" : "fail")}
      <small>${esc(gate.detail)}</small></td>`).join("");
}

function renderGateTable(experiment) {
  const rows = experiment.validation.evaluation.cells.map((cell) => `
    <tr><th>${esc(cell.cell_id)}<small>${esc(cell.decision)}</small></th>${gateCells(cell.gates)}</tr>`).join("");
  return `<div class="table-wrap gate-table"><table><thead><tr><th>Cell</th>
    <th>Magnitude</th><th>Holm p</th><th>CI lower</th><th>Shift null</th>
    <th>Months</th><th>Concentration</th><th>Utility</th><th>Slices</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
}

function foldRows(discovery) {
  return (discovery.fold_audit || []).map((fold) => `
    <tr><td>${esc(fold.fold)}</td><td>${fold.train_dates}</td><td>${fold.train_anchors}</td>
    <td>${fold.score_dates}</td><td>${fold.score_anchors}</td><td>${fold.embargoed_rows}</td></tr>`).join("");
}

function renderTradability(diagnostic) {
  if (!diagnostic) return "";
  return `<section class="subpanel">
    <div class="panel-title"><h3>E002 tradability diagnostic</h3>${chip(diagnostic.status, "fail")}</div>
    <div class="metric-grid">
      <div><span>Net ticks</span><strong>${num(diagnostic.equal_date_net_ticks, 3)}</strong></div>
      <div><span>95% CI</span><strong>[${num(diagnostic.ci_low, 3)}, ${num(diagnostic.ci_high, 3)}]</strong></div>
      <div><span>Mean MAE</span><strong>${num(diagnostic.mean_mae_ticks, 3)}</strong></div>
      <div><span>Episodes</span><strong>${diagnostic.episodes}</strong></div>
      <div><span>Coverage</span><strong>${pct(diagnostic.coverage)}</strong></div>
      <div><span>Round-trip cost</span><strong>${num(diagnostic.round_trip_cost_ticks, 2)} tick</strong></div>
    </div>
    <p class="caveat">Diagnostic only; it cannot change the predictive decision.</p>
  </section>`;
}

function renderExperiment(data, id = state.selectedExperiment) {
  state.selectedExperiment = id;
  const experiment = data.experiments[id];
  const discovery = experiment.discovery;
  const power = discovery.power || {};
  document.getElementById("experiment-evidence").innerHTML = `
    <div class="panel-title">
      <div><span class="eyebrow">PREDICTIVE EVIDENCE</span><h2>${id} validation gates</h2></div>
      <select id="experiment-select"><option>E001</option><option>E002</option></select>
    </div>
    ${renderGateTable(experiment)}
    <div class="two-col">
      <section class="subpanel"><h3>Discovery selection</h3>
        <pre>${esc(JSON.stringify(discovery.selection?.config || discovery.frozen_state?.config, null, 2))}</pre>
        <p>Pooled OOF effect: <strong>${num(discovery.selection?.pooled_effect, 8)}</strong>;
        positive folds: <strong>${discovery.selection?.positive_folds ?? "—"}</strong>.</p>
      </section>
      <section class="subpanel"><h3>Power audit</h3>
        <div class="metric-grid compact">
          <div><span>Gate</span><strong>${power.passes ? "PASS" : "FAIL"}</strong></div>
          <div><span>Required dates</span><strong>${power.required_validation_dates ?? "—"}</strong></div>
          <div><span>Available dates</span><strong>${power.available_validation_dates ?? "—"}</strong></div>
          <div><span>OOF dates</span><strong>${power.discovery_oof_dates ?? "—"}</strong></div>
        </div>
      </section>
    </div>
    <details><summary>Discovery fold audit</summary><div class="table-wrap"><table>
      <thead><tr><th>Fold</th><th>Train dates</th><th>Train anchors</th><th>Score dates</th><th>Score anchors</th><th>Embargoed</th></tr></thead>
      <tbody>${foldRows(discovery)}</tbody></table></div></details>
    ${renderTradability(experiment.validation.tradability_diagnostic)}`;
  const selector = document.getElementById("experiment-select");
  selector.value = id;
  selector.addEventListener("change", () => renderExperiment(data, selector.value));
}

function optionList(values) {
  return `<option value="">All</option>${values.map((value) => `<option>${esc(value)}</option>`).join("")}`;
}

function d001Filters(cells) {
  const unique = (key) => [...new Set(cells.map((cell) => cell[key]))].sort();
  return `<div class="filter-grid">
    <label>Proxy<select data-d001-filter="proxy">${optionList(unique("proxy"))}</select></label>
    <label>Bucket<select data-d001-filter="proxy_bucket">${optionList(unique("proxy_bucket"))}</select></label>
    <label>Session<select data-d001-filter="session">${optionList(unique("session"))}</select></label>
    <label>Volatility<select data-d001-filter="volatility_bucket">${optionList(unique("volatility_bucket"))}</select></label>
    <label>Horizon<select data-d001-filter="horizon_minutes">${optionList(unique("horizon_minutes"))}</select></label>
    <label class="check-label"><input type="checkbox" id="d001-eligible"> Nomination eligible only</label>
  </div>`;
}

function updateD001(data) {
  const controls = [...document.querySelectorAll("[data-d001-filter]")];
  let cells = data.experiments.D001.cells;
  controls.forEach((control) => {
    if (control.value) cells = cells.filter((cell) => String(cell[control.dataset.d001Filter]) === control.value);
  });
  if (document.getElementById("d001-eligible").checked) {
    cells = cells.filter((cell) => cell.nomination_eligible);
  }
  const rows = cells.map((cell) => `
    <tr><td>${esc(cell.cell_id)}</td><td>${cell.anchors}</td><td>${cell.first.dates}</td>
    <td>${num(cell.first.lift, 3)}</td><td>${cell.second.dates}</td><td>${num(cell.second.lift, 3)}</td>
    <td>${cell.nomination_eligible ? chip("YES", "pass") : "—"}</td></tr>`).join("");
  document.getElementById("d001-count").textContent = `${cells.length} / 243 cells`;
  document.getElementById("d001-body").innerHTML = rows;
  Plotly.react("d001-chart", [{
    x: cells.map((cell) => cell.first.lift),
    y: cells.map((cell) => cell.second.lift),
    text: cells.map((cell) => cell.cell_id),
    customdata: cells.map((cell) => cell.anchors),
    mode: "markers",
    marker: {size: cells.map((cell) => Math.max(6, Math.min(20, Math.sqrt(cell.anchors) / 3)))},
    hovertemplate: "%{text}<br>first=%{x:.3f}<br>second=%{y:.3f}<br>anchors=%{customdata}<extra></extra>",
  }], {
    margin: {l: 58, r: 20, t: 20, b: 50},
    paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: {color: "#93a4b5"},
    xaxis: {title: "First-half bounded-share lift", zerolinecolor: "#52606d", gridcolor: "#29394a"},
    yaxis: {title: "Second-half bounded-share lift", zerolinecolor: "#52606d", gridcolor: "#29394a"},
  }, {responsive: true, displaylogo: false});
}

function renderD001(data) {
  const d001 = data.experiments.D001;
  const nominations = d001.nominations.map((row) => `
    <li><strong>${esc(row.cell_id)}</strong> · first ${num(row.first.lift, 3)} · second ${num(row.second.lift, 3)}</li>`).join("");
  document.getElementById("d001-map").innerHTML = `
    <div class="panel-title"><div><span class="eyebrow">DESCRIPTIVE MAP</span><h2>D001 declared 243 cells</h2></div>
      ${chip("NON-EVIDENTIARY", "warning")}</div>
    <div class="notice"><strong>Nominations:</strong><ul>${nominations}</ul></div>
    ${d001Filters(d001.cells)}
    <div class="panel-title"><h3>Chronological-half lift agreement</h3><span id="d001-count"></span></div>
    <div id="d001-chart" class="chart"></div>
    <details><summary>Show cell audit table</summary><div class="table-wrap tall"><table>
      <thead><tr><th>Cell</th><th>Anchors</th><th>First dates</th><th>First lift</th>
      <th>Second dates</th><th>Second lift</th><th>Eligible</th></tr></thead>
      <tbody id="d001-body"></tbody></table></div></details>`;
  document.querySelectorAll("[data-d001-filter]").forEach((control) => {
    control.addEventListener("change", () => updateD001(data));
  });
  document.getElementById("d001-eligible").addEventListener("change", () => updateD001(data));
  updateD001(data);
}

function renderLedger(data) {
  const ledger = data.ledger;
  const splitCards = Object.entries(ledger.split_counts).map(([split, counts]) => `
    <div><span>${esc(split)}</span><strong>${counts.dates} dates × ${counts.targets} targets</strong>
    <small>${counts.rows} burned triples</small></div>`).join("");
  const runRows = ledger.runs.map((run) => `<tr><td>${esc(run.experiment)}</td><td>${esc(run.split)}</td>
    <td>${chip(run.status, run.status === "COMPLETED" ? "pass" : "warning")}</td>
    <td>${run.attempt}</td><td class="mono">${esc((run.output_hash || "").slice(0, 12))}</td></tr>`).join("");
  document.getElementById("ledger-panel").innerHTML = `
    <div class="panel-title"><div><span class="eyebrow">EXPOSURE LEDGER</span><h2>Burned cohorts and run identities</h2></div>
      ${chip(`${ledger.exposures.length} EXPOSURES`, "warning")}</div>
    <div class="metric-grid">${splitCards}</div>
    <p class="caveat">Discovery has no ledger rows because it is already the designated search split. Validation and quarantine are exposed.</p>
    <div class="table-wrap"><table><thead><tr><th>Experiment</th><th>Split</th><th>Status</th><th>Attempt</th><th>Output hash</th></tr></thead>
    <tbody>${runRows}</tbody></table></div>`;
}

const customTemplate = `def fit(rows, config):
    return {"scale": float(config.get("scale", 1.0))}

def transform(rows, frozen_state):
    return [{
        "formation_ts": row["formation_ts"],
        "features": {"custom_feature": float(row.get("mom_5m") or 0.0) * frozen_state["scale"]},
    } for row in rows]`;

function renderSandboxControls(meta) {
  document.getElementById("sandbox-controls").innerHTML = `
    <div class="panel-title"><div><span class="eyebrow">LANE 2</span><h2>Discovery-only sandbox</h2></div>
      ${chip(meta.label, "warning")}</div>
    <div class="notice">The sandbox uses a deterministic 70/30 chronological sub-split inside the already-spent discovery cohort.
      Candidate state and logits fit on the first part; inference scores only the later part.</div>
    <div id="sandbox-search-counts" class="metric-grid compact"></div>
    <form id="sandbox-form">
      <div class="form-grid">
        <label>Candidate<select id="sb-candidate"><option>E001</option><option>E002</option><option>D001</option><option>CUSTOM</option></select></label>
        <label>Treatment feature<input id="sb-treatment" value="flip_count"></label>
        <label>Outcome<select id="sb-outcome"><option>bounded_excursion</option><option>convergence_first_passage</option><option>directional_first_passage</option></select></label>
        <label>Horizon minutes<input id="sb-horizon" type="number" min="5" value="30"></label>
        <label>Warmup minutes<input id="sb-warmup" type="number" min="0" value="60"></label>
        <label class="check-label"><input id="sb-shift" type="checkbox"> Run 999 circular shifts</label>
      </div>
      <div class="two-col">
        <label>Candidate config JSON<textarea id="sb-config" rows="5"></textarea></label>
        <label>Outcome params JSON<textarea id="sb-outcome-params" rows="5">{"threshold_ticks": 4.0}</textarea></label>
      </div>
      <h3>Optional combined cell slice</h3>
      <div class="form-grid">
        <label>Session<select id="sb-session"><option>ALL</option><option>ASIA</option><option>LONDON</option><option>RATES_RTH</option></select></label>
        <label>Event blackout<select id="sb-event"><option value="any">Any</option><option value="exclude">Exclude</option><option value="only">Only</option></select></label>
        <label>Volatility<select id="sb-vol"><option>ALL</option><option>LOW</option><option>MID</option><option>HIGH</option></select></label>
        <label>Roll dates<select id="sb-roll"><option value="any">Any</option><option value="exclude">Exclude</option><option value="only">Only</option></select></label>
        <label>Minimum gap ticks<input id="sb-gap" type="number" min="0" step="0.5" placeholder="none"></label>
      </div>
      <details id="custom-fields"><summary>Custom candidate module</summary>
        <div class="two-col"><label>Allowed inputs (comma-separated)<input id="sb-inputs" value="formation_ts,mom_5m"></label>
        <label>Feature names (comma-separated)<input id="sb-features" value="custom_feature"></label></div>
        <label>Python source<textarea id="sb-source" rows="12">${esc(customTemplate)}</textarea></label>
      </details>
      <button class="primary" type="submit">Run exploratory job</button>
    </form>`;
  bindSandboxForm(meta);
}

async function renderSearchCounts() {
  const journal = await getJson("/api/sandbox/journal");
  const counts = journal.counts;
  document.getElementById("sandbox-search-counts").innerHTML = `
    <div><span>This session</span><strong>${counts.session_searches} attempts</strong></div>
    <div><span>Historical</span><strong>${counts.historical_attempts} attempts</strong></div>
    <div><span>Distinct completed</span><strong>${counts.distinct_completed_searches}</strong></div>
    <div><span>Interpretation</span><strong>Exploratory only</strong></div>`;
}

function bindSandboxForm(meta) {
  const candidate = document.getElementById("sb-candidate");
  const applyDefaults = () => {
    const id = candidate.value;
    if (id === "CUSTOM") {
      document.getElementById("sb-config").value = JSON.stringify({scale: 1.0}, null, 2);
      document.getElementById("sb-treatment").value = "custom_feature";
      document.getElementById("custom-fields").open = true;
      return;
    }
    const defaults = meta.candidates[id];
    document.getElementById("sb-config").value = JSON.stringify(defaults.default_config, null, 2);
    document.getElementById("sb-treatment").value = defaults.treatment_feature;
    document.getElementById("sb-outcome").value = id === "E002" ? "convergence_first_passage" : "bounded_excursion";
    document.getElementById("sb-outcome-params").value = id === "E002"
      ? JSON.stringify({convergence_fraction: 0.5, adverse_fraction: 1.0}, null, 2)
      : JSON.stringify({threshold_ticks: 4.0}, null, 2);
  };
  candidate.addEventListener("change", applyDefaults);
  applyDefaults();
  document.getElementById("sandbox-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const payload = sandboxPayload();
      const response = await getJson("/api/sandbox/jobs", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      renderSandboxJob(response.job);
      pollSandboxJob(response.job.job_id);
    } catch (error) {
      document.getElementById("sandbox-results").innerHTML = `<div class="notice warning">${esc(error.message)}</div>`;
    }
  });
}

function csvValues(id) {
  return document.getElementById(id).value.split(",").map((value) => value.trim()).filter(Boolean);
}

function sandboxPayload() {
  const gap = document.getElementById("sb-gap").value;
  const slice = {
    session: document.getElementById("sb-session").value,
    event_blackout: document.getElementById("sb-event").value,
    volatility_bucket: document.getElementById("sb-vol").value,
    roll: document.getElementById("sb-roll").value,
  };
  if (gap !== "") slice.minimum_gap_ticks = Number(gap);
  return {
    split: "discovery",
    candidate: document.getElementById("sb-candidate").value,
    treatment_feature: document.getElementById("sb-treatment").value.trim(),
    config: JSON.parse(document.getElementById("sb-config").value),
    outcome: {
      name: document.getElementById("sb-outcome").value,
      horizon_minutes: Number(document.getElementById("sb-horizon").value),
      params: JSON.parse(document.getElementById("sb-outcome-params").value),
    },
    warmup_minutes: Number(document.getElementById("sb-warmup").value),
    circular_shift: document.getElementById("sb-shift").checked,
    slice,
    allowed_inputs: csvValues("sb-inputs"),
    feature_names: csvValues("sb-features"),
    source: document.getElementById("sb-source").value,
  };
}

function renderSandboxJob(job) {
  document.getElementById("sandbox-jobs").innerHTML = `
    <div class="panel-title"><h2>Current job</h2>${chip(job.status.toUpperCase(), job.status === "failed" ? "fail" : "warning")}</div>
    <div class="progress"><span style="width:${job.percent}%"></span></div>
    <p>${esc(job.stage)} · ${job.percent}%</p>
    <small class="mono">${esc(job.job_id)}</small>`;
  if (job.status === "failed") {
    document.getElementById("sandbox-results").innerHTML = `<div class="notice warning"><strong>Job failed:</strong> ${esc(job.error)}</div>`;
  } else if (job.status === "completed") {
    renderSandboxResult(job.result);
  }
}

async function pollSandboxJob(jobId) {
  clearInterval(state.jobPoll);
  const check = async () => {
    const response = await getJson(`/api/sandbox/jobs/${jobId}`);
    renderSandboxJob(response.job);
    const terminal = ["completed", "failed"].includes(response.job.status);
    if (terminal) {
      clearInterval(state.jobPoll);
      state.jobPoll = null;
      renderSearchCounts().catch(console.error);
    }
    return terminal;
  };
  if (await check()) return;
  state.jobPoll = setInterval(() => check().catch(console.error), 2000);
}

function exploratoryGates(cell) {
  if (cell.error) return [];
  const utility = cell.utility || {};
  return [
    {name: "Magnitude", passes: cell.effect >= .005 && cell.relative_brier_skill >= .02, detail: `Δ=${num(cell.effect, 6)}; skill=${num(cell.relative_brier_skill, 4)}`},
    {name: "Holm p", passes: cell.p_adjusted <= .05, detail: `p=${num(cell.p_adjusted, 4)}`},
    {name: "CI lower", passes: cell.ci_low > 0, detail: `lower=${num(cell.ci_low, 6)}`},
    {name: "Shift null", passes: cell.shift_p <= .05, detail: `p=${num(cell.shift_p, 4)}`},
    {name: "Months", passes: cell.month_count >= 4 && cell.positive_month_fraction >= .6, detail: `${pct(cell.positive_month_fraction)} / ${cell.month_count}`},
    {name: "Concentration", passes: cell.positive_effect_concentration <= .25, detail: pct(cell.positive_effect_concentration)},
    {name: "Utility", passes: utility.passes, detail: `coverage=${pct(utility.coverage)}; n=${utility.selected_episodes}`},
    {name: "Slices", passes: cell.stability_passes, detail: "required stability slices"},
  ];
}

function renderSandboxResult(result) {
  const rows = result.evaluation.cells.map((cell) => {
    if (cell.error) {
      return `<tr><th>${esc(cell.cell_id)}</th><td colspan="8">${chip(cell.decision, "fail")} ${esc(cell.error)}</td></tr>`;
    }
    return `<tr><th>${esc(cell.cell_id)}<small>${esc(cell.decision)}</small></th>${gateCells(exploratoryGates(cell))}</tr>`;
  }).join("");
  document.getElementById("sandbox-results").innerHTML = `
    <div class="panel-title"><div><span class="eyebrow">SEARCH RESULT</span><h2>${esc(result.candidate)} · ${esc(result.treatment_feature)}</h2></div>
      ${chip(result.label, "warning")}</div>
    <div class="notice warning">Repeated discovery search invalidates confirmatory interpretation. This result is a lead for preregistration, not evidence.</div>
    <div class="metric-grid">
      <div><span>Train</span><strong>${result.train.anchors} anchors</strong><small>${result.train.dates} dates</small></div>
      <div><span>Score</span><strong>${result.score.anchors} anchors</strong><small>${result.score.dates} dates</small></div>
      <div><span>Inference</span><strong>${result.inference_repetitions} resamples</strong><small>${result.circular_shift_enabled ? "shift enabled" : "shift skipped"}</small></div>
      <div><span>Search digest</span><strong class="mono">${esc(result.search_digest.slice(0, 12))}</strong></div>
    </div>
    <div class="table-wrap gate-table"><table><thead><tr><th>Cell</th><th>Magnitude</th><th>Holm p</th>
      <th>CI lower</th><th>Shift null</th><th>Months</th><th>Concentration</th><th>Utility</th><th>Slices</th>
      </tr></thead><tbody>${rows}</tbody></table></div>
    <div class="actions"><button id="freeze-prereg" class="primary">Use as preregistration draft</button></div>
    <details><summary>Full exploratory result</summary><pre>${esc(JSON.stringify(result, null, 2))}</pre></details>`;
  document.getElementById("freeze-prereg").addEventListener("click", () => {
    document.querySelector('[data-tab="preregister"]').click();
    prefillPrereg(result);
  });
}

function renderPreregForm() {
  const hypotheses = state.evidence.registry.hypotheses.hypotheses;
  const options = hypotheses.map((row) => `
    <option value="${esc(row.hypothesis_id)}" ${row.status === "blocked" ? "disabled" : ""}>
      ${esc(row.hypothesis_id)} · ${esc(row.title)}${row.status === "blocked" ? " (blocked)" : ""}
    </option>`).join("");
  const selected = state.preregResult;
  document.getElementById("prereg-form").innerHTML = `
    <div class="panel-title"><div><span class="eyebrow">LANE 3</span><h2>Freeze a preregistration</h2></div>
      ${chip("AUTHORING ONLY / NO HOLDOUT EXECUTION", "warning")}</div>
    <form id="preregistration-form">
      <div class="form-grid">
        <label>New experiment ID<input id="pr-id" value="${selected ? `E${selected.search_digest.slice(0, 6).toUpperCase()}` : ""}" placeholder="E003"></label>
        <label>Hypothesis<select id="pr-hypothesis">${options}</select></label>
        <label>Intended cohort<select id="pr-holdout"><option>prospective</option><option>validation</option><option>quarantine</option></select></label>
      </div>
      <label>Exact predictive claim<textarea id="pr-claim" rows="4">${selected ? esc(`The frozen ${selected.treatment_feature} feature incrementally predicts ${selected.outcome.name} at ${selected.outcome.horizon_minutes} minutes.`) : ""}</textarea></label>
      <div class="notice">${selected
        ? `Linked search: <span class="mono">${esc(selected.search_digest)}</span>`
        : "Run or select an exploratory sandbox result before freezing."}</div>
      <button class="primary" type="submit" ${selected ? "" : "disabled"}>Validate hashes and freeze files</button>
    </form>`;
  if (selected) {
    const mapping = {E001: "H002", E002: "H003", D001: "H001"};
    document.getElementById("pr-hypothesis").value = mapping[selected.candidate] || "H002";
  }
  document.getElementById("preregistration-form").addEventListener("submit", submitPreregistration);
}

function prefillPrereg(result) {
  state.preregResult = result;
  renderPreregForm();
}

async function submitPreregistration(event) {
  event.preventDefault();
  try {
    const response = await getJson("/api/preregistrations", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        experiment_id: document.getElementById("pr-id").value.trim().toUpperCase(),
        hypothesis_id: document.getElementById("pr-hypothesis").value,
        holdout_split: document.getElementById("pr-holdout").value,
        claim: document.getElementById("pr-claim").value.trim(),
        search_result: state.preregResult,
      }),
    });
    const value = response.preregistration;
    document.getElementById("prereg-result").innerHTML = `
      <div class="panel-title"><h2>Frozen preregistration</h2>${chip(value.status, "warning")}</div>
      <div class="metric-grid"><div><span>Base spec hash</span><strong class="mono">${esc(value.base_spec_hash.slice(0, 16))}</strong></div>
      <div><span>Effective hash</span><strong class="mono">${esc(value.effective_spec_hash.slice(0, 16))}</strong></div></div>
      <p class="mono">${esc(value.directory)}</p>
      <div class="notice warning"><strong>Execution unavailable:</strong> ${esc(value.execution_blocker)}</div>
      <pre>${esc(value.future_cli_template)}</pre>`;
    renderHoldoutPanel(await getJson("/api/holdout-availability"));
  } catch (error) {
    document.getElementById("prereg-result").innerHTML = `<div class="notice warning">${esc(error.message)}</div>`;
  }
}

function renderHoldoutPanel(data) {
  const registered = data.registered.map((item) => `
    <article class="holdout-item"><div class="panel-title"><h3>${esc(item.experiment_id)}</h3>${chip("UNAVAILABLE", "fail")}</div>
      <ul>${item.reasons.map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul></article>`).join("");
  const blocked = data.blocked_hypotheses.map((item) => `
    <div class="notice warning"><strong>${esc(item.hypothesis_id)} / ${esc(item.family)} blocked:</strong> ${esc(item.reason)}</div>`).join("");
  document.getElementById("holdout-panel").innerHTML = `
    <div class="panel-title"><div><span class="eyebrow">HOLDOUT FIREWALL</span><h2>Execution availability</h2></div>
      ${chip("NO EXECUTION ENDPOINT", "pass")}</div>
    <p>${esc(data.global_reasons.join(" "))}</p>
    <div class="three-col">${registered}</div>
    ${blocked}
    ${data.drafts.length ? `<h3>Frozen drafts</h3>${data.drafts.map((item) => `<div class="notice"><strong>${esc(item.experiment_id)}</strong>: ${esc(item.reasons.join(" "))}</div>`).join("")}` : ""}`;
}

async function boot() {
  state.config = await getJson("/api/config");
  state.evidence = await getJson("/api/evidence");
  renderSummary(state.evidence);
  renderIntegrity(state.evidence);
  renderExperiment(state.evidence);
  renderD001(state.evidence);
  renderLedger(state.evidence);
  state.sandboxMeta = await getJson("/api/sandbox/meta");
  renderSandboxControls(state.sandboxMeta);
  await renderSearchCounts();
  renderPreregForm();
  renderHoldoutPanel(await getJson("/api/holdout-availability"));
}

boot().catch((error) => {
  document.getElementById("campaign-summary").textContent = error.message;
});
