# Aggression Autoresearch Campaign Design

Status: `HISTORICAL CAMPAIGN COMPLETE / E001-E002 NOT CONFIRMED / CONFIRMATION LOCKED`

Frozen at: `2026-07-22T17:07:04.239793Z`

## 1. Scope and research boundary

This campaign asks whether causal features derived from the Treasury aggression
matrix improve prediction of future TY path behavior. This document and the
three frozen specs define methods. Historical discovery, one-shot validation,
and diagnostic quarantine are now complete under append-only execution
manifests. No confirmation cohort was opened.

The fixed evaluator owns anchors, cells, targets, future paths, censoring,
costs, inference, multiplicity, and decisions. Candidate code receives causal
formation rows only and returns a registered finite feature map. Validation is
transform-only and one-shot after discovery selection and state freezing.
Confirmation requires a separate owner approval bound to the canonical spec
hash and retires the family cohort before targets can load.

## 2. Data

The authoritative input is immutable publication
`v20260722_phase0_v3_dense_20260226`, under
`workspace/analysis/aggression_autoresearch_data/versions/`.

- Matrix SHA-256:
  `dd2cceb815a503836556ae19b555474518eb3c51a5bcee0107e85397a30d7949`
- Rows: `580,860`
- Eligible dates: `423`
- Eligible range: `2024-07-02` through `2026-02-26`
- Frequency: one-minute TBBO features
- Instrument: causally selected TY/ZN outright; each date uses the prior
  complete session's volume ranking
- Session: prior calendar day 18:00 ET through trading date 17:00 ET
- TY tick: `0.015625` points (`1/64`)
- Exact full-record duplicates are removed and audited
- Every exclusion is retained in the publication manifest

The wider through-2026-07-21 audit artifact remains available, but is not an
experimental input. The PostgreSQL source truncates 85 mostly Monday-Thursday
sessions after 2026-02-26 near 09:00 ET. The owner selected the dense cutoff
instead of paid Databento backfill. Sparse later Friday snapshots are therefore
not mixed into historical inference.

Legacy parity uses the contract-stable 2026-01-27 through 2026-01-29 benchmark.
It passed after documented exact-dedup attribution, fixed economic equivalence
bounds for sparse vendor quote revisions, and correct legacy warm-up handling.
Broad roll-date parity is not meaningful because the legacy generic symbol can
map to a different outright.

## 3. Frozen cohorts and context

The final split manifest is
`workspace/analysis/aggression_autoresearch_campaign/manifests/split_manifest_v2.json`
(SHA-256
`76e613b172094a576325a660378edc9de2156a809a4e0be078404ba8ab49e932`).

- Discovery: eligible dates in `2024-07-01`–`2025-08-31`; `298` dates
- Validation: eligible dates in `2025-09-01`–`2026-01-23`; `101` dates
- Historical quarantine: `2026-01-26`–`2026-02-26`; `24` dates,
  diagnostic-only and never untouched evidence
- Prospective confirmation: first complete research-eligible session strictly
  after `2026-07-22T17:07:04.239793Z`; no end date is preassigned

The point-in-time high-importance US event manifest covers the historical
matrix and has SHA-256
`c3730de19a073e62fcb46c63d644e22fc26135b1b481e4e2ff13d5ba5e7463ed`.
Event blackout is `[-10,+30]` minutes around the scheduled timestamp.

Session labels are:

- `ASIA`: 18:00–02:59 ET
- `LONDON`: 03:00–07:59 ET
- `RATES_RTH`: 08:00–15:00 ET
- `LATE`: 15:01–16:59 ET

The frozen exposure manifest has SHA-256
`a88df9a6b01a8e0d7d6f065a1b7f041911dd8a996746d3c2bb617fa089f728fb`.
The append-only key is trading date × target × semantic hypothesis family.
Children cannot relabel an exposed family/date/target cohort as untouched.

## 4. Common predictive protocol

### 4.1 Discovery selection

All grids are explored on discovery only with a 30-minute embargo and four
expanding outer folds:

1. train through 2024-12-31; score 2025-01-01–2025-02-28
2. train through 2025-02-28; score 2025-03-01–2025-04-30
3. train through 2025-04-30; score 2025-05-01–2025-06-30
4. train through 2025-06-30; score 2025-07-01–2025-08-31

Preprocessing hyperparameters use inner expanding-date folds. Every
configuration is transformed on the same evaluator-owned anchors. A winner
must improve Brier in at least three outer folds. The declared tie-break then
applies. The winner is refit on full discovery and its state is frozen.
Validation cannot fit, search, change an outcome, or change a grid.

### 4.2 Anchors and censoring

Anchors are grouped by date/session, at least 30 minutes apart, never cross a
session boundary, contain at least 80% of expected future minute observations,
and have a price observation within two minutes of the horizon. All exact
boundary policies are experiment-specific and frozen below.

