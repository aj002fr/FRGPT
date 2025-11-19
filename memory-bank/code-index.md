# Code Index

## File Bus (`src/bus/`)

### file_bus.py
Atomic file operations for inter-agent communication. Provides `write_atomic()` using temp file + rename pattern, `read_json()` for safe reading, and `ensure_dir()` for directory creation. All file writes are atomic to prevent corruption.

### manifest.py
Manifest management for incremental filename generation. `Manifest` class manages `meta.json` per agent workspace, tracking `next_id`, `last_updated`, and `total_runs`. Provides `increment()` for atomic ID allocation and `get_next_filepath()` for generating output paths like `000001.json`.

### schema.py
Output schema validation and templates. `OutputSchema` validates structure (data + metadata). `validate_market_data()` checks market-specific requirements. `create_output_template()` generates standardized output format.

## MCP Infrastructure (`src/mcp/`)

### client.py
Simple MCP client for code-mode tool execution. `MCPClient` discovers and calls tools as direct Python functions (no network protocol). Provides `call_tool()` for execution and `discover_tools()` for scanning servers.

### discovery.py
Tool discovery via decorator registration. `@register_tool` decorator registers tools in global registry. `discover_tools()` scans `src/servers/` and imports modules to trigger registration. `get_tool()` retrieves function by name.

### taskmaster_client.py
AI-powered task planning and validation client (renamed from original taskmaster concept). `TaskPlannerClient` uses OpenAI/Anthropic APIs for intelligent task decomposition and answer validation. Provides `plan_task()` for breaking queries into subtasks and `validate_answer()` for completeness checking. Falls back to rule-based planning if no AI API available. Supports both OpenAI GPT-4 and Anthropic Claude models.

## Market Data Tool (`src/servers/marketdata/`)

### run_query.py
SQL execution tool with progressive disclosure. `@register_tool` decorated `run_query()` function executes parameterized queries on SQLite. Returns full dataset + small sample for logging. Supports templates (by_symbol, by_date, etc.) with column whitelist.

### schema.py
Database schema and security. Defines `ALLOWED_COLUMNS` whitelist, `QUERY_TEMPLATES` for parameterized queries, and validation functions. `build_column_list()` creates safe SQL fragments.

## Polymarket Tool (`src/servers/polymarket/`)

### schema.py
Polymarket tool schema and constants. Defines `POLYMARKET_API_BASE_URL` (CLOB API), `POLYMARKET_GAMMA_BASE_URL`, result limits, market status constants, database table name, and validation functions. Provides `format_market_result()` for standardizing API responses, `parse_probability_from_price()`, `calculate_avg_probability()`, and `calculate_total_volume()` for market data processing.

### get_price_history.py
Historical price data tool using Polymarket CLOB API. `fetch_price_history_from_polymarket()` calls the `prices-history` endpoint to get time-series price data. `find_price_at_target_time()` uses weighted averaging to find prices at specific timestamps. `@register_tool` decorated `get_market_price_history()` returns prices for a specific date. `get_market_price_range()` fetches price trends over date ranges. Uses stdlib urllib only (no web3 or blockchain dependencies).

### search_markets.py
Polymarket market search tool built on the Gamma API. `@register_tool` decorated `search_polymarket_markets()` can fetch recent/popular markets, run keyword filtering, and (optionally) apply LLM-based relevance scoring. Validates market data and stores results in `polymarket_markets.db` with prices, volume, liquidity, and outcomes. The main Polymarket agent now uses a simpler `/markets` volume-sorted flow instead of the full hybrid LLM pipeline.

### llm_relevance_scorer.py
LLM-powered relevance scoring utilities for Polymarket search. Provides `score_market_relevance_batch()` for batch GPT-4 scoring of markets (0-10 scale with reasons), `score_market_relevance_streaming()` for individual scoring, and `hybrid_search()` combining fast keyword filtering with accurate LLM re-ranking. Auto-loads OpenAI API key from `config/keys.env` or environment. Gracefully falls back to keyword-only if API key unavailable or LLM calls fail. Always returns at least 1 result if any markets exist (multi-layer fallback: LLM → keyword → top by volume).

