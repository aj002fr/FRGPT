"""Fixture lock for V2.7 × V1 zone scoring. Analysis-only; does not touch 5063/5050."""

from __future__ import annotations

from scripts.aggression_v27_v1_zone_score import pair_trades, score_zones, window_stability


def _alert(kind: str, banner: str, window: list[str], ticks=None, exit_reason=None, held=5.0) -> dict:
    return {
        "kind": kind,
        "alert_ts_utc": "2026-09-01T14:00:00+00:00",
        "direction": "SHORT_RICH",
        "exit_reason": exit_reason,
        "dir_pnl_trade_ticks": ticks,
        "minutes_held": held if kind == "EXIT" else None,
        "v1_banner_at_alert": banner,
        "v1_window": window,
    }


def test_single_vs_mixed_and_n3_nomination() -> None:
    quiet = ["QUIET"] * 11
    mixed = ["QUIET"] * 5 + ["BALANCED"] + ["QUIET"] * 5
    payload = {
        "alerts": [
            _alert("ENTRY", "QUIET", quiet),
            _alert("EXIT", "QUIET", quiet, ticks=2.0, exit_reason="FLIP"),
            _alert("ENTRY", "QUIET", quiet),
            _alert("EXIT", "QUIET", quiet, ticks=1.0, exit_reason="Z_RESET"),
            _alert("ENTRY", "QUIET", mixed),
            _alert("EXIT", "BALANCED", mixed, ticks=-4.0, exit_reason="FLIP"),
        ]
    }
    trades = pair_trades(payload)
    assert [t["S"] for t in trades] == ["SINGLE", "SINGLE", "MIXED"]
    assert [t["E"] for t in trades] == ["QUIET", "QUIET", "QUIET"]
    assert window_stability(quiet) == "SINGLE"
    assert window_stability(mixed) == "MIXED"

    summary = score_zones(trades)
    quiet_all = summary["E"]["QUIET"]
    assert quiet_all["n_closed"] == 3
    assert quiet_all["ticks"] == -1.0
    assert quiet_all["nominated"] is False  # ticks not > 0

    single = summary["ExS"]["QUIET_SINGLE"]
    assert single["n_closed"] == 2
    assert single["ticks"] == 3.0
    assert single["hit_rate"] == 1.0
    assert single["nominated"] is False  # n_closed < 3

    mixed_cell = summary["ExS"]["QUIET_MIXED"]
    assert mixed_cell["n_closed"] == 1
    assert mixed_cell["nominated"] is False
    assert summary["nominated"] == []
    assert summary["ship_gate"] is False


def test_n3_positive_cell_is_nominated() -> None:
    quiet = ["QUIET"] * 11
    payload = {
        "alerts": [
            _alert("ENTRY", "QUIET", quiet),
            _alert("EXIT", "QUIET", quiet, ticks=1.0, exit_reason="FLIP"),
            _alert("ENTRY", "QUIET", quiet),
            _alert("EXIT", "QUIET", quiet, ticks=2.0, exit_reason="FLIP"),
            _alert("ENTRY", "QUIET", quiet),
            _alert("EXIT", "QUIET", quiet, ticks=-0.5, exit_reason="Z_RESET"),
        ]
    }
    summary = score_zones(pair_trades(payload))
    assert summary["ExS"]["QUIET_SINGLE"]["nominated"] is True
    assert summary["E"]["QUIET"]["nominated"] is True
    assert [row["cell"] for row in summary["nominated"]] == ["QUIET", "QUIET_SINGLE"]
