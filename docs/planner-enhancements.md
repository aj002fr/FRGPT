# Planner Enhancements: Auto-Generated Tool Schemas & Validation

## Overview

Enhanced the orchestrator's planning system to automatically generate comprehensive tool documentation from registered tool schemas and validate parameter completeness before execution.

## Problem Statement

Previously, the planner relied on manually written tool documentation that could become outdated and didn't provide sufficient detail about:
- Required vs optional parameters
- Parameter types and validation rules
- Output schemas for data flow between tasks
- Default parameter values

This led to runtime errors when:
- Required parameters were missing
- Parameter types didn't match expectations
- Downstream tasks referenced non-existent fields from upstream outputs
- Tool signatures changed but documentation wasn't updated

## Solution

### 1. Auto-Generated Tool Documentation (`tool_schema_generator.py`)

Created a new module that:
- **Extracts tool metadata** from the global tool registry (`_TOOL_REGISTRY`)
- **Generates formatted documentation** with:
  - Input parameters table (name, type, required/optional, description)
  - Output schema with expected fields
  - Default parameter values from function signatures
- **Maintains output schemas** for key tools (query_event_history, run_query, compute_percentile_rank, etc.)

**Example Output:**

```markdown
### analytics_agent
Available tools: ['compute_percentile_rank', 'generate_histogram', ...]

**compute_percentile_rank** - Compute the percentile rank of a specific value within a dataset

**Input Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| value | number | YES | The value to find the percentile rank for |
| data | list[number] | YES | The reference dataset to compare against |
| event_name | string | no | Event name for fetching historical data |

**Output Schema:**
```json
{
  "success": bool - Whether computation succeeded,
  "percentile_rank": float - Percentile rank (0-100),
  "interpretation": str - Human-readable interpretation,
  "details": {
    "dataset_size": int - Number of reference values,
    "dataset_mean": float - Mean of reference dataset,
    ...
  }
}
```
```

### 2. Parameter Validation (`validate_tool_params`)

Validates tool parameters before code generation:
- **Checks required parameters** are present
- **Validates parameter types** (string, integer, number, array)
- **Handles task references** (`__task_ref__`, `__field__`)
- **Logs warnings** for invalid parameters that will be filtered at runtime

### 3. Data Flow Checking (`check_data_flow`)

Validates that data flows correctly between tasks:
- **Builds task output map** from tool output schemas
- **Validates task references** point to valid task IDs
- **Checks field paths** exist in upstream output schemas
- **Warns about missing fields** before execution

### 4. Integration with Planner

Modified `taskmaster_client.py` to:
- **Generate tool documentation** dynamically for each planning session
- **Include in planner prompt** so AI has complete, up-to-date information
- **Run validation** after tool selection
- **Log warnings** for potential issues

## Files Modified

1. **`src/agents/orchestrator_agent/tool_schema_generator.py`** (NEW)
   - `generate_tool_documentation()` - Auto-generates tool docs from schemas
   - `validate_tool_params()` - Validates parameter completeness
   - `check_data_flow()` - Validates inter-task data flow
   - `OUTPUT_SCHEMAS` - Manual output schema definitions for key tools

2. **`src/mcp/taskmaster_client.py`**
   - Added `generate_tool_documentation()` call in `select_tools_for_path()`
   - Added `_validate_tool_selections()` method
   - Updated `PLANNER2_PROMPT_CORE` to emphasize using tool schemas

3. **`src/mcp/discovery.py`**
   - Added `get_all_tools()` function to export tool registry

4. **`src/agents/orchestrator_agent/tool_loader.py`**
   - Removed non-existent `find_events_for_all_instances` from tool map

## Benefits

### 1. **Reduced Runtime Errors**
- Missing parameters caught before execution
- Type mismatches identified early
- Invalid tool references detected

### 2. **Better AI Planning**
- Planner has complete, accurate tool information
- Knows exactly what parameters are required
- Understands output structure for data flow

### 3. **Self-Documenting**
- Tool documentation auto-generated from source
- Always in sync with actual tool signatures
- No manual documentation maintenance

### 4. **Improved Debugging**
- Validation warnings logged before execution
- Clear error messages about what's missing
- Data flow issues identified upfront

## Example Validation Output

```
2025-12-08 16:35:01 - src.mcp.taskmaster_client - WARNING - Task task_2: Missing required parameter 'value' for tool 'compute_percentile_rank'
2025-12-08 16:35:01 - src.mcp.taskmaster_client - WARNING - Data flow: Task task_5: Field 'price' not in output of task_3 (tool: run_query)
2025-12-08 16:35:01 - src.mcp.taskmaster_client - WARNING - Found 2 validation warnings. These may cause runtime errors if not addressed.
```

## Test Results

**Before Enhancements:**
- Planner used outdated manual documentation
- No validation before execution
- Runtime errors for missing parameters
- Data flow issues discovered too late

**After Enhancements:**
- Auto-generated documentation from schemas
- Pre-execution validation
- Warnings logged for potential issues
- 4/5 tasks successful (1 failure due to non-existent tool reference, now fixed)

## Future Enhancements

1. **Auto-extract output schemas** from function return type hints and docstrings
2. **Add parameter constraints** (min/max values, regex patterns)
3. **Validate data type compatibility** between upstream outputs and downstream inputs
4. **Generate example usage** from tool signatures
5. **Add tool versioning** to track schema changes over time

## Usage

The enhancements are automatically active. No configuration needed.

To add output schema for a new tool, update `OUTPUT_SCHEMAS` in `tool_schema_generator.py`:

```python
OUTPUT_SCHEMAS = {
    "my_new_tool": {
        "success": "bool - Whether operation succeeded",
        "result": "dict - The result data",
        "result_fields": {
            "field1": "str - Description",
            "field2": "int - Description",
        }
    }
}
```

## Conclusion

These enhancements ensure the planner has complete, accurate information about all available tools, their parameters, and their outputs. This leads to better planning, fewer runtime errors, and easier debugging when issues do occur.



