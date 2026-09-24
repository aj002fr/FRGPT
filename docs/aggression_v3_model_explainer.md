# Aggression V3 Model Explainer

V3 is a copy of V2 with an added MBP-10 market-depth layer. The V2 signal is unchanged: WATCH/DISLOCATION still comes from prior-blended causal residual z, and a signal resolves when `abs(z) < 1.0`.

## What The Depth Layer Adds

Depth is used as market context, not as a fair-value model input.

### Gamma Bid Risk Score

The score runs from `0` to `5`. It adds one point for each stressed condition:

- low time-adjusted depth (`depth_z` bottom 20%)
- 30-minute depth depletion
- high cancel/add ratio
- high trailing realized volatility
- heavy sweep activity

Research result: score `0 -> 5` moved future 30-minute realized vol from about `0.0081` to `0.0159`, and average absolute 30-minute move from about `0.0249` to `0.0869` price points. The score-5 bucket was small, so treat it as a warning regime rather than a standalone trade signal.

### Depth Regime

`Normal`, `Thin`, or `Disappearing`. Raw low depth alone was not enough in research; the useful read is low depth plus depletion/cancels/sweeps/vol.

### Book Pressure

Compares quote OFI to trade aggression:

- `agree`: book pressure and trades point the same way.
- `diverge`: trades and book updates disagree.
- `book_only`: book pressure with little trade confirmation.
- `trade_only`: trade pressure without book confirmation.

Research result: agreement was better than divergence, but still not a standalone signal.

### Sweep / Absorption

Sweep-heavy minutes are flagged, but the research showed sweeps often absorb rather than continue. Do not chase sweeps blindly.

### Top-of-Book Concentration

High top-of-book share means more liquidity is concentrated near the touch. Research showed this coincided with larger future moves, which may indicate a fragile book.

## How To Use V3

- Use the unchanged V2 residual-z signal for rich/cheap direction.
- Use the depth layer to judge market conditions around that signal.
- If Gamma Bid Risk Score is high, expect larger moves and more adverse excursion.
- If a dislocation fires in thin/disappearing depth, treat depth as a caution flag, not a confidence booster.

## Data Collection

V3 writes a daily `depth_log_YYYYMMDD.csv` under `workspace/analysis/aggression_v3_live/`. This captures every computed minute-level depth feature plus price/fair-value/z fields where available. Every alert episode is also tagged with depth regime, score, and book pressure at open.

When enough live history has accumulated, rerun the depth research on the live-collected data before promoting depth into model features or alert filters.
