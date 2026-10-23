from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from scripts.aggression_dashboard_v3 import panels
from scripts.aggression_dashboard_v3.banner import STATE_STYLE, _payload
from scripts.aggression_dashboard_v3.live_publisher import _cap_list, compact_banner, wire_point
from scripts.aggression_dashboard_v3.panels import (
    PRINT_PANEL_FIELDS,
    append_print_rows,
    build_dashboard_payload,
    print_panel_rows,
    state_styles_payload,
)

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_JS = ROOT / "scripts" / "aggression_dashboard_v3" / "static" / "dashboard.js"
HOVER_FIELDS = (
    "status", "state", "headline", "color", "family", "group", "group_headline", "group_color",
    "as_of", "contribution_line", "reason", "label",
)


def _frame(n: int, *, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Print frame and Panel 4 path with missing values in every pattern the panels filter on."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-10-22T22:00:00Z", periods=n, freq="250ms")
    frame = pd.DataFrame({
        "total_aggress": np.cumsum(rng.normal(0.0, 50.0, n)),
        "price": 110.0 + np.cumsum(rng.choice([-1, 0, 1], n)) / 64.0,
        "buy_aggress": rng.integers(0, 40, n).astype(float),
        "sell_aggress": rng.integers(0, 40, n).astype(float),
        "buysell_raw": rng.uniform(-100.0, 100.0, n),
    }, index=idx)
    frame.loc[frame.index[3::11], "total_aggress"] = np.nan
    frame.loc[frame.index[5::13], "price"] = np.nan
    blank = frame.index[7::17]
    frame.loc[blank, ["total_aggress", "price", "buy_aggress", "sell_aggress"]] = np.nan
    path = pd.DataFrame({
        "price": frame["price"],
        "implied_price": 110.0 + frame["total_aggress"].fillna(0.0) * 1e-4,
        "large_residual": False,
    }, index=idx)
    path.loc[path.index[2::9], "implied_price"] = np.nan
    marked = path.index[::23]
    path.loc[marked[path.loc[marked, "price"].notna().to_numpy()], "large_residual"] = True
    return frame, path


def _without_rows(panel: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in panel.items() if key not in ("data", "rows")}


def _live_points(start: pd.Timestamp, n: int, *, seed: int) -> List[Dict[str, Any]]:
    rng = np.random.default_rng(seed)
    points = []
    for i in range(n):
        missing = i % 5 == 0
        points.append({
            "x": panels._to_et_iso(start + pd.Timedelta(milliseconds=250 * (i + 1))),
            "price": None if missing else float(110.0 + rng.integers(-8, 8) / 64.0),
            "total_aggress": float(rng.normal(0.0, 500.0)),
            "buysell_ratio": float(rng.uniform(-100.0, 100.0)),
            "buy_aggress": float(rng.integers(0, 40)),
            "sell_aggress": -float(rng.integers(0, 40)),
            "implied_price": None if i % 3 == 0 else float(110.0 + rng.normal(0.0, 0.1)),
        })
    return points


def test_print_columns_match_the_old_panel_rows(monkeypatch):
    monkeypatch.setattr(panels, "TICK_PLOT_MAX_POINTS", 60)
    frame, path = _frame(150)
    old = build_dashboard_payload(frame, path, {}, columnar=False)
    new = build_dashboard_payload(frame, path, {})
    columns = new["prints"]
    assert len({len(column) for column in columns.values()}) == 1
    assert "p5" not in old["panels"] and "p5" not in new["panels"]
    for key, fields in PRINT_PANEL_FIELDS.items():
        assert "data" not in new["panels"][key]
        assert print_panel_rows(columns, new["panels"][key]["rows"], fields) == old["panels"][key]["data"]
        assert _without_rows(new["panels"][key]) == _without_rows(old["panels"][key])
    assert new["panels"]["p2"] == old["panels"]["p2"]
    assert new["meta"] == old["meta"]


