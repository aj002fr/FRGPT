# Aggression Dashboard V2.6

Isolated, manual-only research copy of V2.2. It keeps the existing V2
WATCH/DISLOCATION panel and **LIFO FRIENDLY** context, adds a reduced one-minute
C018 fade lean, and collects human grades for LIFO/C018 episodes.

Fixed status:

`PROVISIONAL / RESEARCH-ONLY / NON-EXECUTABLE`

## Start manually

```powershell
py -3 -m scripts.aggression_dashboard_v26.app --host 127.0.0.1 --port 5062
```

- Port: `5062` (browser-safe; 5061 is blocked by Chromium as SIP-TLS)
- Store: `workspace/analysis/aggression_v26_live/`
- No SOD, Tailscale, Telegram, or automated execution integration
- A file change requires a manual V2.6 restart. Do not restart V2.2.

## Reduced C018 rule

This is **not** the offline MBP-1 C018 cycle.

1. Ignore the newest/forming one-minute bar.
2. For each completed candidate bar, calculate high, low, and range from the
   30-bar completed window ending at that bar.
3. Edge-trigger when the candidate newly reaches at least 8 ticks above the
   prior low or below the prior high.
4. Require prior range of at least `10.5` ticks. This value is hard-coded from
   the discovery-prefix 67th percentile of
   `C018_vol_regime/episodes_c018.csv::range_ticks`; it is not retuned live.
5. Display `FADE DOWN` after an up move or `FADE UP` after a down move.
6. Decluster episode locks by 15 minutes.

C018 rows retain:

`PROVISIONAL / C018-FADE-LEAN / NOT OFFLINE C018 / NON-EXECUTABLE`

The fade lean is display-only. It is not a ticket, fill, stop, target, or PnL
claim.

## LIFO FRIENDLY

The V2.2 H5 rule remains unchanged: `|residual_z| >= 1` and trade intensity at
or below its session expanding 33rd percentile. The visible alert text remains
**LIFO FRIENDLY**. Its existing RANGE/TREND scoring is observational only.

## Human feedback

After a new LIFO or C018 episode locks, a small non-blocking corner prompt asks:

`Was this alert correct?`

`Yes`, `No`, and `Skip` post to `/api/feedback`. Every submission appends to
`feedback.jsonl`; the episode JSON stores the latest value. Re-grading is
allowed and the latest grade wins. Skip leaves `human_correct` unset, so it
continues to count as pending, but the same browser prompt does not immediately
repeat.

The `/api/live` payload reports today-in-ET counts for each alert kind:
issued, pending grade, graded, marked correct, marked wrong, and hit rate among
graded. “Right” means the human grade only.

## Isolation

V2.6 writes only below `workspace/analysis/aggression_v26_live/`. It does not
edit or write stores for V1, V1.2, V2, V2.1, V2.2, V2.3, V2.4, or V3. The
shared V2 feature/model definitions and V2.2 failover class are imported as
stateless definitions; V2.6 owns its buffer, model, and stores.
