from datetime import date

import numpy as np
import pandas as pd
import pytest

from scripts import aggression_v3_roll_research as rr


def _weekdays(first, last):
    return [d.date() for d in pd.bdate_range(first, last)]


def test_roll_contracts_quarterly_cycle_and_year_wrap():
    assert rr.roll_contracts("ZN", 2026, 2) == ("ZNH6", "ZNM6")
    assert rr.roll_contracts("ZB", 2026, 5) == ("ZBM6", "ZBU6")
    assert rr.roll_contracts("ZT", 2026, 8) == ("ZTU6", "ZTZ6")
    assert rr.roll_contracts("ZF", 2019, 11) == ("ZFZ9", "ZFH0")
    with pytest.raises(ValueError):
        rr.roll_contracts("ZN", 2026, 3)


def test_build_windows_anchor_is_last_session_and_offsets_are_symmetric():
    sessions = _weekdays("2026-02-02", "2026-03-31")
    win = rr.build_windows(sessions, 2)
    feb = win.loc[win["window_id"] == "2026-02"].sort_values("offset")
    assert feb["anchor"].iloc[0] == date(2026, 2, 27)
    assert list(feb["offset"]) == [-2, -1, 0, 1, 2]
    assert list(feb["session"]) == [date(2026, 2, 25), date(2026, 2, 26), date(2026, 2, 27),
                                    date(2026, 3, 2), date(2026, 3, 3)]


def test_build_windows_crosses_year_boundary_for_november():
    sessions = _weekdays("2025-11-03", "2026-01-15")
    win = rr.build_windows(sessions, 5)
    nov = win.loc[win["window_id"] == "2025-11"]
    assert len(nov) == 11
    assert nov["anchor"].iloc[0] == date(2025, 11, 28)
    assert nov["session"].max() == date(2025, 12, 5)


def test_holiday_without_prints_is_skipped_when_offsets_are_recounted():
    sessions = _weekdays("2021-05-03", "2021-06-30")
    win = rr.build_windows(sessions, 3)
    may = win.loc[win["window_id"] == "2021-05"]
    traded = may.loc[may["session"] != date(2021, 5, 27)]
    recounted = rr.reindex_offsets(traded).sort_values("session")
    assert recounted.loc[recounted["session"] == date(2021, 5, 26), "offset"].iloc[0] == -2
    assert recounted.loc[recounted["session"] == date(2021, 5, 31), "offset"].iloc[0] == 0


def test_outright_mask_drops_user_defined_and_spreads():
    symbols = pd.Series(["ZNM6", "ZNH6", "UD:ZN: TL 2508322", "ZNH6-ZNM6", "ZBZ25", "OZNM6", "ZN"])
    assert list(rr.outright_mask(symbols)) == [True, True, False, False, True, False, False]


def test_aggregate_contract_days_session_split_merges_first_and_last():
    # 2026-02-24 23:30 UTC is 18:30 ET on the 24th -> session closing the 25th; two monthly parts.
    part_a = pd.DataFrame({
        "ts": pd.to_datetime(["2026-02-24T23:30:00Z", "2026-02-24T23:31:00Z"]),
        "price": [110.0, 110.5], "size": [5, 3], "side": ["B", "A"],
        "raw_symbol": ["ZNM6", "UD:ZN: TL 1"],
    })
    part_b = pd.DataFrame({
        "ts": pd.to_datetime(["2026-02-25T15:00:00Z", "2026-02-25T16:00:00Z"]),
        "price": [110.25, 110.75], "size": [2, 4], "side": ["A", "B"],
        "raw_symbol": ["ZNM6", "ZNM6"],
    })
    merged = rr.merge_contract_days([rr.aggregate_contract_days(part_a), rr.aggregate_contract_days(part_b)])
    assert len(merged) == 1
    row = merged.iloc[0]
    assert row["day"] == date(2026, 2, 25)
    assert row["volume"] == 11
    assert row["first_px"] == 110.0 and row["last_px"] == 110.75


def test_walk_forward_uses_only_strictly_earlier_days():
    train = pd.DataFrame({
        "date": [f"2020-01-{d:02d}" for d in range(1, 11)],
        "x_net": np.arange(10, dtype=float),
        "y_bps": 2.0 * np.arange(10, dtype=float),
    })
    poisoned = train.copy()
    poisoned.loc[poisoned["date"] >= "2020-01-06", "y_bps"] = 1e9
    test = pd.DataFrame({"date": ["2020-01-06"], "x_net": [100.0], "y_bps": [0.0]})
    clean = rr.walk_forward_predict(train, test, min_train=3)
    leaked = rr.walk_forward_predict(poisoned, test, min_train=3)
    assert clean[0] == pytest.approx(200.0)
    assert leaked[0] == pytest.approx(clean[0])
    too_early = pd.DataFrame({"date": ["2020-01-02"], "x_net": [1.0], "y_bps": [0.0]})
    assert np.isnan(rr.walk_forward_predict(train, too_early, min_train=3)[0])


def test_window_stats_coverage_purity_and_recommendation():
    sessions = pd.DataFrame({"offset": [-4, -3, -1, 0, 1, 2, 3],
                             "share_in": [0.3, 0.5, 0.4, 0.95, 0.99, 0.99, 0.99]})
    stats = rr.window_stats(sessions, ((2, 2), (4, 0), (5, 5)))
    by = stats.set_index(["before", "after"])
    assert by.loc[(2, 2), "coverage"] == pytest.approx(1 / 3)
    assert by.loc[(4, 0), "coverage"] == pytest.approx(1.0)
    assert by.loc[(4, 0), "purity"] == pytest.approx(3 / 4)
    assert rr.recommend_window(stats) == (4, 0)
    assert rr.build_windows(_weekdays("2026-02-02", "2026-03-31"), 4, 0)["offset"].tolist() == [-4, -3, -2, -1, 0]


def test_output_root_refuses_live_store(tmp_path):
    with pytest.raises(RuntimeError):
        rr.output_root(rr.LIVE_STORE_DIR / "roll")
    assert rr.output_root(tmp_path / "ok").is_dir()
