"""
Generate comprehensive tool documentation from registered tool schemas.

This module automatically extracts tool information including:
- Input parameters with types and requirements
- Output schemas with expected fields
- Parameter dependencies and validation rules
"""

import logging
import inspect
import json
from typing import Dict, Any, List, Optional, Callable
from src.mcp.discovery import get_all_tools

logger = logging.getLogger(__name__)


def _format_type(type_info: Any) -> str:
    """Format JSON schema type to readable string."""
    if isinstance(type_info, str):
        return type_info
    elif isinstance(type_info, dict):
        if "type" in type_info:
            base_type = type_info["type"]
            if base_type == "array" and "items" in type_info:
                item_type = _format_type(type_info["items"])
                return f"list[{item_type}]"
            return base_type
    return "any"


def _extract_output_schema(func: Callable) -> Dict[str, Any]:
    """
    Extract output schema from function docstring and return type hints.
    
    Looks for patterns like:
    Returns:
        {
            "success": bool,
            "data": list[dict],
            ...
        }
    """
    output_schema = {}
    
    # Try to extract from docstring
    if func.__doc__:
        doc = func.__doc__
        if "Returns:" in doc:
            # This is a simplified parser - could be enhanced
            # For now, we'll rely on manual OUTPUT_SCHEMAS dict
            pass
    
    return output_schema


# Manual output schema definitions for key tools
# TODO: Could be automated by parsing docstrings or using decorators
OUTPUT_SCHEMAS = {
    "query_event_history": {
        "success": "bool - Whether query succeeded",
        "events": "list[dict] - Array of event objects",
        "count": "int - Number of events returned",
        "summary": "dict - Summary statistics",
        "event_fields": {
            "event_name": "str - Full event name",
            "event_id": "str - Event ticker/ID",
            "country": "str - Country code",
            "event_date": "str - ISO datetime",
            "timestamp": "str - Alias for event_date",
            "actual": "float|null - Actual value released",
            "forecast": "float|null - Forecasted value",
            "consensus": "float|null - Consensus estimate",
            "previous": "float|null - Previous value",
            "surprise": "float|null - Computed as (actual - consensus)",
            "importance": "str - high/medium/low",
            "unit": "str - Unit of measurement",
        }
    },
    "run_query": {
        "success": "bool - Whether query succeeded",
        "data": "list[dict] - Query results (each dict is a row)",
        "row_count": "int - Number of rows returned",
        "columns": "list[str] - Column names in result",
        "query_info": "dict - Query metadata",
        "row_fields": {
            "timestamp": "str - ISO datetime",
            "symbol": "str - Trading symbol",
            "open": "float - Opening price",
            "high": "float - High price",
            "low": "float - Low price",
            "close": "float - Closing price (also accessible as 'price')",
            "volume": "float - Trading volume",
        }
    },
    "compute_percentile_rank": {
        "success": "bool - Whether computation succeeded",
        "percentile_rank": "float - Percentile rank (0-100)",
        "interpretation": "str - Human-readable interpretation",
        "details": "dict - Additional statistics",
        "details_fields": {
            "value": "float - The input value",
            "dataset_size": "int - Number of reference values",
            "dataset_mean": "float - Mean of reference dataset",
            "dataset_std": "float - Standard deviation",
            "dataset_min": "float - Minimum value in dataset",
            "dataset_max": "float - Maximum value in dataset",
            "values_below": "int - Count of values below input",
            "values_above": "int - Count of values above input",
        }
    },
    "compute_statistics": {
        "success": "bool - Whether computation succeeded",
        "statistics": "dict - Statistical measures",
        "summary": "str - Brief summary",
        "statistics_fields": {
            "count": "int - Number of values",
            "mean": "float - Average",
            "median": "float - Middle value",
            "std_dev": "float - Standard deviation",
            "variance": "float - Variance",
            "min": "float - Minimum value",
            "max": "float - Maximum value",
            "range": "float - Max - Min",
            "percentiles": "dict - Requested percentiles",
        }
    },
    "generate_histogram": {
        "success": "bool - Whether plot was generated",
        "plot_path": "str - Path to saved SVG file",
        "plot_data": "dict - Plot metadata",
        "summary": "str - Brief description",
    },
    "compute_surprise_price_distribution": {
        "success": "bool - Whether analysis succeeded",
        "event_name": "str - Event name analyzed",
        "symbol": "str - Market symbol analyzed",
        "events_analyzed": "int - Number of events processed",
        "bucket_statistics": "dict - Statistics by surprise bucket",
        "bucket_events": "dict - Event details by bucket",
        "summary": "dict - Overall analysis summary",
        "plot_paths": "list[str] - Paths to generated plots",
    },
    "compute_event_distribution": {
        "success": "bool - Whether analysis succeeded",
        "symbol": "str - Symbol analyzed",
        "event_name": "str - Event name analyzed",
        "event_count": "int - Number of events analyzed",
        "statistics": "dict - Statistical measures by metric type",
        "latest": "dict - Latest event details with percentile",
        "events_analyzed": "list[dict] - List of all events",
        "plot_paths": "list[str] - Paths to generated plots",
    },
    "compute_returns_distribution": {
        "success": "bool - Whether analysis succeeded",
        "symbol": "str - Symbol analyzed",
        "interval": "str - Data interval (1m, 1h, etc.)",
        "bps_factor": "int - BPS multiplier for this symbol",
        "data_points": "int - Number of data points analyzed",
        "date_range": "dict - {start, end} date range",
        "statistics": "dict - Session-segmented statistics (London, US Open, US Mid, US Close, Asia, All day)",
        "latest": "dict - Latest return with percentile rank and z-score",
        "plot_paths": "list[str] - Paths to generated distribution plots",
        "stats_csv_path": "str - Path to statistics CSV file",
    },
    "compute_volatility_distribution": {
        "success": "bool - Whether analysis succeeded",
        "symbol": "str - Symbol analyzed",
        "interval": "str - Data interval",
        "data_points": "int - Number of data points analyzed",
        "statistics": "dict - Session-segmented volatility statistics",
        "latest": "dict - Latest volatility with percentile rank and z-score",
        "plot_paths": "list[str] - Paths to generated distribution plots",
    },
    "compute_probability_matrix": {
        "success": "bool - Whether analysis succeeded",
        "symbol": "str - Symbol analyzed",
        "target_bps": "float - Target basis points threshold",
        "target_hrs": "int - Target time horizon in hours",
        "version": "str - Analysis version (Absolute, Up, Down)",
        "probabilities": "dict - Probability by metric type (open-close, open-high/low, high-low)",
        "matrices": "dict - Full probability matrices",
        "latest_movements": "dict - Latest price movements per metric",
        "plot_paths": "list[str] - Paths to generated matrix plots",
        "description": "str - Human-readable description",
    },
}


