# Move Exhaustion V2 — Open Questions Register

Contradictions, ambiguities, and missing information found while initializing the
research harness (2026-08-10 inspection of `docs/move_exhaustion_v2_experiments.md`
and the repository). **None of these are resolved here.** Each requires an owner
(user) decision, recorded as a dated addendum in `research/PROGRAM.md` and — where
it touches frozen config — a registered amendment to `config/frozen/` with a
manifest re-hash.

Status values: `OPEN`, `ANSWERED (date, where recorded)`.

| ID | Status | Blocks |
|----|--------|--------|
| OQ-1 | ANSWERED (2026-08-11, PROGRAM §10) | — |
| OQ-2 | ANSWERED (2026-08-11, PROGRAM §10) | — |
| OQ-3 | DEFERRED (2026-08-11, inactive for X1) | — |
| OQ-4 | DEFERRED (2026-08-11, inactive for X1) | — |
| OQ-5 | ANSWERED (2026-08-11, PROGRAM §10) | — |
| OQ-6 | ANSWERED (2026-08-11, PROGRAM §10) | — |
| OQ-7 | OPEN | X2–X8 (gate strictness) |
| OQ-8 | OPEN | X4 |
| OQ-9 | OPEN | Phase 2 feature scope |
| OQ-10 | ANSWERED (2026-08-11, PROGRAM §10) | — |
| OQ-11 | OPEN | X7 gate evaluation |
| OQ-12 | OPEN | Program scope statement |
| OQ-13 | OPEN | X2 headline definition |
| OQ-14 | OPEN | Verdict computation consistency |
| OQ-15 | ANSWERED (2026-08-11, PROGRAM §10) | — |

---

## OQ-1 — When are the primary episode parameters frozen?

PROGRAM.md §4.1 states primary `B = 8 ticks`, `x = 2 ticks` as if frozen now. The
program plan text says primary parameters are "chosen on discovery data only and
frozen" (i.e., selected during X1). These conflict: either B/x are frozen today by
fiat, or they are provisional pending an X1 discovery-data selection rule.
`config/frozen/episodes.yaml` currently records them with
`primary_status: stated_in_design__freeze_timing_unresolved`.
**Decision needed:** frozen now, or selected in X1 by a preregistered rule (and if
so, what rule)?
**2026-08-11 decision:** frozen now at `B=8`, `x=2` ticks.

## OQ-2 — Tick-denominated vs bps-denominated episode grids

The original research spec (user report, §2) defines the pullback grid in bps:
`x ∈ {1, 2, 3, 5} bps`. PROGRAM.md §4.1 froze tick-denominated grids
(`x ∈ {1, 2, 3, 4}` ticks, `B ∈ {4, 6, 8, 12}` ticks) with bps documented as
per-era equivalents. ZN price ranged ~105–135 over 2010–2026, so the two
denominations diverge across eras.
**Decision needed:** confirm tick denomination as primary (bps as reporting), or
revert to the spec's bps grids.
**2026-08-11 decision:** tick denomination is primary; bps is reporting only.

## OQ-3 — Vol-normalized pullback variant is under-specified

§4.1 lists a variant `x = 0.5·σ_5m`. Unspecified: units of σ (ticks, bps, price
points), the estimator (realized vol of what return series), and the estimation
window/clock. Cannot be implemented as written.
**2026-08-11 decision:** deferred and inactive for X1.

## OQ-4 — "Price-change-time horizon variants" have no preregistered values

§4.2 includes "price-change-time horizon variants" in the H grid but no values
(e.g., H ∈ {N price changes}) are stated. If these count as headline cells they
affect FDR denominators; if descriptive-only that should be stated.
**2026-08-11 decision:** deferred and inactive for X1.

## OQ-5 — Split windows are labeled "proposed" but the freeze mechanism is unstated

§3 says splits are "frozen at end of Phase 0, before any episode is inspected,"
and the table is titled "(proposed)": Discovery 2010-06→2024-12, Validation
2025-01→2025-12, Holdout 2026-01→2026-04. It is not stated whether X0 freezes
exactly these windows or derives them from a rule (e.g., relative to actual
eligible-date coverage). `config/frozen/splits.yaml` is marked
`status: proposed_pending_X0_freeze`.
**Decision needed:** confirm the windows (or the derivation rule) before X0
completes.
**2026-08-11 decision:** Discovery 2022–2024, Validation 2025, and locked
Holdout through the last Q3-passing session no later than 2026-04-01.

