# I/O Schema

## Overview

Complete I/O contracts for the code-mode MCP system.

---

## Market Data Agent (Producer)

### Inputs

| Name | Type | Description | Example |
|------|------|-------------|---------|
| template | str | Query template name | "by_symbol", "by_date", "all_valid" |
| params | dict | Query parameters | {"symbol_pattern": "%.C"} |
| columns | list[str] | Columns to select (optional) | ["symbol", "bid", "ask"] |
| limit | int | Row limit (optional, max 10000) | 100 |

### Outputs

**Output File**: `workspace/agents/market-data-agent/out/{id:06d}.json`

```json
{
  "data": [
    {"symbol": "XCME.OZN.AUG25.113.C", "bid": 0.007, "ask": 0.015625, ...},
    ...
  ],
  "metadata": {
    "query": "SELECT * FROM market_data WHERE ...",
    "timestamp": "2025-11-11T12:00:00Z",
    "row_count": 42,
    "agent": "market-data-agent",
    "version": "1.0"
  }
}
```

**Run Log**: `workspace/agents/market-data-agent/logs/{run_id}.json`

```json
{
  "run_id": "20251111_120000",
  "sql": "SELECT * FROM market_data WHERE symbol LIKE ?",
  "params": {"symbol_pattern": "%.C"},
  "output_path": "workspace/agents/market-data-agent/out/000001.json",
  "status": "success",
  "row_count": 42,
  "timestamp": "2025-11-11T12:00:00Z",
  "duration_ms": 15.3,
  "agent": "market-data-agent",
  "version": "1.0"
}
```

---

## Consumer Agent

### Inputs

| Name | Type | Description | Example |
|------|------|-------------|---------|
| input_path | Path | Path to producer output | "workspace/.../out/000001.json" |

### Outputs

**Output File**: `workspace/agents/consumer-agent/out/{id:06d}.json`

```json
{
  "data": [{
    "source": "market-data-agent",
    "source_query": "SELECT ...",
    "source_row_count": 42,
    "timestamp": "2025-11-11T12:00:00Z",
    "statistics": {
      "total_records": 42,
      "records_with_bid": 40,
      "records_with_ask": 40,
      "bid_min": 0.007,
      "bid_max": 1.5,
      "bid_avg": 0.245,
      "ask_min": 0.015,
      "ask_max": 1.6,
      "ask_avg": 0.267
    }
  }],
  "metadata": {
    "query": "Processed from workspace/.../out/000001.json",
    "timestamp": "2025-11-11T12:00:05Z",
    "row_count": 1,
    "agent": "consumer-agent",
    "version": "1.0"
  }
}
```

**Run Log**: `workspace/agents/consumer-agent/logs/{run_id}.json`

```json
{
  "run_id": "20251111_120005",
  "input_path": "workspace/agents/market-data-agent/out/000001.json",
  "output_path": "workspace/agents/consumer-agent/out/000001.json",
  "status": "success",
  "timestamp": "2025-11-11T12:00:05Z",
  "duration_ms": 8.2,
  "agent": "consumer-agent",
  "version": "1.0"
}
```

---

## Manifest Format

**File**: `workspace/agents/{agent-name}/meta.json`

```json
{
  "next_id": 3,
  "last_updated": "2025-11-11T12:00:00Z",
  "total_runs": 2
}
```

| Field | Type | Description |
|-------|------|-------------|
| next_id | int | Next available ID for output file |
| last_updated | str | ISO-8601 timestamp of last update |
| total_runs | int | Total number of runs |

**Filename Pattern**: `{id:06d}.json` (e.g., 000001.json, 000002.json, ...)

---

## Market Data Tool

### run_query Tool

**Inputs**:

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| template | str | Query template | "by_symbol" |
| columns | list[str] | Column list (optional) | ["symbol", "bid", "ask"] |
| params | dict | Query parameters | {"symbol_pattern": "%.C"} |
| limit | int | Row limit (optional) | 100 |

**Output**:

```json
{
  "data": [...],
  "metadata": {
    "query": "SELECT ...",
    "params": [...],
    "row_count": 42,
    "sample": [...],  // First 5 rows for logging
    "columns": "symbol, bid, ask"
  }
}
```

---

## Predictive Markets Agent (Producer)

### Inputs

| Name | Type | Description | Example |
|------|------|-------------|---------|
| query | str | User search query | "Will Trump win 2024?" |
| session_id | str (optional) | Session identifier (auto-generated if not provided) | "20251112143022_a3f2e9" |
| domains | list[str] (optional) | Domains to search (default: all allowed) | ["polymarket.com", "kalshi.com"] |
| max_results | int (optional) | Maximum results (default: 10, max: 20) | 10 |

### Outputs

**Output File**: `workspace/agents/predictive-markets-agent/out/{id:06d}.json`

