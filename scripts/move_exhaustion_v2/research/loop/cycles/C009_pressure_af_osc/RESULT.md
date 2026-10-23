# C009 — Result                                     label: EXPLORATORY / NOT EVIDENCE

## What I did

Rank-first tercile split of `AF_depth` at `t*` on the C001 feature cache; tested whether high AF raises P(OSCILLATORY_UNRESOLVED) vs resolved outcomes.

- **Data:** `episodes_c001.csv` (`episode_id`, `trading_date`, `AF_depth`, `outcome`), filtered to X1 `dates_scored` (2022-01-05 → 2022-04-25). All three scored classes retained.
- **Target:** `y = 1{outcome == OSCILLATORY_UNRESOLVED}`.
- **Binning:** rank-first terciles of `AF_depth` (T3 = highest).
- **Headline:** P(osc | T3) − P(osc | T1); date-clustered bootstrap 400 reps, seed 42; CI in percentage points.
- **Diagnostic:** P(REVERSAL) and P(CONTINUATION) by AF tercile.
- **n-floor:** ≥ 30 episodes per tercile (required to headline pp lift).
- No store, no NR/β/mfe matching, no validation/holdout, no C010.

## Data actually used

| Item | Value |
|------|-------|
| Window | 2022-01-05 → 2022-04-25 (75 dates) |
| Episodes scored | 1,244 |
| Episodes analyzed (AF_depth present) | 1,244 |
| Holdout opened | false |

## Findings

**Headline (EXPLORATORY / NOT EVIDENCE):** High `AF_depth` does **not** raise oscillatory probability. T3 is slightly *below* T1.

| AF tercile | n | P(osc) | P(rev) | P(cont) |
|------------|---|--------|--------|---------|
| T1 (low AF) | 415 | 31.3% | 39.3% | 29.4% |
| T2 | 414 | 31.9% | 35.0% | 33.1% |
| T3 (high AF) | 415 | 30.8% | 31.6% | 37.6% |

**T3 − T1 oscillatory lift (point estimate):** −0.48 pp  
**Date-clustered bootstrap (400 reps, seed 42):** −0.59 pp [−8.41, +6.74]

| Gate | Result |
|------|--------|
| n-floor (≥ 30 per tercile) | **PASS** (415 / 414 / 415) |
| T3−T1 ≥ 5 pp | **FAIL** (−0.59 pp) |
| CI not entirely ≤ 0 | pass (upper bound +6.74) |
| **Recommend** | **PARK** |

High AF shifts mass toward continuation (T3 P(cont)=37.6% vs T1 29.4%) and away from reversal (31.6% vs 39.3%), with oscillatory share flat across terciles (~31%). No evidence that pressure-building flow tags unresolved chop on this prefix.

## Things tried that failed

1. **Headline oscillatory lift (T3−T1):** −0.48 pp point / −0.59 pp bootstrap mean — wrong sign and far below the 5 pp advance bar.
2. **Monotone oscillatory gradient across terciles:** flat (~31–32%); T2 slightly highest, not T3.
3. **Reversal as hidden headline:** T3−T1 reversal lift = −7.6 pp (high AF lowers reversal); not preregistered — diagnostic only.

Did **not** try (deferred per brief): mfe matching, NR/β conditioning, quintile-within-mfe (C001 style), validation/holdout, C010.

## Leakage / confound check

- Features reused from C001 cache (`AF_depth` at `t*` only); no store read, no future data.
- Rank-first terciles are in-sample on the same 75-date prefix; no cross-split generalization claimed.
- Date-clustered bootstrap addresses day concentration; CI still wide (±~7 pp).
- C008 showed oscillatory = 100% tight-range; AF may not discriminate within that homogeneous subclass.

## Limitations & surprises

- Oscillatory rate is ~31% regardless of AF tercile — consistent with C001's null continuation signal but opposite the hypothesized direction.
- High AF appears to favor continuation over reversal, not chop.
- Single discovery prefix; exploratory multiplicity not controlled.

## Suggested next steps

- **PARK** BL-12 pressure→oscillatory thread unless advisor sees a refined angle (e.g., joint AF×range structure post-C008).
- If iterating: consider whether AF discriminates *within* tight-range oscillatory only, or test orthogonal pressure features (NR, decel) on oscillatory target.
