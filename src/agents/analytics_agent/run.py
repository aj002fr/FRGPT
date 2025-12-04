"""Analytics Agent - Main logic for statistical analysis and visualization."""

import logging
import sqlite3
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
    get_market_data_db_path,
    get_economic_events_db_path,
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
    1. Query market_data and economic_events databases
    2. Compute descriptive statistics
    3. Calculate percentile ranks
    4. Compare distributions
    5. Analyze correlations
    6. Generate SVG plots (histograms, line charts, scatter plots, bar charts)
    7. Analyze event impacts and surprises
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
    # Database Access
    # =========================================================================
    
    def _get_market_data_connection(self) -> sqlite3.Connection:
        """Get connection to market data database."""
        db_path = get_market_data_db_path()
        if not db_path.exists():
            raise FileNotFoundError(f"Market data database not found: {db_path}")
        
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_economic_events_connection(self) -> sqlite3.Connection:
        """Get connection to economic events database."""
        db_path = get_economic_events_db_path()
        if not db_path.exists():
            raise FileNotFoundError(f"Economic events database not found: {db_path}")
        
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def query_market_data(
        self,
        columns: List[str] = None,
        symbol_pattern: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = MAX_ROWS
    ) -> List[Dict[str, Any]]:
        """
        Query market data database.
        
        Args:
            columns: Columns to select (default: all)
            symbol_pattern: LIKE pattern for symbol
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
            limit: Max rows to return
            
        Returns:
            List of row dictionaries
        """
        conn = self._get_market_data_connection()
        cursor = conn.cursor()
        
        try:
            # Build query
            col_str = ", ".join(columns) if columns else "*"
            sql = f"SELECT {col_str} FROM market_data WHERE is_valid = 1"
            params = []
            
            if symbol_pattern:
                sql += " AND symbol LIKE ?"
                params.append(symbol_pattern)
            
            if date_from:
                sql += " AND file_date >= ?"
                params.append(date_from)
            
            if date_to:
                sql += " AND file_date <= ?"
                params.append(date_to)
            
            sql += f" ORDER BY file_date DESC, timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
            
        finally:
            conn.close()
    
    def query_economic_events(
        self,
        columns: List[str] = None,
        event_name_pattern: Optional[str] = None,
        country: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        importance: Optional[str] = None,
        limit: int = MAX_ROWS
    ) -> List[Dict[str, Any]]:
        """
        Query economic events database.
        
        Args:
            columns: Columns to select (default: all)
            event_name_pattern: LIKE pattern for event_name
            country: Country filter
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
            importance: Importance filter (low/medium/high)
            limit: Max rows to return
            
        Returns:
            List of row dictionaries
        """
        conn = self._get_economic_events_connection()
        cursor = conn.cursor()
        
        try:
            # Build query
            col_str = ", ".join(columns) if columns else "*"
            sql = f"SELECT {col_str} FROM economic_events WHERE 1=1"
            params = []
            
            if event_name_pattern:
                sql += " AND event_name LIKE ?"
                params.append(f"%{event_name_pattern}%")
            
            if country:
                sql += " AND LOWER(country) LIKE LOWER(?)"
                params.append(f"%{country}%")
            
            if date_from:
                sql += " AND event_date >= ?"
                params.append(date_from)
            
            if date_to:
                sql += " AND event_date <= ?"
                params.append(date_to)
            
            if importance:
                sql += " AND importance = ?"
                params.append(importance.lower())
            
            sql += f" ORDER BY event_date DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            return [dict(row) for row in rows]
            
        finally:
            conn.close()
    
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
    
    # =========================================================================
    # High-Level Analysis Methods
    # =========================================================================
    
    def analyze_event_surprises(
        self,
        event_name_pattern: str,
        country: Optional[str] = None,
        current_surprise: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Analyze historical surprises for an economic event.
        
        Surprise = actual - consensus
        
        Args:
            event_name_pattern: Event name pattern (e.g., "Nonfarm")
            country: Country filter (e.g., "US")
            current_surprise: Current surprise value to rank (optional)
            
        Returns:
            Analysis result with statistics and optional percentile rank
        """
        start_time = time.time()
        
        # Query historical events
        events = self.query_economic_events(
            columns=["event_name", "event_date", "actual", "consensus", "forecast", "previous"],
            event_name_pattern=event_name_pattern,
            country=country,
        )
        
        if not events:
            return {
                "success": False,
                "error": f"No events found matching '{event_name_pattern}'"
            }
        
        # Calculate surprises (actual - consensus)
        surprises = []
        events_with_surprise = []
        
        for event in events:
            actual = event.get("actual")
            consensus = event.get("consensus")
            
            if actual is not None and consensus is not None:
                surprise = actual - consensus
                surprises.append(surprise)
                events_with_surprise.append({
                    **event,
                    "surprise": surprise
                })
        
        if len(surprises) < MIN_DATA_POINTS_FOR_STATS:
            return {
                "success": False,
                "error": f"Insufficient data: only {len(surprises)} events with actual and consensus values"
            }
        
        # Compute statistics on surprises
        stats_result = self.compute_statistics(surprises, include_outliers=True)
        
        result = {
            "success": True,
            "event_pattern": event_name_pattern,
            "country": country,
            "total_events": len(events),
            "events_with_surprise": len(surprises),
            "surprise_statistics": stats_result.get("statistics", {}),
            "recent_events": events_with_surprise[:10],  # Last 10 events
        }
        
        # If current surprise provided, compute its percentile rank
        if current_surprise is not None:
            rank_result = self.compute_percentile_rank(current_surprise, surprises)
            result["current_surprise"] = {
                "value": current_surprise,
                "percentile_rank": rank_result.get("percentile_rank"),
                "z_score": rank_result.get("z_score"),
                "interpretation": rank_result.get("interpretation"),
            }
        
        # Generate histogram of surprises
        hist_result = self.generate_histogram(
            surprises,
            title=f"Surprise Distribution: {event_name_pattern}",
            x_label="Surprise (Actual - Consensus)",
            y_label="Frequency"
        )
        
        if hist_result.get("success"):
            result["histogram_path"] = hist_result.get("svg_path")
        
        result["duration_ms"] = round((time.time() - start_time) * 1000, 2)
        
        return result
    
    def analyze_market_on_event_dates(
        self,
        event_name_pattern: str,
        symbol_pattern: str,
        country: Optional[str] = None,
        price_column: str = "price"
    ) -> Dict[str, Any]:
        """
        Analyze market prices on economic event dates.
        
        Args:
            event_name_pattern: Event name pattern
            symbol_pattern: Symbol pattern for market data
            country: Country filter for events
            price_column: Column to analyze (price, bid, ask)
            
        Returns:
            Analysis result with statistics and visualization
        """
        start_time = time.time()
        
        # Get event dates
        events = self.query_economic_events(
            columns=["event_name", "event_date", "actual", "consensus"],
            event_name_pattern=event_name_pattern,
            country=country,
        )
        
        if not events:
            return {
                "success": False,
                "error": f"No events found matching '{event_name_pattern}'"
            }
        
        # Extract unique dates (just the date part)
        event_dates = set()
        for event in events:
            date_str = event.get("event_date", "")
            if date_str:
                # Extract just YYYY-MM-DD
                event_dates.add(date_str[:10])
        
        # Query market data for those dates
        prices_on_event_dates = []
        date_price_map = {}
        
        for date in event_dates:
            market_data = self.query_market_data(
                columns=[price_column, "file_date", "symbol"],
                symbol_pattern=symbol_pattern,
                date_from=date,
                date_to=date,
                limit=1000
            )
            
            for row in market_data:
                price = row.get(price_column)
                if price is not None:
                    prices_on_event_dates.append(price)
                    if date not in date_price_map:
                        date_price_map[date] = []
                    date_price_map[date].append(price)
        
        if len(prices_on_event_dates) < MIN_DATA_POINTS_FOR_STATS:
            return {
                "success": False,
                "error": f"Insufficient market data: only {len(prices_on_event_dates)} prices found"
            }
        
        # Compute statistics
        stats_result = self.compute_statistics(prices_on_event_dates)
        
        # Calculate average price per event date
        avg_prices_by_date = []
        sorted_dates = sorted(date_price_map.keys())
        for date in sorted_dates:
            prices = date_price_map[date]
            avg_prices_by_date.append(sum(prices) / len(prices))
        
        result = {
            "success": True,
            "event_pattern": event_name_pattern,
            "symbol_pattern": symbol_pattern,
            "event_dates_found": len(event_dates),
            "total_price_observations": len(prices_on_event_dates),
            "price_statistics": stats_result.get("statistics", {}),
            "dates_analyzed": sorted_dates[-20:],  # Last 20 dates
        }
        
        # Generate histogram
        hist_result = self.generate_histogram(
            prices_on_event_dates,
            title=f"{symbol_pattern} Prices on {event_name_pattern} Dates",
            x_label=price_column.capitalize(),
            y_label="Frequency"
        )
        
        if hist_result.get("success"):
            result["histogram_path"] = hist_result.get("svg_path")
        
        # Generate line chart of average prices over time
        if len(avg_prices_by_date) >= 3:
            line_result = self.generate_line_chart(
                avg_prices_by_date,
                title=f"Average {price_column.capitalize()} on Event Dates",
                x_label="Event Occurrence",
                y_label=price_column.capitalize()
            )
            if line_result.get("success"):
                result["line_chart_path"] = line_result.get("svg_path")
        
        result["duration_ms"] = round((time.time() - start_time) * 1000, 2)
        
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
            if analysis_type == "surprise_analysis":
                result = self.analyze_event_surprises(**params)
            
            elif analysis_type == "event_impact":
                result = self.analyze_market_on_event_dates(**params)
            
            elif analysis_type == "descriptive":
                data = params.get("data", [])
                if not data:
                    # Try to fetch from database
                    db = params.get("database", "market_data")
                    column = params.get("column", "price")
                    
                    if db == "market_data":
                        rows = self.query_market_data(
                            columns=[column],
                            symbol_pattern=params.get("symbol_pattern"),
                            date_from=params.get("date_from"),
                            date_to=params.get("date_to"),
                        )
                        data = [r[column] for r in rows if r.get(column) is not None]
                    else:
                        rows = self.query_economic_events(
                            columns=[column],
                            event_name_pattern=params.get("event_name_pattern"),
                            country=params.get("country"),
                        )
                        data = [r[column] for r in rows if r.get(column) is not None]
                
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

