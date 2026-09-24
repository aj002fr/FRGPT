# Analytics Plugins

This directory contains plugin modules that extend the analytics server with additional tools.

Plugins are automatically discovered and loaded when the analytics server starts.

## Quick Start

### Creating a Plugin

1. Create a new directory with an `__init__.py`:

```
src/servers/analytics/plugins/
└── my_plugin/
    ├── __init__.py    # Required: Contains @register_tool functions
    ├── helpers.py     # Optional: Internal helpers
    └── README.md      # Recommended: Documentation
```

2. Define your tools in `__init__.py`:

```python
"""My Custom Plugin - provides custom analytics tools."""

from src.mcp.discovery import register_tool
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)


@register_tool(
    name="my_custom_tool",
    description="Brief description of what the tool does",
    input_schema={
        "type": "object",
        "properties": {
            "data": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Input data array"
            },
            "option": {
                "type": "string",
                "description": "An optional parameter",
                "default": "default_value"
            }
        },
        "required": ["data"]
    },
    agent="analytics_agent"  # Optional: auto-detected from path
)
def my_custom_tool(
    data: List[float],
    option: str = "default_value"
) -> Dict[str, Any]:
    """
    Generate custom analytics.
    
    Args:
        data: Input data array
        option: Optional configuration
        
    Returns:
        Dictionary with success status and results
    """
    try:
        # Your implementation here
        result = sum(data) / len(data) if data else 0
        
        return {
            "success": True,
            "result": result,
            "metadata": {"count": len(data)}
        }
    except Exception as e:
        logger.exception(f"Error in my_custom_tool: {e}")
        return {"success": False, "error": str(e)}
```

3. That's it! The tool is automatically discovered on next server restart.

---

## Adding a Plugin via Git Submodule

For team members developing plugins in separate repositories:

### In your plugin repository:

```bash
# Initialize your plugin repo
mkdir my-analytics-plugin && cd my-analytics-plugin
git init

# Create __init__.py with your @register_tool functions
# Create README.md documenting your tools

git add .
git commit -m "Initial plugin"
git remote add origin git@github.com:yourteam/my-analytics-plugin.git
git push -u origin main
```

### In the main project:

```bash
cd src/servers/analytics/plugins
git submodule add git@github.com:yourteam/my-analytics-plugin.git my_plugin
git commit -m "Add my_plugin submodule"
git push
```

### For users pulling changes:

```bash
git pull
git submodule update --init --recursive
```

---

## Plugin Requirements

### Required

| Item | Description |
|------|-------------|
| `__init__.py` | Entry point file in plugin directory |
| `@register_tool` decorator | Import from `src.mcp.discovery` |
| `input_schema` | JSON Schema defining parameters |
| `description` | Clear description for AI planner |
| Return dict with `success` | All tools must return `{"success": True/False, ...}` |

### Recommended

| Item | Description |
|------|-------------|
| Type hints | Function parameters and return type |
| Docstring | Detailed documentation |
| Error handling | Graceful failure with error messages |
| README.md | Usage documentation |

---

## Return Format Convention

### Success Response

```python
{
    "success": True,
    "data": {...},           # Tool-specific results
    "metadata": {...}        # Optional metadata
}
```

### Error Response

```python
{
    "success": False,
    "error": "Human-readable error message"
}
```

### For Plotting Tools

```python
{
    "success": True,
    "png_path": "/absolute/path/to/plot.png",
    "metadata": {
        "plot_type": "histogram",
        "data_points": 100
    }
}
```

---

## Input Schema Format

Use JSON Schema to define parameters:

```python
input_schema={
    "type": "object",
    "properties": {
        # String parameter
        "symbol": {
            "type": "string",
            "description": "Trading symbol (e.g., 'ZN', 'ES')"
        },
        # Integer with bounds
        "limit": {
            "type": "integer",
            "description": "Max results",
            "default": 100,
            "minimum": 1,
            "maximum": 10000
        },
        # Enum (fixed choices)
        "chart_type": {
            "type": "string",
            "enum": ["line", "bar", "scatter"],
            "description": "Type of chart"
        },
        # Array of numbers
        "values": {
            "type": "array",
            "items": {"type": "number"},
            "description": "Numeric values"
        }
    },
    "required": ["symbol", "values"]
}
```

---

## Available Utilities

You can import these from the main codebase:

```python
# Core plotting (generates PNG files)
from src.servers.analytics.core_plotting import (
    plot_distribution,      # Histogram with KDE
    plot_timeseries,        # Line/candlestick charts
    plot_categorical,       # Bar charts
    plot_correlation,       # Scatter plots with regression
    plot_multi_distribution # Grid of distributions
)

# Schema utilities
from src.servers.analytics.schema import (
    get_plots_dir,          # Returns Path to workspace/plots/
    validate_numeric_data,  # Validates and converts data to floats
    format_number,          # Formats numbers for display
    DEFAULT_PERCENTILES,    # [5, 10, 25, 50, 75, 90, 95]
    COLOR_SCHEMES,          # "default", "dark", "warm"
)

# Plot factory (context-aware routing)
from src.servers.analytics.plot_factory import (
    create_plot,            # Unified plotting interface
    get_available_contexts  # List of supported contexts
)

# Logging
import logging
logger = logging.getLogger(__name__)
```

---

## Error Handling Pattern

```python
@register_tool(name="my_tool", ...)
def my_tool(param1: str, param2: int = 10) -> Dict[str, Any]:
    """Tool docstring."""
    
    # 1. Validate inputs early
    if not param1:
        return {"success": False, "error": "param1 is required"}
    
    # 2. Check optional dependencies (lazy import)
    try:
        import pandas as pd
    except ImportError:
        return {
            "success": False, 
            "error": "Missing dependency: pip install pandas"
        }
    
    # 3. Wrap main logic in try/except
    try:
        result = do_something(param1, param2)
        return {
            "success": True,
            "data": result,
            "metadata": {"param1": param1}
        }
    except Exception as e:
        logger.exception(f"Error in my_tool: {e}")
        return {"success": False, "error": str(e)}
```

---

## Testing Your Plugin

Before mounting as a submodule, test locally:

```python
# test_my_plugin.py
import sys
sys.path.insert(0, "/path/to/market_data_puller")

from src.servers.analytics.plugins.my_plugin import my_custom_tool

result = my_custom_tool(data=[1, 2, 3, 4, 5])
print(result)
assert result["success"] == True
```

---

## Directory Naming

| Pattern | Behavior |
|---------|----------|
| `my_plugin/` | Normal plugin, auto-discovered |
| `_example/` | Example plugin, loaded for reference |
| `_internal/` | Ignored (starts with `_`) |
| `__pycache__/` | Ignored (Python cache) |
| `.hidden/` | Ignored (starts with `.`) |

---

## Troubleshooting

### Plugin not loading?

1. Check that `__init__.py` exists in your plugin directory
2. Check logs for import errors: `logger = logging.getLogger(__name__)`
3. Ensure `src.mcp.discovery` is importable

### Tool not appearing?

1. Verify `@register_tool` decorator is applied
2. Check that `input_schema` has `"type": "object"` and `"properties"`
3. Restart the server after adding new tools

### Import errors?

1. Use lazy imports for optional dependencies
2. Ensure the main project's PYTHONPATH includes the project root

