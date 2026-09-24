import math

from src.core.dashboard_helpers import (
    be_multiplier,
    headline_bucket_index,
    histogram_edges_zero_aligned,
    is_strictly_increasing,
    lmh_bucket,
    normalize_price_to_bps,
    percentile,
    sort_by_ts,
    surprise_z,
    trim_outliers_p5_p95,
    window_move_bps,
)


def test_is_strictly_increasing():
    assert is_strictly_increasing([1, 2, 3]) is True
    assert is_strictly_increasing([1, 1, 2]) is False
    assert is_strictly_increasing([]) is True


def test_sort_by_ts():
    ts, a, b = sort_by_ts([3, 1, 2], ["c", "a", "b"], [30, 10, 20])
    assert ts == [1, 2, 3]
    assert a == ["a", "b", "c"]
    assert b == [10, 20, 30]


def test_normalize_price_to_bps():
    out = normalize_price_to_bps([100.0, 100.25], 100.0)
    assert out[0] == 0.0
    assert out[1] > 0.0


def test_window_move_bps_and_multiplier():
    move = window_move_bps(100.0, 100.25)
    assert move > 0
    assert be_multiplier(move, 10.0) == move / 10.0
    assert be_multiplier(move, 0.0) is None


def test_lmh_bucket():
    assert lmh_bucket(-1.0, 0.5) == "Low"
    assert lmh_bucket(0.0, 0.5) == "Medium"
    assert lmh_bucket(1.0, 0.5) == "High"


def test_percentile():
    vals = [0, 10, 20, 30, 40]
    assert percentile(vals, 0) == 0
    assert percentile(vals, 100) == 40
    assert math.isclose(percentile(vals, 50), 20.0)


def test_trim_outliers_p5_p95():
    vals = [0, 1, 2, 3, 100]
    trimmed = trim_outliers_p5_p95(vals)
    assert 100 not in trimmed


def test_histogram_edges_zero_aligned():
    edges = histogram_edges_zero_aligned(-7.0, 6.0, 2.0)
    assert 0.0 in edges
    assert edges == sorted(edges)


def test_surprise_z():
    assert math.isclose(surprise_z(1.2, 1.0, 0.2), 1.0)
    assert surprise_z(None, 1.0, 0.2) is None
    assert surprise_z(1.2, 1.0, 0.0) is None


def test_headline_bucket_index():
    assert headline_bucket_index(-0.1, 0.5) == -1
    assert headline_bucket_index(0.0, 0.5) == 0
    assert headline_bucket_index(0.6, 0.5) == 1


