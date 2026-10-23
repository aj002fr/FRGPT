# Move Exhaustion V2 — Autoresearch Loop Protocol

**Established:** 2026-08-11 (user-directed restructure; PROGRAM.md §10 addendum).
**Purpose:** replace the fixed X2–X10 experiment ladder with an advisor/worker
loop that can freely generate and test ideas — without weakening the frozen
scientific invariants (holdout lock, causality, splits, outcome vocabulary) or
the evidence bar (gates apply at the confirmatory tier).

---

## 1. Roles

### Advisor panel (orchestrator + second opinion)

Two advisors (per `AGENT_ROSTER.md`: Fable 5 + GPT Sol — the session's parent
agent chairs, the other reviews as a subagent). The panel owns the research
direction and never runs experiments directly. Every cycle review and every
promotion decision gets **independent opinions from both advisors, then a
consolidated decision by the chair** (disagreement → conservative option;
promotion requires unanimity; split promotion opinion → ESCALATED). Duties:

1. Maintain `BACKLOG.md` (prioritized idea queue; seeded from the retired
   X2–X10 science and the original research spec).
2. Open cycles: write a `BRIEF.md` per cycle — problem statement, motivation,
   data window (discovery only for exploratory), success criteria, what NOT to do.
3. Review every `RESULT.md`: interrogate methodology (leakage, confounds,
   date concentration, effect size vs noise), then decide:
   **ITERATE** (refined brief, new cycle) / **PARK** (to backlog or parking lot) /
   **PROMOTE_REQUEST** (draft confirmatory contract, escalate to human) /
   **ESCALATE** (human input needed).
4. Feed workers more data/context when useful (pointers to store partitions,
   prior cycle artifacts, feature caches) — never validation/holdout dates for
   exploratory work.
5. Log every action in the journal (§4) and keep `STATE.md` current.
6. Assign worker models per `AGENT_ROSTER.md` and record `worker_model` in
   every cycle's `meta.json` (mechanically enforced).
7. Keep `SCOREBOARD.md` current (candidate-findings ledger after every review;
   KPIs at every retrospective) and maintain `PLAYBOOK.md` as process memory.
8. Run retrospectives on the required cadence (§9) — the loop must improve its
   own process, not just its hypotheses.

### Worker (experiment agent)

Executes exactly one brief per cycle. Duties:

1. Read the brief, the frozen configs (`config/frozen/`), and this protocol.
2. Run the experiment **within the brief's data window** (mechanically checked:
   exploratory cycles that reference validation/holdout dates fail the test suite).
3. Write `RESULT.md` (template §5): what was done, exact parameters, findings
   with uncertainty, honest negative results, surprises, limitations,
   suggested next steps. Emit artifacts + `meta.json` under the cycle folder's
   artifact path.
4. Never: change frozen configs/contracts, touch validation/holdout, resolve
   OPEN_QUESTIONS, claim evidentiary status, start another cycle.

### Human (program owner)

Consulted at defined checkpoints (§6). Sets direction, approves promotions,
answers open questions, can halt or redirect the loop at any time.

---

## 2. Evidence tiers

| | EXPLORATORY | CONFIRMATORY |
|---|---|---|
| Who initiates | Advisor (freely) | Human approval required |
| Data | **Discovery dates only** (per `config/frozen/splits.yaml`) | Discovery-frozen models scored one-shot on validation |
| Preregistration | Brief (lightweight, logged) | Frozen contract under `experiments/` + manifest re-hash |
| Statistical gate | None — no verdicts issued | Full `config/frozen/gates.yaml` (bootstrap, BH-FDR, SESOI, stability) |
| Label | `EXPLORATORY / NOT EVIDENCE` on every artifact and result | Gate verdict vocabulary (PASS/PROVISIONAL/FAIL/…) |
| May inform | Backlog, briefs, promotion proposals | Program conclusions, final report |
| May never | Touch validation/holdout; be cited as evidence | Touch holdout (separate lock; user approval only) |

Multiplicity honesty: exploratory results carry an unknown/large search
multiplicity by construction. That is WHY they are not evidence. The advisor
must track (in the journal) roughly how many things were tried on the way to
any finding it proposes for promotion, and the confirmatory contract's FDR
family must reflect the promoted claims, not just the survivor.

**Unchanged invariants for BOTH tiers:** causality (features use only event
time ≤ decision time; no centered windows, no future data, no random splits),
episode/label outcome vocabulary from `config/frozen/` for any claim intended
for promotion, writes only to approved paths, `holdout_opened: false`
everywhere, live-surface isolation.

