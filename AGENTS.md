# AGENTS.md

## Cursor Cloud specific instructions

### Overview

Pure Python MCP system for market data analysis and prediction market intelligence. Three main pipelines:
1. **SQL Market Data** — queries local SQLite `market_data.db` for options data
2. **Polymarket Intelligence** — AI-powered prediction market analysis (requires `OPENAI_API_KEY`)
3. **Direct Polymarket Search** — API-only search (no AI key needed, but hits public Polymarket API)

### Key commands

See `README.md` for full list. Quick reference:
- `python main.py` — run unified demo (all 3 pipelines)
- `python -m pytest tests/e2e/ -v` — run E2E tests
- `python scripts/test_queries.py --query 1` — run individual SQL query
- `python scripts/show_logs.py` — view run logs

### Database setup

The `market_data.db` SQLite file must exist at the project root with a `market_data` table. If it's missing, create it with sample data matching the schema in `src/servers/marketdata/schema.py` (columns: `id, symbol, bid, ask, price, bid_quantity, offer_quantity, timestamp, file_date, data_source, is_valid, created_at`). Also run `python scripts/setup_polymarket_db.py` to initialize the `polymarket_markets.db`.

### Known issues (pre-existing)

- **Polymarket API UTF-8 errors**: `search_polymarket_markets` can fail with `'utf-8' codec can't decode byte` errors from the Gamma API response. This is a code-level issue in `src/servers/polymarket/search_markets.py`.
- **Test `test_full_pipeline`**: Fails intermittently due to two runs getting the same second-precision `run_id` when they execute within the same second.
- **Test `test_polymarket_e2e.py`**: Cannot be collected — imports `src.servers.polymarket.expand_keywords` which does not exist in the codebase.
- **Orchestrator tests requiring AI**: Tests like `test_price_range_query_single_task` and `test_polymarket_only_query` require `OPENAI_API_KEY` set in `config/keys.env` to fully pass; without it, fallback planning runs which may produce different task counts.
- **Attribute mismatch**: `test_agent_initialization` and `test_validator_initialization` in orchestrator tests expect a `taskmaster` attribute that doesn't exist on the objects.

### Environment notes

- Python 3.12 is available as `python3`. Packages install to `~/.local/` (user site).
- No linter is configured in this project.
- The `OPENAI_API_KEY` is optional — core SQL pipeline and most tests work without it.
