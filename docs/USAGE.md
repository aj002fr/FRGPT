# Usage Guide

## Quick Start

```bash
# 1. Run demo
python main.py

# 2. See test queries
python scripts/test_queries.py --list

# 3. Run a query
python scripts/test_queries.py --query 1

# 4. View results
python scripts/show_logs.py
```

---

## Running Agents

### Producer Agent

**Using test script (easiest):**
```bash
python scripts/test_queries.py --query 1
```

**Using CLI:**
```bash
python scripts/run_agent.py producer --template by_symbol --params "{\"symbol_pattern\": \"%.C\"}" --limit 20
```

**In Python:**
```python
from src.agents.market_data_agent import MarketDataAgent

agent = MarketDataAgent()
output_path = agent.run(
    template="by_symbol",
    params={"symbol_pattern": "%.C"},
    limit=20
)
print(f"Output: {output_path}")
```

### Consumer Agent

**Using CLI:**
```bash
python scripts/run_agent.py consumer --input workspace/agents/market-data-agent/out/000001.json
```

**In Python:**
```python
from src.agents.consumer_agent import ConsumerAgent

consumer = ConsumerAgent()
output_path = consumer.run(producer_output_path)
```

---

## Query Templates

| Template | Required Params | Example |
|----------|----------------|---------|
| `by_symbol` | symbol_pattern | `{"symbol_pattern": "%.C"}` |
| `by_date` | file_date | `{"file_date": "2025-07-21"}` |
| `by_symbol_and_date` | symbol_pattern, file_date | `{"symbol_pattern": "%.C", "file_date": "2025-07-21"}` |
| `all_valid` | none | `{}` |

---

## Viewing Results

### Show All Logs & Artifacts
```bash
python scripts/show_logs.py
```

### View Specific Output
```bash
python -m json.tool workspace/agents/market-data-agent/out/000001.json
```

### View Run Log
```bash
python -m json.tool workspace/agents/market-data-agent/logs/20251111_120000.json
```

### Check Manifest
```bash
type workspace\agents\market-data-agent\meta.json
```

---

## Common Patterns

### Query by Product
```bash
# OZN
python scripts/run_agent.py producer --template by_symbol --params "{\"symbol_pattern\": \"XCME.OZN.%\"}"

# VY3
python scripts/run_agent.py producer --template by_symbol --params "{\"symbol_pattern\": \"XCME.VY3.%\"}"
```

### Query by Option Type
```bash
# Calls only
python scripts/run_agent.py producer --template by_symbol --params "{\"symbol_pattern\": \"%.C\"}"

# Puts only
python scripts/run_agent.py producer --template by_symbol --params "{\"symbol_pattern\": \"%.P\"}"
```

### Query with Multiple Filters
```bash
# OZN calls on specific date
python scripts/run_agent.py producer --template by_symbol_and_date --params "{\"symbol_pattern\": \"XCME.OZN.%.C\", \"file_date\": \"2025-07-21\"}"
```

---

## Testing

### Run E2E Tests
```bash
python -m pytest tests/e2e/ -v
```

### Run E2E Script
```bash
python scripts/e2e.py
```

### Run All Test Queries
```bash
python scripts/test_queries.py --consumer
```

---

## Workspace Management

### Clean Workspace
```powershell
Remove-Item -Recurse -Force workspace
```

### View Workspace
```bash
Get-ChildItem -Recurse workspace
```

---

## Troubleshooting

### Check logs
```bash
Get-Content logs\market_data_puller_20251111.log | Select-Object -Last 50
```

### Find errors
```bash
Get-Content logs\market_data_puller_20251111.log | Select-String "ERROR"
```

### Verify system
```bash
python main.py
```

---

**See `START_TESTING.md` for detailed testing instructions!**


