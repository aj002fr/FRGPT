# V1 Dispersion — ATR Range vs 8bps Non-Event Breakout

Exploratory redefinition of forward outcomes for the causal V1 residual caution
state (`|z|≥1` and widening 15m). Does **not** change V1.2 live scoring.

## Definitions

| Label | Rule |
| --- | --- |
| **BREAKOUT** | 30m \|net\| ≥ **8 yield bps** (= **16 TY ticks** at 2 ticks/bp) AND not event-near (±30m) |
| **CONTINUED_RANGE** | 30m high–low ≤ **k × prior-day RTH ATR** AND not BREAKOUT |
| **OTHER** | neither |

Primary filtered set = non-event bars labeled BREAKOUT or CONTINUED_RANGE.

## Sample mix (non-event)

| Split | BREAKOUT | CONTINUED_RANGE (k=1) | OTHER | Median prior ATR |
| --- | ---: | ---: | ---: | ---: |
| Discovery | 78 (0.23%) | 91.8% | 4.1% | 18 ticks |
| Validation | 55 (0.42%) | 94.3% | 1.0% | 15 ticks |

Breakouts are rare. With `k=1.0` (full prior-day ATR on a 30m window), almost everything is CONTINUED_RANGE — the filter is too loose.

## Headline: large+widening caution

### On filtered BREAKOUT ∪ CONTINUED_RANGE (`k=1`)

| Split | n | Breakout rate | Baseline | Lift | Range rate | Baseline | Lift |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Discovery | 1352 | 0.37% | 0.25% | +0.12pp | 99.63% | 99.75% | −0.12pp |
| Validation | 571 | 0.53% | 0.44% | +0.09pp | 99.47% | 99.56% | −0.09pp |

CIs cross zero; no useful separation.

### ATR sensitivity (validation, large+widening)

| ATR rule | Caution n | Breakout rate | Baseline | Lift |
| --- | ---: | ---: | ---: | ---: |
| 1.0× daily | 571 | 0.53% | 0.44% | +0.09pp |
| 0.5× daily | 525 | 0.76% | 0.48% | +0.28pp |
| 0.25× daily | 382 | 1.05% | 0.70% | +0.34pp |
| Horizon-scaled √(30/420) | 399 | 1.00% | 0.67% | +0.33pp |

Tighter ATR makes OTHER larger and breakout slightly less rare inside the filtered set, but lifts stay **<0.4pp** and economically trivial.

## Verdict

Under ATR + 8bps non-event definitions, the large+widening residual state does **not** predict breakout vs continued range in a meaningful way. It mostly sits in ATR-range minutes because true 8bps non-event 30m breakouts are scarce.

Do **not** retune V1.2 outcome scoring to this definition based on this run. If revisited, pre-register a horizon-scaled ATR (or rolling 30m ATR) and a rarer breakout threshold, then hold out FINAL.

Artifacts: `workspace/analysis/aggression_v1_dispersion_atr_breakout/`
