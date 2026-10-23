"""Anomaly Notes Agent - Main logic.

Agent for detecting unusual market moves and generating contextual notes.
Uses hybrid approach: internal event data first, Perplexity web search fallback.
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

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    get_workspace_path,
    OUT_DIR,
    LOGS_DIR,
    DEFAULT_Z_THRESHOLD,
    DEFAULT_SYMBOL,
    AVAILABLE_ACTIONS,
    REQUIRED_PARAMS,
)

logger = logging.getLogger(__name__)


class AnomalyNotesAgent:
    """
    Anomaly Notes Agent - Detects unusual market moves and generates notes.
    
    This is a producer agent that:
    1. Detects anomalous price moves (z-score > threshold)
    2. Matches anomalies to scheduled economic events
    3. Uses Perplexity API for unexplained moves
    4. Stores notes in PostgreSQL (market_anomaly_notes table)
    5. Writes output to file bus
    """
    
    def __init__(self):
        """Initialize agent."""
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        self.mcp_client = MCPClient()
        
        logger.info(f"AnomalyNotesAgent initialized at {self.workspace}")
    
    def run(
        self,
        action: str = "annotate_date_range",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        date: Optional[str] = None,
        symbol: str = DEFAULT_SYMBOL,
        z_threshold: float = DEFAULT_Z_THRESHOLD,
        use_perplexity: bool = True,
        force_search: bool = False,
        min_z_score: Optional[float] = None,
        limit: int = 100,
    ) -> Path:
        """
        Execute action and write to file bus.
        
        Args:
            action: Action to perform (annotate_date_range, detect_anomalies, query_notes, explain_move)
            start_date: Start date for range-based actions (YYYY-MM-DD)
            end_date: End date for range-based actions (YYYY-MM-DD)
            date: Specific date for explain_move action
            symbol: Market symbol (default: TY)
            z_threshold: Z-score threshold for anomaly detection (default: 1.5)
            use_perplexity: Use Perplexity for unexplained moves (default: True)
            force_search: Force Perplexity search in explain_move (default: False)
            min_z_score: Minimum z-score filter for query_notes
            limit: Maximum results for query_notes
            
        Returns:
            Path to output file
            
        Raises:
            ValueError: If invalid inputs
            Exception: If action fails
        """
        start_time = time.time()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Run {run_id}: action={action}, symbol={symbol}")
        
        try:
            # Validate inputs
            self._validate_inputs(action, start_date, end_date, date)
            
            # Execute action
            if action == "annotate_date_range":
                result = self._annotate_date_range(
                    start_date, end_date, symbol, z_threshold, use_perplexity
                )
            elif action == "detect_anomalies":
                result = self._detect_anomalies(
                    start_date, end_date, symbol, z_threshold
                )
            elif action == "query_notes":
                result = self._query_notes(
                    start_date, end_date, symbol, min_z_score, limit
                )
            elif action == "explain_move":
                result = self._explain_move(date, symbol, force_search)
            else:
                raise ValueError(f"Unknown action: {action}")
            
            # Create output
            output = create_output_template(
                agent=AGENT_NAME,
                tool=action,
                data=result,
            )
            output["metadata"]["run_id"] = run_id
            output["metadata"]["duration_seconds"] = round(time.time() - start_time, 3)
            output["metadata"]["symbol"] = symbol
            output["metadata"]["z_threshold"] = z_threshold
            
            # Write to file bus
            out_dir = self.workspace / OUT_DIR
            ensure_dir(out_dir)
            
            next_file = self.manifest.next_file(str(out_dir))
            output_path = write_atomic(next_file, output)
            
            logger.info(f"Run {run_id}: Wrote output to {output_path}")
            
            # Log run
            self._log_run(run_id, action, result, output_path, time.time() - start_time)
            
            return output_path
            
        except Exception as e:
            logger.error(f"Run {run_id}: Failed - {e}")
            raise
    
    def _validate_inputs(
        self,
        action: str,
        start_date: Optional[str],
        end_date: Optional[str],
        date: Optional[str],
    ) -> None:
        """Validate input parameters."""
        if action not in AVAILABLE_ACTIONS:
            raise ValueError(f"Invalid action '{action}'. Valid: {AVAILABLE_ACTIONS}")
        
        required = REQUIRED_PARAMS.get(action, [])
        
        if "start_date" in required and not start_date:
            raise ValueError(f"Action '{action}' requires start_date")
        if "end_date" in required and not end_date:
            raise ValueError(f"Action '{action}' requires end_date")
        if "date" in required and not date:
            raise ValueError(f"Action '{action}' requires date")
        
        # Validate date format
        for d in [start_date, end_date, date]:
            if d:
                try:
                    datetime.strptime(d, "%Y-%m-%d")
                except ValueError:
                    raise ValueError(f"Invalid date format '{d}'. Use YYYY-MM-DD")
    
    def _annotate_date_range(
        self,
        start_date: str,
        end_date: str,
        symbol: str,
        z_threshold: float,
        use_perplexity: bool,
    ) -> Dict[str, Any]:
        """Execute annotate_date_range action."""
        return self.mcp_client.call_tool(
            "annotate_date_range",
            arguments={
                "start_date": start_date,
                "end_date": end_date,
                "symbol": symbol,
                "z_threshold": z_threshold,
                "use_perplexity": use_perplexity,
            },
        )
    
    def _detect_anomalies(
        self,
        start_date: str,
        end_date: str,
        symbol: str,
        z_threshold: float,
    ) -> Dict[str, Any]:
        """Execute detect_anomalies action."""
        return self.mcp_client.call_tool(
            "detect_anomalies",
            arguments={
                "start_date": start_date,
                "end_date": end_date,
                "symbol": symbol,
                "z_threshold": z_threshold,
            },
        )
    
    def _query_notes(
        self,
        start_date: Optional[str],
        end_date: Optional[str],
        symbol: str,
        min_z_score: Optional[float],
        limit: int,
    ) -> Dict[str, Any]:
        """Execute query_notes action."""
        return self.mcp_client.call_tool(
            "query_anomaly_notes",
            arguments={
                "start_date": start_date,
                "end_date": end_date,
                "symbol": symbol,
                "min_z_score": min_z_score,
                "limit": limit,
            },
        )
    
    def _explain_move(
        self,
        date: str,
        symbol: str,
        force_search: bool,
    ) -> Dict[str, Any]:
        """Execute explain_move action."""
        return self.mcp_client.call_tool(
            "explain_move",
            arguments={
                "date": date,
                "symbol": symbol,
                "force_search": force_search,
            },
        )
    
    def _log_run(
        self,
        run_id: str,
        action: str,
        result: Dict[str, Any],
        output_path: Path,
        duration: float,
    ) -> None:
        """Log run to logs directory."""
        logs_dir = self.workspace / LOGS_DIR
        ensure_dir(logs_dir)
        
        log_entry = {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "success": result.get("success", True),
            "notes_created": result.get("notes_created", 0),
            "anomaly_days": result.get("anomaly_days", result.get("count", 0)),
            "output_path": str(output_path),
            "duration_seconds": round(duration, 3),
        }
        
        log_file = logs_dir / f"{run_id}.json"
        write_atomic(log_file, log_entry)






