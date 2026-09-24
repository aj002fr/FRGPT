from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v21 import app as app_module
from scripts.aggression_dashboard_v21.magnitude_context import (
    LABEL,
    LOOKBACK_BARS,
    RANGE_P70_TICKS,
    TICK_SIZE,
    VOL_P70,
    magnitude_context,
)


def _frame(prices: list[float]) -> pd.DataFrame:
    index = pd.date_range("2026-07-10T12:00:00Z", periods=len(prices), freq="1min")
    return pd.DataFrame({"price": prices}, index=index)


def test_empty_and_short_frames_are_not_ready() -> None:
    empty = magnitude_context(pd.DataFrame())
    short = magnitude_context(_frame([110.0] * (LOOKBACK_BARS - 1)))
    for result in (empty, short):
        assert result["state"] == "NORMAL"
        assert result["ready"] is False
        assert result["prior10_realized_vol"] is None
        assert result["prior10_range_ticks"] is None
        assert result["non_directional"] is True


def test_normal_state_below_both_thresholds() -> None:
    result = magnitude_context(_frame([110.0] * LOOKBACK_BARS))
    assert result["ready"] is True
    assert result["state"] == "NORMAL"
    assert result["prior10_realized_vol"] < VOL_P70
    assert result["prior10_range_ticks"] < RANGE_P70_TICKS
    assert result["reasons"] == []


def test_elevated_when_range_reaches_threshold() -> None:
    prices = np.linspace(110.0, 110.0 + RANGE_P70_TICKS * TICK_SIZE, LOOKBACK_BARS).tolist()
    result = magnitude_context(_frame(prices))
    assert result["state"] == "ELEVATED"
    assert result["prior10_range_ticks"] >= RANGE_P70_TICKS
    assert any("range" in reason for reason in result["reasons"])


def test_elevated_when_realized_vol_exceeds_threshold() -> None:
    prices = [110.0 + (0.03 if index % 2 else 0.0) for index in range(LOOKBACK_BARS)]
    result = magnitude_context(_frame(prices))
    assert result["state"] == "ELEVATED"
    assert result["prior10_realized_vol"] >= VOL_P70
    assert any("volatility" in reason for reason in result["reasons"])


def test_output_is_always_provisional_and_non_directional() -> None:
    result = magnitude_context(_frame([110.0] * LOOKBACK_BARS))
    assert result["label"] == LABEL == "PROVISIONAL / NON-DIRECTIONAL"
    assert result["non_directional"] is True
    assert result["validated_context"]["directional_pnl_validated"] is False
    assert result["validated_context"]["big_move_rate_elevated"] > result["validated_context"]["big_move_rate_baseline"]
    assert result["validated_context"]["resolution_rate_elevated"] > result["validated_context"]["resolution_rate_baseline"]


def test_v21_template_and_live_api_include_magnitude_context(monkeypatch) -> None:
    model = _frame([110.0] * LOOKBACK_BARS)
    model["fair_value"] = model["price"]
    model["residual"] = 0.0
    model["residual_z"] = 0.0
    monkeypatch.setattr(app_module, "_ensure_live", lambda: None)
    monkeypatch.setattr(app_module, "_evaluate_once", lambda: (model, model, {"ok": True}, {}))
    monkeypatch.setattr(app_module.LIVE_BUFFER, "status", lambda: {"connected": False})
    client = app_module.app.test_client()
    homepage = client.get("/")
    assert homepage.status_code == 200
    assert b"Magnitude confidence" in homepage.data
    response = client.get("/api/live_signal")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["magnitude_context"]["state"] == "NORMAL"
    assert payload["magnitude_context"]["non_directional"] is True
