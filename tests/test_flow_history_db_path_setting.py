"""FLOW_HISTORY_DB_PATH points V1's store and every reader at one explicit database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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
