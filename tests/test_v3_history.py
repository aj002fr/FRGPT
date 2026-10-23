from __future__ import annotations

import gzip
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from scripts.aggression_dashboard_v3 import beta_ref as beta_ref_module
from scripts.aggression_dashboard_v3 import history_store as hs
from scripts.aggression_dashboard_v3.banner import (
    STATE_IDS,
    STATUS_NAMES,
    STATUS_READY,
    STATUS_WARMING,
    build_banner_series,
    classify_state,
    classify_states,
    window_states,
)
from scripts.aggression_dashboard_v3.beta_ref import BETA_VERSION, _session_key
from scripts.aggression_dashboard_v3.core import build_print_frame, completed_session_starts
from scripts.aggression_dashboard_v3.history_client import (
    PRIORITY_BUILD,
    PRIORITY_SERVE,
    HistoryPool,
    HistoryScheduler,
    HistoryWorkerError,
)
from scripts.aggression_dashboard_v3.history_worker import encode_body

EASTERN = ZoneInfo("America/New_York")
TAU = 1.0 / 64.0
DV01 = {"TY": 85.0, "FV": 42.0, "TU": 36.0, "US": 125.0}
MON_OPEN = datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc)  # Sun 18:00 ET, session "Mon Sep 21"
TUE_OPEN = datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
NS = 1_000_000_000


def _session_trades(start: datetime, *, seed: int = 0, n: int = 4000, hours: float = 2.0, gap_at: float | None = None):
    rng = np.random.default_rng(seed)
    offsets = np.sort(rng.uniform(1.0, hours * 3600, size=n))
    if gap_at is not None:
        offsets = offsets[(offsets < gap_at) | (offsets > gap_at + 8 * 60)]
    count = len(offsets)
    return pd.DataFrame({
        "ts": pd.Timestamp(start) + pd.to_timedelta(offsets, unit="s"),
        "price": 112.0 + np.cumsum(rng.choice([-1, 0, 0, 1], size=count)) * TAU,
        "size": rng.integers(1, 20, size=count),
        "side": rng.choice(["A", "B"], size=count),
        "instrument_id": "42001080",
        "raw_symbol": "ZNZ6",
        "dashboard_symbol": "TY",
    })


def _patch_cache(monkeypatch, trades_by_start):
    """Serve synthetic trades per session and keep the real beta / trade cache out of the test."""
    from scripts.aggression_dashboard_v3 import tag_beta as tb

    def fake_load(symbols, start):
        trades = trades_by_start[start]
        status = [{
            "symbol": "TY",
            "file_date": start.strftime("%Y-%m-%d"),
            "start": start,
            "end": start + timedelta(hours=23),
            "state": "ok",
            "rows": len(trades),
        }]
        return {"TY": trades}, status

    def refuse_write(_payload):
        raise AssertionError("history must never write beta_ref.json")

    monkeypatch.setattr(hs, "load_session_trades", fake_load)
    monkeypatch.setattr(hs, "_entries_from_disk", lambda: {})
    monkeypatch.setattr(hs, "cache_ready", lambda view, start, now=None: start in trades_by_start)
    monkeypatch.setattr(beta_ref_module, "_save_cache", refuse_write)
    # Force the superseded 10-session fallback so these tests do not read the live tag_beta.json.
    monkeypatch.setattr(tb, "TAG_BETA_PATH", Path("__no_tag_beta__"))


def _seeded_slopes(root, session, betas):
    slopes = hs.SlopeCache("ty", root)
    for prior, beta in zip(completed_session_starts(session, n=10), betas):
        slopes.put(_session_key(prior), {"ok": True, "beta": beta, "n": 100}, "test")
    return slopes


def test_classify_states_matches_classify_state_on_every_sign_boundary():
    values = [-3.0, -1.0000001, -1.0, -0.5, 0.0, 0.5, 1.0, 1.0000001, 3.0]
    grid = np.array(list(product(values, repeat=3)))
    codes = classify_states(grid[:, 0], grid[:, 1], grid[:, 2])
    assert [STATE_IDS[code] for code in codes] == [classify_state(d, a, r) for d, a, r in grid]


