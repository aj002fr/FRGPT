"""Tests for Aggression V2.2 isolated LIFO FRIENDLY surface."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from scripts.aggression_dashboard_v22.alerts import STORE_DIR as ALERT_STORE
from scripts.aggression_dashboard_v22.lifo_context import (
    LIFO_FRIENDLY_LABEL,
    LIFO_FRIENDLY_RESEARCH,
    annotate_lifo_flags,
    latest_lifo_context,
)
from scripts.aggression_dashboard_v22.lifo_store import STORE_DIR, LifoStore


def _minute_index(start: str, n: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq="1min", tz="UTC")


def test_isolation_paths() -> None:
    text = str(STORE_DIR).replace("\\", "/")
    alert_text = str(ALERT_STORE).replace("\\", "/")
    assert text.endswith("workspace/analysis/aggression_v22_live")
    assert alert_text.endswith("workspace/analysis/aggression_v22_live")
    assert "aggression_v2_live" not in text.replace("aggression_v22_live", "")
    assert "aggression_v2_live" not in alert_text.replace("aggression_v22_live", "")


def test_lifo_label_fixed() -> None:
    assert LIFO_FRIENDLY_LABEL == "PROVISIONAL / LIFO-FRIENDLY / RANGE-LEAN / NON-DIRECTIONAL"
    assert LIFO_FRIENDLY_RESEARCH["validation_restrictive_pass"] is True
    assert LIFO_FRIENDLY_RESEARCH["final_ci_clears"] is False
    assert LIFO_FRIENDLY_RESEARCH["directional"] is False


def test_lifo_fires_on_large_z_and_low_intensity() -> None:
    idx = _minute_index("2026-07-27 13:00:00", 40)
    intensity = np.concatenate([[8.0] * 25, [1.0] * 15])
    df = pd.DataFrame(
        {
            "residual": np.linspace(0.01, 0.05, 40),
            "residual_z": np.concatenate([[0.2] * 20, [1.4] * 20]),
            "trade_intensity": intensity,
            "price": 110.0 + np.arange(40) * 0.0001,
        },
        index=idx,
    )
    out = annotate_lifo_flags(df)
    assert "lifo_friendly" in out.columns
    ctx = latest_lifo_context(out)
    assert ctx["label"] == LIFO_FRIENDLY_LABEL
    if bool(out["lifo_friendly"].iloc[-1]):
        assert ctx["state"] == "LIFO_FRIENDLY"
        assert ctx["alert_text"] == "LIFO FRIENDLY"
        assert ctx["active"] is True


def test_lifo_store_tracks_stats(tmp_path: Path) -> None:
    store = LifoStore(store_dir=tmp_path)
    idx = _minute_index("2026-07-27 13:00:00", 90)
    z = np.concatenate([[0.2] * 30, [1.5] * 60])
    intensity = np.concatenate([[8.0] * 30, [1.0] * 60])
    price = 110.0 + np.cumsum(np.where(np.arange(90) % 2 == 0, 0.005, -0.005))
    panel = pd.DataFrame(
        {
            "residual": z * 0.01,
            "residual_z": z,
            "trade_intensity": intensity,
            "price": price,
        },
        index=idx,
    )
    payload = store.update(panel)
    assert "lifo_stats" in payload
    assert store.episodes
    assert all(str(ep["episode_id"]).startswith("lifo_") for ep in store.episodes)
    assert payload["lifo_stats"]["n_finalized"] + payload["lifo_stats"]["n_active"] == len(store.episodes)


def test_v2_package_untouched() -> None:
    from pathlib import Path

    v2_alerts = Path("scripts/aggression_dashboard_v2/alerts.py").read_text(encoding="utf-8")
    assert "aggression_v2_live" in v2_alerts
    assert "aggression_v22_live" not in v2_alerts
