"""
Dashboard Module - Chat-Driven Dynamic Dashboard System.

This module provides:
- Component registry with widget definitions
- Dashboard config schema and validation  
- Preset dashboard templates

The orchestrator already handles query understanding - this module
just formats the output for dashboard rendering.

Usage:
    from src.dashboard import DashboardConfig, WIDGET_REGISTRY
    from src.dashboard.schema import create_pre_event_dashboard
"""

from .schema import (
    DashboardConfig,
    WidgetConfig,
    WidgetPosition,
    validate_dashboard_config,
    create_pre_event_dashboard,
    create_post_event_dashboard,
    create_adhoc_analysis_dashboard,
)
from .components.registry import WIDGET_REGISTRY, get_widget_schema

__all__ = [
    "DashboardConfig",
    "WidgetConfig",
    "WidgetPosition",
    "validate_dashboard_config",
    "WIDGET_REGISTRY",
    "get_widget_schema",
    "create_pre_event_dashboard",
    "create_post_event_dashboard",
    "create_adhoc_analysis_dashboard",
]