def test_live_tip_appends_match_the_old_panel_rows(monkeypatch):
    limit = 60
    monkeypatch.setattr(panels, "TICK_PLOT_MAX_POINTS", limit)
    monkeypatch.setattr(panels, "COLUMN_TRIM_CHUNK", 8)
    frame, path = _frame(150)
    old = build_dashboard_payload(frame, path, {}, columnar=False)
    new = build_dashboard_payload(frame, path, {})
    start = frame.index.max()
    for round_ in range(12):
        points = _live_points(start + pd.Timedelta(seconds=10 * round_), 1 + round_ % 4 * 7, seed=round_)
        for key, fields in PRINT_PANEL_FIELDS.items():
            data = old["panels"][key]["data"]
            data.extend({"x": point["x"], **{field: point.get(field) for field in fields}} for point in points)
            _cap_list(data, limit)
        append_print_rows(new, points, limit=limit)
        columns = new["prints"]
        assert len({len(column) for column in columns.values()}) == 1
        for key, fields in PRINT_PANEL_FIELDS.items():
            assert print_panel_rows(columns, new["panels"][key]["rows"], fields) == old["panels"][key]["data"]
    assert len(new["prints"]["x"]) < 150 + 12 * 22


def test_panel4_keeps_its_own_rows_when_the_path_differs():
    frame, path = _frame(40)
    shifted = path.copy()
    shifted.index = shifted.index + pd.Timedelta(milliseconds=1)
    old = build_dashboard_payload(frame, shifted, {}, columnar=False)
    new = build_dashboard_payload(frame, shifted, {})
    assert "implied_price" not in new["prints"]
    assert "rows" not in new["panels"]["p4"]
    assert new["panels"]["p4"] == old["panels"]["p4"]


def test_empty_session_keeps_the_old_shape():
    payload = build_dashboard_payload(pd.DataFrame(), pd.DataFrame(), {})
    assert "prints" not in payload
    assert set(payload["panels"]) == {"p1", "p2", "p3", "p4"}
    assert all(panel["data"] == [] for panel in payload["panels"].values())


def _banners() -> List[Dict[str, Any]]:
    rows = []
    for i, state in enumerate(STATE_STYLE):
        rows.append(_payload(
            "READY", state, d=1.5 + i, a=0.75 * i, b=-0.25 * i,
            beta_ref=1e-6, beta_display=-0.4, as_of="2026-10-23T10:44:00-04:00",
        ))
    rows.append(_payload("READY", "UNCLASSIFIED", d=2.0, a=1.0, b=1.0, as_of="2026-10-23T01:10:00-04:00"))
    rows.append(_payload("WARMING", "WARMING", reason="Waiting for a 5-minute window.", as_of="2026-10-23T09:31:00-04:00"))
    rows.append(_payload("UNAVAILABLE", "UNAVAILABLE", reason="Live feed disconnected."))
    return rows


def test_compact_banner_keeps_every_field_the_page_cannot_rebuild():
    for banner in _banners():
        row = compact_banner({**banner, "x": "2026-10-23T10:44:00"})
        assert row["state"] == banner["state"] and row["status"] == banner["status"]
        assert row["as_of"] == banner["as_of"]
        assert row["window_min"] == banner["parameters"]["window_min"]
        assert row.get("contribution_line") == banner["contribution_line"]
        assert row.get("reason") == banner["reason"]
        assert row["x"] == "2026-10-23T10:44:00"


def test_wire_point_drops_the_full_banners():
    banner = _banners()[0]
    point = {"x": "2026-10-23T10:44:00", "price": 110.0, "total_aggress": 5.0, "buysell_ratio": 1.0,
             "buy_aggress": 2.0, "sell_aggress": -1.0, "implied_price": 110.01,
             "banner": banner, "hover": {**banner, "x": "2026-10-23T10:44:00"}}
    wire = wire_point(point)
    assert "banner" not in wire and "hover" not in wire
    assert wire["h"] == compact_banner(banner)
    assert len(json.dumps(wire)) < len(json.dumps(banner)) / 2