def test_window_states_reset_at_the_session_boundary_and_after_gaps():
    minute = 60 * NS
    base = pd.Timestamp("2026-09-21T21:50:00Z").value
    ts = base + np.arange(26) * minute
    sessions = np.where(ts < pd.Timestamp("2026-09-21T22:00:00Z").value, "A", "B")
    status, state, d, a, r = window_states(ts, 112.0 + np.arange(26) * TAU, np.arange(26) * 10.0, sessions, 1e-3, TAU)
    at = {pd.Timestamp(t, tz="UTC").strftime("%H:%M"): i for i, t in enumerate(ts)}
    assert status[at["21:57"]] == STATUS_READY
    assert status[at["22:02"]] == STATUS_WARMING and state[at["22:02"]] == -1
    assert status[at["22:05"]] == STATUS_WARMING
    assert status[at["22:15"]] == STATUS_READY
    assert np.allclose(r[status == STATUS_READY], (d - a)[status == STATUS_READY])

    gap_ts = np.concatenate([base + np.arange(11) * minute, base + (20 + np.arange(11)) * minute])
    status, *_ = window_states(gap_ts, np.full(22, 112.0), np.zeros(22), np.full(22, "A"), 1e-3, TAU)
    at = {pd.Timestamp(t, tz="UTC").strftime("%H:%M"): i for i, t in enumerate(gap_ts)}
    assert status[at["22:12"]] == STATUS_READY
    assert status[at["22:15"]] == STATUS_READY

    day = pd.Timestamp("2026-09-21T14:00:00Z").value
    day_ts = np.concatenate([day + np.arange(6) * minute, day + (16 + np.arange(6)) * minute])
    status, *_ = window_states(day_ts, np.full(12, 112.0), np.zeros(12), np.full(12, "A"), 1e-3, TAU)
    at = {pd.Timestamp(t, tz="UTC").strftime("%H:%M"): i for i, t in enumerate(day_ts)}
    assert status[at["14:17"]] == STATUS_WARMING
    assert status[at["14:21"]] == STATUS_READY