### get_history.py
Query history retrieval tool for Polymarket. `@register_tool` decorated `get_polymarket_history()` function queries `polymarket_markets.db` table `prediction_queries` by session_id, date range, or limit. Returns historical queries with market results (parsed JSON including relevance scores if LLM was used).

## Market Data Agent (`src/agents/market_data_agent/`)

### run.py
Producer agent main logic. `MarketDataAgent` class orchestrates: input validation, MCP tool calling, manifest management, atomic output writing, and run logging. `run()` method returns output path. Each execution writes `out/{id}.json` and `logs/{timestamp}.json`.

### config.py
Agent configuration constants. Defines `AGENT_NAME`, workspace paths, `DEFAULT_COLUMNS`, `AVAILABLE_TEMPLATES`, `REQUIRED_PARAMS` per template, and `MAX_ROWS` limit.

### prompt.md
Agent instructions and examples. Documents purpose, inputs, outputs, process flow, and example usage patterns.

## Consumer Agent (`src/agents/consumer_agent/`)

### run.py
Consumer agent that processes producer output. `ConsumerAgent` reads file from producer, validates schema, computes summary statistics (bid/ask min/max/avg), and emits derived artifact. Demonstrates inter-agent communication.

### config.py
Consumer configuration constants. Defines `AGENT_NAME`, workspace paths, output directories.

## Polymarket Agent (`src/agents/polymarket_agent/`)

### run.py
Unified Polymarket agent implementing a simple, API-only search. `PolymarketAgent` calls the Gamma `/markets` endpoint to fetch active markets sorted by volume, filters them by keyword relevance and high volume, validates prices, and writes standardized outputs to the file bus (`out/{id}.json`) and logs (`logs/{timestamp}.json`) under `workspace/agents/polymarket-agent/`. The same simple behavior is used for all callers (scripts, orchestrator, tests).

### config.py
Agent configuration constants. Defines `AGENT_NAME`, workspace path helper, output/log directory names, `LOW_VOLUME_THRESHOLD`, `DEFAULT_LOOKBACK_DAYS`, `MAX_MARKETS_TO_RETURN`, and `MAX_QUERY_LENGTH` for safe parsing.

### prompt.md
Agent instructions and examples. Documents purpose, inputs (query, session_id, limit), unified reasoning flow (current + historical), volume flagging, and integration with Polymarket tools and `polymarket_markets.db`.

## Orchestrator Agent (`src/agents/orchestrator_agent/`) - Two-Stage Planner

### run.py
Meta-agent with **Two-Stage Planner Architecture**. `OrchestratorAgent` implements 6-step workflow: (1) Planner 1: AI task decomposition with dependency analysis, (2) Planner 2: lazy tool loading per dependency path, (3) Coder: async Python script generation with DB writes, (4) Workers: parallel/sequential execution with dual storage (SQLite + file bus), (5) Runner: DB-based consolidation with AI validation, (6) file bus output. Key features: context-efficient tool loading, full DAG support, dependency-aware execution, queryable task history. Replaces single-stage planning with path-isolated tool discovery.

### config.py
Orchestrator configuration with Two-Stage Planner settings. Defines `AGENT_CAPABILITIES` registry (market_data_agent, polymarket_agent), database paths (`get_db_path()` for orchestrator_results.db), execution timeouts including `DEPENDENCY_WAIT_TIMEOUT`, and directory structure (including DB_DIR). Provides workspace and database configuration for dual storage system.

### planner_stage1.py
Stage 1 Planner - Task decomposition and dependency analysis. `PlannerStage1` uses `TaskPlannerClient` for AI-powered task breakdown, `TaskMapper` for agent assignment, and `DependencyAnalyzer` for DAG extraction. `plan()` returns subtasks with dependencies, parallel execution groups, and dependency paths. Normalizes task IDs, validates for cycles, calculates execution depth. Outputs structured plan with mappable/unmappable task separation.

