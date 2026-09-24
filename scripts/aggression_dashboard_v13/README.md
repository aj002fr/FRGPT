# Aggression V1.3 — per-print TotalAggress

Isolated copy of current V1 (5050) with panel 1 cumsum on **every trade print**.

Existing **V1.2** on 5056 is the RANGE-caution monitor and is not this page.

- Port: `5055`
- Writes: `workspace/analysis/aggression_v13_live/` only
- Label: `PROVISIONAL / RESEARCH-ONLY / TICK-TOTALAGGRESS / NON-EXECUTABLE`
- Does not write V1 `flow_history.sqlite`, V1/V2.7 banner alerts, or any other `*_live/` store
- Manual start only. Do not add a watchdog. Do not edit live V1.

```bat
Y:\Aastha\SOD\run_aggression_monitor_v13.bat
```

Panel 1: `TotalAggress(print) = cumsum(sign * size * $DV01)`, rebased at the first session print. Panels 2–5 and the dominance banner stay on completed 1-minute bars. Display lookback still clips the chart; the running sum is session-absolute. If a lookback window has more than 80,000 prints, only the most recent 80,000 points are sent to Plotly.
