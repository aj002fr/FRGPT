"""FLOW_HISTORY_DB_PATH points V1's store and every reader at one explicit database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _paths(env_value: str | None) -> list[str]:
    env = dict(os.environ)
    env.pop("FLOW_HISTORY_DB_PATH", None)
    if env_value is not None:
        env["FLOW_HISTORY_DB_PATH"] = env_value
    code = (
        "import scripts.aggression_dashboard.flow_history as fh, scripts.aggression_flow_history_dash.cache as c, "
        "scripts.supply_weeks_live.flow as f, scripts.supply_weeks_live.compare as k; "
        "print(fh.DB_PATH); print(c.DB_PATH); print(f.DB_PATH); print(k.DB_PATH)"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()


def test_default_path_is_the_repo_workspace() -> None:
    paths = _paths(None)
    assert len(set(paths)) == 1
    assert paths[0].endswith(str(Path("workspace/analysis/aggression_v1_live/flow_history.sqlite")))


def test_env_override_reaches_all_readers(tmp_path: Path) -> None:
    target = str(tmp_path / "local" / "flow_history.sqlite")
    assert _paths(target) == [target] * 4


def test_legacy_backfill_refuses_the_canonical_owned_db(tmp_path: Path) -> None:
    tape = tmp_path / "flow_tape.sqlite"
    tape.write_bytes(b"")
    hist = tmp_path / "flow_history.sqlite"
    env = {**os.environ, "FLOW_TAPE_DB_PATH": str(tape), "FLOW_HISTORY_DB_PATH": str(hist)}
    code = (
        "import sys; from scripts.aggression_flow_history_dash.backfill_accessapi import guard_canonical_owner as g\n"
        "try:\n    g(None)\nexcept SystemExit as e:\n    print('refused', 'canonical flow writer' in str(e))\n"
        f"g(r'{tmp_path / 'audit.sqlite'}'); print('audit ok')"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["refused", "True", "audit", "ok"]


def test_reader_is_read_only_without_ddl_and_reports_provisional(tmp_path: Path) -> None:
    import sqlite3

    from scripts.aggression_dashboard import flow_history as fh
    from scripts.aggression_flow_history_dash.cache import feed_for

    db = tmp_path / "flow_history.sqlite"
    assert fh.read_connection(db) is None and not db.exists()
    with sqlite3.connect(db) as conn:
        conn.executescript(fh._SCHEMA)
        conn.execute(
            "INSERT INTO v1_aggress_flow_1m VALUES ('2026-09-24T17:05:00+00:00', '2026-09-24 13:05:00 EDT', 'TY', "
            "1, 0, 85, 0, 1, 85, 1, 105.0, 'databento_provisional')"
        )
    reader = fh.read_connection(db)
    with pytest.raises(sqlite3.OperationalError):
        reader.execute("DELETE FROM v1_aggress_flow_1m")
    assert fh.latest_bar("TY", db_path=db) == ("2026-09-24T17:05:00+00:00", "databento_provisional")
    assert feed_for("TY", db_path=db)["provisional"] is True
    assert len(fh.query_flow_history("TY", db_path=db)) == 1