### planner_stage2.py
Stage 2 Planner - Tool discovery per dependency path. `PlannerStage2` loads only relevant tools for agents in specific path using `ToolLoader` for lazy loading. `discover_tools_and_params()` extracts tool parameters from task descriptions, builds execution plan with tool-specific params. Context isolation: each path sees only its own tools. Factory function `create_planners_for_paths()` creates one Planner 2 instance per dependency path. Supports agent-specific parameter extraction for `market_data_agent` (SQL) and `polymarket_agent` (search + reasoning analysis).

### dependency_analyzer.py
DAG analysis and path extraction. `DependencyAnalyzer` builds dependency graphs, detects cycles via DFS, extracts execution paths from leaf to root tasks, and computes parallel execution groups via topological sort. `analyze()` returns dependency paths, parallel groups, max depth, and cycle detection. `_extract_paths()` traces all paths from leaves to roots. `_compute_parallel_groups()` identifies tasks that can run simultaneously. Supports transitive dependency queries and topological ordering.

### tool_loader.py
Lazy tool loading for context isolation. `ToolLoader` loads tools only for specific agents rather than all upfront. Maps agents to tools (AGENT_TOOL_MAP), discovers tools from servers/ directory on demand, caches loaded tools. `load_tools_for_agents()` loads minimal tool set per dependency path. `get_tool_function()` retrieves callable, `get_tool_metadata()` returns schema. Reduces context size by loading only needed tools per path.

### coder.py
Python script generation with DB persistence. `Coder` generates async execution scripts that call MCP tools and write to both SQLite and file bus. `generate()` creates task functions with WorkersDB writes (start_task, complete_task, store_task_output), dependency-aware main() function with topological sort, and full error handling. Generated scripts include DB connection management, output reading from file bus, and result collection. Supports parallel and sequential execution based on dependency structure.

### workers_db.py
SQLite database for worker task outputs. `WorkersDB` manages two tables: worker_runs (execution metadata) and task_outputs (result data). Provides `start_task()`, `complete_task()`, `store_task_output()` for task lifecycle, `get_task_output()` and `get_all_task_outputs()` for retrieval, `are_dependencies_complete()` for dependency resolution, and `get_run_summary()` for analytics. Enables dual storage: DB for metadata and queryable results, file bus for large datasets. Context manager support for automatic connection cleanup.

### worker_executor.py
Task execution with dependency handling. `WorkerExecutor` executes generated scripts, manages DB persistence via WorkersDB, handles dependency waiting with timeout. `execute_all()` runs multiple path scripts, `_execute_script()` executes individual script via exec() in isolated namespace with asyncio.run(). `wait_for_dependencies()` polls DB for dependency completion. `get_dependency_outputs()` retrieves dependency results for downstream tasks. Helper `save_script()` writes generated code to filesystem for debugging.

### runner.py
Final result consolidation from database. `Runner` queries WorkersDB for all task outputs by run_id, merges data by agent type (market_data, polymarket, reasoning), generates natural language answer with statistics, and optionally validates via `AnswerValidator`. `consolidate()` builds unified result with merged data, summary statistics, and validation report. `_merge_task_data()` combines outputs from multiple workers, `_calculate_summary_stats()` computes aggregates (price ranges, volumes, counts), `_generate_answer()` creates human-readable summary. Replaces ResultConsolidator with DB-driven approach.

### task_mapper.py
Task-to-agent mapping with parameter extraction. `TaskMapper` maps task descriptions to agents via keyword scoring, extracts agent-specific parameters using regex and heuristics. Enhanced `_extract_market_data_params()` supports SQL templates, date ranges, price filters, ORDER BY, and LIMIT. `_extract_polymarket_params()` parses search queries and limits. `_extract_reasoning_params()` handles analysis tasks. Returns mapped tasks with agent names and extracted parameters for Planner 2.

