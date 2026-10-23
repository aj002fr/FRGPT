# Code-Mode MCP Architecture

## Overview

A comprehensive **pure Python** code-mode MCP system with file-based inter-agent communication. It now includes market data querying, predictive market search, economic event ingestion/analysis, Distro-style distribution analytics on OHLCV data, and a two-stage orchestration layer for multi-agent workflows.

## Design Principles

1. **Progressive Disclosure**: Full datasets returned by tools, small samples logged
2. **Tools-as-Code**: Direct Python function calls, no network protocol
3. **File-Based Bus**: Agents communicate via atomic file operations
4. **Manifest-Driven**: Incremental filenames managed centrally
5. **Run Logging**: Every execution logged with full metadata
6. **Multi-User Support**: Session-based tracking for concurrent operations

---

## System Architecture

### Complete System Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        File-Based Bus                           │
│             workspace/agents/{agent}/out/{id}.json              │
└─────────────────────────────────────────────────────────────────┘
         ▲                    │                    ▲
         │                    ▼                    │
┌────────────────┐   ┌────────────────┐   ┌──────────────────┐
│ Market Data    │   │   Consumer     │   │  Polymarket      │
│ Agent          │   │   Agent        │   │  Agent           │
│ (SQL queries)  │   │ (statistics)   │   │ (search)         │
└────────────────┘   └────────────────┘   └──────────────────┘
         │                                          │
         ▼                                          ▼
┌────────────────┐                        ┌──────────────────┐
│  MCP Client    │◄───────────────────────┤  MCP Client      │
│ (tool caller)  │                        │  (tool caller)   │
└────────────────┘                        └──────────────────┘
         │                                          │
         ▼                                          ▼
┌────────────────┐                        ┌──────────────────┐
│ Market Data    │                        │  Polymarket      │
│ Tool Server    │                        │  Tool Server     │
│ (SQL executor) │                        │ (Direct API +    │
└────────────────┘                        │  LLM scoring)    │
         │                                └──────────────────┘
         │                                          │
         ▼                                          ▼
┌────────────────────────────────────────────────────────────┐
│                    market_data.db (SQLite)                 │
│  ┌──────────────────┐  ┌──────────────────────────────┐    │
│  │   market_data    │  │   polymarket_queries         │    │
│  │   (price data)   │  │   (search history)           │    │
│  └──────────────────┘  └──────────────────────────────┘    │
└────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. File Bus (`src/bus/`)

**Purpose**: Inter-agent communication via filesystem

**Key Files**:
- `file_bus.py` - Atomic write operations
- `manifest.py` - Incremental filename management
- `schema.py` - Output validation

**Guarantees**:
- Atomic writes (temp + rename)
- Deterministic incremental IDs
- Schema validation

### 2. MCP Infrastructure (`src/mcp/`)

**Purpose**: Tool discovery and execution

**Key Files**:
- `client.py` - Simple tool executor
- `discovery.py` - Decorator-based registration

**Features**:
- No network protocol (local Python calls)
- Decorator registration: `@register_tool`
- Dynamic tool discovery

### 3. Tool Servers (`src/servers/`)

**Purpose**: Tools-as-code for data operations

#### Market Data Tool (`marketdata/`)
- `run_query.py` - SQL execution on SQLite
- Column whitelist and parameterized queries
- Template-based SQL (by_symbol, by_date, etc.)

#### Polymarket Tools (`polymarket/`)
- `search_markets.py` / `unified_search.py` - Direct Polymarket Gamma API search (volume-sorted, keyword-filtered; optional LLM scoring)
- `get_history.py` - Query history retrieval
- `get_price_history.py` - Historical price data via CLOB API
- `schema.py` - Constants and validation

#### Trading Economics Tools (`tradingeconomics/`)
- `fetch_calendar.py` - Fetch and update economic calendar into `economic_events.db`
- `query_events.py` - Query historical economic events with rich filters
- `search_event_names.py` - Fuzzy search over event names
- `websocket_client.py` - WebSocket client for live economic releases
- `schema.py`, `filters.py`, `event_dictionary.py` - Schema, filtering helpers, and canonical event mappings

#### Distro Analytics Tools (`distro_analytics/`) – Optional
- `returns_distribution.py` - Session-based returns distribution on OHLCV (PostgreSQL `ohlcv_1m` or parquet)
- `volatility_distribution.py` - Session-based volatility (High–Low) distribution
- `probability_matrix.py` - Probability of price moving X bps within Y hours
- `event_distribution.py` - Event-specific return distributions (NFP, CPI, FOMC, etc.)
- `surprise_price_distribution.py` - Surprise buckets (Big Beat/Miss, etc.) vs price reaction
- `db_reader.py` - Shared loaders for OHLCV and `economic_events.db` with lazy optional-dependency handling