def test_snapshot_for_a_long_session_stays_under_ten_megabytes():
    n = 70_000
    idx = pd.date_range("2026-10-22T22:00:00Z", periods=n, freq="100ms")
    rng = np.random.default_rng(3)
    frame = pd.DataFrame({
        "total_aggress": np.cumsum(rng.normal(0.0, 1500.0, n)),
        "price": 112.0 + np.cumsum(rng.choice([-1, 0, 1], n)) / 64.0,
        "buy_aggress": rng.integers(0, 400, n).astype(float),
        "sell_aggress": rng.integers(0, 400, n).astype(float),
        "buysell_raw": rng.uniform(-100.0, 100.0, n),
    }, index=idx)
    path = pd.DataFrame({
        "price": frame["price"],
        "implied_price": 112.0 + rng.normal(0.0, 0.05, n),
        "large_residual": False,
    }, index=idx)
    payload = build_dashboard_payload(frame, path, {})
    banner = _banners()[0]
    payload["flow_dominance"] = banner
    payload["flow_dominance_series"] = [
        compact_banner({**banner, "x": panels._to_et_iso(ts)}) for ts in idx[:: n // 4000]
    ]
    size = len(json.dumps(payload, separators=(",", ":")))
    assert size < 10 * 1024 * 1024


def test_live_sources_frozen_for_pmr_are_unchanged():
    v3 = ROOT / "scripts" / "aggression_dashboard_v3"
    head = {
        name: hashlib.sha256((v3 / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in ("banner.py", "beta_ref.py", "core.py")
    }
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not available")
    for name, digest in head.items():
        tagged = subprocess.run(
            [git, "-C", str(ROOT), "show", f"yshare-v3-20261023:scripts/aggression_dashboard_v3/{name}"],
            capture_output=True,
        )
        if tagged.returncode != 0:
            pytest.skip("tag yshare-v3-20261023 not in this checkout")
        assert hashlib.sha256(tagged.stdout.replace(b"\r\n", b"\n")).hexdigest() == digest, name


def _node() -> str:
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not Path(node).exists():
        pytest.skip("node not available")
    return node


def _js_function(source: str, name: str) -> str:
    match = re.search(rf"^function {name}\(.*?^}}$", source, flags=re.S | re.M)
    assert match, name
    return match.group(0)


def _js_const(source: str, name: str) -> str:
    match = re.search(rf"^const {name} = .*?;$", source, flags=re.S | re.M)
    assert match, name
    return match.group(0)


def _run_js(body: str, payload: Any) -> Any:
    source = DASHBOARD_JS.read_text(encoding="utf-8")
    script = "\n".join([
        _js_const(source, "LABEL_TEXT"),
        _js_const(source, "PRINT_PANEL_FIELDS"),
        "let stateStyles = null;",
        _js_function(source, "expandBanner"),
        _js_function(source, "expandPrintColumns"),
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));",
        body,
    ])
    result = subprocess.run(
        [_node(), "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_page_expands_compact_hover_rows_to_the_old_hover_fields():
    banners = _banners()
    expanded = _run_js(
        "stateStyles = input.styles;"
        "process.stdout.write(JSON.stringify(input.rows.map(expandBanner)));",
        {"styles": state_styles_payload(), "rows": [compact_banner(row) for row in banners]},
    )
    for banner, row in zip(banners, expanded):
        for field in HOVER_FIELDS:
            assert row[field] == banner[field], (banner["state"], field)
        assert row["parameters"]["window_min"] == banner["parameters"]["window_min"]


def test_page_rebuilds_the_old_panel_rows_from_the_columns(monkeypatch):
    monkeypatch.setattr(panels, "TICK_PLOT_MAX_POINTS", 60)
    frame, path = _frame(150)
    old = json.loads(json.dumps(build_dashboard_payload(frame, path, {}, columnar=False)))
    new = json.loads(json.dumps(build_dashboard_payload(frame, path, {})))
    rebuilt = _run_js(
        "expandPrintColumns(input);"
        "process.stdout.write(JSON.stringify({p1: input.panels.p1.data, p3: input.panels.p3.data, p4: input.panels.p4.data}));",
        new,
    )
    for key in PRINT_PANEL_FIELDS:
        assert rebuilt[key] == old["panels"][key]["data"], key
