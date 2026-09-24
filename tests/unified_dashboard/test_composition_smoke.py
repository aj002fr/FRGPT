import os
import urllib.request

import pytest

BASE_URL = os.getenv("UNIFIED_DASHBOARD_URL", "http://localhost:8800")


def _get_text(path: str) -> str:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=5) as resp:
        return resp.read().decode("utf-8")


def _server_available() -> bool:
    try:
        _get_text("/api/config")
        return True
    except Exception:
        return False


def test_dashboard_html_composition_smoke():
    if not _server_available():
        pytest.skip("Unified dashboard server not running.")
    html = _get_text("/")
    assert "eventDropdown" in html
    assert "rawPathsChart" in html
    assert "rawPathsChartPost" in html
    assert "preeventDistroChart" in html
    assert "posteventDistroChart" in html or "postDistroChart" in html
    assert "adhocTab" in html
    assert "adhocPathsChart" in html
    assert "weekmatchTab" in html
    assert "weekmatchPriceChart" in html
    assert "polymarketScenariosTab" in html

