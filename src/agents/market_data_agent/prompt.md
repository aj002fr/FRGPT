# Market Data Agent

## Purpose
Query market data from database and write results to file bus for other agents to consume.

## Inputs
- `template`: Query template name (by_symbol, by_date, by_symbol_and_date, all_valid, custom)
- `params`: Query parameters (e.g., {"symbol_pattern": "%.C", "file_date": "2025-07-21"})
- `columns`: Optional list of columns to select (default: standard set)
- `limit`: Optional row limit
- `order_by_column`: Optional column to sort by (e.g., "file_date", "price")
- `order_by_direction`: Optional sort direction ("ASC" or "DESC", default: "ASC")

## Process
1. Validate inputs
2. Call marketdata tool via MCP client
3. Get next incremental filename from manifest
4. Write output to file bus: `workspace/agents/market-data-agent/out/000001.json`
5. Write run log: `workspace/agents/market-data-agent/logs/{timestamp}.json`
6. Increment manifest

## Outputs
- Data file: `workspace/agents/market-data-agent/out/{id:06d}.json`
- Run log: `workspace/agents/market-data-agent/logs/{timestamp}.json`

## Output Format
```json
{
  "data": [
    {"symbol": "XCME.OZN.AUG25.113.C", "bid": 0.007, ...},
    ...
  ],
  "metadata": {
    "query": "SELECT ... FROM market_data WHERE ...",
    "timestamp": "2025-11-11T12:00:00Z",
    "row_count": 42,
    "agent": "market-data-agent",
    "version": "1.0"
  }
}
```

## Run Log Format
```json
{
  "run_id": "20251111_120000",
  "sql": "SELECT * FROM market_data WHERE symbol LIKE ?",
  "params": {"symbol_pattern": "%.C"},
  "output_path": "workspace/agents/market-data-agent/out/000001.json",
  "status": "success",
  "row_count": 42,
  "timestamp": "2025-11-11T12:00:00Z",
  "duration_ms": 15.3
}
```

## Examples

### Query call options
```python
agent = MarketDataAgent()
output_path = agent.run(
    template="by_symbol",
    params={"symbol_pattern": "%.C"}
)
```

### Query specific date
```python
output_path = agent.run(
    template="by_date",
    params={"file_date": "2025-07-21"}
)
```

### Query with specific columns
```python
output_path = agent.run(
    template="by_symbol_and_date",
    params={
        "symbol_pattern": "XCME.OZN.%",
        "file_date": "2025-07-21"
    },
    columns=["symbol", "bid", "ask", "price"]
)
```

### Query with sorting
```python
output_path = agent.run(
    template="by_symbol",
    params={"symbol_pattern": "%ZN%"},
    order_by_column="file_date",
    order_by_direction="DESC",
    limit=10
)
```

### Query with price range (custom template)
```python
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

### Complex query: Most recent date with price in range
```python
# This single call replaces what used to require 4 separate tasks:
# 1. Get prices -> 2. Filter range -> 3. Sort -> 4. Get first
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


