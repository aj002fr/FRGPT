# Aggression V1 Flow Dominance Banner

Status: `DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL / NOT RANGE-TREND VALIDATED`

This file is the **live 5050 implementation** record (four states, session OLS beta, 2-tick Quiet, INCREASING/SLOWING). The 15 September 2026 discussion spec is `docs/aggression_v1_monitor.tex` (six states, fixed reference beta, 1-tick Quiet, no dynamics). That spec is not live until separately implemented.

## Purpose

Aggression Monitor V1 now has a compact banner above its charts that describes
which component of the current price move is larger:

- `AGGRESS DOMINANT`
- `IMPLIED PASSIVE DOMINANT`
- `BALANCED`
- `QUIET`
- `WARMING` or `UNAVAILABLE` when inputs are insufficient

“Implied passive” means the change in the fair-value residual. It does not
identify resting orders, passive participants, or market-depth behavior.

## Live calculation

The banner uses V1’s existing intraday OLS model, which is fitted once per
session and then frozen:

`price = alpha + beta * TotalAggress + residual`

For completed one-minute bars:

- aggress contribution: `beta * delta(TotalAggress)`
- implied-passive contribution: `delta(residual)`
- price contribution check: `delta(price)`

Each contribution is summed over 15 completed minutes and classified from
those local price sums. There is no session-expanding z. A uniformly
aggress-led day stays `AGGRESS DOMINANT` because the running mean cannot eat
the signal.

The fixed role/absorption display rules are:

- `AGGRESS DOMINANT`: the 15-minute aggress sum has the same sign as the
  15-minute price move and its absolute value is at least 1.5 times the
  implied-passive absolute sum.
- `IMPLIED PASSIVE DOMINANT`: implied-passive absolute 15-minute sum is at
  least 1.5 times aggress and either fights aggress or has the price-move sign.
- `QUIET`: both absolute 15-minute contributions are below 2 TY ticks
  (`2 / 64` in price).
- `BALANCED`: activity is present, but neither dominance rule fires.

The first 14 minutes of a session are warmup (`WARMING`) until the 15-minute
window is complete.

The forming minute is excluded. Although the browser polls every second, the
banner therefore changes only when a completed one-minute bar changes the
state.

## Shift and speed text

The banner reports the prior distinct state and elapsed minutes in the current
state. For each contribution, it compares the absolute 15-minute sum now with
the absolute 15-minute sum five minutes earlier:

- increase greater than `1` tick: `INCREASING`
- decrease less than `-1` tick: `SLOWING`
- otherwise: `STEADY`

These are transparent display heuristics, not validated prediction features.

## API

`POST /api/aggression` includes a top-level `flow_dominance` object with:

- `status`, `state`, `research_state`, `reason`, `as_of`
- fixed `label`, `non_directional=true`, `predictive=false`
- `aggress` and `implied_passive`: current 15-minute ticks, five-minute trend, tick change
- `transition`: previous/current state, transition time, minutes in state
- `model`: intraday model name, beta, and fit timestamp
- `parameters`: 15-minute window, 1.5 ratio, 2-tick Quiet floor, 1-tick dynamics
- completed/ready observation counts

Missing models, beta, required columns, completed bars, or z-score history fail
closed to `UNAVAILABLE` or `WARMING`. Banner calculation failure does not block
the five existing chart panels.

The same response includes `flow_dominance_series`: one compact snapshot per
completed bar (`x`, `status`, `state`, `reason`, `aggress`, `implied_passive`,
`transition`, `as_of`). It is computed on the full unclipped intraday model so
session expanding z and minutes-in-state match the live banner, then clipped to
the chart lookback. A live-feed disconnect still forces only the current banner
to `UNAVAILABLE`; historical hover rows keep their reconstructed state.

## Banner on hover

The V1 controls have a local **Banner on hover** checkbox (default on). It does
not change the request payload. When enabled, hovering any of the five chart
timestamps fills a preview strip under the live banner with the completed-bar
state that would have been showing at that time. The live top banner stays on
the current bar and is never overwritten by hover. Unhover returns the preview
to `HOVER A TIMESTAMP`. Hovering a forming minute maps to the last completed
bar.

## Evidence boundary

The 2026-07-17 offline aggress/passive dominance study produced zero
restrictive PASS cells and did not open FINAL. This banner:

- does not forecast RANGE or TREND;
- does not emit long/short, fade, breakout, or execution guidance;
- does not claim parity with the offline prior-day-beta research panel;
- does not add alerts, episode scoring, persistence, or live-store writes.

See `docs/aggression_v1_aggress_passive_dominance_experiments.md` for the failed
predictive gate.

## Operations and verification

The current V1 process is not restarted automatically. A manual V1 restart is
required for these code/template changes to appear.

Focused verification:

`py -3 -m pytest tests/test_v1_flow_dominance_context.py tests/test_aggression_feed_failover.py -q`

JavaScript syntax:

`node --check scripts/aggression_dashboard/static/dashboard.js`
