# Aggression V2 Event-Window Experiments

## Executive Read

This was **research-only**. Live V1 (`5050`), V2 (`5052`), and V3 (`5053`) were **not** modified. No dashboard, no alert-emission change, and no writes to `*_live/`.

Script: `scripts/aggression_v2_event_window_research.py`

Artifacts: `workspace/analysis/aggression_v2_event_window/`

## Bottom Line

**Strict gate FAIL, but this is the best new research lead.**

After de-duplicating multiple economic-event components that share the same timestamp, first-5-minute post-release flow direction has strong holdout expectancy, but the main cells have `n=49` versus the required `n>=50`.

| Horizon | Rule | n | After Cost | Hit Rate | Median Adverse | Gate |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| 5m | first-5m flow follow | 49 | +1.30 ticks | 65.3% | 0.0 | FAIL: n<50 |
| 15m | first-5m flow follow | 49 | +2.15 ticks | 65.3% | -1.0 | FAIL: n<50 |
| 30m | first-5m flow follow | 49 | +1.89 ticks | 63.3% | -1.0 | FAIL: n<50 |

EV-gated subsets looked stronger but were much too small:

| Horizon | Rule | n | After Cost | Hit Rate |
| --- | --- | ---: | ---: | ---: |
| 5m | EV top 10% | 9 | +3.72 ticks | 88.9% |
| 15m | EV top 10% | 8 | +6.63 ticks | 87.5% |
| 30m | EV top 10% | 6 | +8.00 ticks | 100.0% |

## What Was Tested

From `workspace/economic_events.db`, the script isolated scheduled US release timestamps and collapsed multiple components at the same timestamp into one release observation.

For each release:

- Direction = sign of first-5-minute post-release signed aggressor flow.
- Outcome = TY price move over 5/15/30 minutes after the release.
- PnL = direction * move - 0.5 tick.
- EV gate applied only where an EV score exists at the event timestamp.

## Why The Gate Still Fails

The original raw event rows had duplicates because several components share the same release timestamp. After de-duplication, holdout observations dropped from `n=68` to `n=49`.

That is one observation short of the strict `n>=50` threshold. The economics are encouraging, but the rule says no dashboard unless the gate is clean.

## Cost/Data Honesty

This one is a sample-size failure, unlike EV selectivity and meta-labeling.

At current de-duplicated coverage:

- Main flow-follow cells need `+1` additional holdout release observation to meet `n>=50`.
- EV-gated cells need roughly `+40` more selected holdout observations, or a less aggressive selection threshold.

The right next step is to rerun this exact script after more event days accrue, not to build a live surface now.

## Verdict

No dashboard. No live change.

But event-window flow persistence is the first clean lead that has both direction and magnitude after costs. Keep this as the highest-priority re-gate candidate once more de-duplicated event observations are available.
