"""Central run and agent output persistence for orchestration runs.

This module provides a small, well-typed API around a single SQLite
database at ``data/metadata.db`` plus a standardized on-disk layout:

    runs/{run_id}/
        query.json
        planner1.json
        planner2/{task_id}.json
        scripts/{task_id}.py
        workers/{agent_name}/{task_id}.json
        runner_output.json

The database keeps lightweight metadata only:

    Table ``runs``:
        - run_id     TEXT PRIMARY KEY
        - user_query TEXT
        - created_at TEXT (ISO-8601)
        - status     TEXT ('success' | 'failed' | 'running')

    Table ``agent_outputs``:
        - id          INTEGER PRIMARY KEY AUTOINCREMENT
        - run_id      TEXT
        - agent       TEXT
        - step        INTEGER
        - task_id     TEXT NULL
        - summary     TEXT NULL
        - data_pointer TEXT  (path to JSON/script relative to project root)
        - created_at  TEXT (ISO-8601)

Runtime dependencies are stdlib-only: ``sqlite3``, ``json``, ``pathlib``,
``datetime``, and ``uuid``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_DIR: Path = PROJECT_ROOT / "data"
METADATA_DB_PATH: Path = DATA_DIR / "metadata.db"
RUNS_ROOT: Path = PROJECT_ROOT / "runs"


def _utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _ensure_base_dirs() -> None:
    """Ensure that ``data/`` and ``runs/`` directories exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)


def init_db(db_path: Optional[Path] = None) -> Path:
    """Initialize the central metadata database if needed.

    Args:
        db_path: Optional override for database path (useful for tests).

    Returns:
        Path to the SQLite database file.
    """
    _ensure_base_dirs()
    db_file = Path(db_path) if db_path is not None else METADATA_DB_PATH

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        cursor = conn.cursor()

        # Runs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id     TEXT PRIMARY KEY,
                user_query TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status     TEXT NOT NULL
            )
            """
        )

        # Agent outputs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_outputs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id       TEXT NOT NULL,
                agent        TEXT NOT NULL,
                step         INTEGER NOT NULL,
                task_id      TEXT,
                summary      TEXT,
                data_pointer TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
            """
        )

        # Helpful indices
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_outputs_run_id "
            "ON agent_outputs(run_id)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_outputs_agent "
            "ON agent_outputs(agent)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_outputs_step "
            "ON agent_outputs(step)"
        )

        conn.commit()
    finally:
        conn.close()

    logger.info("Central metadata DB initialized at %s", db_file)
    return db_file


def generate_run_id() -> str:
    """Generate a unique, human-readable run ID.

    Format: ``YYYYMMDD_HHMMSS_xxxxxx`` where the suffix is 6 hex chars.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid4().hex[:6]
    return f"{timestamp}_{suffix}"


def get_run_dir(run_id: str) -> Path:
    """Return the filesystem directory for a given run."""
    _ensure_base_dirs()
    run_dir = RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically-like (simple overwrite, stdlib only).

    For now we keep this simple as these artifacts are relatively small.
    The core file bus already provides fully atomic writes for agent
    workspaces; here we only need best-effort persistence.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_run_artifact(
    run_id: str,
    relative_path: str | Path,
    payload: Dict[str, Any],
) -> Path:
    """Write a JSON artifact under ``runs/{run_id}/``.

    Args:
        run_id: Parent run identifier.
        relative_path: Path relative to ``runs/{run_id}/`` such as
            ``"planner1.json"`` or ``"planner2/task_1.json"``.
        payload: JSON-serializable dictionary to persist.

    Returns:
        Absolute ``Path`` to the written artifact.
    """
    run_dir = get_run_dir(run_id)
    artifact_path = run_dir / relative_path
    _write_json(artifact_path, payload)
    return artifact_path


@dataclass
class RunRecord:
    """Lightweight view of a row in the ``runs`` table."""

    run_id: str
    user_query: str
    created_at: str
    status: str


def create_run(user_query: str, db_path: Optional[Path] = None) -> str:
    """Create a new run record and ``runs/{run_id}/query.json`` artifact.

    Args:
        user_query: Raw natural language query from the user.
        db_path: Optional override for database path.

    Returns:
        Newly created ``run_id``.
    """
    db_file = init_db(db_path=db_path)
    run_id = generate_run_id()
    created_at = _utc_now_iso()

    # Persist query.json under runs/{run_id}/
    run_dir = get_run_dir(run_id)
    query_path = run_dir / "query.json"
    _write_json(
        query_path,
        {
            "run_id": run_id,
            "user_query": user_query,
            "created_at": created_at,
        },
    )

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO runs (run_id, user_query, created_at, status)
            VALUES (?, ?, ?, ?)
            """,
            (run_id, user_query, created_at, "running"),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Created run %s (query length=%d)", run_id, len(user_query))
    return run_id