## OQ-6 — X0 pass criterion covers only 2024–2026

X0's pass criterion is "Q1–Q8 pass on ≥ 95% of days in **2024–2026**." No QC
pass-rate criterion is stated for 2010–2023, which forms most of discovery. What
governs research eligibility of earlier eras — same 95% bar per era, per year, or
no bar (day-level QC only)?
**2026-08-11 decision:** for 2022–2023, require ≥95% expected-session coverage
and ≥95% Q1–Q6 passes separately per year; Q7/Q8 are overlap-level gates.

## OQ-7 — Stability-gate unit for non-model experiments

§3.1: "week-level AUC/effect spread ≤ 0.15." For model experiments this is AUC
units. For conditional-probability experiments (X2–X6) the "effect" is a lift in
percentage points — is the cap 0.15 (i.e., 15pp), or something else? 15pp spread
vs 0.15 AUC spread are very different strictness levels.

## OQ-8 — X4 checkpoint-set cells overlap

X4 cells are "{t*}, {75%, 100% of B}" — but 100% of B **is** t*. Is cell 2 meant
to be {75% only}, {75% pooled with t*}, or something else? Affects the FDR
denominator (2 cells preregistered).
**2026-08-13 exploratory note (not a confirmatory resolution):** BL-3 spec
`research/loop/specs/BL3_impact_shortfall_spec.md` refuses barrier-aligned
checkpoints. Realized move is the lookback window’s own Δm, not % of B. OQ-8
remains OPEN for any future frozen X4 FDR family.

## OQ-9 — Cross-tenor / curve features are absent from the V2 feature dictionary

The prior program's ablation (E28) found `cross_tenor` the most important feature
family, and the original spec's context includes curve flow. The V2 feature
dictionary (§5) has no cross-tenor family — the MBP-1 archive is ZN-only. Options
include: deliberate exclusion (event-granularity purity), or joining the 1m TBBO
curve aggression (FV/TU/US) as backward as-of context features (mixed
granularity).
**Decision needed:** is the omission intentional?

## OQ-10 — Econ/auction event-day source is not pinned

§5 context features and X1 conditioning reference "existing econ/auction DB
(read-only)" without a path. The prior program used an optional econ/auction DB
for E16. The exact database file/table and its coverage over 2010–2026 must be
pinned before X1. (Mechanical, but the choice of source changes the event-day
definition, which is scientific.)
**2026-08-11 decision:** pin read-only `workspace/economic_events.db` and reuse
the existing US high-importance and coupon/TIPS auction definitions.

## OQ-11 — Where is X7's era-consistency requirement evaluated?

X7's gate requires the effect "sign-consistent in ≥ 3 of 5 eras," but validation
is 2025-only (one era). Presumably era consistency is evaluated on discovery
walk-forward folds and the one-shot validation is pooled — not explicitly stated.

## OQ-12 — Symbol scope

The original spec and prior program included the curve (FV/TU/US). V2 is
implicitly ZN-only because of the archive. Confirm ZN-only is the intended scope
for the whole program (this also interacts with OQ-9).

## OQ-13 — X2 primary normalization ambiguity

X2's Method says quintiles of `AF^D_dir` at t*, but Cells says "2 AF
normalizations × 3 lookbacks = 6 headline cells." Which normalization carries the
headline H1 statement, and is the other confirmatory or co-equal?

## OQ-14 — Sample-floor mismatch between program gate and shared gate lib

PROGRAM.md §3.1 sets n ≥ 50 episodes; `scripts/aggression_gate_lib.py`
`classify()` enforces n ≥ 30 internally. The program presumably applies the
stricter floor on top, but which threshold is recorded in `conclusion.json`
verdicts must be pinned so verdicts are reproducible from artifacts alone.

## OQ-15 — Holdout end boundary vs archive end

The archive is described as "~2010-06 → 2026-04" (exact end date unverified until
X0 decompresses the last pack). The proposed holdout is 2026-01→2026-04. If April
2026 is partial, does the holdout end at the last fully-covered date, or at
2026-04-30 with partial coverage accepted?
**2026-08-11 decision:** end at the last Q3-passing fully covered session no
later than 2026-04-01; partial sessions are not accepted.