**Security**:
- Column whitelist
- Parameterized queries
- API key isolation

### 4. Agents (`src/agents/`)

**Purpose**: Task execution and orchestration

#### Market Data Agent (`market_data_agent/`)
- **Type**: Producer
- **Function**: SQL queries on market data
- **Output**: Filtered market data with bid/ask/price
- **Tools Used**: `run_query`

#### Consumer Agent (`consumer_agent/`)
- **Type**: Consumer
- **Function**: Process producer output
- **Output**: Statistics (min/max/avg)
- **Input**: Market data agent output

#### Polymarket Agent (`polymarket_agent/`)
- **Type**: Producer
- **Function**: Search Polymarket markets (Gamma API) by natural-language query
- **Output**: Structured markets with prices, volumes, URLs, and metadata
- **Tools Used**: `search_polymarket_markets`, `get_polymarket_history`, `get_market_price_history`
- **Features**: Auto-generated session IDs, query history, optional historical price comparison

#### EventData Puller Agent (`eventdata_puller_agent/`)
- **Type**: Producer
- **Function**: Fetch, store, and query economic calendar events (Trading Economics)
- **Output**: Event lists with actual/forecast/previous values, surprises, and summary stats
- **Tools Used**: `fetch_economic_calendar`, `query_event_history`, WebSocket streaming tools

#### Analytics / Distro Agent (`analytics_agent/`)
- **Type**: Consumer/Analyzer
- **Function**: Perform statistical analysis and generate plots (histograms, scatter, bar) over arbitrary numeric data and market/economic datasets
- **Output**: Statistics JSON + SVG/PNG plots; can call Distro analytics tools for OHLCV-based distributions

#### Orchestrator Agent (`orchestrator_agent/`)
- **Type**: Meta-agent
- **Function**: Two-stage planning, tool selection, code generation, and execution across agents (market data, Polymarket, EventData, analytics)
- **Output**: Consolidated answer + structured worker outputs; writes both to DB and file bus

**Common Features**:
- Input validation
- Manifest management
- Run logging
- Error handling

### 5. Core (`src/core/`)

**Purpose**: Shared utilities

**Current**:
- `logging_config.py` - Centralized logging setup
- `telegram_notifier.py` - Telegram Bot API for push alerts
- `schedule_store.py` - SQLite-based event scheduling
- `run_store.py` - Central run persistence for orchestration (`data/metadata.db` + `runs/{run_id}/`)

---

## Data Flows

### Flow 1: Market Data Query

```
1. User Request
   agent.run(template="by_symbol", params={"symbol_pattern": "%.C"})
   
2. Input Validation
   Check template, params, limits
   
3. Tool Discovery & Call
   mcp_client.call_tool("run_query", arguments={...})
   
4. SQL Execution
   Parameterized query on SQLite
   Returns: {data: [...], metadata: {...}}
   
5. Manifest Allocation
   manifest.increment() → next_id
   
6. Output Write
   write_atomic(out/000001.json, output_data)
   
7. Run Log Write
   write_atomic(logs/{timestamp}.json, log_data)
   
8. Consumer Read (Optional)
   consumer.run(producer_output_path)
   
9. Statistics Computation
   Calculate min/max/avg for bid/ask
   
10. Consumer Output
    write_atomic(out/000001.json, processed_data)
```

### Flow 2: Polymarket Search

```
1. User Query
   agent.run(query="Will Bitcoin reach $100k in 2025?")
   
2. Session ID Generation
   {timestamp}_{random_hash} → "20251112143022_a3f2e9"
   
3. Input Validation
   Check query, limit parameters
   
4. Tool Discovery & Call
   mcp_client.call_tool("search_polymarket_markets", arguments={...})
   
5. API Call & Filtering
   Direct Polymarket API with keyword filtering
   Optional: GPT-4 semantic re-ranking
   Returns: Markets with relevance scores
   
6. Parse Response
   Extract market data with metadata
   
7. Database Storage
   Store in polymarket_queries table with session_id
   
8. Manifest Allocation
   manifest.increment() → next_id
   
9. Output Write
   write_atomic(out/000001.json, output_data)
   
10. Run Log Write
   write_atomic(logs/{timestamp}.json, log_data)
```

### Flow 3: Economic Event Data (EventData Puller)

```
1. Calendar Update
   eventdata_puller_agent.run(action="update_calendar", country="US", importance="high")

2. Trading Economics Fetch
   fetch_economic_calendar → Trading Economics REST API

3. SQLite Storage
   Upsert into workspace/economic_events.db (economic_events table)

4. Querying
   eventdata_puller_agent.run(action="query_event", event_name="Non-Farm Payrolls")
   → query_event_history tool → returns events + summary stats

5. File Bus Output
   write_atomic(workspace/agents/eventdata-puller-agent/out/000001.json, output_data)
```