def update_run_status(
    run_id: str,
    status: str,
    db_path: Optional[Path] = None,
) -> None:
    """Update the status of a run.

    Args:
        run_id: Run identifier.
        status: New status (e.g. ``'success'`` or ``'failed'``).
        db_path: Optional override for database path.
    """
    if status not in {"running", "success", "failed"}:
        logger.warning("Unexpected run status '%s' for run %s", status, run_id)

    db_file = init_db(db_path=db_path)
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE runs SET status = ? WHERE run_id = ?",
            (status, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def log_agent_output(
    run_id: str,
    agent: str,
    step: int,
    data_pointer: Path | str,
    task_id: Optional[str] = None,
    summary: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Log an agent output artifact in ``agent_outputs``.

    Args:
        run_id: Parent run ID.
        agent: Logical agent name (e.g. ``'planner1'``, ``'planner2'``,
            ``'coder'``, ``'worker_marketdata'``, ``'runner'``).
        step: Pipeline stage number (1–5).
        data_pointer: Path to JSON/script blob. Stored relative to project
            root when possible.
        task_id: Optional task identifier (for per-task artifacts).
        summary: Optional short, LLM-friendly summary.
        db_path: Optional override for database path.

    Returns:
        Auto-incremented ``id`` from ``agent_outputs``.
    """
    db_file = init_db(db_path=db_path)

    # Normalize data_pointer to string, preferably relative to project root.
    if isinstance(data_pointer, Path):
        try:
            data_str = str(data_pointer.relative_to(PROJECT_ROOT))
        except ValueError:
            data_str = str(data_pointer)
    else:
        data_str = data_pointer

    created_at = _utc_now_iso()

    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO agent_outputs (
                run_id,
                agent,
                step,
                task_id,
                summary,
                data_pointer,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, agent, step, task_id, summary, data_str, created_at),
        )
        conn.commit()
        output_id = int(cursor.lastrowid)
    finally:
        conn.close()

    logger.debug(
        "Logged agent_output id=%s run_id=%s agent=%s step=%s task_id=%s",
        output_id,
        run_id,
        agent,
        step,
        task_id,
    )
    return output_id


def get_run(run_id: str, db_path: Optional[Path] = None) -> Optional[RunRecord]:
    """Fetch a single run by ID."""
    db_file = init_db(db_path=db_path)
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT run_id, user_query, created_at, status "
            "FROM runs WHERE run_id = ?",
            (run_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return RunRecord(
            run_id=row["run_id"],
            user_query=row["user_query"],
            created_at=row["created_at"],
            status=row["status"],
        )
    finally:
        conn.close()


def list_recent_runs(
    limit: int = 50, db_path: Optional[Path] = None
) -> List[RunRecord]:
    """Return recent runs ordered by creation time (descending)."""
    db_file = init_db(db_path=db_path)
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT run_id, user_query, created_at, status
            FROM runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cursor.fetchall()
        return [
            RunRecord(
                run_id=row["run_id"],
                user_query=row["user_query"],
                created_at=row["created_at"],
                status=row["status"],
            )
            for row in rows
        ]
    finally:
        conn.close()



