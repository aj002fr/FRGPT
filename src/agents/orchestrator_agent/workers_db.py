"""Database schema and operations for worker task outputs."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class WorkersDB:
    """
    Manages database for worker task outputs and planning metadata.
    
    Tables:
    - worker_runs: execution metadata per task
    - task_outputs: structured outputs per task
    - task_plan: planning table (Planner 1 + Planner 2 enrichment)
    """
    
    def __init__(self, db_path: Path):
        """
        Initialize database connection.
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Enable dict-like access
        
        self._init_schema()
        
        logger.info(f"WorkersDB initialized at {self.db_path}")
    
    def _init_schema(self):
        """Initialize database schema."""
        cursor = self.conn.cursor()
        
        # Worker runs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS worker_runs (
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                duration_ms REAL,
                error TEXT,
                output_file_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (run_id, task_id)
            )
        """)
        
        # Task outputs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_outputs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                output_data TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (run_id, task_id) REFERENCES worker_runs(run_id, task_id)
            )
        """)
        
        # Task planning table (Planner 1 + Planner 2)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                agent_name TEXT,
                agent_description TEXT,
                dependency_path TEXT,  -- JSON array of task IDs
                tools TEXT,            -- JSON array of tool names
                tool_params TEXT,      -- JSON object: tool_name -> params
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Indices for task_outputs
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_run_id 
            ON task_outputs(run_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_id 
            ON task_outputs(task_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent 
            ON task_outputs(agent_name)
        """)
        
        # Indices for worker_runs
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_worker_run_id 
            ON worker_runs(run_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_worker_task_id 
            ON worker_runs(task_id)
        """)
        
        # Indices for task_plan
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_plan_run_id 
            ON task_plan(run_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_plan_task_id 
            ON task_plan(task_id)
        """)
        
        self.conn.commit()
        logger.debug("Database schema initialized")
    
    def start_task(
        self,
        run_id: str,
        task_id: str,
        agent_name: str
    ) -> None:
        """
        Record task start.
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            agent_name: Agent name
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            INSERT INTO worker_runs (run_id, task_id, agent_name, status, started_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            run_id,
            task_id,
            agent_name,
            'running',
            datetime.now(timezone.utc).isoformat()
        ))
        
        self.conn.commit()
        logger.debug(f"Task {task_id} started for run {run_id}")

    # ------------------------------------------------------------------
    # Planning table helpers (Planner 1 + Planner 2)
    # ------------------------------------------------------------------

    def insert_task_plan_row(
        self,
        run_id: str,
        task_id: str,
        agent_name: Optional[str],
        agent_description: str,
        dependency_path: List[str]
    ) -> int:
        """
        Insert initial planning row for a task (Planner 1).
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            agent_name: Assigned agent name (may be None for unmappable)
            agent_description: Human-readable agent description
            dependency_path: Ordered list of task IDs from root to this task
            
        Returns:
            Row ID in task_plan table
        """
        cursor = self.conn.cursor()
        
        cursor.execute(
            """
            INSERT INTO task_plan (
                run_id,
                task_id,
                agent_name,
                agent_description,
                dependency_path,
                tools,
                tool_params
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                agent_name,
                agent_description,
                json.dumps(dependency_path),
                json.dumps([]),
                json.dumps({})
            )
        )
        
        self.conn.commit()
        row_id = cursor.lastrowid
        logger.debug(
            "Inserted task_plan row for run %s task %s (row_id=%s)",
            run_id,
            task_id,
            row_id,
        )
        return row_id

    def update_task_plan_tools(
        self,
        run_id: str,
        task_id: str,
        tools: List[str],
        tool_params: Dict[str, Any]
    ) -> None:
        """
        Update planning row with tools and parameters (Planner 2).
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            tools: List of tool names
            tool_params: Mapping tool_name -> params dict
        """
        cursor = self.conn.cursor()
        
        cursor.execute(
            """
            UPDATE task_plan
            SET tools = ?, tool_params = ?
            WHERE run_id = ? AND task_id = ?
            """,
            (
                json.dumps(tools),
                json.dumps(tool_params),
                run_id,
                task_id,
            ),
        )
        
        self.conn.commit()
        logger.debug(
            "Updated task_plan tools for run %s task %s (tools=%s)",
            run_id,
            task_id,
            tools,
        )

    def get_task_plan(self, run_id: str) -> List[Dict[str, Any]]:
        """
        Get planning table for a run.
        
        Args:
            run_id: Orchestration run ID
            
        Returns:
            List of planning rows with parsed JSON fields
        """
        cursor = self.conn.cursor()
        
        cursor.execute(
            """
            SELECT
                task_id,
                agent_name,
                agent_description,
                dependency_path,
                tools,
                tool_params,
                created_at
            FROM task_plan
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        )
        
        rows = cursor.fetchall()
        results: List[Dict[str, Any]] = []
        
        for row in rows:
            results.append(
                {
                    "task_id": row["task_id"],
                    "agent_name": row["agent_name"],
                    "agent_description": row["agent_description"],
                    "dependency_path": json.loads(row["dependency_path"])
                    if row["dependency_path"]
                    else [],
                    "tools": json.loads(row["tools"]) if row["tools"] else [],
                    "tool_params": json.loads(row["tool_params"])
                    if row["tool_params"]
                    else {},
                    "created_at": row["created_at"],
                }
            )
        
        return results
    
    def complete_task(
        self,
        run_id: str,
        task_id: str,
        status: str,
        duration_ms: float,
        output_file_path: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """
        Record task completion.
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            status: 'success' or 'failed'
            duration_ms: Execution duration in milliseconds
            output_file_path: Path to output file (optional)
            error: Error message if failed (optional)
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            UPDATE worker_runs
            SET status = ?,
                completed_at = ?,
                duration_ms = ?,
                output_file_path = ?,
                error = ?
            WHERE run_id = ? AND task_id = ?
        """, (
            status,
            datetime.now(timezone.utc).isoformat(),
            duration_ms,
            output_file_path,
            error,
            run_id,
            task_id
        ))
        
        self.conn.commit()
        logger.debug(f"Task {task_id} completed with status {status}")
    
    def store_task_output(
        self,
        run_id: str,
        task_id: str,
        agent_name: str,
        output_data: Dict[str, Any],
        metadata: Dict[str, Any]
    ) -> int:
        """
        Store task output data.
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            agent_name: Agent name
            output_data: Output data dictionary
            metadata: Metadata dictionary
            
        Returns:
            Output record ID
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            INSERT INTO task_outputs (run_id, task_id, agent_name, output_data, metadata)
            VALUES (?, ?, ?, ?, ?)
        """, (
            run_id,
            task_id,
            agent_name,
            json.dumps(output_data),
            json.dumps(metadata)
        ))
        
        self.conn.commit()
        output_id = cursor.lastrowid
        
        logger.debug(f"Stored output for task {task_id} (ID: {output_id})")
        return output_id
    
    def get_task_output(
        self,
        run_id: str,
        task_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get task output by run_id and task_id.
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            
        Returns:
            Output dictionary or None if not found
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT output_data, metadata, agent_name, created_at
            FROM task_outputs
            WHERE run_id = ? AND task_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (run_id, task_id))
        
        row = cursor.fetchone()
        
        if row:
            return {
                'output_data': json.loads(row['output_data']),
                'metadata': json.loads(row['metadata']),
                'agent_name': row['agent_name'],
                'created_at': row['created_at']
            }
        
        return None
    
    def get_all_task_outputs(
        self,
        run_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all task outputs for a run.
        
        Args:
            run_id: Orchestration run ID
            
        Returns:
            List of output dictionaries
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT task_id, output_data, metadata, agent_name, created_at
            FROM task_outputs
            WHERE run_id = ?
            ORDER BY created_at ASC
        """, (run_id,))
        
        rows = cursor.fetchall()
        
        outputs = []
        for row in rows:
            outputs.append({
                'task_id': row['task_id'],
                'output_data': json.loads(row['output_data']),
                'metadata': json.loads(row['metadata']),
                'agent_name': row['agent_name'],
                'created_at': row['created_at']
            })
        
        return outputs
    
    def get_task_status(
        self,
        run_id: str,
        task_id: str
    ) -> Optional[str]:
        """
        Get task status.
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            
        Returns:
            Status string or None if not found
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT status
            FROM worker_runs
            WHERE run_id = ? AND task_id = ?
        """, (run_id, task_id))
        
        row = cursor.fetchone()
        return row['status'] if row else None
    
    def get_all_task_statuses(
        self,
        run_id: str
    ) -> Dict[str, str]:
        """
        Get status for all tasks in a run.
        
        Args:
            run_id: Orchestration run ID
            
        Returns:
            Dictionary mapping task_id to status
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT task_id, status
            FROM worker_runs
            WHERE run_id = ?
        """, (run_id,))
        
        rows = cursor.fetchall()
        return {row['task_id']: row['status'] for row in rows}
    
    def get_run_summary(
        self,
        run_id: str
    ) -> Dict[str, Any]:
        """
        Get summary of all tasks in a run.
        
        Args:
            run_id: Orchestration run ID
            
        Returns:
            Summary dictionary with counts and durations
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_tasks,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                AVG(duration_ms) as avg_duration_ms,
                SUM(duration_ms) as total_duration_ms,
                MIN(started_at) as first_start,
                MAX(completed_at) as last_completion
            FROM worker_runs
            WHERE run_id = ?
        """, (run_id,))
        
        row = cursor.fetchone()
        
        if row:
            return {
                'run_id': run_id,
                'total_tasks': row['total_tasks'],
                'successful': row['successful'],
                'failed': row['failed'],
                'running': row['running'],
                'avg_duration_ms': row['avg_duration_ms'],
                'total_duration_ms': row['total_duration_ms'],
                'first_start': row['first_start'],
                'last_completion': row['last_completion']
            }
        
        return {
            'run_id': run_id,
            'total_tasks': 0,
            'successful': 0,
            'failed': 0,
            'running': 0
        }
    
    def is_task_complete(
        self,
        run_id: str,
        task_id: str
    ) -> bool:
        """
        Check if task is complete.
        
        Args:
            run_id: Orchestration run ID
            task_id: Task ID
            
        Returns:
            True if task is complete (success or failed)
        """
        status = self.get_task_status(run_id, task_id)
        return status in ('success', 'failed') if status else False
    
    def are_dependencies_complete(
        self,
        run_id: str,
        dependency_ids: List[str]
    ) -> bool:
        """
        Check if all dependencies are complete.
        
        Args:
            run_id: Orchestration run ID
            dependency_ids: List of task IDs that are dependencies
            
        Returns:
            True if all dependencies are complete
        """
        if not dependency_ids:
            return True
        
        for dep_id in dependency_ids:
            if not self.is_task_complete(run_id, dep_id):
                return False
        
        return True
    
    def get_failed_tasks(
        self,
        run_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all failed tasks for a run.
        
        Args:
            run_id: Orchestration run ID
            
        Returns:
            List of failed task records
        """
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT task_id, agent_name, error, started_at, completed_at, duration_ms
            FROM worker_runs
            WHERE run_id = ? AND status = 'failed'
        """, (run_id,))
        
        rows = cursor.fetchall()
        
        return [
            {
                'task_id': row['task_id'],
                'agent_name': row['agent_name'],
                'error': row['error'],
                'started_at': row['started_at'],
                'completed_at': row['completed_at'],
                'duration_ms': row['duration_ms']
            }
            for row in rows
        ]
    
    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            logger.debug("Database connection closed")
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

