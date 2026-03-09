# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

Pure Python MCP system for market data analysis with multi-agent orchestration. See `README.md` for full docs and `.cursorrules` for code conventions.

### Running commands

Use `python3` (not `python`) — the VM has Python 3.12 at `/usr/bin/python3`. User-installed binaries (pytest, openai CLI) are in `~/.local/bin`, so prepend `PATH="$HOME/.local/bin:$PATH"` or use `python3 -m pytest`.

### Key commands

- **Tests:** `python3 -m pytest tests/ -v --ignore=tests/e2e/test_polymarket_e2e.py`
- **Demo:** `python3 main.py`
- **SQL queries:** `python3 scripts/test_queries.py --list` / `--query N`
- **View logs:** `python3 scripts/show_logs.py`

### Databases

- `market_data.db` — SQLite database at project root, required for the SQL pipeline. Not checked into the repo; must be seeded before running market data tests/demo. Schema: `market_data` table with columns `id, symbol, bid, ask, price, bid_quantity, offer_quantity, timestamp, file_date, data_source, is_valid, created_at`.
- `polymarket_markets.db` — Created by `python3 scripts/setup_polymarket_db.py`. Stores Polymarket query history.

### Known pre-existing test issues

- `tests/e2e/test_polymarket_e2e.py` — Fails to collect due to missing `src.servers.polymarket.expand_keywords` module. Must be excluded with `--ignore`.
- `test_full_pipeline` — Intermittent assertion on log count (expects 2 logs, sometimes only 1 written due to same-second timestamp collision in log filenames).
- `test_agent_initialization` / `test_validator_initialization` — Tests reference `.taskmaster` attribute that no longer exists on `OrchestratorAgent` / `AnswerValidator`.
- Orchestrator tests requiring AI planning fail gracefully without `OPENAI_API_KEY`.
- Polymarket API calls may hit a UTF-8 decoding error in `search_markets.py` — this is a pre-existing code issue.

### Optional API keys

Place in `config/keys.env` (format: `KEY_NAME=value`):
- `OPENAI_API_KEY` — Enables GPT-4 task planning, reasoning agent, LLM relevance scoring.
- `TRADING_ECONOMICS_API_KEY` — Enables economic calendar data features.
