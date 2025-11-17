"""Polymarket Agent - Main logic."""

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
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
    DEFAULT_MAX_RESULTS,
    MAX_RESULTS,
    SESSION_ID_HASH_LENGTH
)

logger = logging.getLogger(__name__)


class PolymarketAgent:
    """
    Polymarket Agent - Searches prediction markets using Polymarket API.
    
    This is a producer agent that:
    1. Accepts user query
    2. Auto-generates unique session ID
    3. Calls Polymarket search tool (with LLM-powered relevance scoring)
    4. Writes results to file bus
    5. Logs each run with metadata
    
    Search uses hybrid approach:
    - Fast keyword filtering for candidate selection
    - GPT-4 semantic re-ranking for accuracy
    - Falls back to keyword-only if no API key
    """
    
    def __init__(self):
        """Initialize agent."""
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        self.mcp_client = MCPClient()
        
        logger.info(f"PolymarketAgent initialized at {self.workspace}")
    
    def generate_session_id(self) -> str:
        """
        Generate unique session ID.
        
        Format: {timestamp}_{random_hash}
        Example: 20251113143022_a3f2e9
        
        Returns:
            Session ID string
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        random_hash = secrets.token_hex(SESSION_ID_HASH_LENGTH)
        session_id = f"{timestamp}_{random_hash}"
        
        logger.debug(f"Generated session_id: {session_id}")
        return session_id
    
    def run(
        self,
        query: str,
        session_id: Optional[str] = None,
        limit: int = DEFAULT_MAX_RESULTS
    ) -> Path:
        """
        Execute search and write to file bus.
        
        Args:
            query: User search query
            session_id: Session identifier (auto-generated if not provided)
            limit: Maximum results (default: DEFAULT_MAX_RESULTS, max: MAX_RESULTS)
            
        Returns:
            Path to output file
            
        Raises:
            ValueError: If invalid inputs
            Exception: If search or write fails
        """
        start_time = time.time()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Run {run_id}: Starting with query='{query}'")
        
        try:
            # Validate inputs
            self._validate_inputs(query, limit)
            
            # Generate session ID if not provided
            if session_id is None:
                session_id = self.generate_session_id()
            
            logger.info(f"Session ID: {session_id}")
            
            # Call search_polymarket_markets tool
            logger.info("Calling search_polymarket_markets tool")
            result = self.mcp_client.call_tool(
                "search_polymarket_markets",
                arguments={
                    "query": query,
                    "session_id": session_id,
                    "limit": limit
                }
            )
            
            # Extract data
            markets = result["markets"]
            metadata_raw = result["metadata"]
            search_method = metadata_raw.get("search_method", "unknown")
            llm_enabled = metadata_raw.get("llm_scoring_enabled", False)
            
            logger.info(f"Tool returned {len(markets)} markets")
            logger.info(f"Search method: {search_method} (LLM scoring: {llm_enabled})")
            
            # Get next output path
            output_path = self.manifest.get_next_filepath(subdir=OUT_DIR)
            
            # Prepare data for output
            data = [{
                "query": query,
                "session_id": session_id,
                "search_method": search_method,
                "llm_scoring_enabled": llm_enabled,
                "markets": markets,
                "result_count": len(markets)
            }]
            
            # Create standardized output
            output_data = create_output_template(
                data=data,
                query=f"Polymarket search: {query}",
                agent_name=AGENT_NAME,
                version=AGENT_VERSION
            )
            
            # Write output atomically
            logger.info(f"Writing output to {output_path}")
            write_atomic(output_path, output_data)
            
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000
            
            # Write run log
            log_path = self._write_run_log(
                run_id=run_id,
                query=query,
                session_id=session_id,
                output_path=output_path,
                status="success",
                result_count=len(markets),
                duration_ms=duration_ms
            )
            
            logger.info(f"Run {run_id}: Completed successfully. Output: {output_path}")
            logger.info(f"Run log: {log_path}")
            
            return output_path
            
        except Exception as e:
            # Log failure
            duration_ms = (time.time() - start_time) * 1000
            
            self._write_run_log(
                run_id=run_id,
                query=query,
                session_id=session_id or "N/A",
                output_path=None,
                status="failed",
                error=str(e),
                duration_ms=duration_ms
            )
            
            logger.error(f"Run {run_id}: Failed - {e}")
            raise
    
    def _validate_inputs(
        self,
        query: str,
        limit: int
    ) -> None:
        """
        Validate inputs.
        
        Raises:
            ValueError: If validation fails
        """
        # Check query
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        
        # Check limit
        if limit <= 0:
            raise ValueError("limit must be positive")
        
        if limit > MAX_RESULTS:
            raise ValueError(f"limit exceeds maximum: {MAX_RESULTS}")
    
    def _write_run_log(
        self,
        run_id: str,
        query: str,
        session_id: str,
        output_path: Optional[Path],
        status: str,
        result_count: int = 0,
        duration_ms: float = 0,
        error: str = ""
    ) -> Path:
        """
        Write run log.
        
        Args:
            run_id: Run identifier
            query: User search query
            session_id: Session identifier
            output_path: Path to output file (if successful)
            status: "success" or "failed"
            result_count: Number of results returned
            duration_ms: Execution duration in ms
            error: Error message (if failed)
            
        Returns:
            Path to log file
        """
        log_data = {
            "run_id": run_id,
            "query": query,
            "session_id": session_id,
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

