from scripts.dashboard_tabs.adhoc_tab import generate_adhoc_tab_html
from scripts.dashboard_tabs.postevent_tab import generate_postevent_tab_html
from scripts.dashboard_tabs.polymarket_scenarios_tab import (
    generate_polymarket_scenarios_tab_html,
)
from scripts.dashboard_tabs.preevent_tab import generate_preevent_tab_html
from scripts.dashboard_tabs.weekmatch_tab import generate_weekmatch_tab_html
from scripts.live_unified_dashboard import _event_has_detail_payload, generate_html_dashboard


def test_preevent_tab_html_ids():
    html = generate_preevent_tab_html({})
    assert 'id="preeventTab"' in html
    assert 'id="boxplotChart"' in html
    assert 'id="rawPathsChart"' in html
    assert 'id="preeventDistroChart"' in html
    assert 'id="liveStatsPanel"' in html
    assert 'id="preeventDistroWindowBadge"' in html
    assert 'id="preeventDistroBucketList"' in html


def test_postevent_tab_html_ids():
    html = generate_postevent_tab_html({})
    assert 'id="posteventTab"' in html
    assert 'id="boxplotChartPost"' in html
    assert 'id="rawPathsChartPost"' in html
    assert 'id="postDistroChart"' in html
    assert 'id="liveStatsPanelPost"' in html
    assert 'id="distroWindowBadge"' in html
    assert 'id="distroBucketList"' in html


def test_adhoc_tab_html_ids():
    html = generate_adhoc_tab_html({"event_short_name": "NFP"})
    assert 'id="adhocTab"' in html
    assert 'id="adhocPathsChart"' in html
    assert 'id="adhocReturnsHist"' in html
    assert 'id="adhocStatsPanel"' in html
    assert 'id="adhocDistributionPanel"' in html
    assert 'id="adhocVolatilityHist"' in html


def test_weekmatch_tab_html_ids():
    html = generate_weekmatch_tab_html({})
    assert 'id="weekmatchTab"' in html
    assert 'id="weekmatchPriceChart"' in html
    assert 'id="weekmatchCandleChart"' in html
    assert 'id="weekmatchDropzone"' in html
    assert 'id="weekmatchEventsPanel"' in html
    assert 'id="weekmatchResultsPanel"' in html


def test_polymarket_scenarios_tab_html_ids():
    html = generate_polymarket_scenarios_tab_html({})
    assert 'id="polymarketScenariosTab"' in html
    assert 'id="pmNfpTable"' in html
    assert 'id="pmUerTable"' in html
    assert 'id="pmCpiTable"' in html
    assert 'id="pmCpiYoyTable"' in html
    assert 'id="pmHistChart"' in html


def test_generated_dashboard_includes_prepost_runtime_without_pm_regression(tmp_path):
    output_path = tmp_path / "generated_unified_dashboard.html"
    dashboard_data = {
        "event_type": "NFP",
        "events": [{"date": "2026-01-10", "cons_std": 25.0, "surp_pct": 0.5}],
        "cons_std_stats": {"min": 10, "max": 40},
        "all_std_surprises": [],
        "user_cons_std": 25.0,
        "user_fwd_be": 6.5,
    }

    html_path = generate_html_dashboard(
        dashboard_data,
        output_path=output_path,
        show_live=False,
        embed_data=True,
        preevent_only=False,
    )
    html = output_path.read_text(encoding="utf-8")

    assert str(output_path) == html_path
    assert "function updatePreeventDashboard" in html
    assert "function updatePosteventDashboard" in html
    assert "window.__initGeneratedDashboard" in html

    # Guardrail: Polymarket tab runtime remains present in generated HTML.
    assert "function pmRefreshTable(component)" in html


def test_event_has_detail_payload_requires_hydrated_paths():
    assert _event_has_detail_payload({"date": "2026-01-10"}) is False
    assert _event_has_detail_payload({"prices": [], "postevent_prices": []}) is True
    assert _event_has_detail_payload({"prices": [], "postevent_prices": None}) is False

