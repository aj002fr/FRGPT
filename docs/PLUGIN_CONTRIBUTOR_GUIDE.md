# Plugin Contributor Guide

**For external developers creating plugins as git submodules.**

Your repo will be pulled into `src/servers/analytics/plugins/your_plugin/` and auto-discovered. Follow this spec exactly.

---

## Repo Structure (Required)

```
your-plugin-repo/
├── __init__.py          # REQUIRED: Contains @register_tool functions
├── helpers.py           # Optional: Internal utilities
├── requirements.txt     # Optional: Extra dependencies
└── README.md            # Recommended: Usage docs
```

**Critical**: Your repo root becomes the plugin folder. The `__init__.py` MUST be at root level.

---

## `__init__.py` Template

```python
"""
Your Plugin Name - Brief description.

Provides:
- tool_one: Does X
- tool_two: Does Y
"""

from src.mcp.discovery import register_tool
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)


@register_tool(
    name="your_tool_name",                    # Globally unique name
    description="One-line description for AI planner",
    input_schema={
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "What this param does"
            },
            "param2": {
                "type": "integer",
                "description": "Optional param",
                "default": 10
            }
        },
        "required": ["param1"]
    },
    agent="analytics_agent"                   # Always use this for plugins
)
def your_tool_name(
    param1: str,
    param2: int = 10
) -> Dict[str, Any]:
    """
    Full docstring with Args/Returns.
    
    Args:
        param1: Description
        param2: Description
        
    Returns:
        Dict with success status and results
    """
    try:
        # Your logic here
        result = do_something(param1, param2)
        
        return {
            "success": True,
            "data": result,
            "metadata": {"tool": "your_tool_name"}
        }
    except Exception as e:
        logger.exception(f"Error in your_tool_name: {e}")
        return {"success": False, "error": str(e)}
```

---

## Checklist Before Submitting

### ✅ Structure
- [ ] `__init__.py` at repo root (not in a subdirectory)
- [ ] No `src/` or nested package structure — flat layout

### ✅ Imports
- [ ] Use `from src.mcp.discovery import register_tool`
- [ ] NO relative imports to parent packages
- [ ] Lazy-import optional dependencies (see below)

### ✅ Tool Registration
- [ ] Every tool has `@register_tool` decorator
- [ ] `name` is globally unique (prefix with your domain, e.g., `myteam_analyze_x`)
- [ ] `description` is clear, one-line (AI uses this for planning)
- [ ] `input_schema` is valid JSON Schema with `type: object` and `properties`
- [ ] `agent="analytics_agent"` is set

### ✅ Return Format
- [ ] Always return `{"success": True/False, ...}`
- [ ] On error: `{"success": False, "error": "message"}`
- [ ] On success: `{"success": True, "data": ..., "metadata": ...}`

### ✅ No Side Effects on Import
- [ ] No code runs at module load (no DB connections, no API calls)
- [ ] All setup happens inside function bodies

### ✅ Dependencies
- [ ] Core logic uses Python stdlib only, OR
- [ ] Extra deps are lazy-imported with graceful error messages

---

## Handling Extra Dependencies

If your plugin needs packages not in the main project, use lazy imports:

```python
@register_tool(name="my_fancy_tool", ...)
def my_fancy_tool(data: List[float]) -> Dict[str, Any]:
    # Lazy import with helpful error
    try:
        import pandas as pd
        import numpy as np
    except ImportError as e:
        return {
            "success": False,
            "error": f"Missing dependency: {e}. Install with: pip install pandas numpy"
        }
    
    # Now use pandas/numpy safely
    df = pd.DataFrame(data)
    return {"success": True, "data": df.describe().to_dict()}
```

**Include a `requirements.txt`** listing your extra deps (the project maintainer will decide how to handle):

```
pandas>=2.0.0
numpy>=1.24.0
```

---

## Input Schema Reference

Use JSON Schema format. Common patterns:

```python
input_schema={
    "type": "object",
    "properties": {
        # String
        "symbol": {"type": "string", "description": "Trading symbol"},
        
        # Integer with bounds
        "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 10000},
        
        # Float
        "threshold": {"type": "number", "default": 0.05},
        
        # Boolean
        "include_zeros": {"type": "boolean", "default": False},
        
        # Enum (fixed choices)
        "mode": {"type": "string", "enum": ["fast", "accurate", "balanced"]},
        
        # Array of numbers
        "values": {"type": "array", "items": {"type": "number"}},
        
        # Array of strings
        "symbols": {"type": "array", "items": {"type": "string"}},
        
        # Optional date string
        "start_date": {"type": "string", "format": "date", "description": "YYYY-MM-DD"}
    },
    "required": ["symbol", "values"]  # Only truly required params
}
```

