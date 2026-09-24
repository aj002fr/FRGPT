# DISLOCATION x LIFO FRIENDLY — trade-spec harness

**Verdict: FAIL / STOP for shipping as a tradeable signal.**
0 PASS in 576 scored cells (288 explore + 288 holdout). 0 of 288 matched specs are
positive after a 0.5-tick round-trip cost in **both** samples.

Script: `scripts/aggression_v2_lifo_dislocation_trade_spec_research.py`
Artifacts: `workspace/analysis/aggression_v2_lifo_dislocation_trade_spec/`
Offline, read-only. No live V1/V2/V3 surface is imported, written, or restarted.

## Question

Does gating the V2 DISLOCATION fade on the V2.2 LIFO FRIENDLY condition (low trade
intensity) turn the alert into an executable trade? Hypothesis: when a residual gap
opens on *low* trade intensity, the move is not backed by real flow and should revert
more reliably than an average dislocation.

## Method

Panel: `walkforward_predictions.csv` (ridge) joined to `tbbo_feature_matrix.csv`,
50,062 one-minute bars over 69 ET dates (2026-02-03 → 2026-06-05).
Date split: first 48 dates explore, last 21 dates holdout.

Causal context (no look-ahead):
- `residual_z` from a session-expanding `shift(1)` mean/std, `min_periods=20`.
- `trade_int_lo` from a session-expanding `shift(1)` p33 of `trade_intensity`,
  matching the frozen V1.2/V2.2 constants (`TRADE_INT_LO_QUANTILE=0.33`).

Trade spec (this is the part the live alert lacks):
- Entry at the **next** bar after the signal bar, never the signal bar itself.
- Direction `-sign(residual)` (fade rich/cheap).
- Stop ∈ {2, 3, 4, none} ticks; target ∈ {1, 2, 3, none} ticks;
  time stop ∈ {15, 30, 60} minutes; `z_entry` ∈ {1.5, 2.0}.
- If stop and target both trigger within a bar, the stop is assumed to fill (conservative).
- One position at a time; no new entry until the prior trade exits.
- Cost sweep at 0.25 / 0.5 / 1.0 ticks, headline 0.5.

Gate: `scripts/aggression_gate_lib.py` — date-clustered bootstrap, one-sided lower
bound, BH-FDR at q=0.10, `n>=30`, plus a first-half/second-half stability check.

## Results

Explore: **288 FAIL, 0 PROVISIONAL, 0 PASS.** Every cell is negative after 0.5 ticks.

The LIFO filter *does* help in-sample, which is why it looked promising:

| Variant | z | Explore gross | Holdout gross |
| --- | ---: | ---: | ---: |
| DISLOCATION_ONLY | 2.0 | 0.090 | 0.139 |
| DISLOCATION_x_LIFO | 2.0 | **0.144** | 0.085 |
| DISLOCATION_x_NOT_LIFO | 2.0 | 0.098 | **0.182** |
| DISLOCATION_x_LIFO | 1.5 | 0.101 | 0.007 |

The ordering **inverts out of sample**: LIFO is the best variant on explore and the
worst on holdout, while NOT_LIFO is the worst on explore and the best on holdout.
This is the signature of an in-sample artifact, not an edge.

Best explore spec (LIFO, z=2.0, h=60, stop=4, target=3): +0.417 gross ticks on
explore collapses to **−0.036** on holdout.

Cross-sample agreement is weak: correlation of explore vs holdout gross across the
288 matched specs is **0.234**. Specs positive after 0.5 ticks in both samples: **0**.
Even at an unrealistically generous 0.25-tick cost, only 5 of 288 clear both.

Representative exit mix (LIFO, z=2.0, h=30, stop=3, target=2, explore, n=280):
45% target / 17% stop / 38% time stop, 52.1% hit rate, mean MAE −1.24 ticks,
net −0.03 ticks at 0.25 cost and −0.28 at 0.5.

The 5 holdout PROVISIONAL cells are noise: all are `target=none` pure time-stop
variants, none is BH-significant, and every one has a one-sided lower bound far
below zero (−0.43 to −0.96). They also contradict the explore sample.

## Cost reality

0.5 ticks round trip is already optimistic for TY, where the resting spread is
typically a full tick. A spec that only clears at 0.25 ticks is not tradeable.

## Conclusion

The DISLOCATION x LIFO pairing has roughly +0.1 to +0.4 gross ticks of mean
reversion depending on spec, which is real but smaller than the cost of trading it.
Adding entry/stop/target/time-stop mechanics sharpens the *shape* of the outcome
distribution without moving the mean above cost.

Do not build a V2.4 that presents this pairing as a tradeable long/short signal.
The harness itself is reusable and is the right scaffolding for any future candidate.