```json
{
  "data": [{
    "query": "Will Trump win 2024?",
    "session_id": "20251112143022_a3f2e9",
    "results": [
      {
        "url": "https://polymarket.com/...",
        "title": "2024 Election Predictions",
        "snippet": "Current prediction markets show...",
        "domain": "polymarket.com"
      }
    ],
    "result_count": 5,
    "domains_searched": "polymarket.com, kalshi.com, predictit.org"
  }],
  "metadata": {
    "query": "Polymarket search: Will Trump win 2024?",
    "timestamp": "2025-11-12T14:30:22Z",
    "row_count": 1,
    "agent": "polymarket-agent",
    "version": "1.0"
  }
}
```

**Run Log**: `workspace/agents/polymarket-agent/logs/{run_id}.json`

```json
{
  "run_id": "20251112_143022",
  "query": "Will Trump win 2024?",
  "session_id": "20251112143022_a3f2e9",
  "output_path": "workspace/agents/polymarket-agent/out/000001.json",
  "status": "success",
  "result_count": 5,
  "timestamp": "2025-11-12T14:30:22Z",
  "duration_ms": 1523.45,
  "agent": "polymarket-agent",
  "version": "1.0"
}
```

---

## Query Templates

| Template | Required Params | Example |
|----------|----------------|---------|
| by_symbol | symbol_pattern | {"symbol_pattern": "XCME.OZN.%"} |
| by_date | file_date | {"file_date": "2025-07-21"} |
| by_symbol_and_date | symbol_pattern, file_date | {"symbol_pattern": "%.C", "file_date": "2025-07-21"} |
| all_valid | none | {} |

---

## Column Whitelist

Allowed columns in market_data table:

```python
[
    "id",
    "symbol",
    "bid",
    "ask",
    "price",
    "bid_quantity",
    "offer_quantity",
    "timestamp",
    "file_date",
    "data_source",
    "is_valid",
    "created_at"
]
```

---

## Status Values

### Run Log Status

| Value | Description |
|-------|-------------|
| success | Run completed successfully |
| failed | Run failed with error |

### Schema Validation

Output files must pass validation:
- Top-level keys: `data` (list) and `metadata` (dict)
- Metadata must include: `query`, `timestamp`, `row_count`, `agent`, `version`
- Row count must match actual data length

---

## File Bus Operations

### Atomic Write

1. Write to temp file: `{path}.tmp`
2. Flush and fsync
3. Atomic rename to target path

### Manifest Operations

1. Read current manifest
2. Get next_id
3. Update manifest (increment, update timestamp)
4. Atomic write

### Output Path Generation

```python
manifest = Manifest(agent_workspace)
file_id = manifest.increment()  # Returns current, increments for next
filename = f"{file_id:06d}.json"
output_path = agent_workspace / "out" / filename
```

---

## Environment Variables

None required. All configuration in `config/settings.py`.

---

## Polymarket Agent (Producer)

### Inputs

| Name | Type | Description | Example |
|------|------|-------------|---------|
| query | str | Natural language search query | "Will Bitcoin reach $100k?" |
| session_id | str (optional) | Session identifier (auto-generated if not provided) | "20251113143022_a3f2e9" |
| limit | int (optional) | Maximum results (default: 10, max: 50) | 10 |

### Outputs

**Output File**: `workspace/agents/polymarket-agent/out/{id:06d}.json`

```json
{
  "data": [{
    "query": "Will Bitcoin reach $100k?",
    "session_id": "20251113143022_a3f2e9",
    "search_method": "hybrid_llm",
    "llm_scoring_enabled": true,
    "markets": [
      {
        "market_id": "0x123...",
        "title": "Bitcoin to reach $100,000 by end of 2025?",
        "description": "Resolves YES if Bitcoin reaches $100k",
        "outcomes": ["Yes", "No"],
        "prices": {"Yes": 0.65, "No": 0.35},
        "volume": 1250000,
        "liquidity": 85000,
        "status": "active",
        "url": "https://polymarket.com/event/bitcoin-100k-2025",
        "slug": "bitcoin-100k-2025",
        "close_time": "2025-12-31T23:59:59Z",
        "relevance_score": 0.85,
        "relevance_reason": "Directly matches Bitcoin price prediction"
      }
    ],
    "result_count": 5
  }],
  "metadata": {
    "query": "Polymarket search: Will Bitcoin reach $100k?",
    "search_method": "hybrid_llm",
    "timestamp": "2025-11-13T14:30:22Z",
    "row_count": 1,
    "agent": "polymarket-agent",
    "version": "1.0"
  }
}
```

**Run Log**: `workspace/agents/polymarket-agent/logs/{run_id}.json`