### 4.3 Models and estimand

Baseline and augmented logistic models use identical discovery-frozen
imputation/scaling. The augmented model adds only the registered primary
candidate feature. The primary effect is:

`equal-date mean(per-date mean(Brier_baseline - Brier_augmented))`.

Relative Brier skill and calibration intercept/slope are reported. Candidate
direction, cell membership, and outcomes are evaluator-derived.

### 4.4 Inference, power, and usefulness

- Discovery diagnostics: `999` resamples
- Validation/confirmation: `9,999` resamples
- Inference: studentized date-cluster wild bootstrap with equal date weight
- Serial falsification: within-session circular/block shifts of at least
  30 minutes; unrestricted row permutation is forbidden
- Multiplicity: one-sided Holm at `alpha=0.05` within each semantic cell family;
  the original denominator persists through confirmation
- Absolute SESOI: `0.005` Brier
- Relative Brier skill floor: `2%`
- Absolute sample floors: `25` dates and `30` outcomes in each class
- Power target: `80%`
- Required dates:
  `ceil(((z_(1-alpha_cell)+z_0.80)*sigma_date/0.005)^2)`

Validation stays unopened when underpowered. The usefulness selector is the
discovery-frozen 80th percentile of augmented minus baseline probability.
Usefulness additionally requires at least 10% coverage, 30 selected episodes,
8 percentage points of target-rate lift, and clustered CI lower bound above
zero. Predictive evidence and economic actionability are separate.

## 5. E001 — residual oscillation

Family: `FAM_RESIDUAL_OSCILLATION`; hypothesis: `H002`.

Goal: test whether repeated deadbanded sign flips of a causal price-versus-flow
residual incrementally predict bounded future price behavior.

Residual response is `price - day_open_price`. The flow model is fit only on
the current discovery training fold. State resets at 18:00 ET. Sign remains
unknown until residual exits ±deadband; inside the band the initialized sign is
retained. A flip is timestamped only after the new sign persists for the full
dwell and is never backdated.

Grid: window `{15,30,60}` minutes × deadband `{0.5,1,2}` ticks × dwell `{1,2}`
minutes = `18` configurations. All require 60 minutes of same-session warm-up.
Tie-break: smaller window, larger deadband, smaller dwell.

Primary feature: trailing flip count. Absolute residual, residual age,
residual change, and recent momentum are nuisance features supplied to both
models.

Outcome `BOUNDED_4T_30M` is one iff the maximum close-to-formation absolute
displacement over `(t,t+30m]` is strictly less than four ticks. Exact touch is
zero. Registered overlapping cells are `ALL`, `RATES_RTH_NO_EVENT`, and
`OVERNIGHT`. Horizons 15/60, thresholds 3/6 ticks, and quarantine are
diagnostic-only.

Per-cell screen pass requires all of:

1. effect at least `0.005`
2. Holm-adjusted one-sided p at most `0.05`
3. 95% CI lower bound above zero
4. circular/block-shift p at most `0.05`
5. power and class floors
6. positive effect in at least 60% of at least four validation months
7. no date contributes more than 25% of total positive effect
8. no required slice has effect at or below `-0.0025` or significant reversal

Canonical spec hash:
`2fdf32f13566c854bcbb0058030789f52369975583d302c64e468fb0406e61d3`.

## 6. E002 — convergence toward frozen fair value

Family: `FAM_FV_CONVERGENCE`; hypothesis: `H003`.

Fair value predicts `price - day_open_price` with ridge lambda selected only in
inner discovery folds from `{0.1,1,10}`. `FV_t` is frozen at formation. The gap
is `G_t=FV_t-price_t`; evaluator direction is `sign(G_t)`.

Primary candidate feature is the formation mechanism:
`(|delta_price_L|-|delta_FV_L|)/(|delta_price_L|+|delta_FV_L|+epsilon)`.
Lookback grid is `{5,15,30}` minutes. The primary population is fixed at
`|G|>=2` ticks; `LARGE_GAP` is fixed at `|G|>=4`. Tie-break prefers 15 minutes,
then the shorter lookback.

Outcome is a one-minute-close first passage. Target is one when the first close
moving at least 50% of the formation gap toward frozen FV occurs before the
first close moving at least 100% adversely. Equality crosses; adverse-first or
neither by 30 minutes is zero. Intraminute touches do not count.

Registered overlapping cells are `ALL_ELIGIBLE` and `LARGE_GAP`. Predictive,
power, stability, concentration, falsification, and usefulness gates equal
E001's, with Holm denominator two.

Tradability is diagnostic only: enter one minute later toward frozen FV,
charge 0.5 tick round trip, and exit at 50% closure, 100% adverse expansion, or
30 minutes. Report equal-date net expectancy, CI, MAE, turnover, coverage,
RTH/no-event, and overnight stability. Predictive success with failed economics
is `non_actionable`, not tradable.

