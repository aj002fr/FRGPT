# V1 Fair-Value Dispersion Source Attribution

Offline diagnostic: when live TY is far from aggress-implied fair value, what
built that gap?

Does **not** change live V1 or V1.2.

Artifacts: `workspace/analysis/aggression_v1_dispersion_source_attribution/`  
Script: `scripts/aggression_v1_dispersion_source_attribution_research.py`  
Run date: 2026-07-20

## Identity

\[
\mathrm{FV}_t = P_{\mathrm{open}}+\alpha+\beta A_t,\quad R_t=P_t-\mathrm{FV}_t,\quad
\Delta R_t \approx \Delta P_t - \beta\Delta A_t
\]

Causal prior-day \(\beta\). Implied passive only — not resting-order identity.

## Source labels (lookback \(w\in\{1,5,15\}\), dominance 1.5×)

| Source | Meaning |
| --- | --- |
| **PRICE_LED** | Scaled \(\|\Delta P\|\) dominates scaled \(\|\beta\Delta A\|\); residual moves with price |
| **FLOW_UNFOLLOWED** | Scaled aggress contrib dominates (price not following flow / absorption-style) |
| **MIXED_MISMATCH** | Both material; neither dominates or signs fight |
| **QUIET_BUILD** | Both small in-window (residual drift without a clear driver) |
| **BETA_STALE** | Overlay: \(\|z_{\mathrm{causal}}-z_{\mathrm{live}}\|\) high while \(\|z\|\ge1\) |

Universe: non-event, \(\|z\|\ge1\) or \(\|z\|\ge1.5\).

## Headline mix (`w=15`, `|z|≥1.5`, n=21,723)

| Source | Minute share | Share of Σ\|ΔR\| | β-stale rate |
| --- | ---: | ---: | ---: |
| **FLOW_UNFOLLOWED** | **43.1%** | 31.4% | **38.2%** |
| PRICE_LED | 27.3% | **38.5%** | 26.0% |
| MIXED_MISMATCH | 25.2% | 29.4% | 32.2% |
| QUIET_BUILD | 4.4% | 0.7% | 32.2% |

Same pattern at `|z|≥1` (n=33,516): FLOW most frequent (~43%); PRICE_LED carries more residual-change mass (~38%).

### Window sensitivity (`|z|≥1.5` minute share)

| Lookback | PRICE_LED | FLOW_UNFOLLOWED | MIXED |
| ---: | ---: | ---: | ---: |
| 1m | 45.4% | 42.2% | 1.7% |
| 5m | 19.2% | **57.3%** | 17.4% |
| 15m | 27.3% | 43.1% | 25.2% |

Short windows split nearly evenly price vs flow; **5–15m** windows show more **flow-without-follow-through** as the common builder of large gaps.

## Co-occurrence (`w=15`, `|z|≥1.5`)

| Group | n | trade_intensity med | widening_15m rate | β-stale |
| --- | ---: | ---: | ---: | ---: |
| All non-event | 57,125 | 134 | 20.8% | 18.6% |
| LARGE \|z\|≥1.5 | 21,723 | 184 | 20.7% | 33.1% |
| PRICE_LED | 5,940 | 168 | **33.0%** | 26.0% |
| FLOW_UNFOLLOWED | 9,360 | 194 | 11.5% | **38.2%** |
| MIXED_MISMATCH | 5,476 | 200 | 25.3% | 32.2% |
| QUIET_BUILD | 947 | 105 | 7.7% | 32.2% |

PRICE_LED large gaps widen more often; FLOW_UNFOLLOWED large gaps sit in higher intensity / more β-stale regimes and widen less.

## Forward RANGE/TREND (diagnostic only, validation, h=30 frozen)

Among `|z|≥1.5` episodes (`w=15`): mild **anti-TREND** lifts (~−3 to −5pp) across sources; RANGE lifts small (+0.8 to +2pp). **Not a shipping claim** (no restrictive gate; aligns with prior dispersion STOP).

## Plain-language verdict

Large fair-value dispersion is **not one thing**:

1. **Most common builder (by minute count):** aggress flow that price does not follow (`FLOW_UNFOLLOWED`) — especially on 5–15m lookbacks.
2. **Largest residual moves (by \|ΔR\| mass):** more often **price moving away from what aggress implies** (`PRICE_LED`).
3. **β-stale** (prior-day map disagrees with same-day fit) overlays ~26–38% of large minutes, highest under FLOW_UNFOLLOWED.
4. Quiet residual drift is rare as a large-gap builder (~4%).

This is **attribution / description**, not a trade signal. Do **not** add source badges to V1.2 without a separate gated study and approval.

## Guardrails

- Offline only; implied-passive language
- Co-occurrence ≠ causation beyond \(\Delta R \approx \Delta P - \beta\Delta A\)
- Forward table is diagnostic only