```json
{
  "run_id": "20251113_143022",
  "query": "Will Bitcoin reach $100k?",
  "session_id": "20251113143022_a3f2e9",
  "output_path": "workspace/agents/polymarket-agent/out/000001.json",
  "status": "success",
  "result_count": 5,
  "timestamp": "2025-11-13T14:30:22Z",
  "duration_ms": 2134.56,
  "agent": "polymarket-agent",
  "version": "1.0"
}
```

---

## Polymarket Tools

### search_polymarket_markets Tool

**Inputs**:

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| query | str | Search query | "Bitcoin predictions" |
| session_id | str | Session identifier | "20251113143022_a3f2e9" |
| limit | int (optional) | Result limit (default: 10, max: 50) | 10 |

**Output**:

```json
{
  "markets": [
    {
      "market_id": "0x123...",
      "title": "Bitcoin to reach $100k?",
      "description": "...",
      "outcomes": ["Yes", "No"],
      "prices": {"Yes": 0.65, "No": 0.35},
      "volume": 1250000,
      "liquidity": 85000,
      "status": "active",
      "url": "https://polymarket.com/event/bitcoin-100k",
      "slug": "bitcoin-100k",
      "close_time": "2025-12-31T23:59:59Z",
      "relevance_score": 0.85,
      "relevance_reason": "Directly matches Bitcoin price prediction"
    }
  ],
  "metadata": {
    "query": "Bitcoin predictions",
    "search_method": "hybrid_llm",
    "llm_scoring_enabled": true,
    "session_id": "20251113143022_a3f2e9",
    "result_count": 5,
    "platform": "polymarket",
    "timestamp": "2025-11-13T14:30:22Z"
  }
}
```

**Notes**: 
- Markets include `relevance_score` (0-1) and `relevance_reason` when LLM scoring is enabled
- Falls back to keyword-only if OpenAI API key not available
- **Always returns at least 1 result** if any markets exist (fallback: LLM → keyword → top by volume)

### get_polymarket_history Tool

**Inputs**:

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| session_id | str (optional) | Filter by session | "20251113143022_a3f2e9" |
| limit | int (optional) | Max results (default: 10) | 20 |
| start_date | str (optional) | Start date (ISO format) | "2025-11-01T00:00:00Z" |
| end_date | str (optional) | End date (ISO format) | "2025-11-13T23:59:59Z" |

**Output**:

```json
{
  "history": [
    {
      "id": 1,
      "session_id": "20251113143022_a3f2e9",
      "user_query": "Will Bitcoin reach $100k?",
      "timestamp": "2025-11-13T14:30:22Z",
      "results": [...],
      "platform": "polymarket",
      "market_ids": ["0x123...", "0x456..."],
      "avg_probability": 65.5,
      "total_volume": 2500000,
      "result_count": 5,
      "created_at": "2025-11-13T14:30:22Z"
    }
  ],
  "metadata": {
    "count": 1,
    "filters": {
      "session_id": "20251113143022_a3f2e9",
      "start_date": null,
      "end_date": null,
      "limit": 10,
      "platform": "polymarket"
    },
    "timestamp": "2025-11-13T14:35:00Z"
  }
}
```

### get_market_price_history Tool

**Inputs**:

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| market_id | str | Market/token ID | "0x123..." |
| date | str | Target date (ISO format YYYY-MM-DD) | "2024-11-01" |
| date_range_hours | int (optional) | Hours to search around date (default: 12) | 12 |

**Output**:

```json
{
  "market_id": "0x123...",
  "date": "2024-11-01",
  "price": {
    "yes": 0.65,
    "no": 0.35
  },
  "data_points": 24,
  "data_source": "polymarket_clob_api",
  "note": "Historical price from 24 data points (Polymarket CLOB API)"
}
```

**Data Source**: Uses Polymarket CLOB API `prices-history` endpoint. Fetches time-series price data and finds the price closest to the target date using weighted averaging of nearby points.

### get_market_price_range Tool

**Inputs**:

| Parameter | Type | Description | Example |
|-----------|------|-------------|---------|
| market_id | str | Market/token ID | "0x123..." |
| start_date | str | Start date (YYYY-MM-DD) | "2024-10-01" |
| end_date | str | End date (YYYY-MM-DD) | "2024-11-01" |
| interval_days | int (optional) | Days between data points (default: 1) | 1 |

**Output**:

```json
{
  "market_id": "0x123...",
  "start_date": "2024-10-01",
  "end_date": "2024-11-01",
  "prices": [
    {"date": "2024-10-01", "yes": 0.60, "no": 0.40},
    {"date": "2024-10-02", "yes": 0.62, "no": 0.38},
    ...
  ],
  "data_points": 31,
  "price_change": {
    "yes": +0.05,
    "no": -0.05
  },
  "note": "Historical price trend from Polymarket CLOB API (480 raw data points)"
}
```

