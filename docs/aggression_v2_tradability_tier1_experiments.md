# Aggression Monitor Tradability — Tier 1 Experiments

## Executive Read

This was **read-only** research. Live V1 (`5050`), V2 (`5052`), and V3 (`5053`) were **not** modified. No writes to `aggression_v2_live/` or `aggression_v3_live/`. No SOD / Tailscale / port changes.

Outputs only under:

| Idea | Script | Artifacts |
| ---- | ------ | --------- |
| 1 Resolution diagnostics | `scripts/aggression_v2_resolution_diagnostics_research.py` | `workspace/analysis/aggression_v2_resolution/` |
| 2 V2×V3 confluence | `scripts/aggression_v2xv3_confluence_research.py` | `workspace/analysis/aggression_v2xv3_confluence/` |
| 3 Magnitude classifier | `scripts/aggression_v2_magnitude_research.py` | `workspace/analysis/aggression_v2_magnitude/` |
| 4 Stand-aside filter | `scripts/aggression_v2_standaside_research.py` | `workspace/analysis/aggression_v2_standaside/` |

### Bottom line

**All four Tier-1 gates FAIL.** Do **not** build V2.1. Keep logging. Leave V1/V2/V3 untouched.

| Idea | Headline | Gate |
| ---- | -------- | ---- |
| 1 Resolution | EXPIRED / FV_TO_PRICE open-time AUC ~0.73–0.74, but top-decile **n=16 &lt; 50** | **FAIL** |
| 2 Confluence | V3 regime coverage only 4 days (**n_with_regime=66**); best RTH×no-event cell +0.35 ticks after cost | **FAIL** |
| 3 Magnitude | Holdout AUC **0.73**, top-decile lift **+36pp**, but top-decile **n=16 &lt; 50** | **FAIL** |
| 4 Stand-aside | AUC **0.46**; stand-aside pool after-cost **+0.06** (not ≤ −0.5); keep pool still negative | **FAIL** |

Cost assumption throughout: **0.5 tick** round-trip.

Post-run isolation sanity (2026-07-09): ports `5050` / `5052` / `5053` still HTTP 200.

## Isolation check

| Constraint | Status |
| ---------- | ------ |
| Edit `scripts/aggression_dashboard(_v2\|_v3)/` | No |
| Write live V2/V3 stores | No |
| Touch ports / SOD / Tailscale | No |
| Outputs | `workspace/analysis/aggression_v2_<topic>/` + this doc only |

---

## Idea 1 — Resolution-type diagnostics

### Question

When price gives back to FV vs when FV catches up to price (vs never resolves), what does the tape look like at open / midlife / close — and can we predict the bucket at alert open?

### Buckets (`price_share_of_reversion`)

| Bucket | Rule | n (covered) |
| ------ | ---- | ----------- |
| PRICE_TO_FV | `price_share > 0.5` | **12** (rare) |
| FV_TO_PRICE | `price_share < 0.5` | 186 |
| EXPIRED | missing / never resolved | 255 |

`price_share` filled on **198 / 486** alerts — do not over-claim the ~94% FV-catch-up figure on the filled subset alone.

### Diagnostics (life path)

PRICE_TO_FV shows higher median `life_price_vs_fv_path_ratio` (~0.69) vs FV_TO_PRICE (~0.42) and EXPIRED (~0.48). Sample for PRICE_TO_FV is too small to trade on.

Trajectory plots (median + IQR at open / mid / close):

- `workspace/analysis/aggression_v2_resolution/resolution_trajectories.png`
- `workspace/analysis/aggression_v2_resolution/resolution_life_path_bars.png`

### Open-time classifier (chronological holdout)

| Bucket | AUC | Top-decile n | After-cost lift (top vs base fade) | Hit lift |
| ------ | --- | ------------ | ---------------------------------- | -------- |
| EXPIRED | 0.74 | 16 | +1.71 ticks | +27pp |
| FV_TO_PRICE | 0.73 | 16 | +0.65 ticks | +2pp |
| PRICE_TO_FV | 0.42 | 16 | +0.46 ticks | −17pp |

Gate needs AUC ≥ 0.6 **and** top-decile n ≥ 50 with ≥ +0.5 ticks or ≥ +8pp hit. AUC clears for EXPIRED / FV_TO_PRICE; **n fails**.

**Verdict:** interesting separation, underpowered top-decile. No V2.1.

---

## Idea 2 — V2 × V3 confluence

### Question

Does any V3 `flow_effort` regime × V2 alert cell have positive after-cost expectancy with n ≥ 50 on holdout?

### Data constraint

V3 `regime_log_*.csv` only covers **2026-07-06..09** → **66** alerts with a regime tag. Most of the alert history has no V3 join.

### Best cell (holdout)

- Split: RTH × no-event
- Label: `formation=FLOW_LED`, side = follow
- After cost: **+0.35 ticks** (below +0.5 gate)
- n = 65 on that formation cell; regime-conditioned cells thinner

**Verdict:** underpowered + below expectancy bar. Re-run after more V3 regime history accumulates. No V2.1.

---

## Idea 3 — Magnitude classifier (not direction)

### Question

From alert open, does `|price move|` over the next 30m exceed **5 ticks**?

### Features (open-time only)

`open_abs_z`, prior-10m realized vol / range / `|z|` / Δprice / ΔFV, formation sign, V3 regime conviction / presence when joinable. Resolution holdout probabilities were **not** used as train features (holdout-only artifact would leak / empty train).

### Holdout result

| Metric | Value |
| ------ | ----- |
| AUC | **0.727** |
| Base ≥5-tick rate | 13.8% |
| Top-decile rate | 50.0% |
| Lift | **+36.2 pp** |
| Top-decile n | **16** |

Gate: AUC ≥ 0.6 **and** top-decile n sufficient for the plan’s magnitude lift claim with stable sample. AUC and lift look strong; **n=16 fails** the same sample-size discipline as Idea 1.

**Verdict:** promising signal shape, not shippable. Collect more holdout days before re-gating. No V2.1.

---

## Idea 4 — Stand-aside filter

### Question

Can we flag toxic alerts (fade after cost ≤ −1 tick, or adverse ≥ favorable) and leave a remaining pool that is positive after cost?

### Holdout result

| Metric | Value |
| ------ | ----- |
| AUC | **0.462** |
| Stand-aside n (top 30% toxic proba) | 46 |
| Keep n | 106 |
| Stand-aside after-cost | **+0.06** (wanted ≤ −0.5) |
| Keep after-cost | **−0.32** (wanted &gt; 0) |
| Base after-cost | −0.21 |

Filter ranks the wrong way / has no discrimination: the “toxic” slice is not worse after cost, and the keep pool stays negative.

**Verdict:** FAIL. No V2.1; do not change alert emission.

---

## Tier 2 / Tier 3

Queued **only after** a Tier-1 gate pass (or a deliberate re-scope with more data):

- Event-window post-release flow persistence
- Regime-conditional fair value
- Pure flow momentum
- Meta-labeling / stacking
- Cross-asset confirmation
- Adverse-aware eval (Sortino / path DD) as a hard eval layer

**Status after this readout:** stay queued. Do not start Tier 2/3 scripts until more V3 regime history and/or a larger holdout make Idea 1/3 top-decile n ≥ 50, or until an explicit new plan overrides.

## What would be needed to revisit

1. More calendar days of V2 alerts + V3 `regime_log_*` overlap.
2. Re-run Ideas 1 and 3 with the same gates; require top-decile **n ≥ 50**.
3. Only then consider stacking (Idea 8) or a display-only V2.1 on port **5054** after **explicit approval**.
