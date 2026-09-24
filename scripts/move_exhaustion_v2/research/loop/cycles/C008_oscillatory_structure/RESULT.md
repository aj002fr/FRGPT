# C008 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Labels-only bucket composition among `OSCILLATORY_UNRESOLVED` episodes on the X1 75-date discovery prefix.

- **Data:** `labeled_B8_x2.parquet` filtered to `dates_scored` in X1 manifest (2022-01-05 → 2022-04-25). Dropped `CENSORED_INCOMPLETE_HORIZON` only (8 episodes); no `UNRESOLVED_AMBIGUOUS` present.
- **Universe:** `outcome == OSCILLATORY_UNRESOLVED`; skipped `B_ticks == 0` (0 skipped).
- **Buckets (mutually exclusive, priority order):**
  - `both_near`: mfe/B ≥ 0.8 AND mae/B ≥ 0.8
  - `near_cont`: mfe/B ≥ 0.8, not both_near
  - `near_rev`: mae/B ≥ 0.8, not both_near
  - `tight_range`: mfe/B < 0.5 AND mae/B < 0.5
  - `other`: remainder
- **Headline:** bucket shares; date-clustered bootstrap 400 reps, seed 42, on largest-bucket share.
- **n-floor:** ≥ 30 oscillatory total; do not headline a bucket with n < 30.
- **Diagnostic:** median `mae_ticks` / `mfe_ticks` vs REVERSAL and CONTINUATION (no clock columns used — barrier clocks undefined for unresolved).
- No store, no AF/NR/β, no model, no validation/holdout, no C009.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes (after drop) | 1,244 |
| Dropped | CENSORED_INCOMPLETE_HORIZON: 8 |
| Oscillatory (B≠0) | 390 |
| B_ticks==0 skipped | 0 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** OSCILLATORY_UNRESOLVED is **entirely tight range** on this prefix — zero near-miss continuation or reversal.

| Bucket | n | Share |
|--------|---|-------|
| tight_range | 390 | 100.0% |
| both_near | 0 | 0.0% |
| near_cont | 0 | 0.0% |
| near_rev | 0 | 0.0% |
| other | 0 | 0.0% |

**Largest bucket (tight_range) — date-clustered bootstrap (400 reps, seed 42):**

| Metric | Value |
|--------|-------|
| Share | 100.0% |
| 95% CI | [100.0%, 100.0%] |
| CI excludes 50% (below) | yes |
| n-floor (osc ≥ 30) | **PASS** (n=390) |
| Bucket n-floor (≥ 30) | **PASS** |

**Why near-miss buckets are empty:** B=8 ticks for all episodes. Max mfe/B and mae/B among oscillatory rows is 0.4375 (3.5 ticks) — well below the 0.8 near-miss threshold (6.4 ticks). By construction, unresolved episodes that never approached either barrier fall in `tight_range`.

**Diagnostic — median mae/mfe (ticks):**

| Outcome | n | median mae | median mfe |
|---------|---|------------|------------|
| OSCILLATORY_UNRESOLVED | 390 | 2.0 | 2.0 |
| REVERSAL | 439 | 6.0 | 1.0 |
| CONTINUATION | 415 | 1.5 | 6.0 |

Oscillatory episodes sit symmetrically between resolved classes: neither adverse nor favorable excursion dominates, consistent with tight-range chop rather than near-miss barrier pressure.

**Success bar:**

- n-floor: **met** (390 ≥ 30)
- One interpretable bucket ≥ 50% with n ≥ 30: **met** (tight_range 100%, n=390)
- CI clearly majority: **met** (CI [100%, 100%] excludes 50% from below)

**Recommend ADVANCE (diagnostic).** The oscillatory mass is interpretable as tight range, not near-miss continuation or reversal. This is descriptive structure only — not a tradable signal.

## Things tried that failed

1. **Near-miss continuation bucket (mfe/B ≥ 0.8):** 0 episodes — max mfe/B = 0.4375; oscillatory labels never approach the favorable barrier.
2. **Near-miss reversal bucket (mae/B ≥ 0.8):** 0 episodes — max mae/B = 0.4375; same ceiling.
3. **Both-near bucket:** 0 episodes — mutually exclusive with the above; no dual near-miss.
4. **Other / mixed bucket:** 0 episodes — all oscillatory rows satisfy tight_range (both ratios < 0.5).

Did **not** try (deferred per brief): alternate B thresholds, per-date conditioning, flow features, validation/holdout, C009.

## Leakage / confound check

- **Verified:** Labels-only from frozen X1 parquet; dates match manifest `dates_scored`; no store re-read; no validation/holdout dates; holdout not opened; bucket rules use only post-label mae/mfe/B (descriptive, not predictive).
- **Unchecked:** Whether tight_range dominance is an artifact of B=8 cell choice (single cell in X1); session/volatility conditioning; whether oscillatory definition itself caps excursions below 0.5B by horizon censoring.

## Limitations & surprises

- **Degenerate partition:** 100% tight_range is a sharp but expected result given B=8 and max excursion 3.5 ticks — the brief's near-miss thresholds (0.8B = 6.4 ticks) were never reachable for unresolved episodes on this prefix.
- Near-miss buckets may be more informative at larger B or longer horizons; this cycle cannot distinguish them here.
- Diagnostic medians confirm oscillatory sits between reversal (high mae) and continuation (high mfe) — useful context but not novel signal.

## Suggested next steps

- **ADVANCE (diagnostic):** Record that BL-15 oscillatory structure on X1 prefix is tight-range dominated; near-miss hypotheses are falsified on this window/B cell.
- Consider whether a follow-up cycle should test structure at alternate B cells or horizons before parking BL-15 entirely.
- Do not promote to confirmatory; no FDR; no live/dashboard wiring.