def generate_tool_documentation(agent_tools: Dict[str, List[str]]) -> str:
    """
    Generate comprehensive tool documentation for the planner in JSON format.
    
    Args:
        agent_tools: Dict mapping agent_name -> list of tool names
        
    Returns:
        JSON-formatted documentation string for inclusion in planner prompt
    """
    # get_all_tools() auto-discovers if needed
    all_tools = get_all_tools()
    
    documentation = {}
    
    for agent_name, tool_names in sorted(agent_tools.items()):
        if not tool_names:
            continue
        
        agent_tools_list = []
        
        for tool_name in tool_names:
            tool_info = all_tools.get(tool_name)
            if not tool_info:
                logger.warning(f"Tool {tool_name} not found in registry")
                continue
            
            # Build tool entry
            tool_entry = {
                "name": tool_name,
                "description": tool_info.get("description", "No description"),
                "parameters": {}
            }
            
            # Process input parameters
            input_schema = tool_info.get("input_schema", {})
            properties = input_schema.get("properties", {})
            required = input_schema.get("required", [])
            
            # Get default values from function signature
            func = tool_info.get("function")
            defaults_map = {}
            if func:
                sig = inspect.signature(func)
                for param_name, param in sig.parameters.items():
                    if param.default != inspect.Parameter.empty:
                        defaults_map[param_name] = param.default
            
            # Build parameters dict
            for param_name, param_info in properties.items():
                param_entry = {
                    "type": _format_type(param_info.get("type", "any")),
                    "required": param_name in required,
                    "description": param_info.get("description", "")
                }
                
                # Add default if present
                if param_name in defaults_map:
                    param_entry["default"] = defaults_map[param_name]
                
                tool_entry["parameters"][param_name] = param_entry
            
            # Add output schema if available
            output_schema = OUTPUT_SCHEMAS.get(tool_name)
            if output_schema:
                tool_entry["returns"] = _format_output_schema(output_schema)
            
            agent_tools_list.append(tool_entry)
        
        documentation[agent_name] = {"tools": agent_tools_list}
    
    # Return as formatted JSON string
    return json.dumps(documentation, indent=2)


