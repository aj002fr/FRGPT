# Market Data Agent

## Overview

**Type**: Producer Agent  
**Purpose**: Query market data from SQLite database  
**Tools Used**: `run_query`  
**Output**: Filtered market data with bid/ask/price information

## Features

- ✅ SQL query execution with safety constraints
- ✅ Template-based queries (by_symbol, by_date, custom, etc.)
- ✅ **ORDER BY support** with ascending/descending sort
- ✅ **Price range filtering** (BETWEEN, >, <, etc.)
- ✅ **Complex WHERE clauses** via custom template
- ✅ Column whitelist for security
- ✅ Parameterized query support
- ✅ Incremental file output with manifest
- ✅ Complete run logging

## Usage

### Python API

```python
from src.agents.market_data_agent.run import MarketDataAgent

agent = MarketDataAgent()

# Query by symbol pattern
output_path = agent.run(
    template="by_symbol",
    params={"symbol_pattern": "%.C"},
    limit=100
)

# Query by date
output_path = agent.run(
    template="by_date",
    params={"file_date": "2025-07-21"},
    limit=50
)

# All valid records
output_path = agent.run(
    template="all_valid",
    limit=1000
)
```

### CLI

```powershell
# Via test script
py scripts/test_queries.py --query 1

# Via agent runner
py scripts/run_agent.py producer \
  --template by_symbol \
  --params '{"symbol_pattern": "XCME.OZN.%"}' \
  --limit 100
```

## Input Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| template | str | No | "all_valid" | Query template name |
| params | dict | Conditional | None | Query parameters (depends on template) |
| columns | list[str] | No | DEFAULT_COLUMNS | Columns to select |
| limit | int | No | None | Maximum rows (max: 10,000) |
| order_by_column | str | No | None | Column to sort by (e.g., "file_date", "price") |
| order_by_direction | str | No | "ASC" | Sort direction: "ASC" or "DESC" |

## Query Templates

| Template | Required Params | Example |
|----------|----------------|---------|
| by_symbol | symbol_pattern | `{"symbol_pattern": "%.C"}` |
| by_date | file_date | `{"file_date": "2025-07-21"}` |
| by_symbol_and_date | symbol_pattern, file_date | `{"symbol_pattern": "%.C", "file_date": "2025-07-21"}` |
| all_valid | none | `{}` |
| custom | conditions, values | `{"conditions": "price BETWEEN ? AND ?", "values": [100, 200]}` |

## Output Format

**Location**: `workspace/agents/market-data-agent/out/000001.json`

```json
{
  "data": [
    {
      "symbol": "XCME.OZN.AUG25.113.C",
      "bid": 0.007,
      "ask": 0.015625,
      "price": 0.011,
      "timestamp": "2025-07-21T12:00:00Z",
      "file_date": "2025-07-21",
      "is_valid": 1
    }
  ],
  "metadata": {
    "query": "SELECT * FROM market_data WHERE symbol LIKE ? AND is_valid = 1",
    "timestamp": "2025-11-11T12:00:00Z",
    "row_count": 42,
    "agent": "market-data-agent",
    "version": "1.0"
  }
}
```

## Run Log

**Location**: `workspace/agents/market-data-agent/logs/20251111_120000.json`

```json
{
  "run_id": "20251111_120000",
  "sql": "SELECT * FROM market_data WHERE symbol LIKE ? AND is_valid = 1",
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

## Advanced Examples

### Sorting by Date (Most Recent First)

```python
# Get 10 most recent ZN records
output_path = agent.run(
    template="by_symbol",
    params={"symbol_pattern": "%ZN%"},
    order_by_column="file_date",
    order_by_direction="DESC",
    limit=10
)
```

### Price Range Query with Custom Template

```python
# Find records where price is between 112.5 and 112.9
output_path = agent.run(
    template="custom",
    params={
        "conditions": "symbol LIKE ? AND price BETWEEN ? AND ? AND is_valid = 1",
        "values": ["%ZN%", 112.5, 112.9]
    },
    order_by_column="file_date",
    order_by_direction="DESC"
)
```

### Complex Query: Most Recent Date in Price Range

```python
# Single query that replaces 4 separate operations:
# Filter by symbol → Filter by price range → Sort by date → Get first result
output_path = agent.run(
    template="custom",
    params={
        "conditions": "symbol LIKE ? AND price BETWEEN ? AND ? AND is_valid = 1",
        "values": ["%ZN%", 112.5, 112.9]
    },
    order_by_column="file_date",
    order_by_direction="DESC",
    limit=1
)
```

### Price Comparison Query

```python
# Find all records where price > 100
output_path = agent.run(
    template="custom",
    params={
        "conditions": "price > ? AND is_valid = 1",
        "values": [100]
    },
    order_by_column="price",
    order_by_direction="DESC",
    limit=50
)
```

## Available Columns

All queries are restricted to these columns:
- `id`
- `symbol`
- `bid`
- `ask`
- `price`
- `bid_quantity`
- `offer_quantity`
- `timestamp`
- `file_date`
- `data_source`
- `is_valid`
- `created_at`

## Error Handling

### Validation Errors

```python
# Invalid template
ValueError: Invalid template: xyz. Available: by_symbol, by_date, ...

# Missing required parameter
ValueError: Template 'by_symbol' requires params: ['symbol_pattern']

# Invalid limit
ValueError: Limit exceeds maximum: 10000
```

### Failed Runs

Failed runs still create log files with error details:

```json
{
  "run_id": "20251111_120000",
  "status": "failed",
  "error": "Invalid template: xyz",
  "timestamp": "2025-11-11T12:00:00Z",
  "agent": "market-data-agent"
}
```

## Configuration

**File**: `src/agents/market_data_agent/config.py`

```python
AGENT_NAME = "market-data-agent"
AGENT_VERSION = "1.0"

DEFAULT_COLUMNS = [
    "symbol", "bid", "ask", "price", 
    "timestamp", "file_date", "is_valid"
]

MAX_ROWS = 10000

AVAILABLE_TEMPLATES = [
    "by_symbol", "by_date", 
    "by_symbol_and_date", "all_valid"
]
```

## Pre-Configured Test Queries

15 test queries available via `scripts/test_queries.py`:

1. All call options (%.C)
2. All put options (%.P)
3. OZN product
4. Specific instrument
5. Low bid prices
6. High ask prices
7. Wide spreads
8. Specific date
9. Recent data
10. High bid quantities
11. OZN calls
12. OZN puts
13. Low prices
14. Specific date and symbol
15. High volume

## Integration

### With Consumer Agent

```python
# Producer
from src.agents.market_data_agent.run import MarketDataAgent
agent = MarketDataAgent()
output_path = agent.run(template="by_symbol", params={"symbol_pattern": "%.C"})

# Consumer
from src.agents.consumer_agent.run import ConsumerAgent
consumer = ConsumerAgent()
stats_path = consumer.run(input_path=output_path)
```

## Performance

- SQL execution: ~150-200ms
- File write: ~2-5ms
- Total per query: ~200-250ms

## See Also

- [Consumer Agent](CONSUMER_AGENT.md) - Process market data statistics
- [Run Query Tool](../tools/RUN_QUERY_TOOL.md) - SQL execution tool
- [ARCHITECTURE.md](../ARCHITECTURE.md) - System architecture

