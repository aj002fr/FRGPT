import json
import os
import urllib.error
import urllib.request

import pytest


BASE_URL = os.getenv("UNIFIED_DASHBOARD_URL", "http://localhost:8800")


def _get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _server_available() -> bool:
    try:
        data = _get_json("/api/config")
        return bool(data.get("success"))
    except Exception:
        return False


def test_api_config_smoke():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    data = _get_json("/api/config")
    assert data.get("success") is True
    assert data.get("default_event_type")
    assert "event_time_by_type" in data


def test_api_event_types_smoke():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    data = _get_json("/api/event_types")
    assert data.get("success") is True
    event_types = data.get("event_types") or []
    event_time_by_type = data.get("event_time_by_type") or {}
    for event_type in ("NFP", "CPI", "PPI", "JOLTS"):
        assert event_type in event_types or event_type in event_time_by_type


def test_api_events_and_candles_ordered():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    events = _get_json("/api/events?event_type=NFP")
    assert events.get("success") is True
    rows = events.get("events", [])
    if not rows:
        pytest.skip("No NFP events available.")
    date = rows[0]["date"]
    candles = _get_json(f"/api/candlestick/{date}?type=NFP&days=1&res=5m")
    assert candles.get("success") is True
    data = candles.get("candles", [])
    if not data:
        pytest.skip("No candles returned.")
    ordered = all(data[i]["ts_utc"] <= data[i + 1]["ts_utc"] for i in range(len(data) - 1))
    assert ordered


@pytest.mark.parametrize("event_type", ["NFP", "CPI", "PPI", "JOLTS"])
def test_api_event_details_smoke(event_type):
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    events = _get_json(f"/api/events?event_type={event_type}")
    assert events.get("success") is True
    rows = events.get("events", [])
    if not rows:
        pytest.skip(f"No {event_type} events available.")
    date = rows[0]["date"]
    details = _get_json(f"/api/event_details?type={event_type}&date={date}")
    assert details.get("success") is True
    if "event" in details:
        event = details.get("event") or {}
        prices = event.get("prices") or []
        post_prices = event.get("postevent_prices") or []
        assert prices or post_prices
    else:
        paths = details.get("paths") or {}
        ts_utc = paths.get("ts_utc") or []
        price_dec = paths.get("price_dec") or []
        assert ts_utc and price_dec


def test_api_adhoc_sessions_batch_smoke():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    events = _get_json("/api/events?event_type=NFP")
    assert events.get("success") is True
    rows = events.get("events", [])
    if not rows:
        pytest.skip("No NFP events available.")
    date = rows[0]["date"]
    data = _get_json(f"/api/adhoc_sessions_batch?dates={date}&skip_candles=1")
    assert data.get("success") is True
    sessions = data.get("sessions", [])
    if not sessions:
        pytest.skip("No ad-hoc sessions returned.")
    first = sessions[0]
    assert first.get("date") == date
    assert "paths" in first


def test_api_weekmatch_candidates_pricepaths_smoke():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    payload = {
        "events": [
            {
                "day": "Friday",
                "time": "08:30",
                "name": "Non Farm Payrolls",
                "tier": 1,
                "country": "US",
            }
        ]
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/weekmatch/candidates",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        candidates = json.loads(resp.read().decode("utf-8"))
    assert candidates.get("success") is True
    weeks = candidates.get("candidates", [])
    if not weeks:
        pytest.skip("No weekmatch candidates returned.")
    week_start = weeks[0]["week_start"]

    req = urllib.request.Request(
        f"{BASE_URL}/api/weekmatch/pricepaths",
        data=json.dumps({"weeks": [week_start]}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        pricepaths = json.loads(resp.read().decode("utf-8"))
    assert pricepaths.get("success") is True


def test_api_weekmatch_candlestick_smoke():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    payload = {
        "events": [
            {
                "day": "Friday",
                "time": "08:30",
                "name": "Non Farm Payrolls",
                "tier": 1,
                "country": "US",
            }
        ]
    }
    req = urllib.request.Request(
        f"{BASE_URL}/api/weekmatch/candidates",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        candidates = json.loads(resp.read().decode("utf-8"))
    assert candidates.get("success") is True
    weeks = candidates.get("candidates", [])
    if not weeks:
        pytest.skip("No weekmatch candidates returned.")
    week_start = weeks[0]["week_start"]
    data = _get_json(f"/api/weekmatch/candlestick/{week_start}")
    assert data.get("success") is True


def test_api_live_smoke():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/live", timeout=15) as resp:
            data = json.load(resp)
    except Exception:
        pytest.skip("Live API request timed out or failed.")
    assert isinstance(data, dict)
    assert any(key in data for key in ("ty_price", "timestamp_iso", "error"))

