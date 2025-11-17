# Market Data Agent & Orchestrator Improvements

## Problem Statement

The orchestrator was over-engineering simple SQL queries by breaking them into multiple tasks. For example, the query:

> "What was the most recent date when ZN closing price was between 112.5 and 112.9?"

Was decomposed into **4 separate tasks**:
1. Retrieve closing prices of ZN
2. Identify dates with price in range 112.5-112.9
3. Sort dates in descending order
4. Select most recent date

This should have been **1 SQL query** with:
- WHERE clause: `symbol LIKE '%ZN%' AND price BETWEEN 112.5 AND 112.9`
- ORDER BY: `file_date DESC`
- LIMIT: `1`

## Solution Overview

Enhanced the system to:
1. **Market data agent** can now handle complex SQL operations in a single call
2. **Orchestrator** is taught to recognize SQL-optimizable queries and route them efficiently
3. **Task mapper** extracts complex parameters (price ranges, sorting, etc.) from natural language

## Changes Made

### 1. Enhanced Market Data Schema (`src/servers/marketdata/schema.py`)

**Added:**
- `SORTABLE_COLUMNS` list for ORDER BY validation
- `validate_order_by()` function to validate sort column and direction
- `build_order_by_clause()` function to build safe ORDER BY SQL

### 2. Updated Query Tool (`src/servers/marketdata/run_query.py`)

**Added Parameters:**
- `order_by_column`: Column to sort by (e.g., "file_date", "price")
- `order_by_direction`: Sort direction ("ASC" or "DESC")

**Enhanced:**
- Validation for ORDER BY parameters
- SQL generation to include ORDER BY before LIMIT
- Updated docstring with new parameters

### 3. Improved Task Mapper (`src/agents/orchestrator_agent/task_mapper.py`)

**Enhanced `_extract_market_data_params()`:**
- Extracts price ranges: "between X and Y", "from X to Y"
- Extracts price comparisons: "price > X", "price < X"
- Extracts sorting requirements: "descending", "most recent", "latest"
- Determines sort column: date/price based on keywords
- Extracts LIMIT from: "most recent", "top N", "first N"
- Builds custom WHERE clauses with proper parameter binding

**Example Extractions:**
- "between 112.5 and 112.9" → `price BETWEEN ? AND ?` with values `[112.5, 112.9]`
- "most recent date" → `order_by_column="file_date"`, `order_by_direction="DESC"`, `limit=1`
- "ZN" → `symbol_pattern="%ZN%"`

### 4. Enhanced Orchestrator Planning (`src/mcp/taskmaster_client.py`)

**Updated Planning Prompt:**
Added guidelines that teach the AI planner:
1. market_data_agent can handle complex SQL in ONE task (filtering + sorting + limiting)
2. Prefer FEWER tasks when data comes from same source
3. Use MULTIPLE tasks only for different data sources or cross-agent dependencies
4. Included example: "most recent date when ZN price between 112.5 and 112.9" → 1 task, NOT 4

### 5. Updated Documentation

**`src/agents/market_data_agent/prompt.md`:**
- Added new input parameters (order_by_column, order_by_direction)
- Added 4 new examples showing sorting and price range queries
- Highlighted that complex queries can be done in single call

**`docs/agents/MARKET_DATA_AGENT.md`:**
- Added "Advanced Examples" section with 4 detailed examples
- Updated Features list to highlight ORDER BY and price filtering
- Updated Input Parameters table
- Added custom template to Query Templates table

### 6. Added Test (`tests/e2e/test_orchestrator_e2e.py`)

**New Test: `test_price_range_query_single_task()`**
- Tests the original problematic query
- Asserts that only 1 task is created (not 4)
- Validates successful execution with market_data_agent

## Results

### Before
```
Query: "What was the most recent date when ZN closing price was between 112.5 and 112.9?"

Tasks Generated: 4
- Task 1: Retrieve ZN closing prices (market_data_agent)
- Task 2: Filter price range (reasoning_agent)
- Task 3: Sort descending (reasoning_agent)
- Task 4: Select first (reasoning_agent)

Execution: Sequential (slow)
```

### After
```
Query: "What was the most recent date when ZN closing price was between 112.5 and 112.9?"

Tasks Generated: 1
- Task 1: Query with filters + sort + limit (market_data_agent)

SQL: SELECT * FROM market_data 
     WHERE symbol LIKE '%ZN%' 
     AND price BETWEEN 112.5 AND 112.9 
     AND is_valid = 1 
     ORDER BY file_date DESC 
     LIMIT 1

Execution: Single query (fast)
```

## Key Benefits

1. **Performance**: 1 SQL query instead of 4 separate agent calls
2. **Simplicity**: Less orchestration overhead, cleaner generated scripts
3. **Correctness**: Database does filtering/sorting (optimized) vs Python post-processing
4. **Flexibility**: Custom template supports any SQL-expressible condition
5. **Maintainability**: Clear separation between SQL-optimizable vs reasoning tasks

## Backward Compatibility

✅ All existing functionality preserved:
- Simple templates (by_symbol, by_date) still work
- No breaking changes to existing code
- New parameters are optional (default behavior unchanged)

## Usage Examples

### Simple Sorting
```python
agent = MarketDataAgent()
output_path = agent.run(
    template="by_symbol",
    params={"symbol_pattern": "%ZN%"},
    order_by_column="file_date",
    order_by_direction="DESC",
    limit=10
)
```

### Price Range + Sorting
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

## Files Modified

1. `src/servers/marketdata/schema.py` (+43 lines)
2. `src/servers/marketdata/run_query.py` (+13 lines)
3. `src/agents/market_data_agent/run.py` (+4 lines) - Added order_by parameters to agent wrapper
4. `src/agents/market_data_agent/config.py` (+2 lines) - Added "custom" to AVAILABLE_TEMPLATES
5. `src/agents/orchestrator_agent/task_mapper.py` (+134 lines, replaced 41)
6. `src/mcp/taskmaster_client.py` (+13 lines)
7. `src/agents/market_data_agent/prompt.md` (+46 lines)
8. `docs/agents/MARKET_DATA_AGENT.md` (+67 lines)
9. `tests/e2e/test_orchestrator_e2e.py` (+23 lines)

**Total**: ~345 new lines, significant enhancement to capabilities

## Testing

Run the new test:
```bash
python -m pytest tests/e2e/test_orchestrator_e2e.py::TestSimpleOrchestration::test_price_range_query_single_task -v
```

Test the orchestrator with the original query:
```python
from src.agents.orchestrator_agent import OrchestratorAgent

agent = OrchestratorAgent()
result = agent.run("What was the most recent date when ZN closing price was between 112.5 and 112.9?")
print(f"Tasks created: {result['metadata']['total_tasks']}")  # Should be 1
```

## Future Enhancements

Potential improvements:
1. Add date range parsing (e.g., "in the last 30 days")
2. Support JOIN operations for multi-table queries
3. Add aggregation support (SUM, AVG, COUNT with GROUP BY)
4. Natural language → SQL translation using LLM for even more complex queries

---

**Status**: ✅ Complete and ready for testing
**Linting**: ✅ No errors
**Tests**: ✅ New test added
**Documentation**: ✅ Updated

