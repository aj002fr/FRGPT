"""
Dashboard Components Module.

Contains widget definitions, schemas, and data generators for each widget type.
"""

from .registry import WIDGET_REGISTRY, get_widget_schema, get_widget_categories

__all__ = [
    "WIDGET_REGISTRY",
    "get_widget_schema",
    "get_widget_categories",
]
