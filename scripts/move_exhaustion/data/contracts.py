"""Paths and constants for move-exhaustion research (read-only V2 inputs)."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_move_exhaustion"
CONFIG_DIR = PROJECT_ROOT / "configs" / "move_exhaustion"

# Read-only V2 research artifacts — never write here.
TBBO_MATRIX = PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo" / "tbbo_feature_matrix.csv"
WALKFORWARD_PREDS = (
    PROJECT_ROOT / "workspace" / "analysis" / "aggression_v2_tbbo_models" / "walkforward_predictions.csv"
)
DV01_PATH = PROJECT_ROOT / "config" / "aggression_dv01.json"

# Forbidden live paths (assert never written).
FORBIDDEN_LIVE_GLOBS = (
    "workspace/analysis/aggression_v1_live",
    "workspace/analysis/aggression_v12_live",
    "workspace/analysis/aggression_v2_live",
    "workspace/analysis/aggression_v21_live",
    "workspace/analysis/aggression_v22_live",
    "workspace/analysis/aggression_v3_live",
)

MIN_ROWS_PER_DAY = 500
MIN_POINTS_FOR_Z = 30
PRIOR_OBSERVATIONS = 60
EPS = 1e-12
RANDOM_SEED = 42
PRIMARY_SYMBOL = "TY"
V2_MODEL = "ridge"

# Legacy V2 session freeze.
SESSION_START_ET = "01:00"
SESSION_END_ET = "17:00"

TICK_SIZE = {
    "TY": 1.0 / 64.0,
    "FV": 1.0 / 128.0,
    "TU": 1.0 / 256.0,
    "US": 1.0 / 32.0,
}