### Flow 4: Distro Analytics on OHLCV (PostgreSQL)

```
1. User Request
   analytics_agent.run(analysis_type="returns_distribution", params={"symbol": "ZN"})

2. Tool Call
   mcp_client.call_tool("compute_returns_distribution", {"symbol": "ZN", "interval": "1h"})

3. Data Load
   get_ohlcv_from_db(symbol_pattern="ZN%", start_date, end_date) → PostgreSQL ohlcv_1m

4. Distro Processing
   ReturnsCalculator + ReturnsPlotter → statistics + PNG plot(s)

5. File Bus Output
   write_atomic(workspace/agents/analytics-agent/out/000001.json, analytics_result)
```

---

## File Formats

### Market Data Output
```json
{
  "data": [
    {
      "symbol": "XCME.OZN.AUG25.113.C",
      "bid": 0.007,
      "ask": 0.015625,
      "price": 0.011,
      "timestamp": "2025-07-21T12:00:00Z"
    }
  ],
  "metadata": {
    "query": "SELECT * FROM market_data WHERE symbol LIKE ?",
    "timestamp": "2025-11-11T12:00:00Z",
    "row_count": 42,
    "agent": "market-data-agent",
    "version": "1.0"
  }
}
```

### Polymarket Output
```json
{
  "data": [{
    "query": "Will Trump win 2024 election?",
    "session_id": "20251112143022_a3f2e9",
    "search_method": "hybrid",
    "llm_scoring_enabled": true,
    "markets": [
      {
        "question": "Will Trump win the 2024 Presidential Election?",
        "description": "This market will resolve...",
        "outcome": "Yes",
        "price": 0.65,
        "volume": "1234567.89",
        "end_date": "2024-11-05T23:59:59Z",
        "market_slug": "trump-2024-election",
        "relevance_score": 0.95
      }
    ],
    "result_count": 5
  }],
  "metadata": {
    "query": "Polymarket search: Will Trump win 2024 election?",
    "timestamp": "2025-11-12T14:30:22Z",
    "row_count": 1,
    "agent": "polymarket-agent",
    "version": "1.0"
  }
}
```

### Run Log Format
```json
{
  "run_id": "20251111_120000",
  "query": "...",  // or "sql" for market data agent
  "params": {...},
  "output_path": "workspace/.../out/000001.json",
  "status": "success",
  "row_count": 42,
  "timestamp": "2025-11-11T12:00:00Z",
  "duration_ms": 15.3,
  "agent": "agent-name",
  "version": "1.0"
}
```

### Manifest Format
```json
{
  "next_id": 3,
  "last_updated": "2025-11-11T12:00:00Z",
  "total_runs": 2
}
```

---

## Database Schemas

### market_data Table (SQLite - legacy, still used by `market_data_agent`)
```sql
CREATE TABLE market_data (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    bid REAL,
    ask REAL,
    price REAL,
    bid_quantity REAL,
    offer_quantity REAL,
    timestamp TEXT,
    file_date TEXT,
    data_source TEXT,
    is_valid INTEGER,
    created_at TEXT
);
```

### polymarket_queries Table
```sql
CREATE TABLE polymarket_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_query TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    markets TEXT NOT NULL,  -- JSON string
    search_method TEXT NOT NULL,
    result_count INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_session_id ON polymarket_queries(session_id);
CREATE INDEX idx_timestamp ON polymarket_queries(timestamp);
```

### economic_events Table (SQLite)

Backed by `workspace/economic_events.db`, populated by Trading Economics tools and the EventData Puller Agent.

```sql
CREATE TABLE economic_events (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    event_name TEXT NOT NULL,
    country TEXT NOT NULL,
    category TEXT,
    importance TEXT,
    event_date TEXT NOT NULL,
    actual REAL,
    consensus REAL,
    forecast REAL,
    previous REAL,
    revised REAL,
    unit TEXT,
    ticker TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### ohlcv_1m Table (PostgreSQL)

Primary OHLCV source for Distro analytics tools.

```sql
CREATE TABLE ohlcv_1m (
    ts_event TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume DOUBLE PRECISION
);
```

---

## Session Management

### Session ID Format
```
{timestamp}_{random_hash}
Example: 20251112143022_a3f2e9
```

**Benefits**:
- Sortable by time
- Globally unique
- No collision risk
- No user registration needed

**Use Cases**:
- Track user queries
- Historical retrieval
- Analytics and reporting
- Debugging specific sessions

---

## Extensibility

### Adding a New Agent

```python
# 1. Create directory
src/agents/my_agent/
├── __init__.py
├── run.py      # Main agent logic
├── config.py   # Configuration
└── prompt.md   # Documentation

