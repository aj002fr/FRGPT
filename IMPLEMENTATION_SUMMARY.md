
## Overview


```
Planner 1 (Task Decomposition)
    ↓
Planner 2 (Tool Discovery Per Path - Lazy Loading)
    ↓
Coder (Script Generation with DB Writes)
    ↓
Workers (Parallel Execution - SQLite + File Bus)
    ↓
Runner (DB-Based Consolidation + AI Validation)
```

---



---

## Components Implemented

### Phase 1: Core Infrastructure ✅

#### 1. `dependency_analyzer.py` (290 lines)
- **Purpose**: DAG analysis and path extraction
- **Key Features**:
  - Cycle detection via DFS
  - Path extraction from leaf to root tasks
  - Parallel group computation (topological sort)
  - Max depth calculation
  - Transitive dependency queries
- **Classes**: `DependencyAnalyzer`
- **Methods**: `analyze()`, `_detect_cycles()`, `_extract_paths()`, `_compute_parallel_groups()`

#### 2. `workers_db.py` (428 lines)
- **Purpose**: SQLite database for worker outputs
- **Key Features**:
  - Two-table schema (worker_runs, task_outputs)
  - Task lifecycle management (start, complete, store)
  - Dependency resolution checking
  - Run summaries and analytics
  - Context manager support
- **Classes**: `WorkersDB`
- **Tables**: 
  - `worker_runs`: Execution metadata
  - `task_outputs`: Result data (JSON)

#### 3. `tool_loader.py` (183 lines)
- **Purpose**: Lazy tool loading per path
- **Key Features**:
  - Agent-to-tool mapping registry
  - On-demand tool discovery
  - Tool caching
  - Context isolation per path
- **Classes**: `ToolLoader`
- **Map**: `AGENT_TOOL_MAP` (agent → tools)

### Phase 2: Planning Stages ✅

#### 4. `planner_stage1.py` (293 lines)
- **Purpose**: Task decomposition with dependencies
- **Key Features**:
  - AI-powered task breakdown (via TaskPlannerClient)
  - Agent assignment (via TaskMapper)
  - Dependency analysis (via DependencyAnalyzer)
  - Task ID normalization
  - Plan validation
- **Classes**: `PlannerStage1`
- **Output**: Subtasks with dependencies, parallel groups, execution paths

#### 5. `planner_stage2.py` (326 lines)
- **Purpose**: Tool discovery per dependency path
- **Key Features**:
  - One instance per path (context isolation)
  - Lazy tool loading for path agents
  - Tool parameter extraction
  - Execution plan generation
- **Classes**: `PlannerStage2`
- **Factory**: `create_planners_for_paths()`
- **Output**: Path plans with tools and parameters

### Phase 3: Execution ✅

#### 6. `coder.py` (396 lines)
- **Purpose**: Python script generation
- **Key Features**:
  - Async task functions with DB writes
  - Dependency-aware main() generation
  - WorkersDB integration
  - Topological sort for execution order
  - File bus reads + DB writes
- **Classes**: `Coder`
- **Generates**: Executable Python scripts with full error handling

#### 7. `worker_executor.py` (193 lines)
- **Purpose**: Task execution with DB persistence
- **Key Features**:
  - Script execution via exec() + asyncio
  - Dependency waiting with timeout
  - DB persistence (WorkersDB)
  - File bus compatibility
  - Run summaries
- **Classes**: `WorkerExecutor`
- **Helper**: `save_script()` for script persistence

### Phase 4: Consolidation ✅

#### 8. `runner.py` (387 lines)
- **Purpose**: DB-based result consolidation
- **Key Features**:
  - Query DB for all outputs by run_id
  - Merge data by agent type
  - Natural language answer generation
  - Summary statistics calculation
  - AI validation (via AnswerValidator)
- **Classes**: `Runner`
- **Replaces**: `ResultConsolidator` with DB-driven approach



---



## Database Schema

### `orchestrator_results.db`

#### Table: `worker_runs`
```sql
CREATE TABLE worker_runs (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    status TEXT NOT NULL,  -- 'running', 'success', 'failed'
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms REAL,
    error TEXT,
    output_file_path TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, task_id)
);
```

#### Table: `task_outputs`
```sql
CREATE TABLE task_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    output_data TEXT NOT NULL,  -- JSON
    metadata TEXT NOT NULL,      -- JSON
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id, task_id) REFERENCES worker_runs(run_id, task_id)
);
```

**Purpose**: Enable dependency resolution, result consolidation, and queryable history.

---

## Data Flow


**SQLite Database** (`orchestrator_results.db`):
- Task execution metadata
- Task outputs (JSON)
- Dependency resolution
- Run summaries
- Queryable history

**File Bus** (`workspace/agents/orchestrator-agent/out/`):
- Large datasets
- Complete results
- Manifest-based IDs
- Atomic writes

### Task Execution Flow
```
1. Planner 1 creates plan → subtasks with dependencies
2. Planner 2 (per path) → load only needed tools
3. Coder generates script → includes DB writes
4. Worker executes script:
   - DB.start_task()
   - Agent.run() → writes to file bus
   - DB.complete_task()
   - DB.store_task_output()
5. Runner consolidates:
   - Query DB for all outputs
   - Merge by agent type
   - Generate answer
   - Validate (optional)
```

---


## Testing Strategy

### Unit Tests (to be written)
- `test_dependency_analyzer.py`: Cycle detection, path extraction
- `test_workers_db.py`: CRUD operations, dependency checking
- `test_tool_loader.py`: Lazy loading, caching
- `test_planner_stage1.py`: Task decomposition, validation
- `test_planner_stage2.py`: Tool discovery, parameter extraction
- `test_coder.py`: Script generation, topological sort
- `test_worker_executor.py`: Script execution, dependency waiting
- `test_runner.py`: Consolidation, answer generation



