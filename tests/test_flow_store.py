"""Canonical AccessAPI flow store: identity, sessions, front contract, coverage, provisional bars,
lease, restart idempotence, and backfill limits."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from scripts.flow_store import backfill as bf
from scripts.flow_store.aggregate import bars_from_trades
from scripts.flow_store.normalize import normalize_frame, normalize_record, round_us
from scripts.flow_store.sessions import choose_front, previous_session, session_bounds_ns, session_for_et, session_for_ns
from scripts.flow_store.settings import BAR_SOURCE_CANONICAL, BAR_SOURCE_PROVISIONAL, MINUTE_US, SOURCE_HIST, SOURCE_LIVE
from scripts.flow_store.store import FlowStore, LeaseLost
from scripts.flow_store.sessions import EASTERN

T0 = pd.Timestamp("2026-09-24 14:00:00", tz="UTC")  # Thu 10:00 ET, session 2026-09-24
M0 = int(T0.value // 1000)


def live(ts: pd.Timestamp, price: str, size: str = "1", side: str = "B", contract: str = "ZNZ6", seq: int = 1) -> dict:
    return {"action": "T", "symbol": contract, "ts_event": ts.isoformat(), "price": price, "size": size,
            "side": side, "sequence": str(seq), "instrument_id": "42001080", "publisher_id": "1"}


def hist_rows(rows: list[dict]) -> list:
    frame = pd.DataFrame([
        {"ts_event": pd.Timestamp(r["ts_event"]).tz_convert("UTC").tz_localize(None).round("us"),
         "symbol": r["symbol"], "price": r["price"] + "000", "size": int(r["size"]), "side": r["side"]}
        for r in rows
    ])
    return normalize_frame(frame, source=SOURCE_HIST)


@pytest.fixture()
def store(tmp_path):
    s = FlowStore(tmp_path / "tape.sqlite", tmp_path / "hist.sqlite", owner="writer_a")
    assert s.acquire_lease()
    s.ensure_canonical_start(M0 - 60 * MINUTE_US)
    yield s
    s.close()


# ---- identity -------------------------------------------------------------------------
def test_live_and_historical_records_share_one_identity(store):
    recs = [live(T0 + pd.Timedelta(microseconds=10.6), "105.015625", seq=7)]
    a = normalize_record(recs[0], source=SOURCE_LIVE)
    (b,) = hist_rows(recs)
    assert a.key == b.key and a.ts_us == b.ts_us
    added = store.insert_trades([a], mode="append")
    assert len(added) == 1
    assert store.insert_trades([b], mode="set", window=(M0, M0 + MINUTE_US)) == []
    assert store.insert_trades([b], mode="set", window=(M0, M0 + MINUTE_US)) == []
    assert len(store.trades_in_range("TY", M0, M0 + MINUTE_US)) == 1


def test_identical_prints_are_counted_not_merged(store):
    rec = live(T0 + pd.Timedelta(seconds=1), "105.0")
    trades = [normalize_record(rec, source=SOURCE_LIVE) for _ in range(3)]
    assert len(store.insert_trades(trades, mode="append")) == 3
    window = (M0, M0 + MINUTE_US)
    assert store.insert_trades(hist_rows([rec] * 3), mode="set", window=window) == []
    assert len(store.insert_trades(hist_rows([rec] * 4), mode="set", window=window)) == 1
    assert len(store.trades_in_range("TY", *window)) == 4


def _writer(tmp_path):
    from scripts.flow_store.writer_main import FlowWriter

    return FlowWriter(tape_path=tmp_path / "t.sqlite", history_path=tmp_path / "h.sqlite", status_path=tmp_path / "s.json")


def test_coverage_starts_per_root_and_quiet_roots_stay_covered(tmp_path):
    import time as _time

    from scripts.flow_store.settings import COVERAGE_TAIL_US

    w = _writer(tmp_path)
    w.connected = True
    now = _time.time()
    w.last_record_wall = {"TY": now, "FV": None, "TU": None, "US": now - 45}
    w.last_event_us = {"TY": M0 + 50_000_000, "FV": None, "TU": None, "US": M0 + 2_000_000}
    w.root_first_us = {"TY": M0 + 10_000_000, "FV": None, "TU": None, "US": M0 + 2_000_000}
    opened = w._coverage_updates()
    assert set(opened) == {"TY", "US"}
    assert opened["TY"] == ("open", M0 + 10_000_000, M0 + 50_000_000 - COVERAGE_TAIL_US)
    assert opened["US"] == ("open", M0 + 2_000_000, M0 + 50_000_000 - COVERAGE_TAIL_US)
    w.cov_open.update({"TY": 1, "US": 2})
    w.last_event_us["TY"] = M0 + 90_000_000
    assert w._coverage_updates()["US"] == ("extend", 0, M0 + 90_000_000 - COVERAGE_TAIL_US)
    w.last_record_wall = {s: now - 120 if v else None for s, v in w.last_record_wall.items()}
    assert w._coverage_updates() == {}
    assert all(v is None for v in w.cov_open.values()) and all(v is None for v in w.root_first_us.values())


def test_fetch_uses_whole_second_bounds_and_reads_empty_json(monkeypatch):
    import io
    import json as _json
    import urllib.request

    requested = []

    class Reply(io.BytesIO):
        headers = {"Content-Type": "application/json"}
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=0):
        requested.append(url)
        return Reply(_json.dumps({"data": [], "count": 0}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert bf.fetch_accessapi_trades("TU", M0 + 19_017_782, M0 + 22_114_715) == []
    assert "start=2026-09-24T14%3A00%3A19Z" in requested[0] and "end=2026-09-24T14%3A00%3A23Z" in requested[0]


def test_round_us_is_half_to_even():
    assert round_us(1_000_499) == 1000 and round_us(1_000_501) == 1001
    assert round_us(1_000_500) == 1000 and round_us(1_001_500) == 1002


# ---- sessions and front contract ----------------------------------------------------------
@pytest.mark.parametrize("et, expected", [
    ("2026-09-20 17:59", None), ("2026-09-20 18:00", date(2026, 9, 21)), ("2026-09-21 16:59", date(2026, 9, 21)),
    ("2026-09-21 17:00", None), ("2026-09-21 18:00", date(2026, 9, 22)), ("2026-09-25 16:59", date(2026, 9, 25)),
    ("2026-09-25 17:00", None), ("2026-09-26 12:00", None), ("2026-11-01 18:30", date(2026, 11, 2)),
])
def test_globex_session_labels(et, expected):
    assert session_for_et(datetime.fromisoformat(et).replace(tzinfo=EASTERN)) == expected


def test_session_bounds_and_previous_session_cross_dst_and_weekend():
    lo, hi = session_bounds_ns(date(2026, 11, 2))
    assert pd.Timestamp(lo, tz="UTC") == pd.Timestamp("2026-11-01 23:00", tz="UTC")
    assert pd.Timestamp(hi, tz="UTC") == pd.Timestamp("2026-11-02 22:00", tz="UTC")
    assert session_for_ns(lo) == date(2026, 11, 2) and session_for_ns(hi) is None
    assert previous_session(date(2026, 9, 21)) == date(2026, 9, 18)
    assert choose_front({"ZNZ6": 10, "ZNH7": 3}) == "ZNZ6"
    assert choose_front({"ZNZ6": 5, "ZNH7": 5}) == "ZNH7"


def _session_trades(session: date, volumes: dict[str, int]) -> list:
    lo_ns, _ = session_bounds_ns(session)
    base = pd.Timestamp(lo_ns + 3_600_000_000_000, tz="UTC")
    out = []
    for i, (contract, n) in enumerate(volumes.items()):
        out += [normalize_record(live(base + pd.Timedelta(seconds=i * 100 + k), "105.0", contract=contract, seq=k), source=SOURCE_HIST)
                for k in range(n)]
    return out


def test_front_contract_uses_prior_session_volume_and_is_frozen(store):
    calls = []

    def fetch(symbol, lo, hi):
        calls.append((lo, hi))
        return _session_trades(date(2026, 9, 23), {"ZNZ6": 5, "ZNH7": 9})

    assert bf.ensure_front(store, "2026-09-24", "TY", fetch) == "ZNH7"
    assert len(calls) == 1
    assert store.freeze_front_contract("2026-09-24", "TY", "ZNZ6", "manual", 1) == "ZNH7"
    assert bf.ensure_front(store, "2026-09-24", "TY", fetch) == "ZNH7" and len(calls) == 1
    store.set_override("2026-09-24", "TY", "ZNZ6", "roll exception")
    assert store.front_contract("2026-09-24", "TY") == "ZNZ6"


def test_front_contract_switches_only_at_session_boundary_from_the_tape(store):
    prev = date(2026, 9, 23)
    lo_ns, hi_ns = session_bounds_ns(prev)
    store.insert_trades(_session_trades(prev, {"ZNZ6": 4, "ZNH7": 2}), mode="append")
    store.add_coverage("TY", lo_ns // 1000, hi_ns // 1000, SOURCE_LIVE)
    assert bf.ensure_front(store, "2026-09-24", "TY", lambda *a: pytest.fail("fetch not needed")) == "ZNZ6"
    store.insert_trades(_session_trades(date(2026, 9, 24), {"ZNH7": 8}), mode="append")
    assert store.front_contract("2026-09-24", "TY") == "ZNZ6"
    lo24, hi24 = session_bounds_ns(date(2026, 9, 24))
    store.add_coverage("TY", lo24 // 1000, hi24 // 1000, SOURCE_LIVE)
    assert bf.ensure_front(store, "2026-09-25", "TY", lambda *a: pytest.fail("fetch not needed")) == "ZNH7"


# ---- bars, coverage, provisional ------------------------------------------------------------
def test_bars_use_v1_dv01_formulas_and_exclude_n():
    from scripts.aggression_dashboard.core import trades_to_symbol_minute_bars

    recs = [live(T0 + pd.Timedelta(seconds=s), p, size=z, side=d, seq=s)
            for s, p, z, d in ((1, "105.0", "2", "B"), (5, "105.015625", "3", "A"), (9, "105.0", "1", "N"), (61, "105.03125", "4", "B"))]
    trades = [normalize_record(r, source=SOURCE_LIVE) for r in recs]
    ours = bars_from_trades(trades, dv01_unit=85.0)
    frame = pd.DataFrame([{"ts": t.ts_event_ns, "price": t.price, "size": t.size, "side": t.side} for t in trades if t.side != "N"])
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    v1, _ = trades_to_symbol_minute_bars(frame, "TY", dv01_unit=85.0)
    for ts, row in v1.iterrows():
        bar = ours[int(ts.value // 1000)]
        for field in ("buy_contracts", "sell_contracts", "buy_flow", "sell_flow", "signed_contracts", "signed_flow", "trade_price"):
            assert getattr(bar, field) == pytest.approx(float(row[field]), rel=1e-12)
        assert bar.trade_count == int(row["trade_count"])
    assert set(ours) == {M0, M0 + MINUTE_US}


def test_sparse_zero_trade_minute_is_covered_but_has_no_row(store):
    store.add_coverage("TY", M0, M0 + 3 * MINUTE_US, SOURCE_LIVE)
    store.insert_trades([normalize_record(live(T0 + pd.Timedelta(seconds=70), "105.0"), source=SOURCE_LIVE)], mode="append")
    store.freeze_front_contract("2026-09-24", "TY", "ZNZ6", "test", 1)
    for minute in (M0, M0 + MINUTE_US):
        bf.seal_minute(store, "TY", minute, front="ZNZ6", dv01_unit=85.0, reason="test")
    assert store.read_bar("TY", M0) is None and store.minute_covered("TY", M0)
    assert store.read_bar("TY", M0 + MINUTE_US)["trade_count"] == 1
    assert store.gaps("TY", M0, M0 + 5 * MINUTE_US) == [(M0 + 3 * MINUTE_US, M0 + 5 * MINUTE_US)]
    assert not store.minute_covered("TY", M0 + 3 * MINUTE_US)


def test_provisional_bars_only_for_uncovered_minutes_and_replaced_with_revision(store):
    store.freeze_front_contract("2026-09-24", "TY", "ZNZ6", "test", 1)
    store.add_coverage("TY", M0, M0 + MINUTE_US, SOURCE_LIVE)
    backup = [normalize_record(live(T0 + pd.Timedelta(seconds=s), "105.0", size="2"), source="databento_backup") for s in (10, 70, 130)]
    store.insert_trades(backup, mode="append", table="provisional_tape")
    assert bf.seal_provisional(store, "TY", M0, front="ZNZ6", dv01_unit=85.0) == "covered"
    assert bf.seal_provisional(store, "TY", M0 + MINUTE_US, front="ZNZ6", dv01_unit=85.0) == "inserted"
    assert bf.seal_provisional(store, "TY", M0 + 2 * MINUTE_US, front="ZNZ6", dv01_unit=85.0) == "inserted"
    assert store.read_bar("TY", M0 + MINUTE_US)["source"] == BAR_SOURCE_PROVISIONAL

    canonical = hist_rows([live(T0 + pd.Timedelta(seconds=75), "105.0", size="5")])
    now = M0 + 10 * MINUTE_US
    stats = bf.fill_gaps(store, "TY", M0, now, fetch=lambda s, lo, hi: [t for t in canonical if lo <= t.ts_us < hi], dv01_unit=85.0)
    assert stats["revised"] == 2
    bar = store.read_bar("TY", M0 + MINUTE_US)
    assert bar["source"] == BAR_SOURCE_CANONICAL and bar["buy_contracts"] == 5
    assert store.read_bar("TY", M0 + 2 * MINUTE_US) is None
    reasons = [r["reason"] for r in store.revisions()]
    assert reasons == ["backfill", "backfill"]


# ---- lease and restart -----------------------------------------------------------------------
def test_second_writer_is_rejected_and_stale_lease_is_taken_over(store, tmp_path):
    other = FlowStore(tmp_path / "tape.sqlite", tmp_path / "hist.sqlite", owner="writer_b")
    try:
        assert not other.acquire_lease()
        with pytest.raises(LeaseLost):
            other.insert_trades([normalize_record(live(T0, "105.0"), source=SOURCE_LIVE)], mode="append")
        row = store.lease_row()
        assert other.acquire_lease(now=row["heartbeat_epoch"] + 61)
        with pytest.raises(LeaseLost):
            store.heartbeat()
        with pytest.raises(LeaseLost):
            store.write_bar("TY", M0, None, source=BAR_SOURCE_CANONICAL, reason="x")
    finally:
        other.close()


def test_restart_and_replay_add_nothing(tmp_path):
    first = FlowStore(tmp_path / "tape.sqlite", tmp_path / "hist.sqlite", owner="writer")
    assert first.acquire_lease()
    first.ensure_canonical_start(M0)
    first.freeze_front_contract("2026-09-24", "TY", "ZNZ6", "test", 1)
    recs = [live(T0 + pd.Timedelta(seconds=s, microseconds=s * 7.3), "105.0", side="AB"[s % 2], seq=s) for s in range(0, 170, 3)]
    first.insert_trades([normalize_record(r, source=SOURCE_LIVE) for r in recs[:30]], mode="append")
    first.add_coverage("TY", M0, M0 + 90 * 1_000_000, SOURCE_LIVE)
    first.close()  # crash: lease not released

    second = FlowStore(tmp_path / "tape.sqlite", tmp_path / "hist.sqlite", owner="writer")
    assert second.acquire_lease()  # same owner/pid/host resumes
    fetch = lambda s, lo, hi: [t for t in hist_rows(recs) if lo <= t.ts_us < hi]
    now = M0 + 10 * MINUTE_US
    bf.fill_gaps(second, "TY", M0, now, fetch=fetch, dv01_unit=85.0)
    counts = len(second.trades_in_range("TY", M0, now))
    bars = [second.read_bar("TY", M0 + k * MINUTE_US) for k in range(3)]
    stats = bf.fill_gaps(second, "TY", M0, now, fetch=fetch, dv01_unit=85.0)
    assert stats["trades_added"] == 0 and stats["segments"] == 0
    assert counts == len(recs) == len(second.trades_in_range("TY", M0, now))
    assert bars == [second.read_bar("TY", M0 + k * MINUTE_US) for k in range(3)]
    second.close()


def test_backfill_skips_covered_minutes_forming_minute_and_settle_window(store):
    store.freeze_front_contract("2026-09-24", "TY", "ZNZ6", "test", 1)
    store.add_coverage("TY", M0, M0 + 2 * MINUTE_US, SOURCE_LIVE)
    store.insert_trades([normalize_record(live(T0 + pd.Timedelta(seconds=30), "105.0"), source=SOURCE_LIVE)], mode="append")
    bf.seal_minute(store, "TY", M0, front="ZNZ6", dv01_unit=85.0, reason="live")
    before = store.read_bar("TY", M0)
    calls = []

    def fetch(symbol, lo, hi):
        calls.append((lo, hi))
        return hist_rows([live(T0 + pd.Timedelta(seconds=30), "105.0", size="9")])

    now = M0 + 6 * MINUTE_US + 20_000_000
    bf.fill_gaps(store, "TY", M0, now, fetch=fetch, dv01_unit=85.0)
    assert calls == [(M0 + 2 * MINUTE_US, M0 + 4 * MINUTE_US)]
    assert store.read_bar("TY", M0) == before
    assert store.gaps("TY", M0, now) == [(M0 + 4 * MINUTE_US, now)]


def test_bars_are_never_written_before_canonical_start(store):
    store.set_meta("canonical_start_us", str(M0 + MINUTE_US))
    assert store.write_bar("TY", M0, None, source=BAR_SOURCE_CANONICAL, reason="x") == "before_canonical_start"
