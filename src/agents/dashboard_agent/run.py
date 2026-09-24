"""
Dashboard Agent - Transforms orchestrator output into dashboard configuration.

This is a thin wrapper that:
1. Calls the existing OrchestratorAgent (same pipeline as Telegram)
2. Maps worker outputs to dashboard widgets based on what tools were called
3. Returns a DashboardConfig JSON for frontend rendering

The orchestrator already handles:
- Query understanding (Planner1)
- Tool selection (Planner2)
- Execution (Workers)
- Answer generation (RunnerAgent)

We just reformat the output for dashboard rendering.
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.bus.file_bus import ensure_dir, write_atomic
from src.bus.manifest import Manifest

from src.dashboard.schema import (
    DashboardConfig,
    WidgetConfig,
    WidgetPosition,
)
from src.dashboard.components.registry import WIDGET_REGISTRY

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    get_workspace_path,
    get_out_path,
    get_logs_path,
)

logger = logging.getLogger(__name__)


# =============================================================================
# TOOL → WIDGET MAPPING
# =============================================================================

# Maps orchestrator tools to dashboard widgets
TOOL_TO_WIDGET_MAP = {
    # Candlestick/chart tools
    "generate_candlestick_chart": "candlestick_chart",
    "generate_multi_candlestick_charts": "candlestick_chart",
    "plot_timeseries": "candlestick_chart",
    
    # Distribution tools
    "compute_returns_distribution": "distribution_chart",
    "compute_volatility_distribution": "distribution_chart",
    "compute_event_distribution": "distribution_chart",
    "plot_distribution": "distribution_chart",
    "plot_log_bps_distribution": "distribution_chart",
    
    # Correlation/scatter tools
    "compute_surprise_correlation": "scatter_plot",
    "compute_surprise_price_distribution": "scatter_plot",
    "plot_correlation": "scatter_plot",
    
    # Table/data tools
    "run_query": "data_table",
    "run_multi_date_query": "data_table",
    "query_event_history": "data_table",
    "search_events": "data_table",
    
    # Event info tools
    "get_next_event_instance": "event_info_card",
    "get_historical_event_instances": "event_info_card",
    
    # Stats tools
    "compute_statistics": "stats_card",
    "compute_percentile_rank": "stats_card",
    
    # Probability matrix
    "compute_probability_matrix": "box_plot",
}

# Agent → default widgets when no specific tool mapping
AGENT_DEFAULT_WIDGETS = {
    "market_data_agent": ["data_table"],
    "analytics_agent": ["distribution_chart", "stats_card"],
    "eventdata_puller_agent": ["event_info_card", "data_table"],
    "polymarket_agent": ["data_table"],
}


class DashboardAgent:
    """
    Dashboard Agent - thin wrapper around OrchestratorAgent that
    formats output for dashboard rendering.
    """
    
    def __init__(self):
        """Initialize the dashboard agent."""
        self.workspace = get_workspace_path()
        ensure_dir(self.workspace)
        ensure_dir(get_out_path())
        ensure_dir(get_logs_path())
        
        self.manifest = Manifest(self.workspace)
        self._orchestrator = None
        
        logger.info(f"DashboardAgent initialized (v{AGENT_VERSION})")
    
    @property
    def orchestrator(self):
        """Lazy-load the orchestrator to avoid circular imports."""
        if self._orchestrator is None:
            from src.agents.orchestrator_agent import OrchestratorAgent
            self._orchestrator = OrchestratorAgent()
        return self._orchestrator
    
    def run(
        self,
        query: str,
        skip_orchestrator: bool = False,
        orchestrator_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a query and return a dashboard configuration.
        
        Args:
            query: Natural language query
            skip_orchestrator: If True, use orchestrator_result instead of running
            orchestrator_result: Pre-computed orchestrator result (e.g., from Telegram bot reuse)
            
        Returns:
            Dictionary with:
                - dashboard_config: DashboardConfig as dict
                - answer_text: Text answer from orchestrator
                - run_id: Run ID for tracking
                - metadata: Execution metadata
        """
        start_time = time.time()
        
        logger.info(f"DashboardAgent processing query: {query[:100]}...")
        
        # Step 1: Run orchestrator (or use provided result)
        if orchestrator_result:
            result = orchestrator_result
            logger.info("Using pre-computed orchestrator result")
        elif not skip_orchestrator:
            logger.info("Running orchestrator...")
            result = self.orchestrator.run(query=query, skip_validation=True)
        else:
            # Minimal result for testing without orchestrator
            result = {
                "answer": "Orchestrator skipped",
                "worker_outputs": [],
                "metadata": {},
            }
        
        # Extract from orchestrator result
        answer_text = result.get("answer", "")
        worker_outputs = result.get("worker_outputs", [])
        run_id = result.get("metadata", {}).get("orchestration", {}).get("run_id")
        
        # Step 2: Map worker outputs to widgets
        dashboard = self._build_dashboard_from_outputs(
            query=query,
            answer_text=answer_text,
            worker_outputs=worker_outputs,
            run_id=run_id,
            orchestrator_metadata=result.get("metadata", {}),
        )
        
        # Step 3: Build response
        duration_ms = (time.time() - start_time) * 1000
        
        response = {
            "dashboard_config": dashboard.to_dict(),
            "answer_text": answer_text,
            "run_id": run_id,
            "metadata": {
                "agent": AGENT_NAME,
                "version": AGENT_VERSION,
                "duration_ms": duration_ms,
                "timestamp": datetime.now().isoformat(),
                "widget_count": len(dashboard.widgets),
                "orchestrator": result.get("metadata", {}),
            },
        }
        
        # Step 4: Write output to file bus
        output_path = self._write_output(response)
        response["output_path"] = str(output_path)
        
        logger.info(f"DashboardAgent completed in {duration_ms:.1f}ms with {len(dashboard.widgets)} widgets")
        
        return response
    
    def _build_dashboard_from_outputs(
        self,
        query: str,
        answer_text: str,
        worker_outputs: List[Dict[str, Any]],
        run_id: Optional[str],
        orchestrator_metadata: Dict[str, Any],
    ) -> DashboardConfig:
        """
        Build dashboard config by mapping worker outputs to widgets.
        
        Looks at what tools were actually called and creates corresponding widgets.
        """
        dashboard = DashboardConfig(
            title=self._generate_title(query),
            columns=2,
            query=query,
            answer_text=answer_text,
            run_id=run_id,
        )
        
        # Track widgets to add (avoid duplicates)
        widgets_to_add = []
        seen_widget_types = set()
        
        # Add text answer widget first
        if answer_text:
            widgets_to_add.append({
                "type": "text_answer",
                "config": {"content": answer_text, "title": "Analysis"},
                "full_width": True,
            })
        
        # Process each worker output
        for output in worker_outputs:
            agent_name = output.get("agent", "")
            output_data = output.get("output_data", {})
            
            if not isinstance(output_data, dict):
                continue
            
            # Look at each tool result
            for tool_name, tool_result in output_data.items():
                widget_type = TOOL_TO_WIDGET_MAP.get(tool_name)
                
                if widget_type and widget_type not in seen_widget_types:
                    seen_widget_types.add(widget_type)
                    
                    widget_config = self._extract_widget_config(
                        widget_type=widget_type,
                        tool_name=tool_name,
                        tool_result=tool_result,
                    )
                    
                    widgets_to_add.append({
                        "type": widget_type,
                        "config": widget_config,
                        "data": tool_result,  # Include the data for frontend rendering
                    })
        
        # If no specific widgets found, add defaults based on agents used
        if len(widgets_to_add) <= 1:  # Only have answer widget
            agents_used = set(o.get("agent", "") for o in worker_outputs)
            for agent in agents_used:
                for widget_type in AGENT_DEFAULT_WIDGETS.get(agent, []):
                    if widget_type not in seen_widget_types:
                        seen_widget_types.add(widget_type)
                        widgets_to_add.append({
                            "type": widget_type,
                            "config": {},
                        })
        
        # Add widgets to dashboard with auto-positioning
        row = 0
        col = 0
        
        for i, widget_spec in enumerate(widgets_to_add):
            widget_type = widget_spec["type"]
            is_full_width = widget_spec.get("full_width", False)
            
            # Charts and live panels get full width
            widget_def = WIDGET_REGISTRY.get(widget_type, {})
            is_chart = widget_type.endswith("_chart") or widget_type == "box_plot"
            is_live = widget_def.get("poll_interval_ms", 0) > 0
            
            colspan = 2 if (is_full_width or is_chart or is_live) else 1
            
            dashboard.add_widget(
                widget_type=widget_type,
                widget_id=f"{widget_type}_{i+1}",
                position={"row": row, "col": col, "colspan": colspan},
                config=widget_spec.get("config", {}),
            )
            
            # Store data reference in widget metadata if available
            if "data" in widget_spec:
                widget = dashboard.widgets[-1]
                widget.config["_data"] = widget_spec["data"]
            
            if colspan == 2:
                row += 1
                col = 0
            else:
                col += 1
                if col >= 2:
                    col = 0
                    row += 1
        
        return dashboard
    
    def _extract_widget_config(
        self,
        widget_type: str,
        tool_name: str,
        tool_result: Any,
    ) -> Dict[str, Any]:
        """
        Extract widget configuration from tool result.
        """
        config = {}
        
        if not isinstance(tool_result, dict):
            return config
        
        # Extract common fields
        if "symbol" in tool_result:
            config["symbol"] = tool_result["symbol"]
        
        if "event_type" in tool_result or "event_name" in tool_result:
            config["event_type"] = tool_result.get("event_type") or tool_result.get("event_name")
        
        # Extract plot paths (for pre-rendered images)
        if "png_path" in tool_result:
            config["image_path"] = tool_result["png_path"]
        elif "plot_paths" in tool_result and tool_result["plot_paths"]:
            config["image_path"] = tool_result["plot_paths"][0]
        
        # Extract data for tables
        if "data" in tool_result:
            config["data"] = tool_result["data"]
        
        # Extract statistics
        if "statistics" in tool_result:
            config["statistics"] = tool_result["statistics"]
        
        return config
    
    def _generate_title(self, query: str) -> str:
        """Generate a dashboard title from the query."""
        if len(query) > 60:
            return query[:57] + "..."
        return query
    
    def _write_output(self, response: Dict[str, Any]) -> Path:
        """Write the response to the file bus."""
        output_id = self.manifest.increment()
        output_path = get_out_path() / f"{output_id:06d}.json"
        
        # Don't include large data blobs in the saved file
        save_response = response.copy()
        if "dashboard_config" in save_response:
            config = save_response["dashboard_config"].copy()
            for widget in config.get("widgets", []):
                # Remove inline data, keep reference
                if "_data" in widget.get("config", {}):
                    del widget["config"]["_data"]
            save_response["dashboard_config"] = config
        
        write_atomic(output_path, json.dumps(save_response, indent=2, default=str))
        
        logger.info(f"Wrote output to {output_path}")
        return output_path


# =============================================================================
# STANDALONE TESTING
# =============================================================================

def main():
    """Test the dashboard agent standalone."""
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Show TY chart on last NFP"
    
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"{'='*60}\n")
    
    agent = DashboardAgent()
    
    # Simulate orchestrator output for testing
    mock_result = {
        "answer": "Here is the TY candlestick chart for the last NFP event.",
        "worker_outputs": [
            {
                "agent": "analytics_agent",
                "output_data": {
                    "generate_candlestick_chart": {
                        "success": True,
                        "symbol": "TY",
                        "png_path": "/path/to/chart.png",
                    }
                }
            }
        ],
        "metadata": {"orchestration": {"run_id": "test_123"}},
    }
    
    result = agent.run(query, orchestrator_result=mock_result)
    
    print(f"\n{'='*60}")
    print("RESULT:")
    print(f"{'='*60}")
    print(f"Widget count: {result['metadata']['widget_count']}")
    print(f"\nDashboard Config:")
    print(json.dumps(result["dashboard_config"], indent=2))


if __name__ == "__main__":
    main()
