"""EventData Puller Agent - Main logic.

Agent for fetching, querying, and analyzing economic calendar data
from Trading Economics API.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
import time

from src.mcp.client import MCPClient
from src.bus.manifest import Manifest
from src.bus.file_bus import write_atomic, ensure_dir
from src.bus.schema import create_output_template

from src.servers.tradingeconomics.filters import (
    filter_events_list as filter_events,
    is_highlight_event,
    should_exclude_event,
)

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    get_workspace_path,
    OUT_DIR,
    LOGS_DIR,
    DEFAULT_WINDOW_HOURS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_MAX_RESULTS,
    AVAILABLE_ACTIONS,
    MAX_RESULTS,
    MAX_WINDOW_HOURS,
    VALID_IMPORTANCE_LEVELS,
)

logger = logging.getLogger(__name__)


class EventDataPullerAgent:
    """
    EventData Puller Agent - Queries economic events and writes to file bus.
    
    This is a producer agent that:
    1. Updates economic calendar from Trading Economics API
    2. Queries historical event data
    3. Finds correlated events within time windows
    4. Manages WebSocket stream for live events
    5. Writes output with incremented filename
    6. Logs each run with action and parameters
    """
    
    def __init__(self):
        """Initialize agent."""
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        self.mcp_client = MCPClient()
        
        logger.info(f"EventDataPullerAgent initialized at {self.workspace}")
    
    def run(
        self,
        action: str = "query_event",
        event_id: Optional[str] = None,
        event_name: Optional[str] = None,
        country: Optional[str] = None,
        lookback_timestamp: Optional[str] = None,
        lookback_days: Optional[int] = None,
        window_hours: float = DEFAULT_WINDOW_HOURS,
        target_event_date: Optional[str] = None,
        importance: Optional[str] = None,
        keyword: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = DEFAULT_MAX_RESULTS,
        update_calendar: bool = False,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        full_refresh: bool = False,
        include_correlations: bool = True,
        apply_filters: bool = True,
        highlight_only: bool = False,
    ) -> Path:
        """
        Execute action and write to file bus.
        
        Args:
            action: Action to perform (update_calendar, query_event, find_correlations, etc.)
            event_id: Event ID/ticker from Trading Economics
            event_name: Event name for search
            country: Country code filter (e.g., 'US', 'GB')
            lookback_timestamp: Only return events after this date (ISO format)
            lookback_days: Days to look back (alternative to lookback_timestamp)
            window_hours: Hours before/after for correlation analysis
            target_event_date: Specific date for target event in correlation
            importance: Importance filter (low/medium/high)
            keyword: Search keyword for events
            category: Event category filter
            limit: Maximum results to return
            update_calendar: If true, updates calendar before querying
            start_date: Start date for calendar fetch (YYYY-MM-DD)
            end_date: End date for calendar fetch (YYYY-MM-DD)
            full_refresh: If true, refreshes entire calendar history
            include_correlations: If true, includes correlated events with query_event
            apply_filters: If true, applies event exclusion/inclusion filters (default: True)
            highlight_only: If true, only returns high-importance events (default: False)
            
        Returns:
            Path to output file
            
        Raises:
            ValueError: If invalid inputs
            Exception: If action fails
        """
        start_time = time.time()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Run {run_id}: Starting action={action}, event_id={event_id}")
        
        try:
            # Validate inputs
            self._validate_inputs(action, event_id, event_name, window_hours, limit)
            
            # Auto-update calendar if requested
            if update_calendar or action == "update_calendar":
                logger.info("Updating economic calendar")
                calendar_result = self._update_calendar(
                    start_date=start_date,
                    end_date=end_date,
                    country=country,
                    importance=importance,
                    full_refresh=full_refresh
                )
                
                if action == "update_calendar":
                    # If update is the main action, return its result
                    return self._write_output(
                        run_id=run_id,
                        action=action,
                        data=calendar_result,
                        start_time=start_time
                    )
            
            # Execute action
            result = self._execute_action(
                action=action,
                event_id=event_id,
                event_name=event_name,
                country=country,
                lookback_timestamp=lookback_timestamp,
                lookback_days=lookback_days,
                window_hours=window_hours,
                target_event_date=target_event_date,
                importance=importance,
                keyword=keyword,
                category=category,
                limit=limit,
                include_correlations=include_correlations,
                apply_filters=apply_filters,
                highlight_only=highlight_only,
            )
            
            # Write output
            output_path = self._write_output(
                run_id=run_id,
                action=action,
                data=result,
                start_time=start_time
            )
            
            logger.info(f"Run {run_id}: Completed successfully. Output: {output_path}")
            return output_path
            
        except Exception as e:
            # Log failure
            duration_ms = (time.time() - start_time) * 1000
            
            self._write_run_log(
                run_id=run_id,
                action=action,
                params={
                    "event_id": event_id,
                    "event_name": event_name,
                    "country": country,
                },
                output_path=None,
                status="failed",
                error=str(e),
                duration_ms=duration_ms
            )
            
            logger.error(f"Run {run_id}: Failed - {e}")
            raise
    
    def _validate_inputs(
        self,
        action: str,
        event_id: Optional[str],
        event_name: Optional[str],
        window_hours: float,
        limit: int
    ) -> None:
        """Validate inputs."""
        # Check action
        if action not in AVAILABLE_ACTIONS:
            raise ValueError(
                f"Invalid action: {action}. "
                f"Available: {', '.join(AVAILABLE_ACTIONS)}"
            )
        
        # For query_event and find_correlations, need at least one identifier
        if action in ("query_event", "find_correlations"):
            if not event_id and not event_name:
                raise ValueError(
                    f"Action '{action}' requires either event_id or event_name"
                )
        
        # Validate window_hours
        if window_hours <= 0:
            raise ValueError("window_hours must be positive")
        if window_hours > MAX_WINDOW_HOURS:
            raise ValueError(f"window_hours exceeds maximum: {MAX_WINDOW_HOURS}")
        
        # Validate limit
        if limit <= 0:
            raise ValueError("limit must be positive")
        if limit > MAX_RESULTS:
            raise ValueError(f"limit exceeds maximum: {MAX_RESULTS}")
    
    def _update_calendar(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        country: Optional[str] = None,
        event_name: Optional[str] = None,
        importance: Optional[str] = None,
        full_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch events from Trading Economics API and store them in the database.
        
        This method automatically persists all fetched events to the local SQLite database
        for future queries. Events are filtered based on the configured exclusion/inclusion
        rules before being stored.
        
        Args:
            start_date: Start date for fetch (YYYY-MM-DD)
            end_date: End date for fetch (YYYY-MM-DD)
            country: Country filter (optional)
            event_name: Specific event to fetch (optional)
            importance: Importance filter (optional)
            full_refresh: If True, ignores last event date and fetches full history
            
        Returns:
            Dictionary with fetch results including number of events inserted/updated
        """
        result = self.mcp_client.call_tool(
            "fetch_economic_calendar",
            arguments={
                "start_date": start_date,
                "end_date": end_date,
                "country": country,
                "event_name": event_name,
                "importance": importance,
                "full_refresh": full_refresh,
            }
        )
        return result
    
    def _execute_action(
        self,
        action: str,
        event_id: Optional[str],
        event_name: Optional[str],
        country: Optional[str],
        lookback_timestamp: Optional[str],
        lookback_days: Optional[int],
        window_hours: float,
        target_event_date: Optional[str],
        importance: Optional[str],
        keyword: Optional[str],
        category: Optional[str],
        limit: int,
        include_correlations: bool,
        apply_filters: bool = True,
        highlight_only: bool = False,
    ) -> Dict[str, Any]:
        """Execute the requested action."""
        
        if action == "query_event":
            
            # Query event history (try local DB first)
            event_result = self.mcp_client.call_tool(
                "query_event_history",
                arguments={
                    "event_id": event_id,
                    "event_name": event_name,
                    "country": country,
                    "lookback_timestamp": lookback_timestamp,
                    "lookback_days": lookback_days,
                    "limit": limit,
                }
            )
            
            # If results are empty, try to fetch from API first then query again
            if event_result.get("success") and event_result.get("count", 0) == 0:
                logger.info("No events found in database. Attempting to fetch from API...")
                
                # Determine date range for fetch
                fetch_end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                fetch_start = None
                
                if lookback_timestamp:
                    fetch_start = lookback_timestamp[:10]
                elif lookback_days:
                    from datetime import timedelta
                    fetch_start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
                else:
                    # Default to 1 year if not specified
                    from datetime import timedelta
                    fetch_start = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
                
                # Update calendar (pass event_name for efficient API filtering)
                self._update_calendar(
                    start_date=fetch_start,
                    end_date=fetch_end,
                    country=country,
                    event_name=event_name,
                    full_refresh=False
                )
                
                # Query again
                event_result = self.mcp_client.call_tool(
                    "query_event_history",
                    arguments={
                        "event_id": event_id,
                        "event_name": event_name,
                        "country": country,
                        "lookback_timestamp": lookback_timestamp,
                        "lookback_days": lookback_days,
                        "limit": limit,
                    }
                )
            
            # Apply event filters
            # This enforces the "only events we care about" logic across ALL countries if country was None
            if event_result.get("success") and event_result.get("events"):
                if apply_filters:
                    event_result["events"] = filter_events(
                        event_result["events"], 
                        apply_exclusions=True
                    )
                    event_result["count"] = len(event_result["events"])
                
                # Filter to highlight only if requested
                if highlight_only:
                    event_result["events"] = [
                        e for e in event_result["events"] 
                        if e.get("is_highlight", False)
                    ]
                    event_result["count"] = len(event_result["events"])
            
            # Optionally add correlations for each event instance
            if include_correlations and event_result.get("success") and event_result.get("events"):
                event_result["correlations"] = []
                
                # Find correlations for recent instances (max 5)
                recent_events = event_result["events"][:5]
                
                for event in recent_events:
                    corr_result = self.mcp_client.call_tool(
                        "find_correlated_events",
                        arguments={
                            "target_event_id": event.get("event_id"),
                            "target_event_date": event.get("event_date"),
                            "window_hours": window_hours,
                            "exclude_same_event": True,
                            "limit": 20,
                        }
                    )
                    
                    if corr_result.get("success"):
                        # Filter correlated events too
                        corr_events = corr_result.get("correlated_events", [])
                        if apply_filters:
                            corr_events = filter_events(corr_events, apply_exclusions=True)
                        
                        event_result["correlations"].append({
                            "event_date": event.get("event_date"),
                            "correlated_count": len(corr_events),
                            "correlated_events": corr_events[:10],
                        })
            
            return event_result
        
        elif action == "find_correlations":
            # Find correlated events for a specific date
            corr_result = self.mcp_client.call_tool(
                "find_correlated_events",
                arguments={
                    "target_event_id": event_id,
                    "target_event_name": event_name,
                    "target_event_date": target_event_date,
                    "window_hours": window_hours,
                    "exclude_same_event": True,
                    "min_importance": importance,
                    "country": country,
                    "limit": limit,
                }
            )
            
            # Apply event filters to correlated events
            if corr_result.get("success") and corr_result.get("correlated_events"):
                if apply_filters:
                    corr_result["correlated_events"] = filter_events(
                        corr_result["correlated_events"],
                        apply_exclusions=True
                    )
                    corr_result["count"] = len(corr_result["correlated_events"])
                
                # Filter to highlight only if requested
                if highlight_only:
                    corr_result["correlated_events"] = [
                        e for e in corr_result["correlated_events"]
                        if e.get("is_highlight", False)
                    ]
                    corr_result["count"] = len(corr_result["correlated_events"])
            
            return corr_result
        
        elif action == "search_events":
            # Search for events
            search_result = self.mcp_client.call_tool(
                "search_events",
                arguments={
                    "keyword": keyword or event_name,
                    "country": country,
                    "category": category,
                    "importance": importance,
                    "limit": limit,
                }
            )
            
            # Apply event filters
            if search_result.get("success") and search_result.get("events"):
                if apply_filters:
                    search_result["events"] = filter_events(
                        search_result["events"],
                        apply_exclusions=True
                    )
                    search_result["count"] = len(search_result["events"])
                
                # Filter to highlight only if requested
                if highlight_only:
                    search_result["events"] = [
                        e for e in search_result["events"]
                        if e.get("is_highlight", False)
                    ]
                    search_result["count"] = len(search_result["events"])
            
            return search_result
        
        elif action == "stream_start":
            # Start WebSocket stream
            countries = [country] if country else None
            importance_list = [importance] if importance else None
            
            return self.mcp_client.call_tool(
                "start_event_stream",
                arguments={
                    "countries": countries,
                    "importance": importance_list,
                }
            )
        
        elif action == "stream_stop":
            # Stop WebSocket stream
            return self.mcp_client.call_tool(
                "stop_event_stream",
                arguments={}
            )
        
        elif action == "stream_status":
            # Get stream status
            return self.mcp_client.call_tool(
                "get_stream_status",
                arguments={}
            )
        
        elif action == "get_live_events":
            # Get buffered live events
            return self.mcp_client.call_tool(
                "get_live_events",
                arguments={
                    "limit": limit
                }
            )
        
        else:
            raise ValueError(f"Unknown action: {action}")
    
    def _write_output(
        self,
        run_id: str,
        action: str,
        data: Dict[str, Any],
        start_time: float
    ) -> Path:
        """Write output to file bus."""
        # Get next output path
        output_path = self.manifest.get_next_filepath(subdir=OUT_DIR)
        
        # Create standardized output
        output_data = create_output_template(
            data=[data] if isinstance(data, dict) else data,
            query=f"EventData action: {action}",
            agent_name=AGENT_NAME,
            version=AGENT_VERSION
        )
        
        # Add action-specific metadata
        output_data["metadata"]["action"] = action
        output_data["metadata"]["success"] = data.get("success", True)
        
        # Write output atomically
        logger.info(f"Writing output to {output_path}")
        write_atomic(output_path, output_data)
        
        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000
        
        # Write run log
        self._write_run_log(
            run_id=run_id,
            action=action,
            params=data.get("query", {}),
            output_path=output_path,
            status="success",
            result_count=data.get("count", 0),
            duration_ms=duration_ms
        )
        
        return output_path
    
    def _write_run_log(
        self,
        run_id: str,
        action: str,
        params: Dict[str, Any],
        output_path: Optional[Path],
        status: str,
        result_count: int = 0,
        duration_ms: float = 0,
        error: str = ""
    ) -> Path:
        """Write run log."""
        log_data = {
            "run_id": run_id,
            "action": action,
            "params": params,
            "output_path": str(output_path) if output_path else None,
            "status": status,
            "result_count": result_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round(duration_ms, 2),
            "agent": AGENT_NAME,
            "version": AGENT_VERSION
        }
        
        if error:
            log_data["error"] = error
        
        # Write to logs directory
        log_path = self.workspace / LOGS_DIR / f"{run_id}.json"
        ensure_dir(log_path.parent)
        write_atomic(log_path, log_data)
        
        return log_path
    
    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return self.manifest.get_stats()
    
    # ==========================================================================
    # Convenience Methods
    # ==========================================================================
    
    def update_calendar(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        country: Optional[str] = None,
        full_refresh: bool = False
    ) -> Path:
        """
        Convenience method to update economic calendar.
        
        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            country: Country filter
            full_refresh: If true, refreshes entire history
            
        Returns:
            Path to output file
        """
        return self.run(
            action="update_calendar",
            start_date=start_date,
            end_date=end_date,
            country=country,
            full_refresh=full_refresh
        )
    
    def query_event(
        self,
        event_id: Optional[str] = None,
        event_name: Optional[str] = None,
        lookback_days: Optional[int] = None,
        include_correlations: bool = True,
        window_hours: float = DEFAULT_WINDOW_HOURS
    ) -> Path:
        """
        Convenience method to query event history.
        
        Args:
            event_id: Event ID/ticker
            event_name: Event name
            lookback_days: Days to look back
            include_correlations: Include correlated events
            window_hours: Window for correlation analysis
            
        Returns:
            Path to output file
        """
        return self.run(
            action="query_event",
            event_id=event_id,
            event_name=event_name,
            lookback_days=lookback_days,
            include_correlations=include_correlations,
            window_hours=window_hours
        )
    
    def find_correlations(
        self,
        event_id: Optional[str] = None,
        event_name: Optional[str] = None,
        target_event_date: Optional[str] = None,
        window_hours: float = DEFAULT_WINDOW_HOURS
    ) -> Path:
        """
        Convenience method to find correlated events.
        
        Args:
            event_id: Target event ID
            event_name: Target event name
            target_event_date: Specific event date
            window_hours: Hours before/after to search
            
        Returns:
            Path to output file
        """
        return self.run(
            action="find_correlations",
            event_id=event_id,
            event_name=event_name,
            target_event_date=target_event_date,
            window_hours=window_hours
        )