# 2. Implement run.py
class MyAgent:
    def __init__(self):
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        self.mcp_client = MCPClient()
    
    def run(self, **kwargs) -> Path:
        # Your logic here
        output_path = self.manifest.get_next_filepath(subdir="out")
        write_atomic(output_path, data)
        return output_path
```

### Adding a New Tool

```python
# 1. Create directory
src/servers/mytool/
├── __init__.py
├── my_function.py
└── schema.py

# 2. Implement tool with decorator
from src.mcp.discovery import register_tool

@register_tool(
    name="my_tool",
    description="Tool description"
)
def my_tool(param1: str, param2: int) -> dict:
    # Your logic
    return {
        "data": [...],
        "metadata": {...}
    }

# 3. Import in __init__.py
from .my_function import my_tool
__all__ = ['my_tool']
```

### Adding a New Utility

```python
# Add to src/core/my_util.py
# Available to all agents via:
from src.core.my_util import my_function
```

---

## Performance

| Operation | Duration |
|-----------|----------|
| SQL execution | ~150-200ms |
| File operations | ~2-5ms |
| Full pipeline | ~400-500ms |
| Polymarket API call | ~1-3s |
| Network latency | 0ms (local tools) |

**Optimizations**:
- Direct Python calls (no IPC)
- SQLite for fast queries
- Atomic file operations
- Minimal dependencies

---

## Dependencies

### Core Runtime
- Python 3.11+ stdlib
  - `sqlite3` - Local databases (market_data, economic_events, orchestrator_results)
  - `json` - Serialization
  - `pathlib` - File operations
  - `urllib` / `http.client` - HTTP requests (Polymarket, Trading Economics)
  - `secrets` / `uuid` - Session ID generation

### Optional Runtime (for advanced features)
- `openai` / `anthropic` – Orchestrator task planning, optional LLM relevance scoring
- `websockets` – Trading Economics WebSocket client
- `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn`, `pyarrow` – Analytics + Distro analytics
- `psycopg2-binary` – PostgreSQL OHLCV connectivity (`ohlcv_1m` for Distro tools)

### External APIs
- Polymarket API (prediction markets + CLOB price history)
- Trading Economics API + WebSocket (economic calendar and live events)
- OpenAI / Anthropic APIs (optional, for planning and validation)

### Testing
- pytest>=7.4.0

---

## Key Design Decisions

| Decision | Rationale | Trade-offs |
|----------|-----------|------------|
| Stdlib only | Zero dependencies, maximum portability | Limited external integrations |
| File bus | Simple, debuggable, crash-safe | No concurrent writes |
| Manifest | Deterministic IDs, audit trail | Single process only |
| Run logs | Full traceability | Disk space usage |
| Tools-as-code | No network overhead, progressive disclosure | Local execution only |
| Session IDs | Multi-user support without registration | No authentication |
| SQLite | Built-in, fast, reliable | Single process limitations |

---

## Limitations

- **Single process**: No concurrent writes to same agent
- **Local filesystem**: No distributed execution
- **SQLite**: Single-writer constraints
- **No authentication**: Session IDs provide isolation but not security
- **API rate limits**: Polymarket API has rate limits (external)
- **Storage**: All data persisted to disk (not ephemeral)

---

## Future Enhancements

### Potential Features
1. **Cross-Agent Workflows**: Predictions → Analysis → Recommendations
2. **Result Caching**: Cache common queries to reduce API calls
3. **Advanced Filtering**: Sentiment analysis, confidence scoring
4. **Historical Analysis**: Trend analysis across time periods
5. **Notification System**: Alerts for prediction changes
6. **Export Functions**: CSV/JSON export for external analysis
7. **Multi-Domain Support**: Add more prediction market platforms
8. **Consumer for Predictions**: Aggregate and analyze prediction data

### Architectural Evolution
- **Concurrent Operations**: Add locking for multi-process support
- **Distributed Execution**: Add network-based file bus
- **Authentication**: Add user authentication layer
- **Caching Layer**: Add Redis/memcached for performance
- **Async Operations**: Add async/await for API calls

---

## Monitoring & Debugging

### Log Files
- **System logs**: `logs/market_data_puller_{date}.log`
- **Run logs**: `workspace/agents/{agent}/logs/{run_id}.json`

### Debugging Tips
1. Check run logs for execution details
2. Inspect output files directly (JSON)
3. Query polymarket_queries table for search history
4. Use `scripts/show_logs.py` for overview
5. Check `runs/{run_id}/` for orchestration artifacts

### Common Issues
- **Tool not found**: Check `__init__.py` imports in tool server
- **API errors**: Verify OpenAI API key in `config/keys.env` (for LLM scoring)
- **Database errors**: Run `scripts/setup_polymarket_db.py`
- **File permissions**: Check workspace directory permissions

---

**Simple, fast, reliable, and extensible!** 🚀
