"""Output schema validation."""

from typing import Any, Dict, List, Optional
from datetime import datetime


class OutputSchema:
    """Base output schema validator."""
    
    REQUIRED_METADATA_FIELDS = [
        "query",
        "timestamp",
        "row_count",
        "agent",
        "version"
    ]
    
    @staticmethod
    def validate(data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate output structure.
        
        Args:
            data: Output data to validate
            
        Returns:
            (is_valid, error_message)
        """
        # Check top-level structure
        if not isinstance(data, dict):
            return False, "Output must be a dictionary"
        
        if "data" not in data:
            return False, "Missing 'data' field"
        
        if "metadata" not in data:
            return False, "Missing 'metadata' field"
        
        # Check data is list
        if not isinstance(data["data"], list):
            return False, "'data' must be a list"
        
        # Check metadata
        metadata = data["metadata"]
        if not isinstance(metadata, dict):
            return False, "'metadata' must be a dictionary"
        
        # Check required metadata fields
        for field in OutputSchema.REQUIRED_METADATA_FIELDS:
            if field not in metadata:
                return False, f"Missing required metadata field: {field}"
        
        return True, None


def validate_market_data(data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate market data specific schema.
    
    Args:
        data: Market data output
        
    Returns:
        (is_valid, error_message)
    """
    # First validate base schema
    is_valid, error = OutputSchema.validate(data)
    if not is_valid:
        return False, error
    
    # Validate market data specific fields
    data_records = data["data"]
    
    if len(data_records) > 0:
        # Check first record has expected fields
        first_record = data_records[0]
        required_fields = ["symbol"]  # At minimum, symbol is required
        
        for field in required_fields:
            if field not in first_record:
                return False, f"Data records missing required field: {field}"
    
    # Validate metadata specific to market data
    metadata = data["metadata"]
    
    # Check row_count matches actual data length
    if metadata["row_count"] != len(data_records):
        return False, f"Row count mismatch: metadata says {metadata['row_count']}, actual is {len(data_records)}"
    
    return True, None


def create_output_template(
    data: List[Dict[str, Any]],
    query: str,
    agent_name: str,
    version: str = "1.0"
) -> Dict[str, Any]:
    """
    Create standardized output structure.
    
    Args:
        data: List of data records
        query: SQL query that generated the data
        agent_name: Name of the agent
        version: Schema version
        
    Returns:
        Standardized output dictionary
    """
    return {
        "data": data,
        "metadata": {
            "query": query,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "row_count": len(data),
            "agent": agent_name,
            "version": version
        }
    }


