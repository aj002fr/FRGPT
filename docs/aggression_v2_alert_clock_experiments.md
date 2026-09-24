# Aggression V2.7 Alert-Clock Experiments

## Executive Read

This was **research-only**. Live V1 / V1.2 / V2 / V2.1 / V2.2 / V2.3 / V2.6 / V2.7 / V3 were **not** modified, restarted, or written. No `*_live/` writes. No SOD, Tailscale, or Telegram changes.

Script: `scripts/aggression_v2_alert_clock_research.py`

Output: `workspace/analysis/aggression_v2_alert_clocks/`

This is **not** a live V2.7 clock swap and **not** an after-cost ship gate.

### Bottom line

A fixed **10-minute wall clock is a reasonable hold-time default**. It is not an information-time clock. On the 1-minute TBBO panel, **fixed print and volume counts fire in about one minute** because a busy TY minute already has hundreds of prints. Those clocks expire before the 5-minute min-hold, so they look “high % via price” on a handful of rows and should be ignored.

Among clocks that actually last **5–15 minutes** and have **≥50 holdout resolves**:

| Clock | Holdout hit | Avg pnl | Median % via price | Median deadline | Resolve rate |
| --- | --- | --- | --- | --- | --- |
| `tickmins_3` | **53.3%** | +0.09 ticks | 0 | 5.5m | 47% |
| `tickmins_5` | 48.7% | **−0.08 ticks** | 0 | 10m | 81% |
| `wall_15m` | 43.3% | +0.08 ticks | 0 | 15m | 96% |
| `tod_p75_fwd5_prints` | 41.7% | +0.12 ticks | 0 | 8m | 63% |
| `wall_10m` (live default) | 40.9% | +0.03 ticks | 0 | 10m | 87% |
| `prints_1500` | 40.6% | +0.09 ticks | 0 | 7m | 56% |

Every usable clock still has **median % via price = 0**. Changing the deadline does not turn FV-chase resolves into price fades. `tickmins_3` is the only cell near a coin-flip hit, and the full-OOS rate is **49.7%** with +0.11 ticks — not a tradable edge. `ship_gate=false`.

True MBP-1 order-book update counts were **not** available on this panel.

## Isolation Check

| Constraint | Status |
| --- | --- |
| Edit live V1 / V2 / V2.2 / V2.7 dashboards | No |
| Write `*_live/` | No |
| Touch ports / SOD / Tailscale / Telegram | No |
| Overwrite short-horizon FV artifacts | No |
| New outputs | `workspace/analysis/aggression_v2_alert_clocks/` + this doc |

## Frozen rules (same as live V2.7, only the deadline changes)

- Construction: walk-forward `B1_W10` residuals
- Open: `|z| ≥ 1.5`, one episode at a time
- Resolve: `|z| < 0.5` after a **5-minute wall min-hold**
- Exclusive until the **clock deadline**, not until resolve
- Score: fade at the open-bar last print, mark at the deadline-bar last print, no costs
- Same-day only (uncrossed clocks are `censored`)
- Holdout: last 20% of B1_W10 OOS dates, **2026-05-14 through 2026-06-05** (14 dates)
- Adaptive TOD print targets fit on discovery dates only

## What was tested (38 clocks)

| Family | Frozen set |
| --- | --- |
| Wall minutes | 3 / 5 / 8 / 10 / 15 / 20 / 30 |
| Prints after open | 25 / 50 / 100 / 150 / 250 / 400 / 800 / 1500 |
| Volume after open | 100 / 250 / 500 / 1000 / 2000 / 4000 / 8000 contracts (`trade_intensity × avg_trade_size` proxy) |
| Minutes with a ≥0.5-tick price change | 3 / 5 / 8 / 10 / 15 |
| Adaptive TOD prints | p25/median/p75 of next-5m prints (cap 15m); median/p75 of next-10m prints (cap 20m) |
| Hybrids | first-of and both-of wall×prints/volume/ticks |

Proxies only. This is **not** MBP-1 message time.

## How to read the grid

- **Resolve rate** will look great on any long wall clock. It mostly means the nowcast caught price.
- **% via price** is the fade test. Median 0 means price did not close the gap.
- **Hit / avg pnl** are paper last-print marks at the deadline, not flatten-at-resolve.
- Usable filter: holdout `n_resolved ≥ 50` and median deadline in `[5, 15]` minutes. Short print/volume cells fail this on purpose.

## Results

### Wall time

`wall_10m` holdout: 389 issued, **87.4%** resolved, **40.9%** hit, **+0.03 ticks**, median adverse 1 tick, median % via price 0. Matches the earlier 10-minute replay (small count difference because this run censors at the ET-date boundary).

`wall_8m` and `wall_15m` are the same story: hit 41–43%, expectancy < 0.1 tick, median % via price 0. `wall_5m` is too short for the min-hold (39% resolve, 38% hit). `wall_3m` is unusable (1 resolve). `wall_20m`/`wall_30m` leave the 5–15m band and do not improve % via price.

### Print and volume counts

On this 1-minute panel a typical 10-minute window already has **~1,600 prints**. Targets of 25–400 prints usually complete in **1–2 minutes**, so they expire before `|z|` is allowed to reset. That is why `prints_100` showed a high median % via price on **13** resolves — selection junk, not a better clock.

The first print cell inside the usable band is **`prints_1500`** (median 7 minutes, 40.6% hit). Volume clocks behave the same: only `volume_8000` even starts to last, and it still averages 2 minutes.

A **fixed print count is not a substitute for 10 minutes** at 1-minute resolution. It collapses to “exit on the next busy bar.”

### Adaptive time-of-day prints

These behave like wall clocks of similar length. `tod_median_fwd10_prints` ≈ 11 minutes and 41.0% hit. `tod_p75_fwd5_prints` ≈ 8 minutes, 41.7% hit, +0.12 ticks. No material lift in % via price.

### Price-tick minutes

This is the only family that moves hit rate. `tickmins_3` wait until three later minutes actually print a ≥0.5-tick change (median 5.5 minutes). Holdout hit **53.3%**; all-OOS **49.7%**. `tickmins_5` is 48.7% holdout but **negative** average pnl. Median % via price remains 0.

Read that as: marking when price has moved is closer to a 50/50 coin than marking a quiet 10-minute nowcast catch-up. It is still not a fade.

## Verdict

1. Keep **10 minutes** on live V2.7 as the hold-time clock unless a later MBP-1 study says otherwise.
2. Do **not** replace it with a fixed print/volume count on 1-minute bars.
3. Event time is still the right *idea*, but it needs **true MBP-1 / L1-change counts**, not minute aggregates.
4. `tickmins_3` is a research lead only (duration-matched, near-50% hit, ~0.1 tick). Not display, not executable.

Tests: `tests/test_v2_alert_clocks.py` (9/9).
