# C002 — Advisor panel review

## Advisor A opinion (chair, written first, independent)

- **Assessment:** uninteresting as a *ratio* claim, and the placebo is a real methodology failure, not a footnote. T1−T3 reversal lift −2.0 pp (CI crosses 0). Shuffled-half placebo −10.1 pp (CI entirely negative) — larger than the real contrast — so the tercile-of-β-ratio statistic is not calibrated.
- **Methodology:** up-to-t* causality looks respected. The ratio `β_late/β_early` is unstable (T1 mean −2.59 after winsorization). That likely drives both the non-monotonic terciles and the placebo bias. Mean decay is lower on reversals (0.17 vs 0.34) but that is not a usable tail contrast.
- **Recommended decision:** **ITERATE** — drop the ratio. Do not PARK the whole PQ1 thread yet. Next cycle: signed difference `β_late − β_early` on the same prefix, same placebo demand.

## Advisor B opinion (second advisor, independent — saw BRIEF/RESULT only)

*(not produced — Fable 5 seat waived. Solo review; cannot PROMOTE_REQUEST.)*

## Agreements / disagreements

Solo review.

## Consolidated decision

**ITERATE** — BL-2 remains open. C002's ratio specification is dead (placebo fail + wrong-sign/zero-crossing lift). C003 tests the difference specification. EXPLORATORY / NOT EVIDENCE.

## Refinements

C003: `ImpactDecay_diff = β_late − β_early` (winsorize p1/p99); T1 = most negative; same 75-date prefix; same ≥8 trades/half; shuffled-half placebo must be |lift| < 2pp or the cycle parks the thread.

## Multiplicity note

BL-2 scientific attempts after C003 will be 2. C002 counts as 1 failed specification.