---

## 3. Cycle lifecycle

```
BACKLOG idea
   → BRIEFED      (advisor writes cycles/C###_<slug>/BRIEF.md, journal entry)
   → RUNNING      (worker launched; journal entry)
   → RESULTED     (worker writes RESULT.md + artifacts + meta.json; journal entry)
   → REVIEWED     (both advisors review independently; chair writes
                   consolidated FEEDBACK.md; journal entry)
   → one of:
       ITERATE          → new cycle C###+1 with refined brief (link both ways)
       PARKED           → back to BACKLOG.md or HYPOTHESIS_PARKING_LOT.md with reason
       PROMOTE_REQUEST  → advisor drafts contract, escalates to human (§6)
       ESCALATED        → human question logged; cycle blocked until answered
```

- Cycle IDs are sequential: `C001`, `C002`, … Never reuse or renumber.
- One brief = one worker = one cycle. Follow-ups are new cycles.
- Cycle folders: `research/loop/cycles/C###_<slug>/` containing `BRIEF.md`,
  `RESULT.md`, `FEEDBACK.md`, `meta.json`. Heavy artifacts go under
  `workspace/analysis/move_exhaustion_v2/loop/C###_<slug>/` (small) or the D:
  store (large), referenced from `meta.json`.

### meta.json (required per cycle; mechanically validated)

```json
{
  "cycle": "C001",
  "slug": "example",
  "tier": "EXPLORATORY",
  "status": "REVIEWED",
  "opened_utc": "...",
  "closed_utc": "...",
  "backlog_ref": "BL-###",
  "parent_cycle": null,
  "data_window_used": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "dates_used_source": "path to eligible-dates subset or store partitions",
  "holdout_opened": false,
  "artifact_paths": ["..."],
  "advisor_decision": "ITERATE | PARKED | PROMOTE_REQUEST | ESCALATED",
  "worker_model": "model slug (required once RESULTED; see AGENT_ROSTER.md — e.g. 'deepseek-v4-flash (codex exec)')",
  "worker_agent_id": "subagent/CLI session id or null",
  "advisor_models": ["chair model slug", "second advisor model slug (required once REVIEWED; <2 entries requires solo_review_reason)"],
  "headline_metrics": {"<metric_name>": "value with CI/units (required once RESULTED; {} only for pure-infrastructure cycles with reason)"},
  "wall_clock_minutes": 0
}
```

---

## 4. Logging (append-only; the loop's audit trail)

`research/loop/journal/loop_journal.jsonl` — one JSON object per line, one line
per event. Use `tools/loop_journal.py append` (validates and stamps); never
edit or delete existing lines. Corrections are new entries with
`event: "correction"` referencing the corrected `seq`.

Entry schema (validated by tests):

```json
{
  "seq": 1,
  "ts_utc": "2026-08-11T20:00:00Z",
  "actor": "advisor | worker | human",
  "event": "governance | backlog | brief | launch | result | feedback | decision | escalation | human_input | correction | retro | process_change",
  "cycle": "C001 | null",
  "summary": "one to three sentences, plain language",
  "refs": ["relative paths touched or produced"]
}
```

Everything is journaled: backlog changes, every brief/result/feedback, every
decision with its reason, every human question and answer. If it is not in the
journal, it did not happen.

---

## 5. Templates

### BRIEF.md (advisor → worker)

```
# C### — <title>                                    tier: EXPLORATORY
## Problem statement        (one paragraph; the question, not the method)
## Motivation & provenance  (backlog ref, prior cycles, spec sections; what was already tried)
## Data window              (discovery dates only; specific subset if applicable)
## Suggested approach       (advisory, not binding — workers may deviate with justification in RESULT)
## Success criteria         (what result would advance/kill the idea; effect sizes that would matter)
## Constraints              (frozen configs binding; causality; do-not-do list)
## Deliverables             (RESULT.md + artifacts + meta.json)
```

### RESULT.md (worker → advisor)

```
# C### — Result                                     label: EXPLORATORY / NOT EVIDENCE
## What I did               (exact method + parameters; deviations from brief and why)
## Data actually used       (window, n episodes, n dates)
## Findings                 (numbers with uncertainty; plots referenced; negative results stated plainly)
## Things tried that failed (mandatory — the multiplicity record)
## Leakage / confound check (what was verified; what remains unchecked)
## Limitations & surprises
## Suggested next steps     (for the advisor to consider)
```

### FEEDBACK.md (advisor panel — chair consolidates)

