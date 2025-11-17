"""Consumer Agent - Reads and processes producer output."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
import time

from src.bus.manifest import Manifest
from src.bus.file_bus import read_json, write_atomic, ensure_dir
from src.bus.schema import validate_market_data, create_output_template

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    get_workspace_path,
    OUT_DIR,
    LOGS_DIR
)

logger = logging.getLogger(__name__)


class ConsumerAgent:
    """
    Consumer Agent - Reads producer output and emits derived artifacts.
    
    Validates inter-agent communication via file bus.
    """
    
    def __init__(self):
        """Initialize agent."""
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        
        logger.info(f"ConsumerAgent initialized at {self.workspace}")
    
    def run(self, input_path: Path) -> Path:
        """
        Process producer output.
        
        Args:
            input_path: Path to producer output file
            
        Returns:
            Path to consumer output file
            
        Raises:
            ValueError: If validation fails
            FileNotFoundError: If input file not found
        """
        start_time = time.time()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        
        logger.info(f"Run {run_id}: Processing input from {input_path}")
        
        try:
            # Read input
            logger.info("Reading producer output")
            input_data = read_json(Path(input_path))
            
            # Validate schema
            logger.info("Validating schema")
            is_valid, error = validate_market_data(input_data)
            if not is_valid:
                raise ValueError(f"Invalid input schema: {error}")
            
            logger.info("Schema validation passed")
            
            # Process data (example: compute summary statistics)
            processed_data = self._process_data(input_data)
            
            # Get next output path
            output_path = self.manifest.get_next_filepath(subdir=OUT_DIR)
            
            # Create output
            output_data = create_output_template(
                data=[processed_data],  # Wrapped as single record
                query=f"Processed from {input_path}",
                agent_name=AGENT_NAME,
                version=AGENT_VERSION
            )
            
            # Write output
            logger.info(f"Writing output to {output_path}")
            write_atomic(output_path, output_data)
            
            # Calculate duration
            duration_ms = (time.time() - start_time) * 1000
            
            # Write run log
            log_path = self._write_run_log(
                run_id=run_id,
                input_path=input_path,
                output_path=output_path,
                status="success",
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
                input_path=input_path,
                output_path=None,
                status="failed",
                error=str(e),
                duration_ms=duration_ms
            )
            
            logger.error(f"Run {run_id}: Failed - {e}")
            raise
    
    def _process_data(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process input data and compute derived artifact.
        
        Args:
            input_data: Producer output
            
        Returns:
            Processed summary data
        """
        data_records = input_data["data"]
        metadata = input_data["metadata"]
        
        # Compute summary statistics
        total_rows = len(data_records)
        
        # Extract numeric columns if available
        bid_values = [r.get("bid") for r in data_records if r.get("bid") is not None]
        ask_values = [r.get("ask") for r in data_records if r.get("ask") is not None]
        
        summary = {
            "source": metadata.get("agent", "unknown"),
            "source_query": metadata.get("query", ""),
            "source_row_count": total_rows,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "statistics": {
                "total_records": total_rows,
                "records_with_bid": len(bid_values),
                "records_with_ask": len(ask_values)
            }
        }
        
        if bid_values:
            summary["statistics"]["bid_min"] = min(bid_values)
            summary["statistics"]["bid_max"] = max(bid_values)
            summary["statistics"]["bid_avg"] = sum(bid_values) / len(bid_values)
        
        if ask_values:
            summary["statistics"]["ask_min"] = min(ask_values)
            summary["statistics"]["ask_max"] = max(ask_values)
            summary["statistics"]["ask_avg"] = sum(ask_values) / len(ask_values)
        
        logger.info(f"Computed statistics: {summary['statistics']}")
        
        return summary
    
    def _write_run_log(
        self,
        run_id: str,
        input_path: Path,
        output_path: Optional[Path],
        status: str,
        duration_ms: float = 0,
        error: str = ""
    ) -> Path:
        """
        Write run log.
        
        Args:
            run_id: Run identifier
            input_path: Input file path
            output_path: Output file path (if successful)
            status: "success" or "failed"
            duration_ms: Execution duration in ms
            error: Error message (if failed)
            
        Returns:
            Path to log file
        """
        log_data = {
            "run_id": run_id,
            "input_path": str(input_path),
            "output_path": str(output_path) if output_path else None,
            "status": status,
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