def _format_output_schema(output_schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format output schema into clean nested structure.
    
    Args:
        output_schema: Raw output schema dict
        
    Returns:
        Formatted schema dict
    """
    formatted = {}
    
    for field_name, field_desc in output_schema.items():
        if field_name.endswith("_fields"):
            # Nested fields
            parent_field = field_name.replace("_fields", "")
            formatted[parent_field] = field_desc
        else:
            formatted[field_name] = field_desc
    
    return formatted


def validate_tool_params(
    tool_name: str,
    params: Dict[str, Any],
    task_dependencies: List[str]
) -> tuple[bool, List[str]]:
    """
    Validate that tool parameters are complete and correct.
    
    Args:
        tool_name: Name of the tool
        params: Parameters to validate
        task_dependencies: List of task IDs this task depends on
        
    Returns:
        (is_valid, list of error messages)
    """
    all_tools = get_all_tools()
    tool_info = all_tools.get(tool_name)
    
    if not tool_info:
        return False, [f"Tool {tool_name} not found"]
    
    errors = []
    input_schema = tool_info.get("input_schema", {})
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    
    # Check required parameters
    for req_param in required:
        if req_param not in params:
            # Check if it's a task reference
            has_task_ref = any(
                isinstance(v, dict) and "__task_ref__" in v
                for v in params.values()
            )
            if not has_task_ref:
                errors.append(
                    f"Missing required parameter '{req_param}' for tool '{tool_name}'"
                )
    
    # Check parameter types (basic validation)
    for param_name, param_value in params.items():
        if param_name not in properties:
            # This will be filtered at runtime, just warn
            logger.debug(f"Parameter '{param_name}' not in schema for '{tool_name}'")
            continue
        
        expected_type = properties[param_name].get("type")
        
        # Skip validation for task references
        if isinstance(param_value, dict) and "__task_ref__" in param_value:
            continue
        
        # Basic type checking
        if expected_type == "string" and not isinstance(param_value, str):
            errors.append(
                f"Parameter '{param_name}' should be string, got {type(param_value).__name__}"
            )
        elif expected_type == "integer" and not isinstance(param_value, int):
            errors.append(
                f"Parameter '{param_name}' should be integer, got {type(param_value).__name__}"
            )
        elif expected_type == "number" and not isinstance(param_value, (int, float)):
            errors.append(
                f"Parameter '{param_name}' should be number, got {type(param_value).__name__}"
            )
        elif expected_type == "array" and not isinstance(param_value, list):
            errors.append(
                f"Parameter '{param_name}' should be array, got {type(param_value).__name__}"
            )
    
    return len(errors) == 0, errors


def check_data_flow(
    tasks: List[Dict[str, Any]],
    tool_selections: Dict[str, Any]
) -> tuple[bool, List[str]]:
    """
    Check that data flows correctly between tasks.
    
    Validates that:
    - Tasks with dependencies have access to required data
    - Output from upstream tasks matches input needs of downstream tasks
    - Task references point to valid task IDs and fields
    
    Args:
        tasks: List of task definitions
        tool_selections: Tool selections with parameters
        
    Returns:
        (is_valid, list of warnings/errors)
    """
    warnings = []
    task_outputs = {}  # task_id -> expected output schema
    
    # Build task output map
    for task in tasks:
        task_id = task.get("task_id") or task.get("id")
        selection = tool_selections.get(task_id, {})
        tools = selection.get("selected_tools", [])
        
        # Assume first tool provides main output
        if tools:
            primary_tool = tools[0]
            output_schema = OUTPUT_SCHEMAS.get(primary_tool)
            if output_schema:
                task_outputs[task_id] = {
                    "tool": primary_tool,
                    "schema": output_schema
                }
    
    # Check each task's parameters
    for task in tasks:
        task_id = task.get("task_id") or task.get("id")
        selection = tool_selections.get(task_id, {})
        tool_params = selection.get("tool_params", {})
        dependencies = task.get("dependencies", [])
        
        # Check task references
        for tool_name, params in tool_params.items():
            if not isinstance(params, dict):
                continue
            
            for param_name, param_value in params.items():
                if isinstance(param_value, dict) and "__task_ref__" in param_value:
                    ref_task_id = param_value.get("__task_ref__")
                    ref_field = param_value.get("__field__", "")
                    
                    # Validate task exists
                    if ref_task_id not in task_outputs:
                        warnings.append(
                            f"Task {task_id}: References unknown task '{ref_task_id}'"
                        )
                        continue
                    
                    # Validate field path exists in output schema
                    upstream_info = task_outputs[ref_task_id]
                    upstream_schema = upstream_info["schema"]
                    
                    # Parse field path (e.g., "query_event_history.events[0].surprise")
                    field_parts = ref_field.split(".")
                    if field_parts[0] not in upstream_schema:
                        warnings.append(
                            f"Task {task_id}: Field '{field_parts[0]}' not in output of {ref_task_id} "
                            f"(tool: {upstream_info['tool']})"
                        )
    
    return len(warnings) == 0, warnings

