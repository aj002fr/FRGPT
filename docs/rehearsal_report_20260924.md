# Rehearsal report, 2026-09-24 (Milestone 7, partial)

Rehearsal runtime: supervisor profile `rehearsal` (writer + V3/V2.2/V2.7/flow/supply on 7053–7065 since 13:48 ET; 6053–6065 before), data under `C:\dev\mdp_rehearsal`. Production on Y: was not touched.

| Item | Result | Evidence |
|---|---|---|
| Supervisor start, restart, disable | pass | all six processes healthy; `<app>.disabled` stops an app (writer gracefully) and keeps it down; deleting it restarts the app |
| Writer kill and restart | pass after fix | run 2 kill at 13:30:46: FV/TY exact vs AccessAPI historical; 3 ZT/ZB prints 0.5 s after reconnect were lost because each root's stream is subscribed separately. Fixed (`450539f`, per-root coverage start); shadow on that code since 13:39:53 |
| Graceful writer stop | pass | `stop.request` → lease released (lease row empty) → new writer took the lease in 2 s |
| Simulated viewers | pass | 20 viewers per app, 3 min: V2.2/V2.7 p95 27 ms, 5065 p95 24 ms, V3 20 SSE clients clean; 5064 cold build fixed by warmup |
| Share loss | pass by construction | no rehearsal process holds any file on Y:, Z:, D: or a UNC path (`psutil.open_files`) |
| V3 payload (Milestone 5) | pass | snapshot 29.2 MB → 677 KB on 7053; page verified in the browser; per-print wire 1,887 → ~176 B |
| V3 stream continuity | pass after fix | before: TotalAggress stepped −9.93M → +168,882 and −9.67M → +454,979 at rebuilds (prints applied mid-rebuild kept the warming tape's base). After: 8 min, 5 revisions, 934 trade messages, 0 steps over 1M (`ops/rehearsal/stream_continuity.py`) |
| State copy | pass | V3/V2.2/V2.7 stores + banner alerts, 24 MB in 1.7 s from Y:; `banner_alerts.sqlite` quick_check ok |
| Flow DB migration | pass | today's 1,077 MB DB: SMB copy 16.7 s, `VACUUM INTO` 35.1 s, `quick_check` ok 3.2 s, 5,967,274 rows both sides. About 1 minute of the halt |
| Tailscale repoint | commands verified, not executed | `tailscale serve --bg --yes --https=<port> http://127.0.0.1:<port>` (flags from `tailscale serve --help`); executing needs cutover approval |
| Rollback | B/C mechanics tested | per-app disable marker tested; rollback A (repoint back) is the same command with 50xx ports |

Still open before the cutover gate:

- Emission p95 under 1 s through the US open (Milestone 5 target): measure 08:20–10:30 ET tomorrow on 7053 with `ops/release0/stream_probe.py`.
- Writer shadow gate: tonight's halt/reopen and tomorrow's US open on the fixed code.
- Starlette/uvicorn fan-out: deferred (see the final report); V3 runs on the threaded server, 20 SSE clients clean after the payload fix.
- The rehearsal V3 is disabled (`v3.disabled`) because it buys the same Databento backfill as production; re-enable for the US-open measurement.

Findings that affect correctness (not rehearsal failures):

- V3 Databento `ts` is `ts_recv`; AccessAPI is exchange time. The 1 ms matcher misses 30–46% of pairs, so the live-first merge holds 29–46% duplicate prints where live and backfill overlap.
- V3 drops real identical same-packet fills from the live sum (9.4% of prints); each rebuild re-counts them, so every rebuild is a `history` revision.
