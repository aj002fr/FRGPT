# Local runtime: data paths of the five served dashboards

Scope: V3 `5053`, V2.2 `5058`, V2.7 `5063`, flow history `5064`, supply weeks `5065`, plus V1 `5050` (flow writer until the canonical writer replaces it). Classified 2026-09-24 from the import closure of each app (58 project modules, `import_closure.json` in `C:\dev\mdp_backups\foundation_20260924\env\`) and a search of those files for `workspace` paths, absolute paths and environment variables.

Relative paths are under `PROJECT_ROOT/workspace/`; on C: that is `C:\dev\market_data_puller\workspace\` (git-excluded locally).

| Path | Used by | Class | Size (Y:) | Local runtime / cutover action |
|---|---|---|---|---|
| `analysis/aggression_v1_live/flow_history.sqlite` | V1 writes; 5064, 5065 read | live state | 1.08 GB | `FLOW_HISTORY_DB_PATH`; `VACUUM INTO` C: during the cutover halt, then `quick_check` (Milestone 8) |
| `analysis/aggression_v3_live/` | V3 | live state (`beta_ref.json`, logs) | 8.8 MB | copy while V3 is stopped |
| `analysis/aggression_v22_live/` | V2.2 | live state (signal log, alerts, LIFO store) | 5.6 MB | copy only while V2.2 is stopped |
| `analysis/aggression_v27_live/` | V2.7 | live state | 3.8 MB | copy only while V2.7 is stopped |
| `analysis/aggression_v1_v27_banner_alerts/banner_alerts.sqlite` | V1 (banner changes) and V2.7 (ENTRY/EXIT stamped with the latest V1 banner) | live state, two writers | 5.7 MB | **decision needed**: when V1 stops at cutover, banner rows stop and V2.7's stamp goes stale |
| `analysis/aggression_v2_live/signal_log_<date>.csv` | V2.7 same-sign re-entry rule (read-only) | read-only input written by V2 `5052` | 11.9 MB | **risk**: V2 is down and out of scope; its last write was 2026-09-23 19:05, so V2.7 has no V2 log today |
| `analysis/aggression_v2_tbbo/tbbo_feature_matrix.csv` | V2.2, V2.7 model state | read-only input | 28.4 MB (dir) | copied to C: |
| `analysis/aggression_v2_tbbo_models/` | V2.2 | read-only input | 63.2 MB | copied to C: |
| `analysis/aggression_v2_short_horizon_fv/` | V2.7 (B1_W10 nowcast) | read-only input | 197.2 MB | copied to C: |
| `analysis/_totalaggress_cache/` | V3, V1 (Databento trade parquet, per UTC day) | cache; misses cost Databento money | 102.3 MB | copied to C:; completed days only, the current day is always fetched |
| `wenn/*.zip` | `fetch_ohlcv_price` fallback | read-only input | 11.3 MB | copied to C: |
| `config/keys.env` | all (API keys, `ECON_EVENTS_PG_DSN`) | secret | small | copied to C:, gitignored |
| `Z:\Aarsh\Data\AccessAPI\.env` | Databento live backup key (`databento_live_backup.py`) | secret on another share | small | **risk**: Z: must be up when the backup starts; put `DATABENTO_API_KEY` in the environment or C: `keys.env` |
| Postgres `economic_events` on littleboy | 5064, 5065 | read-only input | n/a | done in R0.4 (in-memory cache, last-good file) |
| Postgres 16 OHLCV on this PC, `PG_HOST = 100.82.143.79` in `config/settings.py` | 5065 price series | read-only input | n/a | **risk**: reached through this PC's own Tailscale IP, so it fails when Tailscale is down; use `127.0.0.1` in the local runtime |
| `%LOCALAPPDATA%\market_data_puller\logs\`, `...\cache\` | launchers, events cache | output / cache | small | already local |

Not used by the served apps: `workspace/economic_events.db` (legacy), `config/settings.py` `DistroDashboard` path.

Copies of read-only inputs were made with `robocopy /IPG:2` (throttled) and never write to Y:.