### code_generator.py (deprecated)
Original single-stage code generator. Logic migrated to `coder.py` with enhanced DB persistence. Kept for reference.

### consolidator.py (deprecated)
Original consolidator reading from file paths. Logic migrated to `runner.py` with DB-based retrieval. Kept for reference.

### validator.py
AI-powered answer validation (unchanged). Multi-layer validation with AI and local heuristics for completeness checking. Used by Runner for final validation step.

### prompt.md
Complete orchestrator documentation with Two-Stage Planner architecture, workflow diagrams, usage examples, and API reference. Documents new components (Planner 1/2, Coder, WorkersDB, Runner), dependency paths, tool loading strategies, and dual storage system.

## Core (`src/core/`)

### logging_config.py
Centralized logging setup. `setup_logging()` configures file + console handlers. Creates `logs/market_data_puller_{date}.log`. `get_logger()` returns logger for module. Logs written to project root `logs/` directory.

## Configuration (`config/`)

### settings.py
Application-wide settings. Defines `PROJECT_ROOT`, `DATABASE_PATH`, `WORKSPACE_PATH`, `ALLOWED_COLUMNS`, `MAX_ROWS_PER_QUERY`. No external API keys required - stdlib only.

## Tests (`tests/`)

### e2e/test_marketdata_e2e.py
End-to-end test suite with 7 test classes:
1. `test_producer_correctness` - SQL, schema, manifest, logs
2. `test_producer_completeness` - columns, filters
3. `test_consumer` - schema validation, statistics
4. `test_manifest_increments` - 000001, 000002, 000003
5. `test_run_logs` - log format, required fields
6. `test_validation_errors` - input validation
7. `test_full_pipeline` - producer → consumer flow

### e2e/test_predictions_e2e.py
End-to-end test suite for predictive markets agent with 7 test classes:
1. `TestPredictiveMarketsAgent` - initialization, session ID generation
2. `TestSearchExecution` - search execution, output validation, input errors
3. `TestDatabaseIntegration` - query storage in prediction_queries table
4. `TestMultiUser` - concurrent sessions, session isolation
5. `TestManifestIncrement` - incremental filenames (000001, 000002, 000003)
6. `TestRunLogs` - successful and failed run logs
7. `TestHistoryRetrieval` - get_query_history tool integration

### e2e/test_polymarket_e2e.py
End-to-end test suite for Polymarket agent with 7 test classes:
1. `TestPolymarketAgent` - initialization, session ID generation, uniqueness
2. `TestKeywordExpansion` - LLM keyword expansion, fallback extraction, deduplication
3. `TestMarketSearch` - market filtering by keywords, phrase matching, scoring
4. `TestDatabaseIntegration` - query history storage in polymarket_markets.db
5. `TestMultiUser` - session isolation for concurrent users
6. `TestManifestIncrement` - incremental filenames (000001, 000002, 000003)
7. `TestRunLogs` - validation errors, run log creation, success/failure logging

### e2e/test_orchestrator_e2e.py
End-to-end test suite for Orchestrator Agent with 10 test classes:
1. `TestOrchestratorAgent` - agent initialization, workspace structure
2. `TestSimpleOrchestration` - single-agent queries (polymarket, market data)
3. `TestTaskMapping` - keyword-based agent mapping, parameter extraction
4. `TestCodeGeneration` - script generation (simple, parallel tasks)
5. `TestConsolidation` - result merging, summary statistics
6. `TestValidation` - validator initialization
7. `TestFileOutput` - file bus output, schema compliance
8. `TestManifestIncrement` - sequential ID generation (000001, 000002)
9. `TestRunLogs` - successful run logging with metadata
10. `TestErrorHandling` - empty query handling, error scenarios

### e2e/conftest.py
Pytest fixtures. `clean_workspace` fixture removes/creates workspace before tests. `project_root` fixture provides path to project root.

