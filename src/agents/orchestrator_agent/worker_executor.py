"""Worker Task Executor with Database Persistence."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.bus.file_bus import ensure_dir
from .workers_db import WorkersDB

logger = logging.getLogger(__name__)


class WorkerExecutor:
    """
    Executes worker tasks and persists results.
    
    Responsibilities:
    - Execute generated scripts
    - Write to DB (worker_runs + task_outputs)
    - Write to file bus (data)
    - Handle dependencies (wait for required tasks)
    """
    
    def __init__(self, run_id: str, db_path: Path):
        """
        Initialize worker executor.
        
        Args:
            run_id: Orchestration run ID
            db_path: Path to workers database
        """
        self.run_id = run_id
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db = WorkersDB(self.db_path)
        
        logger.info(f"WorkerExecutor initialized for run {run_id}")
    
    def execute_all(
        self,
        scripts: List[Dict[str, Any]],
        subtasks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Execute all scripts (paths).
        
        Args:
            scripts: List of script dictionaries with 'path_id', 'code', 'path'
            subtasks: All subtasks from Stage 1 plan (for dependency info)
            
        Returns:
            List of all task results
        """
        logger.info(f"Executing {len(scripts)} scripts for run {self.run_id}")
        
        all_results = []
        
        for script_info in scripts:
            script_path = script_info['script_path']
            path_id = script_info.get('path_id', 'unknown')
            
            logger.info(f"Executing script for {path_id}: {script_path}")
            
            try:
                path_results = self._execute_script(script_path)
                all_results.extend(path_results)
                
                logger.info(f"Path {path_id} completed: {len(path_results)} tasks")
                
            except Exception as e:
                logger.error(f"Path {path_id} execution failed: {e}")
                # Continue with other paths
        
        logger.info(f"All scripts executed: {len(all_results)} total results")
        
        return all_results
    
    def _execute_script(
        self,
        script_path: Path
    ) -> List[Dict[str, Any]]:
        """
        Execute a single script.
        
        Args:
            script_path: Path to script file
            
        Returns:
            List of task results
        """
        logger.info(f"Executing script: {script_path}")
        
        # Read script
        with open(script_path, 'r', encoding='utf-8') as f:
            script_code = f.read()
        
        # Execute in isolated namespace
        namespace = {}
        exec(script_code, namespace)
        
        # Get main function and run it
        if 'main' not in namespace:
            raise Exception(f"Generated script {script_path} missing main() function")
        
        main_func = namespace['main']
        results = asyncio.run(main_func())
        
        return results
    
    def wait_for_dependencies(
        self,
        task_id: str,
        dependencies: List[str],
        timeout_seconds: int = 300
    ) -> bool:
        """
        Wait for task dependencies to complete.
        
        Args:
            task_id: Task ID that is waiting
            dependencies: List of task IDs to wait for
            timeout_seconds: Maximum wait time
            
        Returns:
            True if all dependencies completed, False if timeout
        """
        if not dependencies:
            return True
        
        logger.info(f"Task {task_id} waiting for dependencies: {dependencies}")
        
        start_time = time.time()
        
        while True:
            # Check if all dependencies are complete
            if self.db.are_dependencies_complete(self.run_id, dependencies):
                logger.info(f"Task {task_id} dependencies met")
                return True
            
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                logger.error(f"Task {task_id} dependency wait timeout ({timeout_seconds}s)")
                return False
            
            # Wait a bit before checking again
            time.sleep(0.5)
    
    def get_dependency_outputs(
        self,
        dependencies: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get outputs from dependency tasks.
        
        Args:
            dependencies: List of task IDs
            
        Returns:
            Dictionary mapping task_id -> output data
        """
        outputs = {}
        
        for dep_id in dependencies:
            output = self.db.get_task_output(self.run_id, dep_id)
            if output:
                outputs[dep_id] = output
        
        return outputs
    
    def get_run_summary(self) -> Dict[str, Any]:
        """
        Get summary of current run.
        
        Returns:
            Summary dictionary
        """
        return self.db.get_run_summary(self.run_id)
    
    def get_failed_tasks(self) -> List[Dict[str, Any]]:
        """
        Get failed tasks in current run.
        
        Returns:
            List of failed task records
        """
        return self.db.get_failed_tasks(self.run_id)
    
    def close(self):
        """Close database connection."""
        if self.db:
            self.db.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


def save_script(
    script_code: str,
    path_id: str,
    run_id: str,
    scripts_dir: Path
) -> Path:
    """
    Save generated script to file.
    
    Args:
        script_code: Python script code
        path_id: Path identifier
        run_id: Run ID
        scripts_dir: Scripts directory
        
    Returns:
        Path to saved script
    """
    ensure_dir(scripts_dir)
    
    # Create filename with path_id and run_id
    script_filename = f"orchestration_{run_id}_{path_id}.py"
    script_path = scripts_dir / script_filename
    
    # Write script
    with open(script_path, 'w', encoding='utf-8') as f:
        f.write(script_code)
    
    logger.info(f"Script saved: {script_path}")
    
    return script_path

