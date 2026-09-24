"""Vectorized print matching must equal the original row-by-row matcher exactly."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.aggression_dashboard_v3 import print_reconcile as pr

GOLDEN_DIR = Path(os.environ.get("V3_GOLDEN_DIR", r"C:\dev\mdp_golden\v3\20260923"))
SIDES = ["B", "A", "N", "b", " a ", None, "BID", "ASK", "X", float("nan")]
RAWS = ["ZNZ6", "ZNZ6", "ZNZ6", "", None, "ZNH7"]


def _synthetic(seed: int, n_s: int, n_i: int, *, span_ns: int = 40_000_000, sequence: bool = False):
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2026-09-23 12:30", tz="UTC").value
    tick = 1.0 / 64.0

    def rows(n: int) -> pd.DataFrame:
        k = rng.integers(0, 4, n)
        noise = rng.choice([0.0, 0.2, -0.2, 0.5, -0.5], n) * tick
        return pd.DataFrame({
            "ts": pd.to_datetime(base + rng.integers(0, span_ns, n), utc=True),
            "price": 110.0 + k * tick + noise,
            "size": rng.choice([1, 1, 1, 2, 3, 2.5, 1.5], n),
            "side": [SIDES[v] for v in rng.integers(0, len(SIDES), n)],
            "instrument_id": "1",
            "raw_symbol": [RAWS[v] for v in rng.integers(0, len(RAWS), n)],
            "dashboard_symbol": "TY",
        })

    stored = rows(n_s)
    copies = stored.sample(n=min(n_i // 2, n_s), random_state=seed, replace=True).copy()
    jitter = rng.choice([0, 250_000, -250_000, 999_999, 1_000_000, -1_000_000, 1_000_001, 1_500_000], len(copies))
    copies["ts"] = copies["ts"] + pd.to_timedelta(jitter, unit="ns")
    incoming = pd.concat([copies, rows(n_i - len(copies))], ignore_index=True)
    incoming = incoming.sample(frac=1.0, random_state=seed + 1).reset_index(drop=True)
    if sequence:
        stored["sequence"] = rng.choice([1, 2, 3, 4, 5, 5, np.nan], len(stored))
        incoming["sequence"] = rng.choice([1, 2, 3, 4, 5, 6, np.nan], len(incoming))
    return stored, incoming


def _same_matched(stored: pd.DataFrame, incoming: pd.DataFrame, symbol: str = "TY") -> None:
    s = pr._as_trades(stored)
    i = pr._as_trades(incoming)
    expected = pr._matched_incoming_reference(s, i, symbol)
    actual = pr._matched_incoming(s, i, symbol)
    assert np.array_equal(expected, actual), f"{int((expected != actual).sum())} rows differ"


@pytest.mark.parametrize("seed", range(40))
def test_synthetic_dense_clusters_match_reference(seed: int) -> None:
    _same_matched(*_synthetic(seed, 300, 400))


@pytest.mark.parametrize("seed", range(10))
def test_synthetic_sequence_prepass_matches_reference(seed: int) -> None:
    _same_matched(*_synthetic(1000 + seed, 200, 250, sequence=True))


def test_equal_delta_tie_goes_to_lowest_stored_index() -> None:
    t = pd.Timestamp("2026-09-23 12:30", tz="UTC")
    stored = pd.DataFrame({
        "ts": [t + pd.Timedelta(microseconds=500), t - pd.Timedelta(microseconds=500)],
        "price": [110.0, 110.0], "size": [1, 1], "side": ["B", "B"],
        "instrument_id": "1", "raw_symbol": "ZNZ6", "dashboard_symbol": "TY",
    })
    incoming = stored.iloc[[0]].assign(ts=[t])
    _same_matched(stored, incoming)
    assert pr._matched_incoming(pr._as_trades(stored), pr._as_trades(incoming), "TY").tolist() == [True]


def test_bad_inputs_fall_back_to_reference() -> None:
    stored, incoming = _synthetic(7, 50, 60)
    with_nat = incoming.copy()
    with_nat.loc[3, "ts"] = pd.NaT
    _same_matched(stored, with_nat)
    bad_size = incoming.copy()
    bad_size["size"] = bad_size["size"].astype(object)
    bad_size.loc[2, "size"] = float("nan")
    with pytest.raises(ValueError):
        pr._matched_incoming_reference(pr._as_trades(stored), pr._as_trades(bad_size), "TY")
    with pytest.raises(ValueError):
        pr._matched_incoming(pr._as_trades(stored), pr._as_trades(bad_size), "TY")


def _frames_equal(a: pd.DataFrame, b: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True), check_exact=True)


def _replay(stored_all: pd.DataFrame, live_all: pd.DataFrame, symbol: str, steps, *, lookback, cover_every: int = 4,
            late: int | None = None, hole: int | None = None) -> pr.LiveOverlayCache:
    """Rebuild-style calls on a sliding window; every call must equal the full apply_live."""
    cache = pr.LiveOverlayCache()
    stored_all = pr._as_trades(stored_all).sort_values("ts", kind="stable").reset_index(drop=True)
    live_all = pr._as_trades(live_all).sort_values("ts", kind="stable").reset_index(drop=True)
    covered = None
    hole_ts = None
    for k, now in enumerate(steps):
        start = now - lookback
        if covered is None or k % cover_every == 0:
            covered = now - pd.Timedelta(minutes=4)
        stored = stored_all.loc[(stored_all["ts"] >= start) & (stored_all["ts"] <= covered)].reset_index(drop=True)
        live = live_all.loc[(live_all["ts"] >= start) & (live_all["ts"] <= now)].reset_index(drop=True)
        if late is not None and k == late and len(live) > 10:
            row = live.iloc[[len(live) // 2]].copy()
            live = pd.concat([live, row.assign(size=row["size"] + 7)], ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)
        if hole is not None and k == hole and len(stored) > 10:
            hole_ts = stored["ts"].iloc[len(stored) // 2]
        if hole_ts is not None:
            stored = stored.loc[stored["ts"] != hole_ts].reset_index(drop=True)
        _frames_equal(pr.apply_live(stored, live, symbol), cache.apply_live(stored, live, symbol))
    return cache


def test_overlay_cache_replay_on_synthetic_stream() -> None:
    rng = np.random.default_rng(11)
    base = pd.Timestamp("2026-09-23 12:00", tz="UTC")
    n = 6000
    ts = base + pd.to_timedelta(np.sort(rng.integers(0, 3_600_000_000_000, n)), unit="ns")
    burst = rng.random(n) < 0.3
    ts = ts.where(~burst, ts.to_series().shift(1).fillna(ts[0]).to_numpy())
    stored = pd.DataFrame({
        "ts": ts, "price": 110.0 + rng.integers(0, 3, n) / 64.0, "size": rng.choice([1, 1, 2], n),
        "side": rng.choice(["A", "B"], n), "instrument_id": "1", "raw_symbol": "ZNZ6", "dashboard_symbol": "TY",
    })
    live = stored.copy()
    live["ts"] = live["ts"] - pd.to_timedelta(rng.integers(0, 1_400_000, n), unit="ns")
    steps = [base + pd.Timedelta(minutes=10 + k) for k in range(45)]
    cache = _replay(stored, live, "TY", steps, lookback=pd.Timedelta(minutes=20), late=17, hole=30)
    assert cache.reused > 20
    assert cache.full >= 3


def _with_reference(monkeypatch: pytest.MonkeyPatch, fn, *args, **kwargs):
    with monkeypatch.context() as m:
        m.setattr(pr, "_matched_incoming", pr._matched_incoming_reference)
        return fn(*args, **kwargs)


@pytest.mark.parametrize("seed", range(10))
def test_synthetic_reconciled_frames_match_reference(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    stored, incoming = _synthetic(2000 + seed, 250, 300)
    for fn in (pr.apply_live, pr.apply_hist):
        _frames_equal(_with_reference(monkeypatch, fn, stored, incoming, "TY"), fn(stored, incoming, "TY"))


golden = pytest.mark.skipif(not (GOLDEN_DIR / "manifest.json").exists(), reason=f"no golden fixture at {GOLDEN_DIR}")


def _load(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = pd.read_parquet(GOLDEN_DIR / f"hist_{symbol}.parquet")
    live = pd.read_parquet(GOLDEN_DIR / f"live_{symbol}.parquet")
    return pr._as_trades(hist), pr._as_trades(live)


def _window(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    return frame.loc[(frame["ts"] >= lo) & (frame["ts"] <= hi)].reset_index(drop=True)


DAY = "2026-09-23 "
SHORT_WINDOWS = [("12:15", "12:45"), ("12:30", "13:00"), ("13:25", "13:55")]
EXPECTED_WINDOWS = [("11:31", "13:31"), ("12:30", "14:30")]


def _expected(symbol: str, start: str, end: str) -> dict:
    path = GOLDEN_DIR / f"expected_{symbol}_{start.replace(':', '')}_{end.replace(':', '')}.npz"
    if not path.exists():
        pytest.skip(f"missing {path.name}; run golden_fixture --expected-only")
    with np.load(path) as data:
        return {key: data[key] for key in data.files}


def _with_matched(monkeypatch: pytest.MonkeyPatch, matched: np.ndarray, fn, *args):
    with monkeypatch.context() as m:
        m.setattr(pr, "_matched_incoming", lambda *_a, **_k: matched.copy())
        return fn(*args)


@golden
@pytest.mark.parametrize("symbol", ["TY", "FV", "TU", "US"])
def test_golden_us_open_30min_windows_match_reference(symbol: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hist, live = _load(symbol)
    for start, end in SHORT_WINDOWS:
        h = _window(hist, DAY + start, DAY + end)
        lv = _window(live, DAY + start, DAY + end)
        _same_matched(h, lv, symbol)
        _same_matched(lv, h, symbol)
        _frames_equal(_with_reference(monkeypatch, pr.apply_live, h, lv, symbol), pr.apply_live(h, lv, symbol))
        _frames_equal(_with_reference(monkeypatch, pr.apply_hist, lv, h, symbol), pr.apply_hist(lv, h, symbol))
    h = _window(hist, DAY + "12:20", DAY + "12:50")
    lv = _window(live, DAY + "12:35", DAY + "13:05")
    early = _window(hist, DAY + "12:20", DAY + "12:40")
    _frames_equal(
        _with_reference(monkeypatch, pr.reconcile_symbol, early, live=lv, historical=h, symbol=symbol),
        pr.reconcile_symbol(early, live=lv, historical=h, symbol=symbol),
    )


@golden
@pytest.mark.parametrize("symbol", ["TY", "FV", "TU", "US"])
def test_golden_us_open_2h_windows_match_recorded_reference(symbol: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hist, live = _load(symbol)
    for start, end in EXPECTED_WINDOWS:
        expected = _expected(symbol, start, end)
        h = _window(hist, DAY + start, DAY + end)
        lv = _window(live, DAY + start, DAY + end)
        assert (len(h), len(lv)) == (int(expected["n_hist"]), int(expected["n_live"]))
        got_live = pr._matched_incoming(h, lv, symbol)
        got_hist = pr._matched_incoming(lv, h, symbol)
        assert np.array_equal(got_live, expected["matched_live"]), f"{symbol} {start}-{end} live side differs"
        assert np.array_equal(got_hist, expected["matched_hist"]), f"{symbol} {start}-{end} hist side differs"
        _frames_equal(_with_matched(monkeypatch, expected["matched_live"], pr.apply_live, h, lv, symbol), pr.apply_live(h, lv, symbol))
        _frames_equal(_with_matched(monkeypatch, expected["matched_hist"], pr.apply_hist, lv, h, symbol), pr.apply_hist(lv, h, symbol))


@golden
@pytest.mark.parametrize("symbol", ["TY", "US"])
def test_golden_overlay_cache_replay_through_us_open(symbol: str) -> None:
    hist, live = _load(symbol)
    steps = [pd.Timestamp(DAY + "12:00", tz="UTC") + pd.Timedelta(minutes=k) for k in range(0, 150)]
    cache = _replay(hist, live, symbol, steps, lookback=pd.Timedelta(hours=2), late=40, hole=90)
    assert cache.reused > 100
    assert cache.full >= 3


@golden
@pytest.mark.parametrize("symbol", ["TY", "US"])
def test_golden_perturbed_feeds_match_reference(symbol: str, monkeypatch: pytest.MonkeyPatch) -> None:
    hist, live = _load(symbol)
    rng = np.random.default_rng(42)
    h = _window(hist, "2026-09-23 12:25", "2026-09-23 12:55")
    l = _window(live, "2026-09-23 12:25", "2026-09-23 12:55").copy()
    l["ts"] = l["ts"] + pd.to_timedelta(rng.integers(-1_500_000, 1_500_000, len(l)), unit="ns")
    l = l.drop(index=l.sample(frac=0.01, random_state=1).index)
    l = pd.concat([l, l.sample(frac=0.01, random_state=2)], ignore_index=True)
    blank = l.sample(frac=0.05, random_state=3).index
    l.loc[blank, "raw_symbol"] = ""
    other = l.sample(frac=0.01, random_state=4).index
    l.loc[other, "raw_symbol"] = symbol[:2] + "H7"
    l = l.sample(frac=1.0, random_state=5).reset_index(drop=True)
    _same_matched(h, l, symbol)
    _same_matched(l, h, symbol)
    _frames_equal(_with_reference(monkeypatch, pr.apply_live, h, l, symbol), pr.apply_live(h, l, symbol))