Canonical spec hash:
`466f3b12ac904b36435ba3e090934b8541f5a9856674235b4517b8af8e6586ce`.

## 7. D001 — dominance descriptive map

Family: `FAM_DOMINANCE_MAP`; hypothesis: `H001`.

D001 is discovery-only and non-evidentiary. It maps each of three proxies
separately: buysell ratio `{<p10,p10-p90,>p90}`, large-trade-share terciles, and
trade-intensity terciles. Context is session `{ASIA,LONDON,RATES_RTH}` ×
discovery realized-volatility tercile × horizon `{15,30,60}`. This is 243
declared cells, not a 729-cell proxy interaction. `LATE` is aggregate
diagnostic-only.

Each cell reports anchors, dates, bounded-excursion share, resolved share, and
path efficiency `|net move| / sum(|one-minute moves|)`. Discovery is split into
two fixed chronological halves. Nomination requires at least 25 dates and 50
anchors in each half, same-sign bounded-excursion lift of at least 8pp in both,
and no material session/event reversal. Rank by the smaller absolute half-lift,
then simpler proxy/context. At most two children may be proposed; no p-value is
evidence and no validation/confirmation data can load.

Canonical spec hash:
`4cab244b5ef07c9281dea843a753b3af2c5896b413f922923235cbfafb194f88`.

## 8. Decisions and confirmation

Validation states are `screen_pass`, `screen_pass_limited`, `not_confirmed`,
`positive_but_trivial`, `inconclusive`, `invalid`, `blocked_underpowered`, and
`non_actionable` where applicable. Five implementation attempts are allowed;
attempt six closes invalid.

Confirmation uses the identical pipeline and original multiplicity denominator.
`confirmed` requires the primary all-population cell to pass every predictive
gate. `confirmed_limited` requires only a preregistered subgroup to pass while
the primary-cell effect remains positive. CI upper below SESOI is
`not_confirmed`; positive but sub-SESOI is `positive_but_trivial`; unresolved
SESOI-compatible uncertainty is `inconclusive`. The same semantic family gets
one prospective cohort.

No result authorizes a dashboard, live signal, automated execution, or V1/V2/V3
change without a separate explicit decision.

## 9. Immutable artifacts

- `scripts/autoresearch/registry/experiments/E001.yaml`
- `scripts/autoresearch/registry/experiments/E002.yaml`
- `scripts/autoresearch/registry/experiments/D001.yaml`
- `workspace/analysis/aggression_autoresearch_campaign/manifests/spec_manifest.json`
  (SHA-256
  `85bd76e0e5836a1fab05ca20bd77bbf064c16944af2a962dcfa02dd10df94ced`)
- `split_manifest_v2.json`, `event_manifest.json`, `exposure_manifest_v2.json`
- `workspace/analysis/aggression_autoresearch_campaign/state/campaign_state.sqlite`

The earlier `split_manifest.json` and `exposure_manifest.json` are retained as
superseded pre-freeze audit artifacts. Only their `v2` replacements are bound
to the frozen specs.

## 10. Phase 3 executable addendum

Phase 3 preserved every artifact in section 9 and added append-only
`registry/execution_contracts/{E001,E002,D001}.yaml`. These contracts freeze
ordered preprocessing/model inputs, deterministic numerical rules, E002's
inner-lambda then outer-lookback search, and D001's user-approved fixed
four-TY-tick bounded/resolved rule at 15/30/60 minutes. An effective spec hash
binds each original spec hash to its canonical execution contract.

Phase 3 `manifests/execution_manifest_v2.json` bound the data, split, event,
exposure, base/effective specs, candidate sources, and transitive execution
code with authorization flags false. It and version 1 remain superseded audit
records; the Phase 2 methodology files were not rewritten.

The outcome-blind preflight used only the first discovery date's first 90
formation rows. It tested all 18 E001 and 9 E002 candidate configurations plus
the single D001 preprocessing map twice for deterministic state/output hashes.
It passed with counters of zero for outcome construction, scoring, campaign
state reservation, and exposure writes. This is software verification, not an
experiment or evidence.

## 11. Historical execution record

Discovery selected E001 60m/1-tick/2m and E002 30m/ridge-10/2-tick. One-shot
validation used all 101 frozen dates and returned `not_confirmed` for both:
E001 ALL effect `0.00000506` (95% CI `[-0.00008769,0.00009930]`) and E002
ALL_ELIGIBLE effect `-0.00042768` (95% CI
`[-0.00080779,-0.00005070]`). E002's delayed-entry diagnostic was also
negative after cost.

The 24-date historical quarantine is non-evidentiary and below the fixed
25-date floor; it did not overwrite either validation decision. The branching
record closes E001/E002 without retuning and retains D001's two nominations as
proposal-only. Confirmation and live use remain separately locked.
