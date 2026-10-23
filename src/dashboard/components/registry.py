"""
Widget Registry - Defines all available dashboard widgets.

Each widget has:
- category: Grouping for UI (live, chart, stats, table)
- data_source: API endpoint for fetching data
- poll_interval_ms: For live widgets, how often to poll (0 = no polling)
- config_schema: JSON Schema for widget configuration
- default_config: Default configuration values
- render_mode: How to render (plotly, html, table)
"""

from typing import Any, Dict, List, Optional


# =============================================================================
# WIDGET DEFINITIONS
# =============================================================================

WIDGET_REGISTRY: Dict[str, Dict[str, Any]] = {
    
    # -------------------------------------------------------------------------
    # LIVE WIDGETS (auto-polling)
    # -------------------------------------------------------------------------
    
    "live_stats_panel": {
        "category": "live",
        "description": "Real-time stats panel showing TY price, reference price, B/E multiplier",
        "data_source": "/api/live",
        "poll_interval_ms": 2000,
        "render_mode": "html",
        "config_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "enum": ["NFP", "CPI", "PPI", "JOLTS"], "default": "NFP"},
                "mode": {"type": "string", "enum": ["pre_event", "post_event"], "default": "pre_event"},
                "show_be_multiplier": {"type": "boolean", "default": True},
                "show_high_low": {"type": "boolean", "default": True},
                "fwd_be": {"type": "number", "default": 6.5},
            },
            "required": ["event_type"]
        },
        "default_config": {
            "event_type": "NFP",
            "mode": "pre_event",
            "show_be_multiplier": True,
            "show_high_low": True,
            "fwd_be": 6.5,
        }
    },
    
    # -------------------------------------------------------------------------
    # CHART WIDGETS
    # -------------------------------------------------------------------------
    
    "price_paths_chart": {
        "category": "chart",
        "description": "Normalized price paths chart showing multiple trading sessions",
        "data_source": "/api/widget/price_paths",
        "poll_interval_ms": 0,
        "render_mode": "plotly",
        "config_schema": {
            "type": "object",
            "properties": {
                "dates": {"type": "array", "items": {"type": "string"}, "description": "Event dates to show"},
                "window": {"type": "string", "enum": ["pre_event", "post_event", "full_session"], "default": "pre_event"},
                "normalize": {"type": "boolean", "default": True},
                "show_envelope": {"type": "boolean", "default": True, "description": "Show statistical envelope (mean, median, percentiles)"},
                "symbol": {"type": "string", "default": "TY"},
            },
            "required": ["dates"]
        },
        "default_config": {
            "dates": [],
            "window": "pre_event",
            "normalize": True,
            "show_envelope": True,
            "symbol": "TY",
        }
    },
    
    "box_plot": {
        "category": "chart",
        "description": "Box plot showing B/E distribution by bucket",
        "data_source": "/api/widget/box_plot",
        "poll_interval_ms": 0,
        "render_mode": "plotly",
        "config_schema": {
            "type": "object",
            "properties": {
                "bucket_by": {"type": "string", "enum": ["cons_std", "surprise_pctl", "fwd_be"], "default": "cons_std"},
                "bucket_width": {"type": "number", "default": 7},
                "include_outliers": {"type": "boolean", "default": True},
                "event_type": {"type": "string", "default": "NFP"},
            },
            "required": ["bucket_by"]
        },
        "default_config": {
            "bucket_by": "cons_std",
            "bucket_width": 7,
            "include_outliers": True,
            "event_type": "NFP",
        }
    },
    
    "candlestick_chart": {
        "category": "chart",
        "description": "OHLC candlestick chart with optional event markers",
        "data_source": "/api/widget/candlestick",
        "poll_interval_ms": 0,
        "render_mode": "plotly",
        "config_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "default": "TY"},
                "start_date": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "End date (YYYY-MM-DD)"},
                "interval": {"type": "string", "enum": ["1m", "5m", "15m", "1h", "1d"], "default": "5m"},
                "show_events": {"type": "boolean", "default": True},
                "event_filter": {"type": "string", "description": "Filter events by name pattern"},
            },
            "required": ["symbol", "start_date", "end_date"]
        },
        "default_config": {
            "symbol": "TY",
            "interval": "5m",
            "show_events": True,
        }
    },
    
    "distribution_chart": {
        "category": "chart",
        "description": "Histogram/KDE distribution chart",
        "data_source": "/api/widget/distribution",
        "poll_interval_ms": 0,
        "render_mode": "plotly",
        "config_schema": {
            "type": "object",
            "properties": {
                "data_type": {"type": "string", "enum": ["returns", "surprise", "volatility", "be_multiplier"], "default": "returns"},
                "event_type": {"type": "string", "default": "NFP"},
                "bins": {"type": "integer", "default": 30},
                "show_kde": {"type": "boolean", "default": True},
                "highlight_value": {"type": "number", "description": "Value to highlight on the distribution"},
            },
            "required": ["data_type"]
        },
        "default_config": {
            "data_type": "returns",
            "bins": 30,
            "show_kde": True,
        }
    },
    
    "scatter_plot": {
        "category": "chart",
        "description": "Scatter plot with optional regression line",
        "data_source": "/api/widget/scatter",
        "poll_interval_ms": 0,
        "render_mode": "plotly",
        "config_schema": {
            "type": "object",
            "properties": {
                "x_field": {"type": "string", "description": "Field for X axis"},
                "y_field": {"type": "string", "description": "Field for Y axis"},
                "color_by": {"type": "string", "description": "Field to color points by"},
                "show_regression": {"type": "boolean", "default": True},
                "event_type": {"type": "string", "default": "NFP"},
            },
            "required": ["x_field", "y_field"]
        },
        "default_config": {
            "show_regression": True,
        }
    },
    
    # -------------------------------------------------------------------------
    # STATS WIDGETS
    # -------------------------------------------------------------------------
    
    "stats_card": {
        "category": "stats",
        "description": "Statistics summary card (mean, median, percentiles)",
        "data_source": "/api/widget/stats",
        "poll_interval_ms": 0,
        "render_mode": "html",
        "config_schema": {
            "type": "object",
            "properties": {
                "data_type": {"type": "string", "enum": ["returns", "be_multiplier", "surprise", "volatility"]},
                "event_type": {"type": "string", "default": "NFP"},
                "show_percentiles": {"type": "array", "items": {"type": "integer"}, "default": [5, 25, 50, 75, 95]},
                "title": {"type": "string"},
            },
            "required": ["data_type"]
        },
        "default_config": {
            "show_percentiles": [5, 25, 50, 75, 95],
        }
    },
    
    "event_info_card": {
        "category": "stats",
        "description": "Event information card (actual, consensus, surprise)",
        "data_source": "/api/widget/event_info",
        "poll_interval_ms": 0,
        "render_mode": "html",
        "config_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "default": "NFP"},
                "event_date": {"type": "string", "description": "Specific event date or 'latest'"},
                "show_history": {"type": "boolean", "default": False, "description": "Show recent history"},
            },
            "required": ["event_type"]
        },
        "default_config": {
            "event_date": "latest",
            "show_history": False,
        }
    },
    
    # -------------------------------------------------------------------------
    # TABLE WIDGETS
    # -------------------------------------------------------------------------
    
    "data_table": {
        "category": "table",
        "description": "Interactive data table with sorting and filtering",
        "data_source": "/api/widget/table",
        "poll_interval_ms": 0,
        "render_mode": "table",
        "config_schema": {
            "type": "object",
            "properties": {
                "data_type": {"type": "string", "enum": ["events", "ohlc", "returns", "custom"]},
                "columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to display"},
                "filters": {"type": "object", "description": "Column filters"},
                "sort_by": {"type": "string"},
                "sort_order": {"type": "string", "enum": ["asc", "desc"], "default": "desc"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["data_type"]
        },
        "default_config": {
            "sort_order": "desc",
            "limit": 50,
        }
    },
    
    # -------------------------------------------------------------------------
    # TEXT WIDGETS
    # -------------------------------------------------------------------------
    
    "text_answer": {
        "category": "text",
        "description": "Text answer panel for displaying agent response",
        "data_source": None,  # Data comes directly from agent
        "poll_interval_ms": 0,
        "render_mode": "html",
        "config_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Markdown/text content to display"},
                "title": {"type": "string", "default": "Analysis"},
            },
        },
        "default_config": {
            "title": "Analysis",
        }
    },
    
    "markdown_panel": {
        "category": "text",
        "description": "Markdown content panel",
        "data_source": None,
        "poll_interval_ms": 0,
        "render_mode": "html",
        "config_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["content"]
        },
        "default_config": {}
    },
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_widget_schema(widget_type: str) -> Optional[Dict[str, Any]]:
    """Get the full schema for a widget type."""
    return WIDGET_REGISTRY.get(widget_type)


