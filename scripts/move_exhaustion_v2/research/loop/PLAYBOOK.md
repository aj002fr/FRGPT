# Move Exhaustion V2 — Advisor Playbook (living process knowledge)

Advisor-editable, append-preferred. This is the loop's process memory: lessons
about writing briefs, reviewing results, and common failure modes. It is NOT
scientific content — hypotheses and findings live in the backlog, cycles, and
scoreboard. Every material edit gets a `process_change` journal entry.
Retrospectives (`retros/R###.md`) are the main source of new entries.

## Brief-writing heuristics (seed set — refine from experience)

- One question per brief. If the brief contains "and also", split it.
- State the *kill criterion* as precisely as the success criterion — workers
  over-report weak positives when only success is defined.
- Pin exact data windows and the episode/label config revision; "recent data"
  is not a window.
- Tell the worker what was already tried (cite prior cycles) or it will
  rediscover them.
- Require effect sizes with uncertainty, never bare p-values or "looks
  predictive".

## Result-review heuristics (seed set)

- First question: what is the boring explanation? (mechanical correlation with
  the label, leakage, date concentration, one regime driving everything).
- Check the "things tried that failed" section is non-empty and plausible; an
  empty one usually means unreported multiplicity.
- Cross-check any headline number against the X1 baseline tables before
  getting excited — most "signals" are the price path restated.
- Prefer replication (fresh cycle, ideally different model per AGENT_ROSTER)
  before promotion.

## Known worker failure modes (append as observed)

- Codex CLI sandbox cannot access the Y: share (`CreateProcessWithLogonW` 267). Use in-IDE composer-2.5 workers.
- Half-split terciles of β (ratio or difference) produce placebo lifts as large as the real lift (C002, C003).
- Late-window flow sign-flip is extremely rare in N=100 lookbacks on this prefix (C005 n=3).
- Advisor markdown (STATE / BACKLOG / SCOREBOARD / PLAYBOOK) can be reverted by concurrent sessions; restore from cycle `meta.json` + journal, never from a stale STATE.md.

## Process changes adopted (append-only, dated)

- **2026-08-13 (R001):** Briefs must declare a minimum n per headline cell; underpowered cells do not headline a pp lift.
- **2026-08-13 (R001):** At most one iterate per thread on the current 75-date prefix; then PARK.
- **2026-08-13 (R001):** Skip BL-3 until a frozen expected-impact spec exists.
- **2026-08-13 (BL-3 spec):** Spec now exists at `research/loop/specs/BL3_impact_shortfall_spec.md`. Do not open C014 until the human approves that document. Do not treat the spec as a frozen X4 contract.
- **2026-08-13 (C014):** AF-only OLS expected-impact with ~0 OOS R² makes IS ≈ −Δm. E07 corr flag is the kill, not a richer f(·) hunt on this prefix.
- **2026-08-13 (R003):** Session before flow. Future flow briefs must control RTH vs non-RTH (~10 pp gap on this prefix). Descriptive ADVANCE parks; it does not spawn AF×TOD.
- **2026-08-13 (prefix complete):** On this 75-date window, leftover X1 conditioners are done (TOD, event-day, direction, 30m range). Do not reopen AF threads or BL-7. Expanding discovery or MBP-10 needs a new human authorization.
- **2026-08-13 (resume):** After parking PQ1–PQ3 nested-B−A, next cycle is labels-only BL-15 (oscillatory composition), not another AF transform.
- **2026-08-13 (R002):** After 10 closed cycles, stop for §6.3 unless the human approves a specific thread.
- **2026-08-13 (R002):** Leftover diagnostics do not spawn iterates on the same prefix.
- **2026-08-13 (human):** Do not expand the discovery window. Stay on the X1 75-date prefix until the human says otherwise.
