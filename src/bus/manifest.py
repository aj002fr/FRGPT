"""Manifest management for incremental filenames."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from .file_bus import write_atomic, read_json, ensure_dir


class Manifest:
    """
    Manages manifest for an agent's output directory.
    
    Manifest format:
    {
        "next_id": 1,
        "last_updated": "2025-11-11T12:00:00Z",
        "total_runs": 0
    }
    """
    
    def __init__(self, agent_dir: Path):
        """
        Initialize manifest for an agent.
        
        Args:
            agent_dir: Agent's workspace directory
        """
        self.agent_dir = Path(agent_dir)
        self.manifest_path = self.agent_dir / "meta.json"
        
        # Ensure directory exists
        ensure_dir(self.agent_dir)
        
        # Initialize if doesn't exist
        if not self.manifest_path.exists():
            self._initialize()
    
    def _initialize(self) -> None:
        """Initialize new manifest."""
        data = {
            "next_id": 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_runs": 0
        }
        write_atomic(self.manifest_path, data)
    
    def get_next_id(self) -> int:
        """
        Get next available ID without incrementing.
        
        Returns:
            Next ID number
        """
        data = read_json(self.manifest_path)
        return data["next_id"]
    
    def increment(self) -> int:
        """
        Get next ID and increment manifest atomically.
        
        Returns:
            The ID that was allocated
        """
        data = read_json(self.manifest_path)
        current_id = data["next_id"]
        
        # Update manifest
        data["next_id"] = current_id + 1
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        data["total_runs"] = data.get("total_runs", 0) + 1
        
        write_atomic(self.manifest_path, data)
        
        return current_id
    
    def get_filename(self, file_id: int, extension: str = "json") -> str:
        """
        Generate filename from ID.
        
        Args:
            file_id: Numeric ID
            extension: File extension (default: json)
            
        Returns:
            Filename like "000001.json"
        """
        return f"{file_id:06d}.{extension}"
    
    def get_next_filepath(self, subdir: str = "out", extension: str = "json") -> Path:
        """
        Get next output filepath with incremented ID.
        
        Args:
            subdir: Subdirectory (default: "out")
            extension: File extension (default: "json")
            
        Returns:
            Full path to next output file
        """
        file_id = self.increment()
        filename = self.get_filename(file_id, extension)
        filepath = self.agent_dir / subdir / filename
        
        # Ensure output directory exists
        ensure_dir(filepath.parent)
        
        return filepath
    
    def get_stats(self) -> dict:
        """Get manifest statistics."""
        data = read_json(self.manifest_path)
        return {
            "next_id": data["next_id"],
            "total_runs": data.get("total_runs", 0),
            "last_updated": data["last_updated"]
        }