```
# C### — Advisor panel review
## Advisor A opinion (chair, written first, independent)
##   (real/artifact/uninteresting; methodology critique; recommended decision)
## Advisor B opinion (second advisor, independent — saw BRIEF/RESULT only)
##   (same structure)
## Agreements / disagreements
##   (where the two opinions align and diverge; how disagreement was resolved
##    per AGENT_ROSTER consolidation rules)
## Consolidated decision     (ITERATE / PARKED / PROMOTE_REQUEST / ESCALATED + reason;
##                            PROMOTE_REQUEST only if both advisors agree)
## Refinements               (if ITERATE: what changes in the next brief and why)
## Multiplicity note         (running count of attempts on this thread)
```

---

## 6. Human checkpoints (advisor MUST stop and ask)

1. **Start of the loop** — X0 (data build) still requires explicit approval; the
   loop cannot begin before the store exists and X1's baseline is on record.
2. **Any promotion to CONFIRMATORY** — human approves the drafted contract
   before it is frozen and before any validation date is touched.
3. **Periodic review** — after every **10 cycles** (or ~weekly during active
   running, whichever first): status digest of cycles, decisions, backlog, and
   anything trending toward promotion.
4. **Stall** — 5 consecutive cycles on one thread without the advisor being
   able to articulate progress → ask whether to continue, repark, or redirect.
5. **Resources / scope** — any new data pull, any write outside approved paths,
   any frozen-surface amendment, anything touching OPEN_QUESTIONS.
6. **Anomalies** — suspected leakage in past cycles, integrity-test failures,
   contradictions with frozen invariants.

Between checkpoints, the loop runs autonomously: brief → run → review → iterate.

---

## 7. Promotion protocol (exploratory → confirmatory)

1. Advisor drafts `experiments/XC##_<slug>/CONTRACT.md` (same format as X0/X1:
   frozen preregistration section, FDR family enumerating ALL promoted claims,
   dependencies, holdout rule) and a promotion memo in the cycle's FEEDBACK.md
   citing the exploratory provenance and attempt counts.
2. Human approves (or rejects → PARKED).
3. Contract frozen: dated PROGRAM.md §10 addendum + `freeze_manifest.py --write`.
4. One-shot validation run under `config/frozen/gates.yaml`; verdict recorded in
   the contract, the journal, STATE.md, and the docs run log
   (`docs/move_exhaustion_v2_experiments.md` §7).
5. Validation dates consumed by the claim family are recorded; repeated
   promotions cannot silently reuse validation as a second discovery set — the
   advisor tracks validation exposure per hypothesis family in the journal.

## 8. Retired ladder

X2–X10 contracts are `RETIRED_TO_BACKLOG` (2026-08-11): preserved verbatim and
hash-pinned as audit + seed ideas (`BACKLOG.md` BL-1…BL-9). No run may claim
evidentiary status under a retired contract. X0/X1 remain FROZEN prerequisites.

---

## 9. Retrospectives & process self-improvement (mandatory)

The loop must improve its own process, not just its hypotheses. Object-level
learning happens in FEEDBACK.md; process-level learning happens here.

**Cadence (mechanically enforced):** one retrospective per **5 closed cycles**
(closed = advisor decision recorded), written to
`research/loop/retros/R###.md` and journaled with `event: retro`.

**Required content per retro:**

1. **KPIs** — update the SCOREBOARD.md loop-health table: cycles closed,
   decision mix (ITERATE/PARKED/PROMOTE_REQ/ESCALATED), worker failures,
   median cycle wall-clock, threads active.
2. **What worked / what wasted compute** — concrete examples from the last 5
   cycles (briefs that were too vague, workers that chased noise, reviews that
   missed a confound found later).
3. **Process changes** — specific, small changes to how briefs are written,
   which models get assigned (AGENT_ROSTER), or how reviews are done. Each
   adopted change is appended to `PLAYBOOK.md` (dated) and journaled as
   `event: process_change`.
4. **Direction check** — is the backlog ordering still right given what was
   learned? Are any threads converging toward a promotion candidate, and what
   would the confirmatory contract look like?

**Boundaries:** retros may change PROCESS (briefs, review practice, model
assignment, backlog priority). They may NOT change frozen scientific surface
(configs, contracts, gates, splits) — that path remains human approval +
PROGRAM.md §10 addendum + manifest re-hash. A retro that concludes a frozen
definition is wrong records it as an escalation, not an edit.

Retros feed the every-10-cycles human checkpoint (§6.3): the two most recent
retros plus the scoreboard ARE the status digest presented to the human.
