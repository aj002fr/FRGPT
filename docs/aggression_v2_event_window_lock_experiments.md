# Aggression V2 Event-Window Lock-Entry Research

## Executive Read

This corrects the original event-window benchmark's execution timing. The flow
direction is only known after five post-release minutes, so entry is now the
observed T+5 lock price rather than the release price.

This was read-only research. V1/V2/V3 were not modified.

## Holdout Results

| Horizon after lock | n | After-cost mean | Hit rate | Tier |
| --- | ---: | ---: | ---: | --- |
| +5m | 49 | -0.23 ticks | 34.7% | FAIL |
| +15m | 49 | +0.19 ticks | 46.9% | PROVISIONAL |
| +30m | 49 | +0.03 ticks | 53.1% | PROVISIONAL |

None survives statistical testing or BH-FDR. Confidence intervals include zero
at every horizon.

## Interpretation

The strong release-price result was largely movement that occurred during the
same first five minutes used to form the signal. That movement was not
available as an executable entry after signal lock.

The corrected signal has no validated after-cost edge. Any live surface must be
paper tracking only and visibly labeled `PROVISIONAL / RESEARCH-ONLY`.

## Artifacts

- `workspace/analysis/aggression_v2_event_window_lock/lock_entry_rows.csv`
- `workspace/analysis/aggression_v2_event_window_lock/lock_entry_stats.csv`
- `workspace/analysis/aggression_v2_event_window_lock/lock_entry_gate.json`
- `workspace/analysis/aggression_v2_event_window_lock/lock_entry_summary.json`
