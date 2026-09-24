# Aggression V2 Generic Forward-Return Signal Experiments

## Executive Read

This was **research-only**. Live V1 (`5050`), V2 (`5052`), and V3 (`5053`) were **not** modified. No writes to `aggression_v2_live/` or `aggression_v3_live/`. No dashboard, no SOD changes, no Telegram alerts, no Tailscale changes.

Output directory:

`workspace/analysis/aggression_v2_forward_return/`

Script:

`scripts/aggression_v2_forward_return_research.py`

### Bottom line

**Gate FAIL. Do not build a dashboard.**

The forward-return framing is useful diagnostically: the model has real short-horizon direction information, especially at 1-5 minutes. But the predicted edge is too small relative to a 0.5-tick round-trip cost, and the regime-conditioned trade translation does not produce a robust follow/fade cell.

| Horizon | OOS R2 | Direction Hit | AUC | Rank IC | Avg Abs Pred | Avg Abs Realized |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1m | 0.014 | 62.6% | 0.659 | 0.196 | 0.13 ticks | 0.56 ticks |
| 3m | 0.005 | 59.1% | 0.618 | 0.159 | 0.16 ticks | 0.86 ticks |
| 5m | 0.002 | 57.5% | 0.601 | 0.137 | 0.19 ticks | 1.06 ticks |
| 15m | -0.008 | 54.5% | 0.563 | 0.082 | 0.29 ticks | 1.71 ticks |
| 30m | -0.021 | 51.9% | 0.533 | 0.022 | 0.35 ticks | 2.40 ticks |

Interpretation:

- The classifier sees short-horizon sign structure.
- The regression expected move is tiny: average predicted move is well below the 0.5-tick cost at every horizon.
- Skill decays by horizon; 30m is essentially not useful as a generic forward-return model in current features.

## Isolation Check

| Constraint | Status |
| --- | --- |
| Edit `scripts/aggression_dashboard/` | No |
| Edit `scripts/aggression_dashboard_v2/` | No |
| Edit `scripts/aggression_dashboard_v3/` | No |
| Write `aggression_v2_live/` or `aggression_v3_live/` | No |
| Touch ports / SOD / Tailscale | No |
| Build dashboard | No |
| New outputs | `workspace/analysis/aggression_v2_forward_return/` + this doc |

## What Was Tested

The experiment reframes the monitor from fixed mean-reversion into a generic forward-return problem.

For each 1-minute row, targets are built within the same ET date:

- `fwd_ret_1m_ticks`
- `fwd_ret_3m_ticks`
- `fwd_ret_5m_ticks`
- `fwd_ret_15m_ticks`
- `fwd_ret_30m_ticks`

Features include:

- Existing 18 TBBO fair-value features.
- Flow dynamics: `agg_1m`, `agg_rate`, `agg_accel`, `signed_streak`, `flow_decelerating`.
- Cross-tenor agreement/disagreement.
- V3 flow-effort ingredients via read-only `annotate_flow_effort()`.
- Reconstructed causal `residual_z`.
- RTH and event-blackout flags.

Regime counts in the dataset:

| Regime | Rows |
| --- | ---: |
| QUIET | 33,919 |
| EXHAUSTION | 6,254 |
| TREND_FOLLOW | 5,557 |
| FALSE_BREAK | 2,023 |
| WARMUP | 2,001 |
| BREAKOUT | 308 |

## Regime Translation

The trade-translation layer used an early chronological selection slice to pick `FADE`, `FOLLOW`, or `STAND_ASIDE` per regime × horizon, then applied that choice to the later holdout.

Result: almost every cell selected **STAND_ASIDE** because both fade and follow were negative after costs on the selection sample. The only non-stand-aside cell was:

| Regime | Horizon | Thesis | Selection After Cost | Holdout After Cost | Holdout n | RTH x No-Event After Cost |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| FALSE_BREAK | 30m | FADE | +0.04 ticks | -0.22 ticks | 517 | +0.00 ticks |

This fails the +0.5 tick bar and is not a trade candidate.

## Baselines

Holdout after-cost expectancy:

| Horizon | Fade Residual | Follow Residual | Flow Follow | Model Direction |
| --- | ---: | ---: | ---: | ---: |
| 1m | -0.50 | -0.50 | -0.51 | -0.35 |
| 3m | -0.50 | -0.50 | -0.51 | -0.36 |
| 5m | -0.50 | -0.50 | -0.52 | -0.37 |
| 15m | -0.50 | -0.50 | -0.59 | -0.40 |
| 30m | -0.50 | -0.50 | -0.60 | -0.45 |

The model direction is better than naive fade/follow, but still negative after a 0.5-tick cost.

## Gate

Strict gate:

- Chronological holdout.
- n >= 50 per conditioned cell.
- After-cost expectancy >= +0.5 ticks or direction AUC >= 0.6.
- Stable RTH x no-event slice.
- Adverse excursion reported.

Verdict:

**FAIL.**

Why:

- Short-horizon AUC passes numerically at 1m/3m/5m, but the expected move is far below cost and does not translate into a positive after-cost regime thesis.
- No regime x horizon cell clears +0.5 ticks after cost.
- The only selected trade thesis, `FALSE_BREAK` 30m fade, loses on holdout.

## Artifacts

| Artifact | Path |
| --- | --- |
| Dataset | `workspace/analysis/aggression_v2_forward_return/forward_return_dataset.csv` |
| Model predictions | `workspace/analysis/aggression_v2_forward_return/forward_return_predictions.csv` |
| Model metrics | `workspace/analysis/aggression_v2_forward_return/forward_model_metrics.csv` |
| Regime thesis assignments | `workspace/analysis/aggression_v2_forward_return/regime_thesis_assignments.csv` |
| Holdout stats | `workspace/analysis/aggression_v2_forward_return/regime_thesis_holdout_stats.csv` |
| Baselines | `workspace/analysis/aggression_v2_forward_return/baseline_holdout_stats.csv` |
| Gate | `workspace/analysis/aggression_v2_forward_return/forward_return_gate.json` |
| Summary | `workspace/analysis/aggression_v2_forward_return/forward_return_summary.json` |
| Plot | `workspace/analysis/aggression_v2_forward_return/forward_model_metrics.png` |
| Plot | `workspace/analysis/aggression_v2_forward_return/regime_thesis_holdout.png` |
| Plot | `workspace/analysis/aggression_v2_forward_return/baseline_after_cost.png` |

## What This Means

The generic framing is directionally right, but current factors are not enough to make a tradable generic signal after costs.

Useful takeaways:

- The signal is mostly a **short-horizon classification edge**, not an expected-return edge.
- Predicting 30m forward returns from current features is weak.
- Current flow-fair-value residual, V3 regime labels, and flow dynamics do not yet create a robust follow/fade switch.

Next research, if revisited:

- Test confidence/thresholded execution rather than all model-signed minutes.
- Add sub-minute flow shocks only after a separate design review; current 1m features may smooth away the useful impulse.
- Add option-skew/news/event context to forward-return targets, not as a dashboard feature.

No live/dashboard work is recommended from this result.
