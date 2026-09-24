"""Mechanical temporal-causality assertions."""

from __future__ import annotations

import pandas as pd


class TemporalLeakageError(ValueError):
    """Raised when a feature consumes information after its decision time."""


def assert_causal(
    frame: pd.DataFrame,
    *,
    decision_ts_col: str = "decision_ts",
    max_input_ts_col: str = "max_input_ts",
) -> None:
    """Reject rows whose latest input timestamp follows the decision timestamp."""
    missing = {decision_ts_col, max_input_ts_col} - set(frame.columns)
    if missing:
        raise KeyError(f"Missing causality columns: {sorted(missing)}")
    decision = pd.to_datetime(frame[decision_ts_col], utc=True, errors="coerce")
    latest = pd.to_datetime(frame[max_input_ts_col], utc=True, errors="coerce")
    invalid = decision.isna() | latest.isna() | (latest > decision)
    if invalid.any():
        rows = frame.index[invalid].tolist()[:10]
        raise TemporalLeakageError(f"Temporal leakage/invalid timestamps at rows {rows}")
