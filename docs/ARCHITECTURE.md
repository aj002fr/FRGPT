# Code-Mode MCP Architecture

## Overview

A comprehensive **pure Python** code-mode MCP system with file-based inter-agent communication. Features market data querying and predictive markets search capabilities.

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

#### Polymarket Tool (`polymarket/`)
- `search_polymarket_markets.py` - Direct Polymarket API with LLM scoring
- `get_history.py` - Query history retrieval
- `schema.py` - Constants and validation
- Hybrid search: keyword filtering + GPT-4 semantic ranking

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

#### Predictive Markets Agent (`predictive_markets_agent/`)
- **Type**: Producer
- **Function**: Search prediction markets
- **Output**: URLs, titles, snippets from prediction markets
- **Tools Used**: `search_markets`, `get_query_history`
- **Features**: Auto-generated session IDs, query history

**Common Features**:
- Input validation
- Manifest management
- Run logging
- Error handling

### 5. Core (`src/core/`)

**Purpose**: Shared utilities

**Current**:
- `logging_config.py` - Centralized logging setup

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

### market_data Table
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

---

## Security

### SQL Injection Prevention
- Parameterized queries only
- Template-based SQL
- Column whitelist

### File Safety
- Atomic writes prevent corruption
- Temp + rename pattern
- Directory validation

### API Security
- Domain filtering (whitelist only)
- API keys in config file
- Rate limiting (external to system)

### Multi-User Isolation
- Session-based tracking
- No cross-session data leakage
- Unique identifiers per query

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

### Runtime
- Python 3.11+ stdlib only
  - `sqlite3` - Database
  - `json` - Serialization
  - `pathlib` - File operations
  - `urllib` - HTTP requests (Polymarket API)
  - `secrets` - Session ID generation

### External APIs
- Polymarket API (for prediction markets)
- OpenAI API (optional, for LLM-based relevance scoring)

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
5. Run `scripts/verify_setup.py` to check configuration

### Common Issues
- **Tool not found**: Check `__init__.py` imports in tool server
- **API errors**: Verify OpenAI API key in `config/keys.env` (for LLM scoring)
- **Database errors**: Run `scripts/setup_polymarket_db.py`
- **File permissions**: Check workspace directory permissions

---

**Simple, fast, reliable, and extensible!** 🚀