def get_widget_categories() -> Dict[str, List[str]]:
    """Get widgets grouped by category."""
    categories: Dict[str, List[str]] = {}
    for widget_type, widget_def in WIDGET_REGISTRY.items():
        category = widget_def.get("category", "other")
        if category not in categories:
            categories[category] = []
        categories[category].append(widget_type)
    return categories


def get_widgets_by_category(category: str) -> List[str]:
    """Get all widget types in a category."""
    return [
        widget_type 
        for widget_type, widget_def in WIDGET_REGISTRY.items()
        if widget_def.get("category") == category
    ]


def get_default_config(widget_type: str) -> Dict[str, Any]:
    """Get default configuration for a widget type."""
    widget = WIDGET_REGISTRY.get(widget_type)
    if widget:
        return widget.get("default_config", {}).copy()
    return {}


def validate_widget_config(widget_type: str, config: Dict[str, Any]) -> List[str]:
    """
    Validate widget configuration against its schema.
    
    Returns list of validation errors (empty if valid).
    """
    widget = WIDGET_REGISTRY.get(widget_type)
    if not widget:
        return [f"Unknown widget type: {widget_type}"]
    
    schema = widget.get("config_schema", {})
    errors = []
    
    # Check required fields
    required = schema.get("required", [])
    for field in required:
        if field not in config:
            errors.append(f"Missing required field: {field}")
    
    # Basic type validation
    properties = schema.get("properties", {})
    for field, value in config.items():
        if field in properties:
            prop_schema = properties[field]
            expected_type = prop_schema.get("type")
            
            if expected_type == "string" and not isinstance(value, str):
                errors.append(f"Field '{field}' should be string, got {type(value).__name__}")
            elif expected_type == "number" and not isinstance(value, (int, float)):
                errors.append(f"Field '{field}' should be number, got {type(value).__name__}")
            elif expected_type == "boolean" and not isinstance(value, bool):
                errors.append(f"Field '{field}' should be boolean, got {type(value).__name__}")
            elif expected_type == "array" and not isinstance(value, list):
                errors.append(f"Field '{field}' should be array, got {type(value).__name__}")
            elif expected_type == "object" and not isinstance(value, dict):
                errors.append(f"Field '{field}' should be object, got {type(value).__name__}")
            
            # Check enum values
            if "enum" in prop_schema and value not in prop_schema["enum"]:
                errors.append(f"Field '{field}' must be one of {prop_schema['enum']}, got '{value}'")
    
    return errors