---

## Testing Your Plugin Locally

Before pushing, test that your plugin loads correctly:

```python
# test_my_plugin.py (run from main project root)
import sys
sys.path.insert(0, ".")  # Add project root to path

# Simulate plugin discovery
from src.mcp.discovery import discover_tools, get_tool

# Discover all tools (including yours)
tools = discover_tools()
print(f"Discovered {len(tools)} tools")

# Check your tool is registered
if "your_tool_name" in tools:
    print("✅ Tool registered successfully")
    print(f"   Description: {tools['your_tool_name']['description']}")
else:
    print("❌ Tool NOT found - check your __init__.py")

# Test calling it
my_tool = get_tool("your_tool_name")
result = my_tool(param1="test", param2=5)
print(f"Result: {result}")
assert result["success"] == True, f"Tool failed: {result.get('error')}"
```

---

## What NOT to Do

| ❌ Don't | ✅ Do Instead |
|---------|---------------|
| Put `__init__.py` in a subfolder | Keep it at repo root |
| Use `from . import helpers` | Use `from .helpers import func` |
| Import heavy libs at module level | Lazy-import inside functions |
| Run setup code on import | Initialize inside function bodies |
| Use generic names like `analyze` | Use prefixed names like `myteam_analyze_returns` |
| Return bare dicts | Always include `success: True/False` |
| Raise exceptions to caller | Catch and return `{"success": False, "error": ...}` |
| Modify global state | Keep functions pure |

---

## Naming Convention

To avoid collisions with other plugins:

```
{team/domain}_{action}_{target}

Examples:
- quant_compute_sharpe_ratio
- risk_calculate_var
- viz_plot_correlation_matrix
- data_fetch_earnings_calendar
```

---

## Available Utilities (Optional)

If you want to use existing project utilities:

```python
# Plotting (generates PNG files)
from src.servers.analytics.core_plotting import (
    plot_distribution,      # Histogram with KDE
    plot_timeseries,        # Line charts
    plot_categorical,       # Bar charts
)

# Schema utilities
from src.servers.analytics.schema import (
    get_plots_dir,          # Returns Path to workspace/plots/
    validate_numeric_data,  # Validates arrays
)

# Get workspace path
from pathlib import Path
def get_workspace() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent / "workspace"
```


---

## Example: Complete Minimal Plugin

```python
"""
Acme Analytics Plugin - Custom return analysis tools.
"""

from src.mcp.discovery import register_tool
from typing import Dict, Any, List
import logging
import math

logger = logging.getLogger(__name__)


def _calc_mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


@register_tool(
    name="acme_calculate_returns",
    description="Calculate simple and log returns from a price series",
    input_schema={
        "type": "object",
        "properties": {
            "prices": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Array of prices (oldest first)"
            },
            "log_returns": {
                "type": "boolean",
                "default": False,
                "description": "Calculate log returns instead of simple"
            }
        },
        "required": ["prices"]
    },
    agent="analytics_agent"
)
def acme_calculate_returns(
    prices: List[float],
    log_returns: bool = False
) -> Dict[str, Any]:
    """Calculate returns from price series."""
    
    if len(prices) < 2:
        return {"success": False, "error": "Need at least 2 prices"}
    
    try:
        returns = []
        for i in range(1, len(prices)):
            if prices[i-1] <= 0:
                continue
            if log_returns:
                ret = math.log(prices[i] / prices[i-1])
            else:
                ret = (prices[i] - prices[i-1]) / prices[i-1]
            returns.append(round(ret, 6))
        
        return {
            "success": True,
            "returns": returns,
            "statistics": {
                "count": len(returns),
                "mean": round(_calc_mean(returns), 6),
                "min": min(returns) if returns else 0,
                "max": max(returns) if returns else 0
            },
            "metadata": {
                "log_returns": log_returns,
                "input_prices": len(prices)
            }
        }
    except Exception as e:
        logger.exception(f"Error calculating returns: {e}")
        return {"success": False, "error": str(e)}
```

---

## After You Push

1. Notify the project maintainer with your repo URL
2. They will add it as a submodule:
   ```bash
   cd src/servers/analytics/plugins
   git submodule add git@github.com:yourteam/your-plugin.git your_plugin
   ```
3. On next server restart, your tools are auto-discovered

---

## Questions?

If something's unclear, check the example plugin at:
`src/servers/analytics/plugins/_example/__init__.py`

Or open an issue in the main project repo.

