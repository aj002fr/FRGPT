"""Transactional one-shot state, receipts, and family holdout retirement."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from scripts.autoresearch.schemas import (
    Approval,
    PreprocessingIdentity,
    RunIdentity,
    require_hash,
    require_id,
)

from .approval import approval_hash, verify_approval
from .data_access import contained_path
from .preregister import atomic_write, digest_file, digest_object

_IDENTITY_COLUMNS = (
    "experiment,hypothesis_family,split,candidate_hash,state_hash,"
    "evaluator_hash,data_hash,spec_hash"
)
_PREPROCESSING_COLUMNS = (
    "experiment,hypothesis_family,split,candidate_hash,spec_hash,"
    "evaluator_hash,data_hash"
)
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Reservation:
    run_id: int
    attempt: int
    status: str


class StateStore:
    def __init__(
        self,
        path: str | Path,
        *,
        output_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
    ):
        self.path = Path(path)
        if "_live" in str(self.path.resolve(strict=False)).lower():
            raise PermissionError("state store cannot use a live path")
        self.output_roots = tuple(output_roots or ())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(f"""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id INTEGER PRIMARY KEY,
                    experiment TEXT NOT NULL,
                    hypothesis_family TEXT NOT NULL,
                    split TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    state_hash TEXT NOT NULL,
                    evaluator_hash TEXT NOT NULL,
                    data_hash TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('RESERVED','COMPLETED','FAILED_NO_RESULT','CLOSED_INVALID')),
                    attempt INTEGER NOT NULL DEFAULT 1,
                    stats_emitted INTEGER NOT NULL DEFAULT 0 CHECK(stats_emitted IN (0,1)),
                    output_hash TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE({_IDENTITY_COLUMNS})
                );
                CREATE TABLE IF NOT EXISTS preprocessing_jobs (
                    job_id INTEGER PRIMARY KEY,
                    experiment TEXT NOT NULL,
                    hypothesis_family TEXT NOT NULL,
                    split TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    spec_hash TEXT NOT NULL,
                    evaluator_hash TEXT NOT NULL,
                    data_hash TEXT NOT NULL,
                    state_hash TEXT,
                    run_id INTEGER REFERENCES runs(run_id),
                    status TEXT NOT NULL CHECK(status IN
                        ('RESERVED','SEALED','FAILED_NO_RESULT','CLOSED_INVALID')),
                    attempt INTEGER NOT NULL DEFAULT 1,
                    error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE({_PREPROCESSING_COLUMNS})
                );
                CREATE TABLE IF NOT EXISTS family_cohorts (
                    hypothesis_family TEXT PRIMARY KEY,
                    experiment TEXT NOT NULL,
                    cutoff TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    approval_hash TEXT NOT NULL,
                    retired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    receipt_id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL REFERENCES runs(run_id),
                    previous_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS alpha_families (
                    family_id TEXT PRIMARY KEY,
                    allocated_alpha REAL NOT NULL CHECK(
                        allocated_alpha > 0 AND allocated_alpha <= 0.1
                    ),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS alpha_experiment_links (
                    experiment TEXT PRIMARY KEY,
                    family_id TEXT NOT NULL REFERENCES alpha_families(family_id),
                    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS confirmation_approvals (
                    hypothesis_family TEXT NOT NULL,
                    experiment TEXT NOT NULL,
                    approval_hash TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(hypothesis_family, experiment)
                );
                CREATE TABLE IF NOT EXISTS exposure_ledger (
                    exposure_id INTEGER PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    target TEXT NOT NULL,
                    hypothesis_family TEXT NOT NULL,
                    experiment TEXT NOT NULL,
                    split TEXT NOT NULL,
                    run_id INTEGER REFERENCES runs(run_id),
                    exposed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(trading_date,target,hypothesis_family)
                );
            """)

    @staticmethod
    def _where() -> str:
        return " AND ".join(f"{name}=?" for name in _IDENTITY_COLUMNS.split(","))

    def reserve(self, identity: RunIdentity) -> Reservation:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT * FROM runs WHERE {self._where()}", identity.values()
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    f"INSERT INTO runs ({_IDENTITY_COLUMNS},status) "
                    "VALUES (?,?,?,?,?,?,?,?,'RESERVED')", identity.values(),
                )
                connection.commit()
                return Reservation(cursor.lastrowid, 1, "RESERVED")
            if row["status"] == "FAILED_NO_RESULT" and not row["stats_emitted"]:
                if row["attempt"] >= MAX_ATTEMPTS:
                    connection.execute(
                        "UPDATE runs SET status='CLOSED_INVALID',"
                        "updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                        (row["run_id"],),
                    )
                    connection.commit()
                    raise RuntimeError("five-attempt cap reached; run closed invalid")
                connection.execute(
                    "UPDATE runs SET status='RESERVED', attempt=attempt+1, error=NULL, "
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=?", (row["run_id"],),
                )
                connection.commit()
                return Reservation(row["run_id"], row["attempt"] + 1, "RESERVED")
            connection.rollback()
            raise RuntimeError(f"run identity already {row['status']}")

    def reserve_preprocessing(self, identity: PreprocessingIdentity) -> Reservation:
        """Reserve code/spec/data identity before any discovery fit occurs."""
        where = " AND ".join(
            f"{name}=?" for name in _PREPROCESSING_COLUMNS.split(",")
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT * FROM preprocessing_jobs WHERE {where}", identity.values(),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    f"INSERT INTO preprocessing_jobs ({_PREPROCESSING_COLUMNS},status)"
                    " VALUES (?,?,?,?,?,?,?,'RESERVED')", identity.values(),
                )
                connection.commit()
                return Reservation(cursor.lastrowid, 1, "RESERVED")
            if row["status"] == "SEALED" and row["run_id"] is not None:
                run = connection.execute(
                    "SELECT status,stats_emitted,attempt FROM runs WHERE run_id=?",
                    (row["run_id"],),
                ).fetchone()
                if run is not None and run["status"] == "RESERVED" and not run["stats_emitted"]:
                    if max(row["attempt"], run["attempt"]) >= MAX_ATTEMPTS:
                        connection.execute(
                            "UPDATE preprocessing_jobs SET status='CLOSED_INVALID',"
                            "updated_at=CURRENT_TIMESTAMP WHERE job_id=?", (row["job_id"],),
                        )
                        connection.execute(
                            "UPDATE runs SET status='CLOSED_INVALID',"
                            "updated_at=CURRENT_TIMESTAMP WHERE run_id=?", (row["run_id"],),
                        )
                        connection.commit()
                        raise RuntimeError("five-attempt cap reached; sealed run closed invalid")
                    attempt = max(row["attempt"], run["attempt"]) + 1
                    connection.execute(
                        "UPDATE preprocessing_jobs SET attempt=?,updated_at=CURRENT_TIMESTAMP "
                        "WHERE job_id=?", (attempt, row["job_id"]),
                    )
                    connection.execute(
                        "UPDATE runs SET attempt=?,updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                        (attempt, row["run_id"]),
                    )
                    connection.commit()
                    return Reservation(row["job_id"], attempt, "RESUME_SEALED")
            if row["status"] == "FAILED_NO_RESULT":
                if row["attempt"] >= MAX_ATTEMPTS:
                    connection.execute(
                        "UPDATE preprocessing_jobs SET status='CLOSED_INVALID',"
                        "updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                        (row["job_id"],),
                    )
                    connection.commit()
                    raise RuntimeError("five-attempt cap reached; preprocessing closed invalid")
                connection.execute(
                    "UPDATE preprocessing_jobs SET status='RESERVED',"
                    "attempt=attempt+1,error=NULL,updated_at=CURRENT_TIMESTAMP "
                    "WHERE job_id=?", (row["job_id"],),
                )
                connection.commit()
                return Reservation(row["job_id"], row["attempt"] + 1, "RESERVED")
            connection.rollback()
            raise RuntimeError(f"preprocessing identity already {row['status']}")

    def fail_preprocessing_no_result(self, job_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,attempt FROM preprocessing_jobs WHERE job_id=?", (job_id,),
            ).fetchone()
            if row is None or row["status"] != "RESERVED":
                connection.rollback()
                raise RuntimeError("only reserved preprocessing can fail without result")
            connection.execute(
                "UPDATE preprocessing_jobs SET status=?,error=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                (
                    "CLOSED_INVALID" if row["attempt"] >= MAX_ATTEMPTS
                    else "FAILED_NO_RESULT",
                    error[:2000], job_id,
                ),
            )
            connection.commit()

    def seal_preprocessing(self, job_id: int, state_hash: str) -> Reservation:
        """Atomically bind emitted state to the immutable scoring run identity."""
        require_hash(state_hash, "state_hash")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM preprocessing_jobs WHERE job_id=?", (job_id,),
            ).fetchone()
            if job is not None and job["status"] == "SEALED":
                run = connection.execute(
                    "SELECT status,stats_emitted FROM runs WHERE run_id=?", (job["run_id"],),
                ).fetchone()
                if (
                    job["state_hash"] == state_hash and run is not None
                    and run["status"] == "RESERVED" and not run["stats_emitted"]
                ):
                    connection.commit()
                    return Reservation(job["run_id"], job["attempt"], "RESUME_SEALED")
                connection.rollback()
                raise RuntimeError("sealed preprocessing state cannot be changed")
            if job is None or job["status"] != "RESERVED":
                connection.rollback()
                raise RuntimeError("only reserved preprocessing can be sealed")
            identity = RunIdentity(
                job["experiment"], job["hypothesis_family"], job["split"],
                job["candidate_hash"], state_hash, job["evaluator_hash"],
                job["data_hash"], job["spec_hash"],
            )
            try:
                cursor = connection.execute(
                    f"INSERT INTO runs ({_IDENTITY_COLUMNS},status)"
                    " VALUES (?,?,?,?,?,?,?,?,'RESERVED')", identity.values(),
                )
            except sqlite3.IntegrityError as exc:
                existing = connection.execute(
                    f"SELECT * FROM runs WHERE {self._where()}", identity.values()
                ).fetchone()
                if (
                    existing is None or existing["status"] != "FAILED_NO_RESULT"
                    or existing["stats_emitted"] or existing["attempt"] >= MAX_ATTEMPTS
                ):
                    connection.rollback()
                    raise RuntimeError("sealed run identity already exists") from exc
                connection.execute(
                    "UPDATE runs SET status='RESERVED',attempt=attempt+1,error=NULL,"
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                    (existing["run_id"],),
                )
                run_id = existing["run_id"]
            else:
                run_id = cursor.lastrowid
            connection.execute(
                "UPDATE preprocessing_jobs SET status='SEALED',state_hash=?,run_id=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                (state_hash, run_id, job_id),
            )
            connection.commit()
            return Reservation(run_id, job["attempt"], "RESERVED")

    def complete(self, run_id: int, output_hash: str, receipt: dict[str, Any]) -> str:
        require_hash(output_hash, "output_hash")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != "RESERVED":
                connection.rollback()
                raise RuntimeError("only a reserved run can complete")
            previous = connection.execute(
                "SELECT receipt_hash FROM receipts ORDER BY receipt_id DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous["receipt_hash"] if previous else "0" * 64
            payload = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
            receipt_hash = digest_object({
                "run_id": run_id, "previous_hash": previous_hash, "payload": payload,
            })
            connection.execute(
                "INSERT INTO receipts(run_id,previous_hash,payload,receipt_hash) "
                "VALUES (?,?,?,?)", (run_id, previous_hash, payload, receipt_hash),
            )
            connection.execute(
                "UPDATE runs SET status='COMPLETED',stats_emitted=1,output_hash=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=?", (output_hash, run_id),
            )
            connection.commit()
            return receipt_hash

    def fail_no_result(self, run_id: int, error: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status,stats_emitted,attempt FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != "RESERVED" or row["stats_emitted"]:
                connection.rollback()
                raise RuntimeError("failure retry requires reserved run with no statistics")
            connection.execute(
                "UPDATE runs SET status=?,error=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                (
                    "CLOSED_INVALID" if row["attempt"] >= MAX_ATTEMPTS
                    else "FAILED_NO_RESULT",
                    error[:2000], run_id,
                ),
            )
            connection.commit()

    def reopen_preprocessing_after_run_failure(
        self, run_id: int, error: str,
    ) -> None:
        """Return a sealed preprocessing job to retryable state before stats exist."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status,stats_emitted,attempt FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            job = connection.execute(
                "SELECT job_id,status FROM preprocessing_jobs WHERE run_id=?", (run_id,)
            ).fetchone()
            if (
                run is None or job is None or run["status"] != "RESERVED"
                or run["stats_emitted"] or job["status"] != "SEALED"
            ):
                connection.rollback()
                raise RuntimeError("only an unreported sealed run can reopen preprocessing")
            status = "CLOSED_INVALID" if run["attempt"] >= MAX_ATTEMPTS else "FAILED_NO_RESULT"
            connection.execute(
                "UPDATE runs SET status=?,error=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE run_id=?", (status, error[:2000], run_id),
            )
            connection.execute(
                "UPDATE preprocessing_jobs SET status=?,error=?,"
                "updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                (status, error[:2000], job["job_id"]),
            )
            connection.commit()

    def publish_and_complete(
        self, run_id: int, path: str | Path, result: Any, receipt: dict[str, Any]
    ) -> str:
        if not self.output_roots:
            raise PermissionError("atomic publication requires explicit output roots")
        destination = contained_path(path, self.output_roots, must_exist=False)
        content = json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8") + b"\n"
        staging = destination.with_name(f".{destination.name}.run-{run_id}.pending")
        output_hash = atomic_write(staging, content)
        require_hash(output_hash, "output_hash")
        if destination.exists():
            run = self.get_run(run_id)
            recoverable = (
                run["status"] == "RESERVED"
                and not run["stats_emitted"]
                and digest_file(destination) == output_hash
            )
            if not recoverable:
                staging.unlink(missing_ok=True)
                raise FileExistsError(f"immutable result already exists: {destination}")
            destination.unlink()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row["status"] != "RESERVED":
                connection.rollback()
                staging.unlink(missing_ok=True)
                raise RuntimeError("only a reserved run can publish")
            previous = connection.execute(
                "SELECT receipt_hash FROM receipts ORDER BY receipt_id DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous["receipt_hash"] if previous else "0" * 64
            payload = json.dumps(
                receipt, sort_keys=True, separators=(",", ":"), allow_nan=False,
            )
            receipt_hash = digest_object({
                "run_id": run_id, "previous_hash": previous_hash, "payload": payload,
            })
            try:
                os.replace(staging, destination)
                connection.execute(
                    "INSERT INTO receipts(run_id,previous_hash,payload,receipt_hash)"
                    " VALUES (?,?,?,?)",
                    (run_id, previous_hash, payload, receipt_hash),
                )
                connection.execute(
                    "UPDATE runs SET status='COMPLETED',stats_emitted=1,output_hash=?,"
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                    (output_hash, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                destination.unlink(missing_ok=True)
                staging.unlink(missing_ok=True)
                raise
        return receipt_hash

    def retire_confirmation_cohort(
        self,
        identity: RunIdentity,
        *,
        cutoff: str,
        manifest_hash: str,
        approval_hash: str,
    ) -> None:
        if identity.split != "confirmation":
            raise ValueError("only confirmation can retire a cohort")
        require_hash(manifest_hash, "manifest_hash")
        require_hash(approval_hash, "approval_hash")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM family_cohorts WHERE hypothesis_family=?",
                (identity.hypothesis_family,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                raise RuntimeError("confirmation cohort already exposed for family")
            connection.execute(
                "INSERT INTO family_cohorts"
                "(hypothesis_family,experiment,cutoff,manifest_hash,approval_hash)"
                " VALUES (?,?,?,?,?)",
                (identity.hypothesis_family, identity.experiment, cutoff,
                 manifest_hash, approval_hash),
            )
            connection.commit()

    def record_exposures(
        self,
        *,
        trading_dates: list[str] | tuple[str, ...],
        target: str,
        identity: RunIdentity,
        run_id: int | None = None,
    ) -> None:
        """Append date/target/family exposures; quarantine remains non-evidentiary."""
        if not target.strip() or not trading_dates:
            raise ValueError("target and trading dates are required")
        rows = [
            (
                date, target, identity.hypothesis_family, identity.experiment,
                identity.split, run_id,
            )
            for date in trading_dates
        ]
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.executemany(
                    "INSERT INTO exposure_ledger"
                    "(trading_date,target,hypothesis_family,experiment,split,run_id)"
                    " VALUES (?,?,?,?,?,?)", rows,
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("date/target/family cohort was already exposed") from exc

    def reserve_historical_with_exposure(
        self,
        identity: RunIdentity,
        *,
        target: str,
        trading_dates: list[str] | tuple[str, ...],
    ) -> Reservation:
        """Atomically reserve validation/quarantine before outcomes can be loaded."""
        if identity.split not in {"validation", "quarantine"}:
            raise ValueError("historical reservation is validation/quarantine only")
        dates = tuple(trading_dates)
        if not target.strip() or not dates or len(dates) != len(set(dates)):
            raise ValueError("target and unique trading dates are required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT * FROM runs WHERE {self._where()}", identity.values(),
            ).fetchone()
            if row is None:
                cursor = connection.execute(
                    f"INSERT INTO runs ({_IDENTITY_COLUMNS},status)"
                    " VALUES (?,?,?,?,?,?,?,?,'RESERVED')", identity.values(),
                )
                run_id, attempt, status = cursor.lastrowid, 1, "RESERVED"
                try:
                    connection.executemany(
                        "INSERT INTO exposure_ledger"
                        "(trading_date,target,hypothesis_family,experiment,split,run_id)"
                        " VALUES (?,?,?,?,?,?)",
                        [
                            (
                                day, target, identity.hypothesis_family,
                                identity.experiment, identity.split, run_id,
                            )
                            for day in dates
                        ],
                    )
                except sqlite3.IntegrityError as exc:
                    connection.rollback()
                    raise RuntimeError(
                        "historical cohort includes previously exposed dates"
                    ) from exc
            elif row["status"] == "FAILED_NO_RESULT" and not row["stats_emitted"]:
                if row["attempt"] >= MAX_ATTEMPTS:
                    connection.execute(
                        "UPDATE runs SET status='CLOSED_INVALID',"
                        "updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                        (row["run_id"],),
                    )
                    connection.commit()
                    raise RuntimeError("five-attempt cap reached; run closed invalid")
                exposed = connection.execute(
                    "SELECT trading_date,target,split FROM exposure_ledger "
                    "WHERE run_id=? ORDER BY trading_date", (row["run_id"],),
                ).fetchall()
                expected = sorted(dates)
                if (
                    [item["trading_date"] for item in exposed] != expected
                    or any(item["target"] != target or item["split"] != identity.split
                           for item in exposed)
                ):
                    connection.rollback()
                    raise RuntimeError("reserved historical exposure binding changed")
                attempt = row["attempt"] + 1
                connection.execute(
                    "UPDATE runs SET status='RESERVED',attempt=?,error=NULL,"
                    "updated_at=CURRENT_TIMESTAMP WHERE run_id=?",
                    (attempt, row["run_id"]),
                )
                run_id, status = row["run_id"], "RESUME_RESERVED"
            else:
                connection.rollback()
                raise RuntimeError(f"run identity already {row['status']}")
            connection.commit()
            return Reservation(run_id, attempt, status)

    def completed_validation(self, identity: RunIdentity) -> dict[str, Any]:
        """Return the exactly matching completed validation run."""
        if identity.split != "validation":
            raise ValueError("validation identity required")
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM runs WHERE {self._where()}", identity.values(),
            ).fetchone()
        if row is None or row["status"] != "COMPLETED" or not row["stats_emitted"]:
            raise PermissionError("quarantine requires completed matching validation")
        return dict(row)

    def get_exposures(
        self, hypothesis_family: str, target: str
    ) -> list[dict[str, Any]]:
        require_id(hypothesis_family, "hypothesis_family")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT trading_date,target,hypothesis_family,experiment,split,run_id,"
                "exposed_at FROM exposure_ledger "
                "WHERE hypothesis_family=? AND target=? ORDER BY trading_date",
                (hypothesis_family, target),
            ).fetchall()
        return [dict(row) for row in rows]

    def begin_confirmation(
        self,
        identity: RunIdentity,
        approval: Approval,
        *,
        primary_claim_hash: str,
        confirmation_cutoff: str,
        date_manifest_hash: str,
        target: str,
        trading_dates: list[str] | tuple[str, ...],
    ) -> Reservation:
        """Verify, retire, reserve, then ledger exposure in one transaction."""
        if identity.split != "confirmation" or not trading_dates or not target.strip():
            raise ValueError("confirmation identity, target, and dates are required")
        verify_approval(
            approval, identity, primary_claim_hash=primary_claim_hash,
            confirmation_cutoff=confirmation_cutoff,
            date_manifest_hash=date_manifest_hash,
        )
        bound_hash = approval_hash(approval)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            persisted = connection.execute(
                "SELECT approval_hash FROM confirmation_approvals "
                "WHERE hypothesis_family=? AND experiment=?",
                (identity.hypothesis_family, identity.experiment),
            ).fetchone()
            if persisted is None or persisted["approval_hash"] != bound_hash:
                connection.rollback()
                raise PermissionError("confirmation approval is not persisted and spec-bound")
            if connection.execute(
                "SELECT 1 FROM family_cohorts WHERE hypothesis_family=?",
                (identity.hypothesis_family,),
            ).fetchone():
                connection.rollback()
                raise RuntimeError("confirmation cohort already exposed for family")
            connection.execute(
                "INSERT INTO family_cohorts"
                "(hypothesis_family,experiment,cutoff,manifest_hash,approval_hash)"
                " VALUES (?,?,?,?,?)",
                (
                    identity.hypothesis_family, identity.experiment,
                    confirmation_cutoff, date_manifest_hash, bound_hash,
                ),
            )
            try:
                cursor = connection.execute(
                    f"INSERT INTO runs ({_IDENTITY_COLUMNS},status)"
                    " VALUES (?,?,?,?,?,?,?,?,'RESERVED')", identity.values(),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RuntimeError("confirmation run identity already reserved") from exc
            run_id = cursor.lastrowid
            try:
                connection.executemany(
                    "INSERT INTO exposure_ledger"
                    "(trading_date,target,hypothesis_family,experiment,split,run_id)"
                    " VALUES (?,?,?,?,?,?)",
                    [
                        (
                            date, target, identity.hypothesis_family,
                            identity.experiment, identity.split, run_id,
                        )
                        for date in trading_dates
                    ],
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RuntimeError("confirmation cohort includes exposed dates") from exc
            connection.commit()
            return Reservation(run_id, 1, "RESERVED")

    def register_alpha_family(
        self, family_id: str, allocated_alpha: float, experiment: str
    ) -> None:
        require_id(family_id, "alpha family_id")
        require_id(experiment, "experiment")
        if not isinstance(allocated_alpha, (int, float)) or not 0 < allocated_alpha <= 0.1:
            raise ValueError("allocated alpha must be in (0, 0.1]")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM alpha_families WHERE family_id=?", (family_id,)
            ).fetchone():
                connection.rollback()
                raise RuntimeError("alpha family already exists; experiment IDs do not reset it")
            connection.execute(
                "INSERT INTO alpha_families(family_id,allocated_alpha) VALUES (?,?)",
                (family_id, float(allocated_alpha)),
            )
            try:
                connection.execute(
                    "INSERT INTO alpha_experiment_links(experiment,family_id) VALUES (?,?)",
                    (experiment, family_id),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RuntimeError("experiment already linked to an alpha family") from exc
            connection.commit()

    def link_alpha_experiment(self, family_id: str, experiment: str) -> None:
        require_id(family_id, "alpha family_id")
        require_id(experiment, "experiment")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute(
                "SELECT 1 FROM alpha_families WHERE family_id=?", (family_id,)
            ).fetchone():
                connection.rollback()
                raise KeyError(family_id)
            try:
                connection.execute(
                    "INSERT INTO alpha_experiment_links(experiment,family_id) VALUES (?,?)",
                    (experiment, family_id),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RuntimeError("experiment already linked to an alpha family") from exc
            connection.commit()

    def get_alpha_family(self, family_id: str) -> dict[str, Any]:
        require_id(family_id, "alpha family_id")
        with self._connect() as connection:
            family = connection.execute(
                "SELECT family_id,allocated_alpha,created_at FROM alpha_families "
                "WHERE family_id=?", (family_id,),
            ).fetchone()
            if family is None:
                raise KeyError(family_id)
            experiments = connection.execute(
                "SELECT experiment FROM alpha_experiment_links WHERE family_id=? "
                "ORDER BY experiment", (family_id,),
            ).fetchall()
        return {
            "family_id": family["family_id"],
            "allocated_alpha": family["allocated_alpha"],
            "experiments": [row["experiment"] for row in experiments],
            "created_at": family["created_at"],
        }

    def persist_approval(self, approval: Approval) -> str:
        payload_value = asdict(approval)
        payload = json.dumps(
            payload_value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        bound_hash = digest_object(payload_value)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT approval_hash FROM confirmation_approvals "
                "WHERE hypothesis_family=? AND experiment=?",
                (approval.hypothesis_family, approval.experiment),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                raise RuntimeError("manual approval already recorded for family/experiment")
            connection.execute(
                "INSERT INTO confirmation_approvals"
                "(hypothesis_family,experiment,approval_hash,payload) VALUES (?,?,?,?)",
                (approval.hypothesis_family, approval.experiment, bound_hash, payload),
            )
            connection.commit()
        return bound_hash

    def get_approval(self, hypothesis_family: str, experiment: str) -> Approval:
        require_id(hypothesis_family, "hypothesis_family")
        require_id(experiment, "experiment")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload,approval_hash FROM confirmation_approvals "
                "WHERE hypothesis_family=? AND experiment=?",
                (hypothesis_family, experiment),
            ).fetchone()
        if row is None:
            raise KeyError((hypothesis_family, experiment))
        payload = json.loads(row["payload"])
        if digest_object(payload) != row["approval_hash"]:
            raise RuntimeError("persisted approval hash mismatch")
        return Approval(**payload)

    def get_run(self, run_id: int) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

