"""Analytics Agent - Main logic for statistical analysis and visualization."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import time

from src.mcp.client import MCPClient
from src.bus.manifest import Manifest
from src.bus.file_bus import write_atomic, ensure_dir
from src.bus.schema import create_output_template

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    get_workspace_path,
    get_plots_dir,
    OUT_DIR,
    LOGS_DIR,
    PLOTS_DIR,
    DEFAULT_PERCENTILES,
    MAX_ROWS,
    MIN_DATA_POINTS_FOR_STATS,
)

logger = logging.getLogger(__name__)


class AnalyticsAgent:
    """
    Analytics Agent - Performs statistical analysis and generates visualizations.
    
    This agent can:
    1. Compute descriptive statistics
    2. Calculate percentile ranks
    3. Compare distributions
    4. Analyze correlations
    5. Generate SVG plots (histograms, line charts, scatter plots, bar charts)
    6. Generate PNG plots (candlestick charts, multi-candlestick charts)
    """
    
    def __init__(self):
        """Initialize agent."""
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        self.mcp_client = MCPClient()
        
        # Ensure plots directory exists
        plots_dir = get_plots_dir()
        ensure_dir(plots_dir)
        
        logger.info(f"AnalyticsAgent initialized at {self.workspace}")
    
    # =========================================================================
    # Analysis Methods
    # =========================================================================
    
    def compute_statistics(
        self,
        data: List[float],
        percentiles: List[int] = None,
        include_outliers: bool = False
    ) -> Dict[str, Any]:
        """
        Compute descriptive statistics for a dataset.
        
        Args:
            data: List of numeric values
            percentiles: Percentiles to compute
            include_outliers: Whether to detect outliers
            
        Returns:
            Statistics dictionary
        """
        if percentiles is None:
            percentiles = DEFAULT_PERCENTILES
        
        result = self.mcp_client.call_tool(
            "compute_statistics",
            arguments={
                "data": data,
                "percentiles": percentiles,
                "include_outliers": include_outliers,
            }
        )
        
        return result
    
    def compute_percentile_rank(
        self,
        value: float,
        reference_data: List[float]
    ) -> Dict[str, Any]:
        """
        Compute percentile rank of a value within a reference dataset.
        
        Args:
            value: Value to rank
            reference_data: Reference dataset
            
        Returns:
            Percentile rank result
        """
        result = self.mcp_client.call_tool(
            "compute_percentile_rank",
            arguments={
                "value": value,
                "data": reference_data,
            }
        )
        
        return result
    
    def compare_distributions(
        self,
        data_a: List[float],
        data_b: List[float],
        label_a: str = "Dataset A",
        label_b: str = "Dataset B"
    ) -> Dict[str, Any]:
        """
        Compare two distributions statistically.
        
        Args:
            data_a: First dataset
            data_b: Second dataset
            label_a: Label for first dataset
            label_b: Label for second dataset
            
        Returns:
            Comparison result
        """
        result = self.mcp_client.call_tool(
            "compare_distributions",
            arguments={
                "data_a": data_a,
                "data_b": data_b,
                "label_a": label_a,
                "label_b": label_b,
            }
        )
        
        return result
    
    def compute_correlation(
        self,
        data_x: List[float],
        data_y: List[float]
    ) -> Dict[str, Any]:
        """
        Compute correlation between two variables.
        
        Args:
            data_x: X variable
            data_y: Y variable
            
        Returns:
            Correlation result
        """
        result = self.mcp_client.call_tool(
            "compute_correlation",
            arguments={
                "data_x": data_x,
                "data_y": data_y,
            }
        )
        
        return result
    
    # =========================================================================
    # Visualization Methods
    # =========================================================================
    
    def generate_histogram(
        self,
        data: List[float],
        title: str = "Distribution",
        x_label: str = "Value",
        y_label: str = "Frequency",
        bins: int = 20
    ) -> Dict[str, Any]:
        """
        Generate a histogram.
        
        Args:
            data: Data to plot
            title: Chart title
            x_label: X-axis label
            y_label: Y-axis label
            bins: Number of bins
            
        Returns:
            Plot result with SVG path
        """
        result = self.mcp_client.call_tool(
            "generate_histogram",
            arguments={
                "data": data,
                "title": title,
                "x_label": x_label,
                "y_label": y_label,
                "bins": bins,
            }
        )
        
        return result
    
    def generate_line_chart(
        self,
        y_data: List[float],
        x_data: Optional[List[float]] = None,
        title: str = "Line Chart",
        x_label: str = "X",
        y_label: str = "Y"
    ) -> Dict[str, Any]:
        """
        Generate a line chart.
        
        Args:
            y_data: Y values
            x_data: X values (optional)
            title: Chart title
            x_label: X-axis label
            y_label: Y-axis label
            
        Returns:
            Plot result with SVG path
        """
        args = {
            "y_data": y_data,
            "title": title,
            "x_label": x_label,
            "y_label": y_label,
        }
        if x_data is not None:
            args["x_data"] = x_data
        
        result = self.mcp_client.call_tool("generate_line_chart", arguments=args)
        return result
    
    def generate_scatter_plot(
        self,
        x_data: List[float],
        y_data: List[float],
        title: str = "Scatter Plot",
        x_label: str = "X",
        y_label: str = "Y"
    ) -> Dict[str, Any]:
        """
        Generate a scatter plot.
        
        Args:
            x_data: X values
            y_data: Y values
            title: Chart title
            x_label: X-axis label
            y_label: Y-axis label
            
        Returns:
            Plot result with SVG path
        """
        result = self.mcp_client.call_tool(
            "generate_scatter_plot",
            arguments={
                "x_data": x_data,
                "y_data": y_data,
                "title": title,
                "x_label": x_label,
                "y_label": y_label,
            }
        )
        
        return result
    
    def generate_bar_chart(
        self,
        values: List[float],
        labels: Optional[List[str]] = None,
        title: str = "Bar Chart",
        y_label: str = "Value"
    ) -> Dict[str, Any]:
        """
        Generate a bar chart.
        
        Args:
            values: Bar values
            labels: Bar labels
            title: Chart title
            y_label: Y-axis label
            
        Returns:
            Plot result with SVG path
        """
        args = {
            "values": values,
            "title": title,
            "y_label": y_label,
        }
        if labels is not None:
            args["labels"] = labels
        
        result = self.mcp_client.call_tool("generate_bar_chart", arguments=args)
        return result
    
    def generate_candlestick_chart(
        self,
        open_data: List[float],
        high_data: List[float],
        low_data: List[float],
        close_data: List[float],
        timestamps: Optional[List[str]] = None,
        events: Optional[List[Dict[str, Any]]] = None,
        show_event_labels: bool = True,
        auto_fetch_events: bool = False,
        event_country: str = "US",
        title: str = "Candlestick Chart",
        x_label: str = "Time",
        y_label: str = "Price",
        color_scheme: str = "default",
        width: int = 1600,
        height: int = 600
    ) -> Dict[str, Any]:
        """
        Generate a candlestick chart (OHLC) as PNG.
        
        Args:
            open_data: List of opening prices
            high_data: List of high prices
            low_data: List of low prices
            close_data: List of closing prices
            timestamps: Optional list of timestamps
            events: Optional list of events to mark
            show_event_labels: Whether to show event names
            auto_fetch_events: If True, auto-query events for time range
            event_country: Country code for event lookup
            title: Chart title
            x_label: X-axis label
            y_label: Y-axis label
            color_scheme: Color scheme name
            width: Image width in pixels
            height: Image height in pixels
            
        Returns:
            Plot result with PNG path
        """
        args = {
            "open_data": open_data,
            "high_data": high_data,
            "low_data": low_data,
            "close_data": close_data,
            "title": title,
            "x_label": x_label,
            "y_label": y_label,
            "color_scheme": color_scheme,
            "width": width,
            "height": height,
            "show_event_labels": show_event_labels,
            "auto_fetch_events": auto_fetch_events,
            "event_country": event_country,
        }
        
        if timestamps is not None:
            args["timestamps"] = timestamps
        
        if events is not None:
            args["events"] = events
        
        result = self.mcp_client.call_tool("generate_candlestick_chart", arguments=args)
        return result
    
    def generate_multi_candlestick_charts(
        self,
        timeseries_list: List[Dict[str, Any]],
        width: int = 1600,
        height: int = 600
    ) -> Dict[str, Any]:
        """
        Generate multiple candlestick charts from a list of timeseries data.
        
        This is designed for queries like "for each NFP event, show a price chart".
        
        Args:
            timeseries_list: List of timeseries, each with:
                - "title": Chart title (e.g., "NFP Dec 6, 2024")
                - "date": Date string for identification
                - "data": List of OHLC records or separate arrays
            width: Chart width in pixels
            height: Chart height in pixels
            
        Returns:
            Dictionary with list of chart paths and metadata
        """
        args = {
            "timeseries_list": timeseries_list,
            "width": width,
            "height": height,
        }
        
        result = self.mcp_client.call_tool("generate_multi_candlestick_charts", arguments=args)
        return result
    
    # =========================================================================
    # Main Run Method
    # =========================================================================
    
    def run(
        self,
        analysis_type: str,
        params: Dict[str, Any],
        generate_plot: bool = True
    ) -> Path:
        """
        Execute an analysis and write results to file bus.
        
        Args:
            analysis_type: Type of analysis (descriptive, percentile_rank, etc.)
            params: Analysis parameters
            generate_plot: Whether to generate visualization
            
        Returns:
            Path to output file
        """
        start_time = time.time()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Run {run_id}: Starting {analysis_type} analysis")
        
        try:
            # Execute analysis based on type
            if analysis_type in {"surprise_analysis", "event_impact"}:
                result = {
                    "success": False,
                    "error": (
                        f"AnalyticsAgent analysis_type '{analysis_type}' is disabled. "
                        "Fetch data via market_data_agent/eventdata_puller_agent and then run "
                        "analytics tools on the resulting arrays/timeseries."
                    ),
                }
            
            elif analysis_type == "descriptive":
                data = params.get("data", [])
                if not data:
                    result = {
                        "success": False,
                        "error": (
                            "AnalyticsAgent descriptive analysis now requires params['data'] "
                            "(list of numbers). Database fetching was removed; fetch upstream "
                            "via market_data_agent/eventdata_puller_agent tools."
                        ),
                    }
                else:
                    result = self.compute_statistics(data, include_outliers=True)
                
                if generate_plot and result.get("success"):
                    hist = self.generate_histogram(data, title=params.get("title", "Distribution"))
                    result["histogram_path"] = hist.get("svg_path")
            
            elif analysis_type == "percentile_rank":
                result = self.compute_percentile_rank(
                    params.get("value"),
                    params.get("reference_data", [])
                )
            
            elif analysis_type == "comparison":
                result = self.compare_distributions(
                    params.get("data_a", []),
                    params.get("data_b", []),
                    params.get("label_a", "Dataset A"),
                    params.get("label_b", "Dataset B"),
                )
            
            elif analysis_type == "correlation":
                result = self.compute_correlation(
                    params.get("data_x", []),
                    params.get("data_y", [])
                )
                
                if generate_plot and result.get("success"):
                    scatter = self.generate_scatter_plot(
                        params.get("data_x", []),
                        params.get("data_y", []),
                        title="Correlation Plot"
                    )
                    result["scatter_plot_path"] = scatter.get("svg_path")
            
            else:
                result = {
                    "success": False,
                    "error": f"Unknown analysis type: {analysis_type}"
                }
            
            # Get output path
            output_path = self.manifest.get_next_filepath(subdir=OUT_DIR)
            
            # Create output
            duration_ms = (time.time() - start_time) * 1000
            
            output_data = create_output_template(
                data=result,
                query=f"{analysis_type}: {params}",
                agent_name=AGENT_NAME,
                version=AGENT_VERSION
            )
            output_data["metadata"]["duration_ms"] = round(duration_ms, 2)
            output_data["metadata"]["analysis_type"] = analysis_type
            
            # Write output
            write_atomic(output_path, output_data)
            
            # Write run log
            log_path = self._write_run_log(
                run_id=run_id,
                analysis_type=analysis_type,
                params=params,
                output_path=output_path,
                status="success" if result.get("success", False) else "failed",
                duration_ms=duration_ms,
                error=result.get("error", "")
            )
            
            logger.info(f"Run {run_id}: Completed. Output: {output_path}")
            
            return output_path
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            
            self._write_run_log(
                run_id=run_id,
                analysis_type=analysis_type,
                params=params,
                output_path=None,
                status="failed",
                duration_ms=duration_ms,
                error=str(e)
            )
            
            logger.error(f"Run {run_id}: Failed - {e}")
            raise
    
    def _write_run_log(
        self,
        run_id: str,
        analysis_type: str,
        params: Dict[str, Any],
        output_path: Optional[Path],
        status: str,
        duration_ms: float,
        error: str = ""
    ) -> Path:
        """Write run log."""
        log_data = {
            "run_id": run_id,
            "analysis_type": analysis_type,
            "params": params,
            "output_path": str(output_path) if output_path else None,
            "status": status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 2),
            "agent": AGENT_NAME,
            "version": AGENT_VERSION,
        }
        
        if error:
            log_data["error"] = error
        
        log_path = self.workspace / LOGS_DIR / f"{run_id}.json"
        ensure_dir(log_path.parent)
        write_atomic(log_path, log_data)
        
        return log_path
    
    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return self.manifest.get_stats()

