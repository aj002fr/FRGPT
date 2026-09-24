# Move Exhaustion V2 — Agent Roster & Model Assignment Policy

**Established:** 2026-08-11. **Revised 2026-08-11 (user-directed):** DeepSeek
workers via Codex CLI; dual-advisor panel (Fable 5 + GPT Sol) with mandatory
consolidation. Process document (advisor-maintained; changes journaled as
`process_change`). This file defines the *policy*; `meta.json` records the
*fact* per cycle (`worker_model`, `advisor_models`).

## Assignment (user-directed, binding)

| Role | Model | How it runs |
|------|-------|-------------|
| Advisor A (chair) | **Fable 5** (`claude-fable-5-thinking-high`) or **GPT Sol** (`gpt-5.6-sol-max`) — whichever the session is running on | The parent/orchestrator agent in the active session |
| Advisor B (second opinion) | The **other** of Fable 5 / GPT Sol | Launched as a subagent for every cycle review and every promotion/retro decision |
| Worker | **DeepSeek `deepseek-v4-flash`** | Via Codex CLI (`codex exec`), configured in `~/.codex/config.toml` (provider `deepseek`, catalog `models.json`); upgrade to `deepseek-v4-pro` when available, journaled as `process_change` |
| Human | Program owner | LOOP_PROTOCOL §6 checkpoints |

## Dual-advisor consolidation protocol (binding)

1. After a worker RESULT lands, **both** advisors review it **independently**:
   the chair writes its opinion first but does not show it to Advisor B;
   Advisor B receives only the BRIEF, RESULT, artifacts, and frozen configs.
2. Each opinion covers: real/artifact/uninteresting assessment, methodology
   critique (leakage, confounds, date concentration), and a recommended
   decision (ITERATE / PARKED / PROMOTE_REQUEST / ESCALATED).
3. The chair writes the **consolidated** FEEDBACK.md (template in
   LOOP_PROTOCOL §5) recording both opinions verbatim-in-summary, points of
   agreement/disagreement, and the final decision.
4. **Disagreement rules:**
   - Decision disagreement → take the more conservative option
     (ESCALATED > PARKED > ITERATE).
   - **PROMOTE_REQUEST requires both advisors to agree.** One advisor alone
     can never move a finding toward the confirmatory tier; split opinion on
     promotion → ESCALATED to the human with both arguments.
   - Methodology-flaw claims by either advisor must be resolved (re-check or
     new cycle) before the finding can advance.
5. Both advisor models are recorded in `meta.json.advisor_models`. A review
   with fewer than two advisors is allowed only if one is unavailable, and
   must record `solo_review_reason` (mechanically enforced) — solo reviews
   can PARK or ITERATE, never PROMOTE_REQUEST.

## Worker launch pattern (DeepSeek via Codex CLI)

DeepSeek is not available as an in-IDE subagent model, so workers run through
the Codex CLI in non-interactive mode. Canonical launch by the chair:

On this machine `codex` is not on PATH; invoke via `npx --yes @openai/codex`
(verified 2026-08-11, codex-cli 0.147.0, model `deepseek-v4-flash` from
`~/.codex/config.toml`):

```powershell
npx --yes @openai/codex exec --cd y:\Aastha\market_data_puller `
  "You are a worker in the Move Exhaustion V2 autoresearch loop. Read and obey
   scripts/move_exhaustion_v2/research/loop/cycles/C###_<slug>/BRIEF.md and
   scripts/move_exhaustion_v2/research/loop/LOOP_PROTOCOL.md (worker duties).
   Write RESULT.md and meta.json fields into the cycle folder; artifacts only
   under workspace/analysis/move_exhaustion_v2/loop/C###_<slug>/."
```

- The brief must restate the hard boundaries (discovery dates only, no frozen-
  surface edits, approved write paths) because a CLI worker does not inherit
  the IDE harness context — the tests are the backstop, not the only fence.
- After the worker exits, the chair runs the test suite
  (`py -3 -m pytest scripts/move_exhaustion_v2/tests`) before reviewing; a
  red suite means the result is quarantined (journal `correction`).
- Provenance: `worker_model: "deepseek-v4-flash (codex exec)"` plus
  `worker_agent_id` if the CLI reports a session id, else null.
- **Fallback:** if Codex CLI/DeepSeek is unavailable or fails twice on one
  brief, the chair may run the worker as an in-IDE subagent (e.g.
  `composer-2.5` or inherit), records the substitution in `worker_model` and
  the journal, and notes it in the next retro.

## Replication rule (unchanged)

Any result intended for PROMOTE_REQUEST must first be replicated in a fresh
cycle by a fresh worker with no shared context — with DeepSeek workers, this
means a new `codex exec` session at minimum; a different worker model is
preferred for the replication cycle.

## Failure handling

- Worker crashes / returns unusable output → journal a `decision` entry
  (reason: worker failure); close the cycle `PARKED (worker_failure)` or
  re-run as a NEW cycle with the same brief; never silently re-run inside the
  same cycle id.
- Two consecutive worker failures on one brief → simplify or split the brief;
  log the lesson in `PLAYBOOK.md`; fallback model per above.
