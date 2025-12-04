"""Orchestrator Agent - Two-Stage Planner System."""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from src.bus.manifest import Manifest
from src.bus.file_bus import write_atomic, ensure_dir
from src.bus.schema import create_output_template
from src.core.run_store import (
    create_run,
    update_run_status,
    log_agent_output,
    write_run_artifact,
    get_run_dir,
)

from .config import (
    AGENT_NAME,
    AGENT_VERSION,
    AGENT_CAPABILITIES,
    get_workspace_path,
    get_db_path,
    OUT_DIR,
    LOGS_DIR,
    SCRIPTS_DIR,
    DEFAULT_NUM_SUBTASKS,
)
from .planner_stage1 import PlannerStage1
from .planner_stage2 import PlannerStage2, create_planners_for_paths
from .coder import Coder
from .worker_executor import WorkerExecutor, save_script
from .workers_db import WorkersDB
from ..reasoning_agent.run import RunnerAgent

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    """
    Orchestrator Agent - Two-Stage Planner System.
    
    New Architecture:
    1. Planner 1: Task decomposition with dependencies
    2. Planner 2: Tool discovery per dependency path (lazy loading)
    3. Coder: Python script generation
    4. Workers: Task execution with DB + file bus persistence
    5. Runner: Result consolidation with AI validation
    
    Key Features:
    - Context-efficient tool loading (per path)
    - Full DAG dependency support
    - Dual storage (DB for metadata, file bus for data)
    - Parallel path execution
    """
    
    def __init__(self):
        """Initialize orchestrator agent."""
        self.workspace = get_workspace_path()
        self.manifest = Manifest(self.workspace)
        self.db_path = get_db_path()
        
        # Initialize components
        self.planner1 = PlannerStage1()
        self.coder = Coder()
        
        logger.info(f"OrchestratorAgent initialized (Two-Stage Planner)")
        logger.info(f"Workspace: {self.workspace}")
        logger.info(f"Database: {self.db_path}")
    
    def run(
        self,
        query: str,
        num_subtasks: Optional[int] = None,
        skip_validation: bool = False
    ) -> Dict[str, Any]:
        """
        Execute two-stage orchestration workflow.
        
        Args:
            query: Natural language query
            num_subtasks: Number of subtasks (default from config)
            skip_validation: Skip AI validation step
            
        Returns:
            Dictionary with answer, data, validation, and metadata
            
        Raises:
            Exception: If orchestration fails
        """
        start_time = time.time()
        # Create central run record + runs/{run_id}/query.json
        run_id = create_run(user_query=query)
        
        logger.info(f"=== Run {run_id} ===")
        logger.info(f"Query: '{query[:100]}...'")
        logger.info("Starting two-stage orchestration")
        
        try:
            # ==================== STAGE 1: Task Planning ====================
            logger.info("\n[STAGE 1] Task Planning & Dependency Analysis")
            
            plan = self.planner1.plan(
                query=query,
                num_subtasks=num_subtasks or DEFAULT_NUM_SUBTASKS,
                agent_capabilities=AGENT_CAPABILITIES
            )
            
            subtasks = plan['subtasks']
            dependency_paths = plan['dependency_paths']
            metadata = plan['metadata']
            task_dependency_paths = metadata.get("task_dependency_paths", {})
            
            logger.info(f"✓ Plan created:")
            logger.info(f"  - {metadata['total_tasks']} tasks ({metadata['mappable_tasks']} mappable)")
            logger.info(f"  - {len(dependency_paths)} dependency paths")
            logger.info(f"  - Max depth: {metadata['max_depth']}")
            
            if metadata['mappable_tasks'] == 0:
                # Persist empty plan for debugging before failing
                planner1_path = write_run_artifact(
                    run_id,
                    "planner1.json",
                    plan,
                )
                log_agent_output(
                    run_id=run_id,
                    agent="planner1",
                    step=1,
                    task_id=None,
                    summary="Planner1 produced 0 mappable tasks",
                    data_pointer=planner1_path,
                )
                raise Exception("No mappable tasks in plan")

            # Persist Planner 1 view of the task plan
            planner1_path = write_run_artifact(
                run_id,
                "planner1.json",
                plan,
            )
            log_agent_output(
                run_id=run_id,
                agent="planner1",
                step=1,
                task_id=None,
                summary=(
                    f"Planner1 plan with {metadata['total_tasks']} tasks "
                    f"({metadata['mappable_tasks']} mappable, "
                    f"{len(dependency_paths)} paths)"
                ),
                data_pointer=planner1_path,
            )
            
            # Persist initial planning table (Planner 1 view)
            logger.info("\n[STAGE 1b] Persisting planning table (task_plan)")
            with WorkersDB(self.db_path) as db:
                for task in subtasks:
                    task_id = task["id"]
                    agent_name = task.get("assigned_agent")
                    # Agent description from capabilities (if any)
                    agent_config = AGENT_CAPABILITIES.get(agent_name or "", {})
                    agent_description = agent_config.get("description", "")
                    # Dependency path for this task (root → task); fall back to [task_id]
                    dependency_path = task_dependency_paths.get(task_id) or [task_id]
                    db.insert_task_plan_row(
                        run_id=run_id,
                        task_id=task_id,
                        agent_name=agent_name,
                        agent_description=agent_description,
                        dependency_path=dependency_path,
                    )
            
            # ==================== STAGE 2: Tool Discovery Per Path ====================
            logger.info(f"\n[STAGE 2] Tool Discovery (per path)")
            
            # Create Planner 2 for each dependency path
            planner2_instances = create_planners_for_paths(dependency_paths)
            
            path_plans = []
            for planner2 in planner2_instances:
                path_plan = planner2.discover_tools_and_params(subtasks, user_query=query)
                path_plans.append(path_plan)
                
                logger.info(
                    "✓ %s: %s tools, %s tasks",
                    path_plan["path_id"],
                    len(path_plan["tools_loaded"]),
                    len(path_plan["mappable_tasks"]),
                )

            # Persist Planner 2 per-task configuration under runs/{run_id}/planner2/
            logger.info("\n[STAGE 2b] Persisting Planner 2 task configs")
            for path_plan in path_plans:
                for task_plan in path_plan.get("execution_plan", []):
                    task_id = task_plan["task_id"]
                    agent_name = task_plan.get("agent")
                    relative_path = Path("planner2") / f"{task_id}.json"

                    artifact_payload = {
                        "run_id": run_id,
                        "path_id": path_plan["path_id"],
                        "task": task_plan,
                        "tools_loaded": path_plan.get("tools_loaded", []),
                        "agents_in_path": path_plan.get("agents", []),
                        "metadata": path_plan.get("metadata", {}),
                    }

                    planner2_path = write_run_artifact(
                        run_id,
                        relative_path,
                        artifact_payload,
                    )

                    summary = (
                        f"Planner2 config for {task_id} "
                        f"(agent={agent_name or 'unknown'}, "
                        f"tools={len(task_plan.get('tools', []))})"
                    )
                    log_agent_output(
                        run_id=run_id,
                        agent="planner2",
                        step=2,
                        task_id=task_id,
                        summary=summary,
                        data_pointer=planner2_path,
                    )

            # Enrich planning table with tools & parameters (Planner 2 view)
            logger.info("\n[STAGE 2c] Updating planning table with tools & parameters")
            with WorkersDB(self.db_path) as db:
                for path_plan in path_plans:
                    for task_plan in path_plan.get("execution_plan", []):
                        task_id = task_plan["task_id"]
                        tools = task_plan.get("tools", [])
                        tool_params = task_plan.get("tool_params", {})
                        db.update_task_plan_tools(
                            run_id=run_id,
                            task_id=task_id,
                            tools=tools,
                            tool_params=tool_params,
                        )
            
            # ==================== STAGE 3: Code Generation ====================
            logger.info(f"\n[STAGE 3] Script Generation")
            
            scripts_dir = self.workspace / SCRIPTS_DIR
            ensure_dir(scripts_dir)
            runs_scripts_dir = get_run_dir(run_id) / "scripts"
            ensure_dir(runs_scripts_dir)
            
            scripts = []
            for path_plan in path_plans:
                script_code = self.coder.generate(
                    path_plan=path_plan,
                    run_id=run_id,
                    db_path=self.db_path
                )
                
                # Save script in workspace for execution
                script_path = save_script(
                    script_code=script_code,
                    path_id=path_plan['path_id'],
                    run_id=run_id,
                    scripts_dir=scripts_dir
                )

                # Also persist script under runs/{run_id}/scripts/{task_id}.py
                tasks_in_path = path_plan.get("tasks", [])
                # Use the last task in the path as representative task_id;
                # fall back to path_id if unavailable.
                representative_task_id = (
                    tasks_in_path[-1] if tasks_in_path else path_plan["path_id"]
                )
                run_script_path = runs_scripts_dir / f"{representative_task_id}.py"
                with open(run_script_path, "w", encoding="utf-8") as f:
                    f.write(script_code)

                log_agent_output(
                    run_id=run_id,
                    agent="coder",
                    step=3,
                    task_id=representative_task_id,
                    summary=(
                        f"Coder script for path {path_plan['path_id']} "
                        f"({len(tasks_in_path)} tasks)"
                    ),
                    data_pointer=run_script_path,
                )
                
                scripts.append({
                    'path_id': path_plan['path_id'],
                    'script_path': script_path,
                    'code': script_code
                })
                
                logger.info(f"✓ Generated script for {path_plan['path_id']}: {script_path}")
            
            # ==================== STAGE 4: Worker Execution ====================
            logger.info(f"\n[STAGE 4] Worker Execution")
            
            with WorkerExecutor(run_id, self.db_path) as executor:
                task_results = executor.execute_all(scripts, subtasks)
                
                run_summary = executor.get_run_summary()
                failed_tasks = executor.get_failed_tasks()
                
                logger.info(f"✓ Execution complete:")
                logger.info(f"  - Total: {run_summary['total_tasks']}")
                logger.info(f"  - Successful: {run_summary['successful']}")
                logger.info(f"  - Failed: {run_summary['failed']}")
            
            # ==================== STAGE 5: Runner Agent Consolidation ====================
            logger.info(f"\n[STAGE 5] Final consolidation via RunnerAgent")

            # Load worker outputs and planning table for final consolidation.
            with WorkersDB(self.db_path) as db:
                worker_outputs = db.get_all_task_outputs(run_id)
                planning_table = db.get_task_plan(run_id)
                run_summary = db.get_run_summary(run_id)

            runner_agent = RunnerAgent()
            reasoning_output_path = runner_agent.run(
                query=query,
                worker_outputs=worker_outputs,
                planning_table=planning_table,
                run_id=run_id,
            )

            # Read reasoning agent output back for inclusion in orchestrator result
            with open(reasoning_output_path, "r", encoding="utf-8") as f:
                reasoning_output = json.load(f)

            reasoning_data = reasoning_output.get("data", [])
            reasoning_record = reasoning_data[0] if reasoning_data else {}
            answer = reasoning_record.get("final_answer", "")

            logger.info("✓ RunnerAgent consolidation complete")

            # ==================== STAGE 6: Output & Logging ====================
            logger.info(f"\n[STAGE 6] Output & Logging")
            
            duration_ms = (time.time() - start_time) * 1000
            
            # Derive agents used from worker outputs plus runner agent
            agent_names = {w["agent_name"] for w in worker_outputs}
            agent_names.add("runner_agent")

            # Prepare final result
            result = {
                'query': query,
                'answer': answer,
                'data': {
                    'reasoning_result': reasoning_record,
                },
                # Validation is now implicit in the reasoning step; keep field for compatibility.
                'validation': None,
                'metadata': {
                    'total_tasks': run_summary['total_tasks'],
                    'successful_tasks': run_summary['successful'],
                    'failed_tasks': run_summary['failed'],
                    'run_id': run_id,
                    'duration_ms': round(duration_ms, 2),
                    'num_paths': len(dependency_paths),
                    'num_scripts': len(scripts),
                    'script_paths': [str(s['script_path']) for s in scripts],
                    # Backwards-compatible single script path (first path if available)
                    'script_path': str(scripts[0]['script_path']) if scripts else None,
                    'unmappable_tasks': metadata['unmappable_tasks']
                },
                'worker_outputs': worker_outputs,
                'failed_tasks': failed_tasks,
                # New: full planning table (Planner 1 + Planner 2)
                'planning_table': planning_table,
            }
            
            # Write to file bus
            output_path = self._write_output(result, run_id)
            result['output_path'] = str(output_path)
            
            # Write run log
            self._write_run_log(
                run_id=run_id,
                query=query,
                output_path=output_path,
                status='success',
                duration_ms=duration_ms,
                metadata=result['metadata']
            )

            # Update central run status
            update_run_status(run_id=run_id, status="success")
            
            logger.info(f"✓ Output written to: {output_path}")
            logger.info(f"\n=== Run {run_id} Complete ({duration_ms:.2f}ms) ===\n")
            
            return result
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            
            logger.error(f"\n=== Run {run_id} Failed ===")
            logger.error(f"Error: {e}")
            
            # Write failure log
            self._write_run_log(
                run_id=run_id,
                query=query,
                output_path=None,
                status='failed',
                duration_ms=duration_ms,
                error=str(e)
            )

            # Update central run status
            update_run_status(run_id=run_id, status="failed")
            
            raise
    
    def _write_output(
        self,
        result: Dict[str, Any],
        run_id: str
    ) -> Path:
        """Write result to file bus."""
        # Get next output path
        output_path = self.manifest.get_next_filepath(subdir=OUT_DIR)
        
        # Create standardized output
        output_data = create_output_template(
            data=[result],
            query=f"Orchestration: {result.get('query', 'N/A')}",
            agent_name=AGENT_NAME,
            version=AGENT_VERSION
        )
        
        # Add orchestration metadata
        output_data['metadata']['orchestration'] = {
            'run_id': run_id,
            'total_tasks': result.get('metadata', {}).get('total_tasks', 0),
            'successful_tasks': result.get('metadata', {}).get('successful_tasks', 0),
            'failed_tasks': result.get('metadata', {}).get('failed_tasks', 0),
            'agents_used': result.get('metadata', {}).get('agents_used', []),
            'num_paths': result.get('metadata', {}).get('num_paths', 0),
            'validation_passed': result.get('metadata', {}).get('validation_passed')
        }
        
        # Write atomically
        write_atomic(output_path, output_data)
        
        return output_path
    
    def _write_run_log(
        self,
        run_id: str,
        query: str,
        output_path: Optional[Path],
        status: str,
        duration_ms: float,
        metadata: Optional[Dict[str, Any]] = None,
        error: str = ""
    ) -> Path:
        """Write run log."""
        log_data = {
            'run_id': run_id,
            'query': query,
            'output_path': str(output_path) if output_path else None,
            'status': status,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'duration_ms': round(duration_ms, 2),
            'agent': AGENT_NAME,
            'version': AGENT_VERSION,
            'architecture': 'two-stage-planner'
        }
        
        if metadata:
            log_data['metadata'] = metadata
        
        if error:
            log_data['error'] = error
        
        # Write to logs directory
        log_path = self.workspace / LOGS_DIR / f"{run_id}.json"
        ensure_dir(log_path.parent)
        write_atomic(log_path, log_data)
        
        return log_path
    
    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return self.manifest.get_stats()
