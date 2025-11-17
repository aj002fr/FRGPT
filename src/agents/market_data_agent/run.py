"""Market Data Agent - Main logic."""

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
    DEFAULT_COLUMNS,
    AVAILABLE_TEMPLATES,
    REQUIRED_PARAMS,
    MAX_ROWS
)

logger = logging.getLogger(__name__)


class MarketDataAgent:
    """
    Market Data Agent - Queries data and writes to file bus.
    
    This is a producer agent that:
    1. Validates inputs
    2. Calls marketdata tool
    3. Writes output with incremented filename
    4. Logs each run with SQL and output path
    """
    
    def __init__(self):
        """Initialize agent."""
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        self.mcp_client = MCPClient()
        
        logger.info(f"MarketDataAgent initialized at {self.workspace}")
    
    def run(
        self,
        template: str = "all_valid",
        params: Optional[Dict[str, Any]] = None,
        columns: Optional[List[str]] = None,
        limit: Optional[int] = None,
        order_by_column: Optional[str] = None,
        order_by_direction: str = "ASC"
    ) -> Path:
        """
        Execute query and write to file bus.
        
        Args:
            template: Query template name
            params: Query parameters
            columns: Columns to select (default: DEFAULT_COLUMNS)
            limit: Row limit (default: None, max: MAX_ROWS)
            order_by_column: Column to sort by (optional)
            order_by_direction: Sort direction - "ASC" or "DESC" (default: "ASC")
            
        Returns:
            Path to output file
            
        Raises:
            ValueError: If invalid inputs
            Exception: If query or write fails
        """
        start_time = time.time()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Run {run_id}: Starting with template={template}, params={params}")
        
        try:
            # Validate inputs
            self._validate_inputs(template, params, limit)
            
            # Use default columns if not specified
            if columns is None:
                columns = DEFAULT_COLUMNS
            
            # Call marketdata tool
            logger.info("Calling marketdata tool")
            result = self.mcp_client.call_tool(
                "run_query",
                arguments={
                    "template": template,
                    "columns": columns,
                    "params": params or {},
                    "limit": limit,
                    "order_by_column": order_by_column,
                    "order_by_direction": order_by_direction
                }
            )
            
            # Extract data
            data = result["data"]
            metadata_raw = result["metadata"]
            
            logger.info(f"Tool returned {len(data)} rows")
            
            # Get next output path
            output_path = self.manifest.get_next_filepath(subdir=OUT_DIR)
            
            # Create standardized output
            output_data = create_output_template(
                data=data,
                query=metadata_raw["query"],
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
                sql=metadata_raw["query"],
                params=params or {},
                output_path=output_path,
                status="success",
                row_count=len(data),
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
                sql="",
                params=params or {},
                output_path=None,
                status="failed",
                error=str(e),
                duration_ms=duration_ms
            )
            
            logger.error(f"Run {run_id}: Failed - {e}")
            raise
    
    def _validate_inputs(
        self,
        template: str,
        params: Optional[Dict[str, Any]],
        limit: Optional[int]
    ) -> None:
        """
        Validate inputs.
        
        Raises:
            ValueError: If validation fails
        """
        # Check template
        if template not in AVAILABLE_TEMPLATES:
            raise ValueError(
                f"Invalid template: {template}. "
                f"Available: {', '.join(AVAILABLE_TEMPLATES)}"
            )
        
        # Check required params for template
        required = REQUIRED_PARAMS.get(template, [])
        if required and not params:
            raise ValueError(f"Template '{template}' requires params: {required}")
        
        if required and params:
            for param in required:
                if param not in params:
                    raise ValueError(f"Missing required parameter: {param}")
        
        # Check limit
        if limit is not None:
            if limit <= 0:
                raise ValueError("Limit must be positive")
            if limit > MAX_ROWS:
                raise ValueError(f"Limit exceeds maximum: {MAX_ROWS}")
    
    def _write_run_log(
        self,
        run_id: str,
        sql: str,
        params: Dict[str, Any],
        output_path: Optional[Path],
        status: str,
        row_count: int = 0,
        duration_ms: float = 0,
        error: str = ""
    ) -> Path:
        """
        Write run log.
        
        Args:
            run_id: Run identifier
            sql: SQL query
            params: Query parameters
            output_path: Path to output file (if successful)
            status: "success" or "failed"
            row_count: Number of rows returned
            duration_ms: Execution duration in ms
            error: Error message (if failed)
            
        Returns:
            Path to log file
        """
        log_data = {
            "run_id": run_id,
            "sql": sql,
            "params": params,
            "output_path": str(output_path) if output_path else None,
            "status": status,
            "row_count": row_count,
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


