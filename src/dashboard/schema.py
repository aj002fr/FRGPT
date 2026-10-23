"""
Dashboard Config Schema - Defines the structure for dashboard configurations.

A Dashboard Config describes:
- Layout (grid columns, responsive breakpoints)
- Widgets (type, position, configuration)
- Metadata (title, run_id, timestamp)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
import json

from .components.registry import WIDGET_REGISTRY, validate_widget_config


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class WidgetPosition:
    """Position of a widget in the grid layout."""
    row: int = 0
    col: int = 0
    rowspan: int = 1
    colspan: int = 1
    
    def to_dict(self) -> Dict[str, int]:
        return {
            "row": self.row,
            "col": self.col,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, int]) -> "WidgetPosition":
        return cls(
            row=data.get("row", 0),
            col=data.get("col", 0),
            rowspan=data.get("rowspan", 1),
            colspan=data.get("colspan", 1),
        )


@dataclass
class WidgetConfig:
    """Configuration for a single widget instance."""
    id: str  # Unique ID for this widget instance
    type: str  # Widget type from WIDGET_REGISTRY
    position: WidgetPosition = field(default_factory=WidgetPosition)
    config: Dict[str, Any] = field(default_factory=dict)
    title: Optional[str] = None
    visible: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "position": self.position.to_dict(),
            "config": self.config,
            "title": self.title,
            "visible": self.visible,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WidgetConfig":
        position = data.get("position", {})
        if isinstance(position, dict):
            position = WidgetPosition.from_dict(position)
        elif not isinstance(position, WidgetPosition):
            position = WidgetPosition()
        
        return cls(
            id=data.get("id", ""),
            type=data.get("type", ""),
            position=position,
            config=data.get("config", {}),
            title=data.get("title"),
            visible=data.get("visible", True),
        )
    
    def validate(self) -> List[str]:
        """Validate this widget configuration."""
        errors = []
        
        if not self.id:
            errors.append("Widget must have an id")
        
        if not self.type:
            errors.append("Widget must have a type")
        elif self.type not in WIDGET_REGISTRY:
            errors.append(f"Unknown widget type: {self.type}")
        else:
            # Validate config against widget schema
            config_errors = validate_widget_config(self.type, self.config)
            errors.extend(config_errors)
        
        return errors


@dataclass
class DashboardConfig:
    """
    Complete dashboard configuration.
    
    This is the primary output of the DashboardAgent - it describes
    what widgets to show and how to arrange them.
    """
    widgets: List[WidgetConfig] = field(default_factory=list)
    layout: str = "grid"  # "grid", "flex", "masonry"
    columns: int = 2
    title: Optional[str] = None
    answer_text: Optional[str] = None  # Text answer from agent
    run_id: Optional[str] = None
    query: Optional[str] = None
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "layout": self.layout,
            "columns": self.columns,
            "title": self.title,
            "widgets": [w.to_dict() for w in self.widgets],
            "answer_text": self.answer_text,
            "run_id": self.run_id,
            "query": self.query,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DashboardConfig":
        widgets_data = data.get("widgets", [])
        widgets = [WidgetConfig.from_dict(w) for w in widgets_data]
        
        return cls(
            widgets=widgets,
            layout=data.get("layout", "grid"),
            columns=data.get("columns", 2),
            title=data.get("title"),
            answer_text=data.get("answer_text"),
            run_id=data.get("run_id"),
            query=data.get("query"),
            created_at=data.get("created_at"),
            metadata=data.get("metadata", {}),
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "DashboardConfig":
        return cls.from_dict(json.loads(json_str))
    
    def validate(self) -> List[str]:
        """Validate the entire dashboard configuration."""
        errors = []
        
        if self.layout not in ["grid", "flex", "masonry"]:
            errors.append(f"Invalid layout: {self.layout}")
        
        if self.columns < 1 or self.columns > 6:
            errors.append(f"Columns must be between 1 and 6, got {self.columns}")
        
        # Validate each widget
        widget_ids = set()
        for widget in self.widgets:
            if widget.id in widget_ids:
                errors.append(f"Duplicate widget id: {widget.id}")
            widget_ids.add(widget.id)
            
            widget_errors = widget.validate()
            for err in widget_errors:
                errors.append(f"Widget '{widget.id}': {err}")
        
        return errors
    
    def add_widget(
        self,
        widget_type: str,
        widget_id: Optional[str] = None,
        position: Optional[Dict[str, int]] = None,
        config: Optional[Dict[str, Any]] = None,
        title: Optional[str] = None,
    ) -> "DashboardConfig":
        """
        Add a widget to the dashboard.
        
        Returns self for chaining.
        """
        if widget_id is None:
            widget_id = f"{widget_type}_{len(self.widgets) + 1}"
        
        pos = WidgetPosition.from_dict(position or {})
        
        widget = WidgetConfig(
            id=widget_id,
            type=widget_type,
            position=pos,
            config=config or {},
            title=title,
        )
        
        self.widgets.append(widget)
        return self
    
    def get_widget(self, widget_id: str) -> Optional[WidgetConfig]:
        """Get a widget by ID."""
        for widget in self.widgets:
            if widget.id == widget_id:
                return widget
        return None
    
    def remove_widget(self, widget_id: str) -> bool:
        """Remove a widget by ID. Returns True if found and removed."""
        for i, widget in enumerate(self.widgets):
            if widget.id == widget_id:
                self.widgets.pop(i)
                return True
        return False
    
    def get_live_widgets(self) -> List[WidgetConfig]:
        """Get all widgets that require live polling."""
        live_widgets = []
        for widget in self.widgets:
            widget_def = WIDGET_REGISTRY.get(widget.type, {})
            if widget_def.get("poll_interval_ms", 0) > 0:
                live_widgets.append(widget)
        return live_widgets


# =============================================================================
# VALIDATION FUNCTION
# =============================================================================

def validate_dashboard_config(config: Union[Dict[str, Any], DashboardConfig]) -> List[str]:
    """
    Validate a dashboard configuration.
    
    Args:
        config: Either a DashboardConfig object or a dict
        
    Returns:
        List of validation errors (empty if valid)
    """
    if isinstance(config, dict):
        config = DashboardConfig.from_dict(config)
    
    return config.validate()


# =============================================================================
# PRESET TEMPLATES
# =============================================================================

def create_pre_event_dashboard(
    event_type: str = "NFP",
    fwd_be: float = 6.5,
    cons_std: float = 26.0,
) -> DashboardConfig:
    """Create a pre-event analysis dashboard template."""
    return DashboardConfig(
        title=f"{event_type} Pre-Event Analysis",
        columns=2,
        widgets=[
            WidgetConfig(
                id="live_panel",
                type="live_stats_panel",
                position=WidgetPosition(row=0, col=0, colspan=2),
                config={
                    "event_type": event_type,
                    "mode": "pre_event",
                    "fwd_be": fwd_be,
                },
            ),
            WidgetConfig(
                id="price_paths",
                type="price_paths_chart",
                position=WidgetPosition(row=1, col=0),
                config={
                    "dates": [],  # Will be populated with historical dates
                    "window": "pre_event",
                    "normalize": True,
                    "show_envelope": True,
                },
                title="Historical Price Paths",
            ),
            WidgetConfig(
                id="box_plot",
                type="box_plot",
                position=WidgetPosition(row=1, col=1),
                config={
                    "bucket_by": "cons_std",
                    "bucket_width": 7,
                    "event_type": event_type,
                },
                title="B/E by Consensus Std",
            ),
            WidgetConfig(
                id="stats",
                type="stats_card",
                position=WidgetPosition(row=2, col=0),
                config={
                    "data_type": "be_multiplier",
                    "event_type": event_type,
                },
                title="B/E Statistics",
            ),
        ],
    )


def create_post_event_dashboard(
    event_type: str = "NFP",
    actual: Optional[float] = None,
    consensus: Optional[float] = None,
    surprise_pctl: Optional[float] = None,
) -> DashboardConfig:
    """Create a post-event analysis dashboard template."""
    return DashboardConfig(
        title=f"{event_type} Post-Event Analysis",
        columns=2,
        widgets=[
            WidgetConfig(
                id="live_panel",
                type="live_stats_panel",
                position=WidgetPosition(row=0, col=0, colspan=2),
                config={
                    "event_type": event_type,
                    "mode": "post_event",
                },
            ),
            WidgetConfig(
                id="event_info",
                type="event_info_card",
                position=WidgetPosition(row=1, col=0),
                config={
                    "event_type": event_type,
                    "event_date": "latest",
                },
                title="Event Details",
            ),
            WidgetConfig(
                id="price_paths",
                type="price_paths_chart",
                position=WidgetPosition(row=1, col=1),
                config={
                    "dates": [],
                    "window": "post_event",
                    "normalize": True,
                },
                title="Post-Event Price Paths",
            ),
            WidgetConfig(
                id="box_plot",
                type="box_plot",
                position=WidgetPosition(row=2, col=0),
                config={
                    "bucket_by": "surprise_pctl",
                    "bucket_width": 25,
                    "event_type": event_type,
                },
                title="B/E by Surprise Percentile",
            ),
            WidgetConfig(
                id="distribution",
                type="distribution_chart",
                position=WidgetPosition(row=2, col=1),
                config={
                    "data_type": "surprise",
                    "event_type": event_type,
                    "highlight_value": surprise_pctl,
                },
                title="Surprise Distribution",
            ),
        ],
    )


def create_adhoc_analysis_dashboard(
    dates: List[str],
    symbol: str = "TY",
) -> DashboardConfig:
    """Create an ad-hoc analysis dashboard template."""
    return DashboardConfig(
        title="Ad-Hoc Analysis",
        columns=2,
        widgets=[
            WidgetConfig(
                id="price_paths",
                type="price_paths_chart",
                position=WidgetPosition(row=0, col=0, colspan=2),
                config={
                    "dates": dates,
                    "window": "full_session",
                    "normalize": True,
                    "symbol": symbol,
                },
                title=f"Price Paths ({len(dates)} sessions)",
            ),
            WidgetConfig(
                id="stats",
                type="stats_card",
                position=WidgetPosition(row=1, col=0),
                config={
                    "data_type": "returns",
                },
                title="Session Statistics",
            ),
            WidgetConfig(
                id="table",
                type="data_table",
                position=WidgetPosition(row=1, col=1),
                config={
                    "data_type": "returns",
                    "columns": ["date", "open", "high", "low", "close", "return_bps"],
                },
                title="Session Data",
            ),
        ],
    )
