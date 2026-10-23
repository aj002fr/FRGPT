# V1 banner / V2.7 alert log

Append-only SQLite of live V1 banner **changes** and V2.7 paper **ENTRY/EXIT** events.

| Item | Value |
| --- | --- |
| Package | `scripts/aggression_v1_v27_banner_alerts/` |
| Database | `workspace/analysis/aggression_v1_v27_banner_alerts/banner_alerts.sqlite` |
| V1 write | live 5050 only, on banner **state change** |
| V2.7 write | 5063 `AlertStore` on open and close |
| Label | V1 banner stays `DESCRIPTIVE / IMPLIED PASSIVE / NON-DIRECTIONAL`. V2.7 stays research-only. |

Does not change the V1 banner, V2.7 clock, SOD, Tailscale, or Telegram. Existing 5050 / 5063 processes need a **manual restart** for the hooks to run.

## Tables

- `v1_banner_changes` — one row when the 5050 banner state changes (`AGGRESS_DOMINANT`, `IMPLIED_PASSIVE_DOMINANT`, `BALANCED`, `QUIET`, `WARMING`, `UNAVAILABLE`)
- `v27_alert_events` — one row per V2.7 ENTRY and EXIT, with `v1_state_at_alert` and `v1_banner_id` from the latest banner change at or before the alert time

## Query

```sql
SELECT a.ts_et, a.kind, a.direction, a.exit_reason, a.dir_pnl_trade_ticks,
       a.v1_state_at_alert, b.prev_state, b.ts_et AS banner_changed_et
FROM v27_alert_events a
LEFT JOIN v1_banner_changes b ON b.id = a.v1_banner_id
ORDER BY a.ts_utc;
```