## Scripts (`scripts/`)

### e2e.py
E2E demonstration script. Runs producer (2 queries) → consumer (2 processes) → displays results. Shows full file bus communication flow with statistics.

### run_agent.py
CLI for running individual agents. Supports subcommands:
- `producer --template X --params '{...}'`
- `consumer --input path/to/file.json`

### test_predictions.py
CLI for testing predictive markets agent. Provides 8 pre-configured sample queries (election, crypto, AI, economy, sports, climate, finance). Supports `--list` (show queries), `--query N` (run sample), `--custom "text"` (run custom query), `--max-results` (limit results). Displays session ID, result count, and top results with URLs.

### test_polymarket_simple.py
CLI for testing the simple Polymarket agent. Accepts only custom queries (`--custom "text"`) and an optional `--max-results` limit. Calls `PolymarketAgent.run_simple()` to fetch high-volume, keyword-relevant markets from the Gamma `/markets` API and prints titles, URLs (when available), prices, and volumes.

### test_price_history.py
CLI helper for testing Polymarket historical prices end-to-end for a natural language query. Uses `PolymarketAgent.run_simple()` to find markets, derives token IDs via `get_token_id_for_price_history()`, then calls the `get_market_price_history` MCP tool for a past date and for today, printing prices and data-point counts for the top matches.

### test_orchestrator.py
CLI for testing Orchestrator Agent with multi-agent coordination. Provides 8 pre-configured sample queries (simple single-agent and complex multi-agent). Supports `--list` (show queries), `--query N` (run sample), `--custom "text"` (run custom query), `--skip-validation` (faster execution), `--num-subtasks N` (limit subtasks), `--verbose` (debug logging). Displays consolidated answer, metadata (agents used, task counts, duration), validation report, and output/script paths. Demonstrates parallel execution, result consolidation, and AI validation.

### setup_predictions_db.py
Database setup utility. Creates `prediction_queries` table with columns: id, session_id, user_query, timestamp, results (JSON), domains_searched, result_count, created_at. Creates indices on session_id and timestamp for efficient querying.

### setup_polymarket_db.py
Database setup utility for Polymarket. Creates `polymarket_markets.db` with `prediction_queries` table including columns: id, session_id, user_query, expanded_keywords (JSON), timestamp, results (JSON), platform, market_ids (JSON), avg_probability, total_volume, result_count, created_at. Creates indices on session_id, timestamp, and platform for efficient querying.

## Entry Point

### main.py
Main application entry point. Runs demonstration: producer executes 2 queries, consumer processes both outputs, displays statistics and artifact locations. Showcases complete system operation.

## Documentation (`docs/`)

### guides/
User-facing documentation for quick start, logging, testing, and usage patterns.

### deployment/
Deployment checklists and production configuration guides.

### architecture/
Technical architecture documentation and implementation summaries.

## Memory Bank (`memory-bank/`)

### activeContext.md
Current system status, architecture overview, workflow diagram, file formats, testing approach, and next steps.

### io-schema.md
Complete I/O contracts: agent inputs/outputs, manifest format, tool schemas, query templates, column whitelist, status values.

### progress.md
Implementation history, completed phases, features delivered, testing results, and migration notes.

### code-index.md
This file. One-paragraph summary per code file in the system.

## File Count

- Python files: ~20
- Test files: 2 (7 test methods)
- Config files: 2
- Documentation: ~15
- Scripts: 2

## Dependencies

**Runtime**: Python stdlib only (sqlite3, json, pathlib, tempfile, shutil)
**Testing**: pytest>=7.4.0

## Key Design Patterns

1. **Atomic writes**: Temp file + rename for crash safety
2. **Progressive disclosure**: Full data returned, samples logged
3. **Tool registration**: Decorator-based discovery
4. **Manifest-driven**: Incremental IDs managed centrally
5. **Run logging**: Every execution logged with metadata
6. **Schema validation**: All outputs validated before write
