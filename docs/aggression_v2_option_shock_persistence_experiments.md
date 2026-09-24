# TY Option Skew/Vol Shock Persistence + Half-Life Study

## Executive Read

This was read-only research using existing shock artifacts. No gamma/live DB
writes, dashboard edits, live-store writes, or process changes occurred.

## Clear Verdict

**No option-shock persistence or persistence-to-TY-path relationship passes
holdout coverage and FDR.**

The pooled data suggests most option shocks initially mean-revert quickly:

- ATM vol up/down median 50% half-life: about 17 minutes.
- ATM vol up median full reversion: 36 minutes.
- ATM vol down median full reversion: 29 minutes.
- RR50 down half-life: 5.5 minutes; full reversion: 40 minutes.

But option levels often re-expand later, so the path is not monotonic. The
holdout is too small to distinguish persistence from recurring intraday vol
cycles.

## Coverage

- 54 shock events.
- 630 event/horizon/metric persistence rows.
- Valid option endpoints:
  - 1m/5m: 70 rows (includes ATM backbone robustness).
  - 30m/60m: 66.
  - 240m: 64.
  - 8h: 62.
  - strict calendar 24h: 51.

Primary holdout is restricted to `PRICE_QUIET_SHOCK`:

- ATM vol up: 1 event.
- ATM vol down: 1.
- RR50 down: 2.
- RR50 up, call IV up, put IV up, call IV down: 1 each or fewer.

No arm has sufficient holdout coverage.

## Pooled Persistence Diagnostics

### ATM vol up

- Pooled n=16 across 10 dates.
- Median half-life: 17m.
- Full-reversion rate: 81%; median full reversion 36m.
- Mean retention declines from ~0.95 at 1m to ~0.08 at 30m.
- It partially rebounds by 60–120m, falls below zero by 8h on average, and is
  negative at 24h.

Interpretation: the initial vol pop usually retraces quickly; later levels are
dominated by new vol cycles, not simple persistence.

### ATM vol down

- Pooled n=11 across 7 dates.
- Median half-life: 17m.
- Full-reversion rate: 73%; median full reversion 29m.
- Endpoint retention remains positive through 4h and appears elevated again
  at 24h.

Because most events crossed the reversion threshold early and then moved back
in the original direction, this is likely recurring session structure rather
than one shock staying continuously depressed.

### RR/skew

- RR50 down: median half-life 5.5m, median full reversion 40m (n=9).
- RR50 up: half-life 6m, full reversion 421.5m (n=3).
- Skew-index down: half-life 41m, full reversion 170.5m (n=4).

Samples beyond RR50 down are too small for interpretation.

### Backbone robustness

ATM backbone moves revert more slowly than the average call/put ATM measure:

- ATM-up backbone half-life 30m, full reversion 191m (n=12).
- ATM-down backbone half-life 12m, full reversion 19m (n=4).

This is pooled diagnostic evidence only.

## Does Persistence Explain TY Futures Paths?

No validated relationship.

At 60m persistence:

- Persistent group: 3 events.
- Reverted group: 1 event.
- Partial group: 2 events.
- Only 3 holdout dates.

Raw diagnostics suggest persistent shocks had more TY range and realized vol at
30–60m than the single reverted event. Several bootstrap comparisons look
large, but the sample is too small and fails the n>=30 / cluster>=8 guard.

Retention-versus-TY correlations are similarly based on at most 6 events
across 3 dates. No correlation has a valid FDR pass.

There is no evidence that option-vol persistence reliably predicts:

- TY direction;
- trend versus range/chop;
- 8h/24h move delivery.

## Method

- Shock metric measured against t0-15m baseline.
- Retention aligned to original shock direction.
- Strict option-metric endpoints at 1–240m (90s tolerance) and 8h/24h (5m
  tolerance); no forward-fill across market closures.
- Half-life is first retention <=0.5; full reversion is first <=0.
- Chronological holdout dates reused from the prior shock study.
- Price-quiet shocks only for formal holdout.
- Date-clustered CIs and BH-FDR.

## Recommendation

Keep logging option-shock persistence. Current pooled data supports a working
hypothesis that:

1. ATM/RR shocks often retrace within 5–40 minutes;
2. shocks that remain persistent for 60 minutes may accompany more TY
   range/realized vol.

Neither hypothesis is validated. Do not add persistence or range/trend signals
to a dashboard until there are substantially more independent price-quiet
holdout events.

## Artifacts

- `workspace/analysis/aggression_v2_option_shock_persistence/shock_persistence_rows.csv`
- `persistence_stats.csv`
- `half_life_stats.csv`
- `ty_path_by_persistence.csv`
- `retention_ty_correlations.csv`
- `option_shock_persistence_summary.json`
- `persistence_curves.png`