**Data Source**: Uses Polymarket CLOB API `prices-history` endpoint. Fetches entire date range at once and samples at requested intervals for efficiency.

---

## Database Schemas

### Orchestrator Results Database

**Database**: `workspace/orchestrator_results.db`

#### worker_runs Table

Stores execution metadata for each worker task.

| Column | Type | Description |
|--------|------|-------------|
| run_id | TEXT NOT NULL | Orchestration run ID (timestamp-based) |
| task_id | TEXT NOT NULL | Task ID from planner |
| agent_name | TEXT NOT NULL | Agent that executed the task |
| status | TEXT NOT NULL | 'running', 'success', or 'failed' |
| started_at | TEXT NOT NULL | ISO-8601 timestamp of task start |
| completed_at | TEXT | ISO-8601 timestamp of task completion |
| duration_ms | REAL | Task execution duration in milliseconds |
| error | TEXT | Error message if failed |
| output_file_path | TEXT | Path to file bus output |
| created_at | TEXT | Record creation timestamp |

**Primary Key**: (run_id, task_id)

**Indices**:
- `idx_worker_run_id` on `run_id`
- `idx_worker_task_id` on `task_id`

#### task_outputs Table

Stores task output data and metadata.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-incrementing ID |
| run_id | TEXT NOT NULL | Orchestration run ID |
| task_id | TEXT NOT NULL | Task ID |
| agent_name | TEXT NOT NULL | Agent name |
| output_data | TEXT NOT NULL | JSON string of output data |
| metadata | TEXT NOT NULL | JSON string of metadata |
| created_at | TEXT | Record creation timestamp |

**Foreign Key**: (run_id, task_id) REFERENCES worker_runs(run_id, task_id)

**Indices**:
- `idx_run_id` on `run_id`
- `idx_task_id` on `task_id`
- `idx_agent` on `agent_name`

**Usage**: Enables dependency resolution, result consolidation, and queryable task history.

---

### prediction_queries Table (Polymarket)

**Database**: `polymarket_markets.db`
**Table**: `prediction_queries`

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PRIMARY KEY | Auto-incrementing ID |
| session_id | TEXT NOT NULL | Unique session identifier |
| user_query | TEXT NOT NULL | User's original search query |
| expanded_keywords | TEXT NOT NULL | Empty JSON array (legacy, kept for compatibility) |
| timestamp | TEXT NOT NULL | Query timestamp (ISO-8601) |
| results | TEXT NOT NULL | JSON string of market data (may include relevance_score from LLM) |
| platform | TEXT | Platform name (default: 'polymarket') |
| market_ids | TEXT | JSON array of market IDs |
| avg_probability | REAL | Average probability across markets |
| total_volume | INTEGER | Total volume across markets |
| result_count | INTEGER NOT NULL | Number of markets returned |
| created_at | TEXT NOT NULL | Record creation timestamp |

**Indices**:
- `idx_session_id` on `session_id`
- `idx_timestamp` on `timestamp`
- `idx_platform` on `platform`

---

## Constants

| Name | Value | Description |
|------|-------|-------------|
| MAX_ROWS_PER_QUERY | 10000 | Maximum rows per query (market data) |
| MAX_SEARCH_RESULTS | 20 | Maximum search results (predictions) |
| DEFAULT_SEARCH_RESULTS | 10 | Default search results |
| AGENT_VERSION | "1.0" | Agent version |
| OUTPUT_VERSION | "1.0" | Output schema version |

### Prediction Markets Constants

| Name | Value | Description |
|------|-------|-------------|
| ALLOWED_PREDICTION_DOMAINS | ["polymarket.com"] | Allowed prediction market domains (deprecated, kept for compatibility) |
| SESSION_ID_FORMAT | "{timestamp}_{hash}" | Session ID format |
| SESSION_ID_HASH_LENGTH | 3 | Hex bytes for session ID hash |

### Polymarket Constants

| Name | Value | Description |
|------|-------|-------------|
| POLYMARKET_API_BASE_URL | "https://clob.polymarket.com" | Polymarket CLOB API base URL |
| POLYMARKET_GAMMA_BASE_URL | "https://gamma-api.polymarket.com" | Polymarket Gamma API base URL |
| MAX_POLYMARKET_RESULTS | 50 | Maximum results per query |
| DEFAULT_POLYMARKET_RESULTS | 10 | Default results per query |
| MARKET_STATUS_ACTIVE | "active" | Active market status |
| MARKET_STATUS_CLOSED | "closed" | Closed market status |
| MARKET_STATUS_RESOLVED | "resolved" | Resolved market status |

---

## Error Handling

All agents write run logs on both success and failure. Failed runs include `error` field:

```json
{
  "run_id": "20251111_120000",
  "status": "failed",
  "error": "Invalid template: xyz",
  ...
}
```