def test_build_session_matches_live_hover_series_and_keeps_last_print_per_second(tmp_path, monkeypatch):
    trades = _session_trades(MON_OPEN, gap_at=3600.0)
    _patch_cache(monkeypatch, {MON_OPEN: trades})
    betas = [1e-3 * (1 + i / 10) for i in range(10)]
    meta = hs.build_session("ty", MON_OPEN, slopes=_seeded_slopes(tmp_path, MON_OPEN, betas), dv01_table=DV01, now=LATER, root=tmp_path)

    assert meta["ok"] and meta["n_sessions_used"] == 10
    assert meta["beta_ref"] == pytest.approx(np.mean(betas))
    assert meta["price_prints"] == len(trades) and meta["label"] == "Mon Sep 21"
    rows = pd.read_parquet(hs.session_paths("ty", MON_OPEN, tmp_path)[0])
    frame, _meta = build_print_frame({"TY": trades}, price_symbol="TY", dv01_table=DV01, session_start=MON_OPEN)
    ts = pd.DatetimeIndex(frame.index).asi8
    last_of_second = np.r_[ts[1:] // NS != ts[:-1] // NS, True]
    assert rows["t"].tolist() == ts[last_of_second].tolist()
    assert np.allclose(rows["total_aggress"], frame["total_aggress"].to_numpy()[last_of_second])
    assert meta["rows"] == len(rows) <= 4000

    series = build_banner_series(
        frame,
        {"beta_ref": meta["beta_ref"], "beta_display_yield_bp_per_1m": -1.0, "ok": True},
        price_symbol="TY",
        as_of_index=pd.to_datetime(rows["t"].to_numpy(), unit="ns", utc=True),
    )
    assert len(series) == len(rows)
    stored = [
        (STATUS_NAMES[s], STATE_IDS[k] if s == STATUS_READY else STATUS_NAMES[s])
        for s, k in zip(rows["status"], rows["state"])
    ]
    assert stored == [(row["status"], row["state"]) for row in series]
    assert {"READY", "WARMING"} <= {status for status, _ in stored}


def test_session_beta_is_the_mean_of_the_ten_prior_slopes(tmp_path, monkeypatch):
    _patch_cache(monkeypatch, {})
    from scripts.aggression_dashboard_v3 import tag_beta as tb
    monkeypatch.setattr(tb, "TAG_BETA_PATH", tmp_path / "missing_tag_beta.json")
    slopes = hs.SlopeCache("ty", tmp_path)
    priors = completed_session_starts(TUE_OPEN, n=12)
    for i, prior in enumerate(priors):
        slopes.put(_session_key(prior), {"ok": True, "beta": float(i + 1), "n": 50}, "test")
    slopes.put(_session_key(priors[-1]), {"ok": False, "beta": None, "reason": "unreadable"}, "test")
    beta = hs.prior_beta("ty", TUE_OPEN, slopes, DV01, LATER)
    ten = completed_session_starts(TUE_OPEN, n=10)
    assert beta["sessions_used"] + beta["sessions_missing"] == [_session_key(start) for start in ten][:9] + [_session_key(ten[-1])]
    assert beta["n_sessions_used"] == 9 and beta["sessions_missing"] == [_session_key(ten[-1])]
    assert beta["beta_ref"] == pytest.approx(np.mean([float(i + 1) for i in range(2, 11)]))


def test_live_slopes_from_beta_ref_win_and_failed_rows_are_not_seeded(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "_entries_from_disk", lambda: {})
    key, other, failed = (_session_key(start) for start in completed_session_starts(TUE_OPEN, n=3))
    cache = hs.SlopeCache("ty", tmp_path)
    cache.put(key, {"ok": True, "beta": 9e-8}, "cache_fit")
    entries = {"live": {
        "version": BETA_VERSION,
        "price_symbol": "TY",
        "aggression_symbols": ["TY"],
        "dv01_adjust": True,
        "session_rows": [
            {"session_start": key, "ok": True, "beta": 2e-8, "n": 40},
            {"session_start": failed, "ok": False, "beta": None},
        ],
    }, "curve": {
        "version": BETA_VERSION,
        "price_symbol": "TY",
        "aggression_symbols": ["FV", "TU", "TY", "US"],
        "dv01_adjust": True,
        "session_rows": [{"session_start": other, "ok": True, "beta": 7e-8}],
    }}
    assert cache.seed_from_beta_ref(entries) == 1
    cache.put(key, {"ok": True, "beta": 5e-8}, "history_build")
    assert cache.get(key)["beta"] == 2e-8 and cache.get(key)["source"] == "beta_ref"
    assert cache.get(failed) is None and cache.get(other) is None
    cache.save()
    assert hs.SlopeCache("ty", tmp_path).get(key)["beta"] == 2e-8


def test_a_session_with_an_unreadable_cache_file_gets_no_slope():
    frame = pd.DataFrame({"delta_a": np.arange(100.0), "delta_p": np.arange(100.0) * 1e-3})
    status = [{"symbol": "TY", "file_date": "2026-09-15", "state": "unreadable"}]
    fit = hs._slope_fit(frame, status)
    assert not fit["ok"] and "TY 2026-09-15" in fit["reason"]
    assert hs._slope_fit(frame, [{"symbol": "TY", "file_date": "2026-09-16", "state": "ok"}])["ok"]


def test_sessions_in_range_counts_globex_sessions_and_caps():
    fri_noon = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
    mon_noon = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
    opens = hs.sessions_in_range(fri_noon, mon_noon)
    assert [start.astimezone(EASTERN).strftime("%a %H:%M") for start in opens] == ["Thu 18:00", "Sun 18:00"]
    assert hs.sessions_in_range(datetime(2026, 9, 28, 21, 10, tzinfo=timezone.utc), datetime(2026, 9, 28, 21, 50, tzinfo=timezone.utc)) == []
    with pytest.raises(hs.HistoryRangeError):
        hs.sessions_in_range(mon_noon - timedelta(days=21), mon_noon)
    with pytest.raises(hs.HistoryRangeError):
        hs.sessions_in_range(mon_noon, fri_noon)


def test_serve_history_slices_the_range_and_respects_payload_caps(tmp_path, monkeypatch):
    trades = {MON_OPEN: _session_trades(MON_OPEN, seed=1), TUE_OPEN: _session_trades(TUE_OPEN, seed=2)}
    _patch_cache(monkeypatch, trades)
    for start in trades:
        meta = hs.build_session("ty", start, slopes=_seeded_slopes(tmp_path, start, [1e-3] * 10), dv01_table=DV01, now=LATER, root=tmp_path)
        assert meta["ok"]
    monkeypatch.setattr(hs, "PANEL_BUCKETS", 50)
    monkeypatch.setattr(hs, "STRIP_MAX_BINS", 100)
    monkeypatch.setattr(hs, "HOVER_MAX_ROWS", 60)

    wed_open = TUE_OPEN + timedelta(days=1)
    payload = hs.serve_history("ty", MON_OPEN, wed_open + timedelta(hours=2), now=LATER, root=tmp_path)
    assert [(s["label"], s["status"]) for s in payload["sessions"]] == [("Mon Sep 21", "built"), ("Tue Sep 22", "built"), ("Wed Sep 23", "pending")]
    assert any("Wed Sep 23: pending" in text for text in payload["warnings"])
    points = payload["panels"]["p1"]["data"]
    assert len(points) <= 6 * 50 + 1
    breaks = [p for p in points if p["total_aggress"] is None]
    assert [p["x"] for p in breaks] == ["2026-09-21T17:00:00"]
    xs = [p["x"] for p in points]
    assert xs == sorted(xs)
    strip = payload["panels"]["p1_states"]
    assert strip["bin_sec"] > 0
    for runs in (strip["data"], strip["group_data"]):
        assert all(run["x0"] < run["x1"] for run in runs)
        assert all(a["x1"] <= b["x0"] for a, b in zip(runs, runs[1:]))
        assert len(runs) <= 100
    hover = payload["hover"]
    assert len(hover) <= 60 and [row["x"] for row in hover] == sorted(row["x"] for row in hover)

    start = MON_OPEN + timedelta(minutes=30)
    end = MON_OPEN + timedelta(minutes=50)
    part = hs.serve_history("ty", start, end, now=LATER, root=tmp_path)
    start_et, end_et = part["range"]["start_et"], part["range"]["end_et"]
    assert all(start_et <= p["x"] <= end_et for p in part["panels"]["p1"]["data"])
    rows = pd.read_parquet(hs.session_paths("ty", MON_OPEN, tmp_path)[0])
    last = rows[(rows["t"] >= pd.Timestamp(start).value) & (rows["t"] <= pd.Timestamp(end).value)].iloc[-1]
    banner = part["flow_dominance"]
    assert banner["status"] == STATUS_NAMES[int(last["status"])]
    if banner["status"] == "READY":
        assert banner["state"] == STATE_IDS[int(last["state"])]
        assert banner["parameters"]["window_min"] == 15
        assert banner["contribution_line"].startswith("15m price")
    assert part["resolution"]["strip_bin_sec"] == 12

    with pytest.raises(hs.HistoryRangeError):
        hs.serve_history("ty", MON_OPEN - timedelta(days=20), MON_OPEN, now=LATER, root=tmp_path)


def test_quick_ranges_end_at_the_last_built_session():
    statuses = ["built", "failed", "built", "built", "pending", "built", "built", "live"]
    sessions = [{"status": s, "open_et": f"o{i}", "close_et": f"c{i}", "label": f"L{i}"} for i, s in enumerate(statuses)]
    ranges = hs.quick_ranges(sessions)
    assert ranges["last_session"] == {"start": "o6", "end": "c6", "label": "L6"}
    assert ranges["last_5"]["start"] == "o2" and ranges["last_5"]["end"] == "c6" and ranges["last_5"]["sessions"] == 5
    assert hs.quick_ranges([{"status": "pending", "open_et": "o", "close_et": "c", "label": "L"}]) == {"last_session": None, "last_5": None}


def test_decimation_keeps_first_last_and_extremes():
    values = np.sin(np.linspace(0, 40, 10_000))
    values[1234] = 9.0
    values[8765] = -9.0
    picks = hs._decimate_indices([values], 100)
    assert len(picks) <= 400
    assert {0, 9999, 1234, 8765} <= set(picks.tolist())


def test_empty_bins_hold_the_state_until_the_next_print():
    t = np.array([0, 100 * NS], dtype=np.int64)
    ends = hs._cell_ends(t, np.zeros(2, dtype=int))
    starts, stops, codes = hs._binned_cells(t, ends, np.array([3, 5]), 13, 0, 110 * NS, 11)
    assert codes.tolist() == [3] * 10 + [5]
    assert stops[-1] == 110 * NS


class _FakeService:
    def __init__(self, missing):
        self._missing = missing
        self.builds = []
        self.prewarms = 0
        self.invalidations = 0

    def missing(self):
        return self._missing

    def build(self, view, session):
        self.builds.append((view, session))
        return {"built": True}

    def prewarm(self):
        self.prewarms += 1
        return 2

    def invalidate(self):
        self.invalidations += 1


def test_close_snapshot_builds_without_day_files(tmp_path, monkeypatch):
    from scripts.aggression_dashboard_v3 import tag_beta as tb

    trades = _session_trades(TUE_OPEN, seed=3)
    hs.write_close_snapshot({"TY": trades}, TUE_OPEN, tmp_path)
    called = {"cache": 0}

    def refuse_cache(symbols, start):
        called["cache"] += 1
        raise AssertionError("day files must not be read when a close snapshot covers the session")

    monkeypatch.setattr(hs, "load_session_trades", refuse_cache)
    monkeypatch.setattr(hs, "cache_ready", lambda view, start, now=None: False)
    monkeypatch.setattr(hs, "_entries_from_disk", lambda: {})
    monkeypatch.setattr(tb, "TAG_BETA_PATH", tmp_path / "missing_tag_beta.json")
    assert hs.missing_sessions("ty", now=LATER, root=tmp_path) == [TUE_OPEN]
    meta = hs.build_session(
        "ty", TUE_OPEN,
        slopes=_seeded_slopes(tmp_path, TUE_OPEN, [1e-3] * 10),
        dv01_table=DV01, now=LATER, root=tmp_path,
    )
    assert meta["ok"] and meta["source"] == "close_snapshot" and meta["rows"] > 0
    assert called["cache"] == 0
    assert hs.missing_sessions("ty", now=LATER, root=tmp_path) == []


def test_day_files_still_build_a_session_that_has_no_snapshot(tmp_path, monkeypatch):
    seen = {"cache": 0}

    def fake_load(symbols, start):
        seen["cache"] += 1
        return {"TY": _session_trades(TUE_OPEN, seed=2)}, [{
            "symbol": "TY", "file_date": "2026-09-22", "state": "ok", "rows": 1,
        }]

    monkeypatch.setattr(hs, "load_session_trades", fake_load)
    monkeypatch.setattr(hs, "cache_ready", lambda view, start, now=None: True)
    monkeypatch.setattr(hs, "_entries_from_disk", lambda: {})
    hs.write_close_snapshot({"TY": _session_trades(TUE_OPEN, seed=1, n=30)}, TUE_OPEN, tmp_path)
    meta = hs.build_session(
        "ty", TUE_OPEN,
        slopes=_seeded_slopes(tmp_path, TUE_OPEN, [1e-3] * 10),
        dv01_table=DV01, now=LATER, root=tmp_path,
    )
    assert meta["source"] == "trade_cache" and seen["cache"] >= 1


def test_pending_copy_during_and_after_the_close_window(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "cache_ready", lambda view, start, now=None: False)
    during = datetime(2026, 9, 22, 21, 30, tzinfo=timezone.utc)  # Tue 17:30 ET
    after = datetime(2026, 9, 22, 22, 30, tzinfo=timezone.utc)  # Tue 18:30 ET
    assert hs._pending_reason("ty", TUE_OPEN, during, tmp_path) == "Building from the session that just closed."
    missed = hs._pending_reason("ty", TUE_OPEN, after, tmp_path)
    assert "17:00–18:00" in missed and "two days" not in missed
    hs.write_close_snapshot({"TY": _session_trades(TUE_OPEN, seed=1, n=20)}, TUE_OPEN, tmp_path)
    assert hs._pending_reason("ty", TUE_OPEN, after, tmp_path) == "Queued for the next market-closed build window."


def test_scheduler_saves_the_close_snapshot_before_it_builds():
    service = _FakeService({"ty": ["a"]})
    order = []
    service.build = lambda view, session: order.append("build") or {"built": True}
    scheduler = HistoryScheduler(
        service,
        lambda now: False,
        clock=lambda: LATER,
        prepare=lambda: order.append("snapshot"),
    )
    scheduler.run_once()
    assert order[0] == "snapshot" and "build" in order


def test_scheduler_builds_only_while_the_market_is_closed():
    service = _FakeService({"curve": ["a", "b"], "ty": ["c"]})
    market = {"open": True}
    scheduler = HistoryScheduler(service, lambda now: market["open"], clock=lambda: LATER)
    summary = scheduler.run_once()
    assert summary["skipped"] == "market open" and service.builds == [] and service.prewarms == 1

    market["open"] = False
    summary = scheduler.run_once()
    assert service.builds == [("curve", "a"), ("curve", "b"), ("ty", "c")] and summary["built"] == 3
    assert service.invalidations == 1 and service.prewarms == 2

    service.builds.clear()
    checks = iter([False, False, True, True])
    scheduler.market_open = lambda now: next(checks)
    summary = scheduler.run_once()
    assert service.builds == [("curve", "a")] and summary["skipped"] == "market opened"


class _StubSlot:
    def __init__(self):
        self.order = []
        self.gate = threading.Event()
        self.starts = 0
        self.last_used = time.monotonic()

    def alive(self):
        return True

    def request(self, job, timeout):
        self.order.append(f"{job['job']}:{job['tag']}")
        if job["tag"] == "first":
            self.gate.wait(5)
        return {"ok": True, "id": job["id"]}, b""

    def stop(self):
        pass


def test_pool_runs_serve_jobs_ahead_of_queued_builds():
    pool = HistoryPool(1, command=["unused"])
    stub = _StubSlot()
    pool.slots = [stub]
    first = pool.submit({"job": "build", "tag": "first"}, priority=PRIORITY_BUILD, timeout=5)
    deadline = time.monotonic() + 5
    while not stub.order and time.monotonic() < deadline:
        time.sleep(0.01)
    second = pool.submit({"job": "build", "tag": "second"}, priority=PRIORITY_BUILD, timeout=5)
    viewer = pool.submit({"job": "serve", "tag": "viewer"}, priority=PRIORITY_SERVE, timeout=5)
    stub.gate.set()
    for future in (first, second, viewer):
        future.result(timeout=5)
    assert stub.order == ["build:first", "serve:viewer", "build:second"]
    pool.close()


FAKE_WORKER = r"""
import json, os, sys, time
out = sys.stdout.buffer
for line in sys.stdin.buffer:
    job = json.loads(line)
    kind = job.get("job")
    if kind == "die":
        os._exit(3)
    if kind == "sleep":
        time.sleep(job.get("seconds", 5))
    body = json.dumps({"echo": job.get("payload")}).encode() if kind == "echo" else b""
    header = {"ok": kind != "fail", "error": "boom", "code": "range", "id": job.get("id"), "pid": os.getpid(), "length": len(body)}
    out.write(json.dumps(header).encode() + b"\n" + body)
    out.flush()
"""


def test_worker_round_trip_crash_restart_and_timeout():
    pool = HistoryPool(1, command=[sys.executable, "-S", "-c", FAKE_WORKER])
    try:
        header, body = pool.call({"job": "echo", "payload": {"a": [1, 2]}}, priority=PRIORITY_SERVE, timeout=60)
        assert json.loads(body) == {"echo": {"a": [1, 2]}}
        first_pid = header["pid"]
        with pytest.raises(HistoryWorkerError) as failed:
            pool.call({"job": "fail"}, priority=PRIORITY_SERVE, timeout=60)
        assert failed.value.code == "range" and pool.slots[0].starts == 1
        with pytest.raises(HistoryWorkerError) as crashed:
            pool.call({"job": "die"}, priority=PRIORITY_BUILD, timeout=60)
        assert crashed.value.code == "crash"
        header, _body = pool.call({"job": "ping"}, priority=PRIORITY_SERVE, timeout=60)
        assert header["pid"] != first_pid and pool.slots[0].starts == 2
        with pytest.raises(HistoryWorkerError) as slow:
            pool.call({"job": "sleep", "seconds": 20}, priority=PRIORITY_BUILD, timeout=1)
        assert slow.value.code == "timeout" and not pool.slots[0].alive()
        pool.call({"job": "ping"}, priority=PRIORITY_SERVE, timeout=60)
        assert pool.slots[0].starts == 3
    finally:
        pool.close()
    assert pool.workers_alive() == 0


def test_real_worker_answers_a_sessions_job(tmp_path):
    env = {**os.environ, "V3_HISTORY_ROOT": str(tmp_path)}
    pool = HistoryPool(1, env=env)
    try:
        header, body = pool.call({"job": "sessions", "view": "ty"}, priority=PRIORITY_SERVE, timeout=120)
        payload = json.loads(body)
        assert header["ok"] and payload["view"] == "ty" and payload["quick_ranges"]["last_session"] is None
        assert payload["sessions"] and all(s["status"] in ("pending", "live") for s in payload["sessions"])
        with pytest.raises(HistoryWorkerError) as bad:
            pool.call({"job": "serve", "view": "ty", "start": "2026-09-25T21:00:00+00:00", "end": "2026-09-01T21:00:00+00:00"}, priority=PRIORITY_SERVE, timeout=120)
        assert bad.value.code == "range"
    finally:
        pool.close()


def test_history_bodies_have_stable_etags():
    first = encode_body({"b": 1.5, "a": [None, float("nan")]})
    second = encode_body({"b": 1.5, "a": [None, float("nan")]})
    assert first == second
    assert json.loads(gzip.decompress(first[0])) == {"b": 1.5, "a": [None, None]}


def test_history_endpoint_serves_gzip_with_etag_and_304(monkeypatch):
    from scripts.aggression_dashboard_v3 import app as v3app

    payload = {"ok": True, "hover": []}
    body, etag = encode_body(payload)
    calls = []

    def fake_serve(view, start, end):
        calls.append((view, start, end))
        return etag, body, len(calls) > 1

    monkeypatch.setattr(v3app.HISTORY, "serve", fake_serve)
    client = v3app.app.test_client()
    url = "/api/history?view=ty&start=2026-09-24T18:00&end=2026-09-25T17:00"
    first = client.get(url, headers={"Accept-Encoding": "gzip, deflate"})
    assert first.status_code == 200 and first.headers["Content-Encoding"] == "gzip"
    assert first.headers["ETag"] == f'"{etag}"' and first.headers["X-History-Cache"] == "miss"
    assert json.loads(gzip.decompress(first.data)) == payload
    assert calls[0] == ("ty", datetime(2026, 9, 24, 22, 0, tzinfo=timezone.utc), datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc))
    again = client.get(url, headers={"Accept-Encoding": "gzip", "If-None-Match": f'"{etag}"'})
    assert again.status_code == 304 and again.data == b""
    plain = client.get(url)
    assert "Content-Encoding" not in plain.headers and plain.get_json() == payload
    assert client.get("/api/history?view=ty&start=2026-09-25T17:00&end=bad").status_code == 400
