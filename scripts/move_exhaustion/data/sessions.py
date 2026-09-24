"""Session helpers — freeze legacy V2 01:00 ET day-open convention."""

from __future__ import annotations

import pandas as pd

from scripts.move_exhaustion.data.contracts import SESSION_END_ET, SESSION_START_ET


def assert_day_open_matches_first_price(df: pd.DataFrame) -> dict[str, object]:
    """Verify day_open_price equals first TY price of each et_date (V2 convention).

    Uses the earliest bucket in ``df`` per date. Callers should pass the full
    TBBO feature matrix (not the OOS-merged residual panel), because walk-forward
    predictions often start after the session open.
    """
    mismatches = []
    for et_date, day in df.groupby("et_date", sort=True):
        day = day.sort_values("bucket_ts")
        first = float(day["price"].iloc[0])
        opens = pd.to_numeric(day["day_open_price"], errors="coerce")
        if not bool((opens - first).abs().fillna(1.0).le(1e-9).all()):
            mismatches.append(str(et_date))
    return {
        "session_start_et": SESSION_START_ET,
        "session_end_et": SESSION_END_ET,
        "ok": len(mismatches) == 0,
        "n_mismatch_dates": len(mismatches),
        "mismatch_dates_sample": mismatches[:10],
        "audited_on": "full_feature_matrix_first_bar",
    }
