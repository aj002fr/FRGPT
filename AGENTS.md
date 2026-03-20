# AGENTS.md

## Cursor Cloud specific instructions

### Overview

Pure Python MCP system for market data extraction and multi-agent orchestration. See `README.md` for full documentation and `memory-bank/` for the knowledge base.

### Services

| Service | Description | How to run |
|---------|-------------|------------|
| SQL Market Data Pipeline | Queries local SQLite `market_data.db` | `python main.py` or `python scripts/test_queries.py --query N` |
| Polymarket Pipeline | Searches Polymarket prediction markets via public API | `python main.py` or `python scripts/test_polymarket_simple.py --custom "topic"` |
| Orchestrator | GPT-4-powered multi-agent coordination | `python scripts/test_orchestrator.py --query N` (requires `OPENAI_API_KEY`) |

### Key commands

- **Run demo**: `python main.py`
- **Run tests**: `python -m pytest tests/e2e/test_marketdata_e2e.py tests/e2e/test_orchestrator_e2e.py -v`
- **Run specific query**: `python scripts/test_queries.py --query 1`
- **List queries**: `python scripts/test_queries.py --list`
- **View logs**: `python scripts/show_logs.py`

### Non-obvious caveats

- **`market_data.db` must be seeded**: The repo `.gitignore`s `*.db` files. The `market_data.db` SQLite database must be created and populated with sample data for the SQL pipeline and related tests to work. It needs a `market_data` table with columns: `id`, `symbol`, `bid`, `ask`, `price`, `bid_quantity`, `offer_quantity`, `timestamp`, `file_date`, `data_source`, `is_valid`, `created_at`. See `src/servers/marketdata/schema.py` for the full schema.
- **`polymarket_markets.db` setup**: Run `python scripts/setup_polymarket_db.py` to create the Polymarket history database (empty schema is sufficient).
- **No dedicated linter**: The project has no configured linter (no ruff, flake8, mypy, etc.). Use `python -m py_compile <file>` for basic syntax checks.
- **`test_polymarket_e2e.py` has a broken import**: It references `src.servers.polymarket.expand_keywords` which does not exist. Skip this test file when running the suite.
- **OpenAI API key is optional**: The orchestrator and Polymarket reasoning pipeline use GPT-4 but have fallback logic for when `OPENAI_API_KEY` is absent. Set it in `config/keys.env` as `OPENAI_API_KEY=sk-...` to enable AI features.
- **PATH for pip-installed tools**: Scripts installed by pip (pytest, etc.) go to `~/.local/bin`. Ensure `export PATH="$HOME/.local/bin:$PATH"` is set.
- **`clean_workspace` fixture**: E2E tests use a `clean_workspace` fixture that deletes and recreates the `workspace/` directory. This also deletes `workspace/economic_events.db`. If you need that DB after tests, re-run `python scripts/setup_eventdata_db.py`.
