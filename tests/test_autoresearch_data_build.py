"""Synthetic and mocked tests for the research-only Phase-0 TBBO builder."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pytest

from scripts import aggression_autoresearch_data_build as build


def _contract_pair(root: str, code: str = "H6") -> tuple[str, str]:
    return f"{build.CME_ROOTS[root]}{code}", f"{root}{code} Comdty"


def _rows(
    trading_date: str = "2026-03-10",
    *,
    interval_minutes: int = 10,
    contracts: dict[str, str] | None = None,
) -> pd.DataFrame:
    start, end = build.session_bounds(trading_date)
    timestamps = pd.date_range(
        pd.Timestamp(start),
        pd.Timestamp(end) - pd.Timedelta(minutes=1),
        freq=f"{interval_minutes}min",
    )
    selected = contracts or {root: "H6" for root in build.ROOTS}
    rows: list[dict[str, object]] = []
    for root in build.ROOTS:
        cme, bbg = _contract_pair(root, selected[root])
        for number, timestamp in enumerate(timestamps):
            price = 110.0 + (number % 4) / 64
            rows.append(
                {
                    "ts_event": timestamp,
                    "ts_recv": timestamp + pd.Timedelta(microseconds=1),
                    "symbol": cme,
                    "bloomberg_symbol": bbg,
                    "price": price,
                    "size": 2 if root != "TY" else 4,
                    "side": "B" if number % 2 == 0 else "A",
                    "bid_price": price - 1 / 128,
                    "ask_price": price + 1 / 128,
                    "bid_size": 100,
                    "ask_size": 120,
                }
            )
    return pd.DataFrame(rows)


def _requested(code: str = "H6") -> list[build.Contract]:
    return [build.parse_contract(f"{root}{code}") for root in build.ROOTS]  # type: ignore[list-item]


def _selected_clean(frame: pd.DataFrame, trading_date: str = "2026-03-10") -> pd.DataFrame:
    filtered, _ = build.verify_and_filter_symbols(frame, _requested())
    selected, _, _, _ = build.select_front_contracts(
        filtered, trading_date=trading_date
    )
    start, end = build.session_bounds(trading_date)
    return build.clean_tbbo(selected, start, end)


def _empty_duplicate_attribution(trading_date: str) -> dict[str, object]:
    return {
        "trading_date": trading_date,
        "affected_bucket_minutes": [],
        "removed_row_impact": [],
        "removed_exact_rows": 0,
    }


def test_source_has_no_live_dashboard_imports() -> None:
    source = Path(build.__file__).read_text(encoding="utf-8")
    forbidden = "aggression_" + "dashboard"
    assert forbidden not in source
    assert "_live" not in source


def test_session_bounds_are_prior_18_to_17_and_dst_safe() -> None:
    winter_start, winter_end = build.session_bounds("2026-03-06")
    summer_start, summer_end = build.session_bounds("2026-03-10")
    assert winter_start.isoformat() == "2026-03-05T23:00:00+00:00"
    assert winter_end.isoformat() == "2026-03-06T22:00:00+00:00"
    assert summer_start.isoformat() == "2026-03-09T22:00:00+00:00"
    assert summer_end.isoformat() == "2026-03-10T21:00:00+00:00"
    assert winter_end - winter_start == timedelta(hours=23)
    assert summer_end - summer_start == timedelta(hours=23)


def test_holiday_and_partial_session_schedule() -> None:
    assert build.expected_session("2026-04-03")["kind"] == "closed"  # Good Friday
    thanksgiving = build.expected_session("2026-11-26")
    assert thanksgiving["kind"] == "partial"
    assert thanksgiving["expected_last_trade_utc"].astimezone(build.EASTERN).hour == 13
    assert build.expected_session("2026-07-18")["kind"] == "closed"


def test_latest_complete_trading_date_skips_active_session_and_weekend() -> None:
    during_monday = pd.Timestamp("2026-03-09T19:00:00Z").to_pydatetime()
    assert build.latest_complete_trading_date(during_monday) == date(2026, 3, 6)
    after_monday = pd.Timestamp("2026-03-09T22:00:00Z").to_pydatetime()
    assert build.latest_complete_trading_date(after_monday) == date(2026, 3, 9)


def test_ignored_symbol_filter_fixture_is_filtered_and_audited() -> None:
    frame = _rows()
    ignored = frame[frame["symbol"] == "ZNH6"].iloc[[0]].copy()
    ignored["symbol"] = "ZNM6"
    ignored["bloomberg_symbol"] = "TYM6 Comdty"
    mixed = pd.concat([frame, ignored], ignore_index=True)
    filtered, audit = build.verify_and_filter_symbols(mixed, _requested())
    assert len(filtered) == len(frame)
    assert audit["ignored_unrequested_rows"] == 1
    assert audit["ignored_unrequested_pairs"] == ["ZNM6|TYM6 Comdty"]


def test_wrong_root_mapping_aborts() -> None:
    frame = _rows().iloc[[0]].copy()
    frame["bloomberg_symbol"] = "TYH6 Comdty"
    with pytest.raises(build.BuildError, match="mapping mismatch"):
        build.verify_and_filter_symbols(frame, _requested())


def test_seed_front_selection_is_noneligible_bootstrap() -> None:
    h = _rows(interval_minutes=60)
    m = _rows(interval_minutes=60, contracts={root: "M6" for root in build.ROOTS})
    m["size"] = np.where(m["symbol"].str.startswith("ZN"), 20, 1)
    all_requested = _requested("H6") + _requested("M6")
    filtered, _ = build.verify_and_filter_symbols(pd.concat([h, m]), all_requested)
    selected, audit, current_audit, selection = build.select_front_contracts(
        filtered, trading_date="2026-03-10"
    )
    winners = {item["root"]: item["contract"] for item in audit}
    assert winners["TY"] == "TYM6"
    assert winners["FV"] == "FVH6"  # equal total volume, earlier expiry wins
    assert set(selected[selected["dashboard_symbol"] == "TY"]["contract"]) == {"TYM6"}
    assert all(item["rule"] == build.FRONT_RULE for item in audit)
    assert current_audit
    assert selection["mode"] == "bootstrap_same_session"
    assert selection["source_date"] == "2026-03-10"
    assert not selection["research_eligible"]


def test_roll_transition_uses_prior_session_volumes() -> None:
    prior_h = _rows(interval_minutes=60)
    prior_m = _rows(
        interval_minutes=60, contracts={root: "M6" for root in build.ROOTS}
    )
    prior_m["size"] = np.where(prior_m["symbol"].str.startswith("ZN"), 20, 1)
    requested = _requested("H6") + _requested("M6")
    prior_filtered, _ = build.verify_and_filter_symbols(
        pd.concat([prior_h, prior_m]), requested
    )
    prior_audit = build.contract_volume_audit(prior_filtered)
    reference = {
        "trading_date": "2026-03-09",
        "front_candidate_audit": prior_audit,
        "reference_sha256": build._reference_hash("2026-03-09", prior_audit),
    }

    current_h = _rows(interval_minutes=60)
    current_m = _rows(
        interval_minutes=60, contracts={root: "M6" for root in build.ROOTS}
    )
    current_h["size"] = 100  # same-session volume must not change prior choice
    current_filtered, _ = build.verify_and_filter_symbols(
        pd.concat([current_h, current_m]), requested
    )
    selected, audit, _, selection = build.select_front_contracts(
        current_filtered, trading_date="2026-03-10", prior_reference=reference
    )
    winners = {item["root"]: item["contract"] for item in audit}
    assert winners["TY"] == "TYM6"
    assert set(selected[selected["dashboard_symbol"] == "TY"]["contract"]) == {"TYM6"}
    assert selection["mode"] == "causal_prior_session"
    assert selection["source_date"] == "2026-03-09"
    assert selection["research_eligible"]
    assert selection["prior_volume_table"] == prior_audit


def test_missing_prior_selected_contract_fails_without_fallback() -> None:
    prior_h = _rows(interval_minutes=60)
    prior_m = _rows(
        interval_minutes=60, contracts={root: "M6" for root in build.ROOTS}
    )
    prior_m["size"] = 100
    requested = _requested("H6") + _requested("M6")
    prior_filtered, _ = build.verify_and_filter_symbols(
        pd.concat([prior_h, prior_m]), requested
    )
    prior_audit = build.contract_volume_audit(prior_filtered)
    reference = {
        "trading_date": "2026-03-09",
        "front_candidate_audit": prior_audit,
    }
    current_filtered, _ = build.verify_and_filter_symbols(_rows(), requested)
    with pytest.raises(build.BuildError, match="Prior-selected contract .* absent"):
        build.select_front_contracts(
            current_filtered, trading_date="2026-03-10", prior_reference=reference
        )


def test_small_exact_duplication_is_removed_and_coverage_passes() -> None:
    clean = _selected_clean(_rows())
    clean["instrument_id"] = clean["symbol"]
    duplicated = pd.concat([clean, clean.iloc[[0]]], ignore_index=True)
    deduped, duplicate_info = build.deduplicate_trade_records(duplicated)
    report = build.coverage_report(deduped, "2026-03-10", duplicate_info)
    assert duplicate_info["pre_dedup_rows"] == len(duplicated)
    assert duplicate_info["post_dedup_rows"] == len(clean)
    assert duplicate_info["exact_duplicate_rows"] == 2
    assert duplicate_info["exact_duplicate_groups"] == 1
    assert duplicate_info["removed_exact_rows"] == 1
    assert duplicate_info["removed_exact_fraction"] == pytest.approx(1 / len(duplicated))
    assert duplicate_info["exact_duplicate_row_fraction"] < 0.01
    assert "ts_recv" in duplicate_info["identity_keys"]
    assert "instrument_id" in duplicate_info["identity_keys"]
    assert duplicate_info["conflicting_identity_groups"] == 0
    assert report["ok"]


def test_conflicting_duplicate_identity_blocks_coverage() -> None:
    clean = _selected_clean(_rows())
    clean["sequence"] = np.arange(len(clean))
    conflict = clean.iloc[[0]].copy()
    conflict["price"] += 1 / 64
    combined = pd.concat([clean, conflict], ignore_index=True)
    deduped, duplicate_info = build.deduplicate_trade_records(combined)
    report = build.coverage_report(deduped, "2026-03-10", duplicate_info)
    assert duplicate_info["removed_exact_rows"] == 0
    assert duplicate_info["conflicting_identity_groups"] == 1
    assert duplicate_info["conflicting_identity_rows"] == 2
    assert duplicate_info["identity_mode"] == "source_sequence"
    assert "sequence" in duplicate_info["identity_keys"]
    assert not report["ok"]
    assert "conflicting_duplicate_identity" in report["failures"]


def test_fallback_full_record_identity_declares_sequence_limitation() -> None:
    clean = _selected_clean(_rows())
    distinct_trade = clean.iloc[[0]].copy()
    distinct_trade["size"] += 1
    _, distinct_report = build.deduplicate_trade_records(
        pd.concat([clean, distinct_trade], ignore_index=True)
    )
    assert distinct_report["identity_mode"] == "fallback_full_record_fingerprint"
    assert distinct_report["conflict_detection"] == "unavailable_without_source_sequence"
    assert "Source supplied no sequence" in distinct_report["identity_limitation"]
    assert distinct_report["conflicting_identity_groups"] == 0

    quote_conflict = clean.iloc[[0]].copy()
    quote_conflict["bid_size"] += 10
    _, conflict_report = build.deduplicate_trade_records(
        pd.concat([clean, quote_conflict], ignore_index=True)
    )
    assert conflict_report["conflicting_identity_groups"] == 0
    assert conflict_report["removed_exact_rows"] == 0


def test_excessive_exact_duplication_blocks_coverage() -> None:
    clean = _selected_clean(_rows())
    combined = pd.concat([clean, clean.iloc[:10]], ignore_index=True)
    deduped, duplicate_info = build.deduplicate_trade_records(combined)
    report = build.coverage_report(deduped, "2026-03-10", duplicate_info)
    assert duplicate_info["removed_exact_rows"] == 10
    assert duplicate_info["exact_duplicate_row_fraction"] > 0.01
    assert not report["ok"]
    assert "excessive_exact_duplication" in report["failures"]


def test_build_manifest_preserves_pre_post_duplicate_counts(tmp_path: Path) -> None:
    raw = _rows()
    raw = pd.concat([raw, raw.iloc[[0]]], ignore_index=True)

    def fetcher(**_kwargs):
        return raw, {"parameters": {"symbols": "specific-contracts"}, "attempts": 1}

    manifest = build.build_date(
        date(2026, 3, 10),
        output_dir=tmp_path,
        api_base="http://example.test",
        timeout=1,
        retries=1,
        contracts=_requested(),
        fetcher=fetcher,
        write=False,
    )
    duplicate_info = manifest["coverage"]["duplicates"]
    assert manifest["coverage"]["ok"]
    assert manifest["rows"]["raw_selected_pre_dedup"] == len(raw)
    assert manifest["rows"]["raw_selected"] == len(raw) - 1
    assert duplicate_info["pre_dedup_rows"] == len(raw)
    assert duplicate_info["post_dedup_rows"] == len(raw) - 1
    assert duplicate_info["removed_exact_rows"] == 1
    assert duplicate_info["exact_duplicate_groups"] == 1
    assert build._has_dedup_attribution_schema(
        manifest, date(2026, 3, 10)
    )
    assert not manifest["research_eligible"]
    assert manifest["front_selection"]["mode"] == "bootstrap_same_session"


def test_empty_api_response_becomes_auditable_date_exclusion(tmp_path: Path) -> None:
    def fetcher(**_kwargs):
        return pd.DataFrame(), {"parameters": {}, "attempts": 1}

    manifest = build.build_date(
        date(2026, 3, 10),
        output_dir=tmp_path,
        api_base="http://example.test",
        timeout=1,
        retries=1,
        contracts=_requested(),
        fetcher=fetcher,
        write=False,
    )
    assert manifest["status"] == "failed"
    assert manifest["coverage"]["failures"] == ["empty_api_response"]
    assert manifest["exclusion_reasons"][0]["code"] == "empty_api_response"


def test_gap_check_still_blocks_after_dedup() -> None:
    clean = _selected_clean(_rows())
    gap_frame = clean[
        ~clean["ts_event"].between(
            pd.Timestamp("2026-03-10T05:00:00Z"),
            pd.Timestamp("2026-03-10T06:00:00Z"),
        )
    ]
    gap_report = build.coverage_report(
        gap_frame, "2026-03-10", build.duplicate_report(gap_frame)
    )
    assert not gap_report["ok"]
    assert "excessive_TY_minute_gap" in gap_report["failures"]


def test_feature_formulas_use_local_dv01_and_session_reset() -> None:
    clean = _selected_clean(_rows(interval_minutes=1))
    matrix = build.build_feature_matrix(clean, "2026-03-10", build.DEFAULT_DV01)
    assert list(matrix.columns) == [
        "price", "day_open_price", "target_price_delta", "et_date",
        *build.FEATURE_COLUMNS,
    ]
    assert matrix["agg_cum_daily"].iloc[0] == 0
    assert matrix["minutes_since_open"].iloc[0] == 0
    assert matrix["minutes_since_open"].iloc[-1] == 1379
    assert matrix["TY_agg_5m"].iloc[4] != 0
    assert matrix.index.tz is not None


def test_arrow_decode_rejects_truncated_stream() -> None:
    table = pa.table({"x": [1, 2, 3]})
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    raw = sink.getvalue().to_pybytes()
    decoded = build.decode_tbbo_response(raw, "application/vnd.apache.arrow.stream")
    assert decoded["x"].tolist() == [1, 2, 3]
    with pytest.raises(build.BuildError, match="Incomplete or invalid Arrow"):
        build.decode_tbbo_response(raw[:-8], "application/vnd.apache.arrow.stream")


def test_fetch_uses_plural_symbols_and_specific_contracts() -> None:
    observed: dict[str, object] = {}
    table = pa.Table.from_pandas(_rows().iloc[:4], preserve_index=False)
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    body = sink.getvalue().to_pybytes()

    class Response:
        headers = {
            "Content-Type": "application/vnd.apache.arrow.stream",
            "Content-Length": str(len(body)),
        }

        def __init__(self) -> None:
            self.offset = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int = -1) -> bytes:
            if self.offset >= len(body):
                return b""
            if size < 0:
                size = len(body)
            chunk = body[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    def opener(url: str, timeout: int):
        observed["query"] = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        observed["timeout"] = timeout
        return Response()

    start, end = build.session_bounds("2026-03-10")
    frame, meta = build.fetch_tbbo(
        contracts=_requested(), start=start, end=end,
        api_base="http://example.test", timeout=7, retries=1, opener=opener,
    )
    query = observed["query"]
    assert "symbols" in query
    assert "symbol" not in query
    assert set(query["symbols"][0].split(",")) == {"FVH6", "TUH6", "TYH6", "USH6"}
    assert len(frame) == 4
    assert meta["attempts"] == 1


def test_http_429_honors_retry_after_with_capped_backoff() -> None:
    table = pa.Table.from_pandas(_rows().iloc[:4], preserve_index=False)
    sink = pa.BufferOutputStream()
    with pa_ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    body = sink.getvalue().to_pybytes()
    calls = 0
    sleeps: list[float] = []

    class Response:
        headers = {
            "Content-Type": "application/vnd.apache.arrow.stream",
            "Content-Length": str(len(body)),
        }

        def __init__(self) -> None:
            self.offset = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, size: int = -1) -> bytes:
            if self.offset >= len(body):
                return b""
            chunk = body[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    def opener(url: str, timeout: int):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                url, 429, "Too Many Requests", {"Retry-After": "7"}, None
            )
        return Response()

    start, end = build.session_bounds("2026-03-10")
    frame, meta = build.fetch_tbbo(
        contracts=_requested(), start=start, end=end,
        api_base="http://example.test", timeout=7, retries=2,
        opener=opener, sleep_fn=sleeps.append,
    )
    assert len(frame) == 4
    assert calls == 2
    assert sleeps == [7.0]
    assert "429" in meta["failures"][0]


def test_content_length_truncation_is_detected() -> None:
    class Response:
        headers = {"Content-Length": "10"}

        def read(self, _size: int = -1) -> bytes:
            if hasattr(self, "done"):
                return b""
            self.done = True
            return b"short"

    with pytest.raises(build.BuildError, match="Truncated response"):
        build._read_stream_response(Response())


def test_restart_reuses_checksum_valid_shard(monkeypatch, tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-{tmp_path.name}"
    paths = build.shard_paths(output, "2026-03-10")
    try:
        paths["raw"].parent.mkdir(parents=True, exist_ok=True)
        paths["features"].parent.mkdir(parents=True, exist_ok=True)
        paths["raw"].write_bytes(b"raw")
        paths["features"].write_bytes(b"features")
        manifest = {
            "trading_date": "2026-03-10",
            "feature_version": build.FEATURE_VERSION,
            "coverage": {
                "ok": True,
                "duplicates": _empty_duplicate_attribution("2026-03-10"),
            },
            "research_eligible": False,
            "front_candidate_audit": [
                {"root": root, "candidates": [{"contract": f"{root}H6", "session_volume": 1.0}]}
                for root in build.ROOTS
            ],
            "checksums": {
                "raw": build.sha256_file(paths["raw"]),
                "features": build.sha256_file(paths["features"]),
            },
        }
        manifest["front_selection"] = {
            "mode": "bootstrap_same_session",
            "source_date": "2026-03-10",
            "reference_sha256": build._reference_hash(
                "2026-03-10", manifest["front_candidate_audit"]
            ),
        }
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")

        def forbidden_fetch(**_kwargs):
            raise AssertionError("valid shard should be reused")

        result = build.build_date(
            date(2026, 3, 10), output_dir=output, api_base="unused",
            timeout=1, retries=1, fetcher=forbidden_fetch,
        )
        assert result["status"] == "reused"
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_restart_rejects_pre_attribution_phase0_v3_shard(tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-old-schema-{tmp_path.name}"
    paths = build.shard_paths(output, "2026-03-10")
    try:
        paths["raw"].parent.mkdir(parents=True, exist_ok=True)
        paths["features"].parent.mkdir(parents=True, exist_ok=True)
        paths["raw"].write_bytes(b"raw")
        paths["features"].write_bytes(b"features")
        audit = [
            {"root": root, "candidates": [
                {"contract": f"{root}H6", "session_volume": 1.0}
            ]}
            for root in build.ROOTS
        ]
        manifest = {
            "trading_date": "2026-03-10",
            "feature_version": build.FEATURE_VERSION,
            "coverage": {"ok": True, "duplicates": {"removed_exact_rows": 1}},
            "research_eligible": False,
            "front_candidate_audit": audit,
            "front_selection": {
                "mode": "bootstrap_same_session",
                "source_date": "2026-03-10",
                "reference_sha256": build._reference_hash("2026-03-10", audit),
            },
            "checksums": {
                "raw": build.sha256_file(paths["raw"]),
                "features": build.sha256_file(paths["features"]),
            },
        }
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
        assert build.valid_existing_shard(
            paths, trading_date=date(2026, 3, 10), prior_reference=None
        ) is None
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_causal_reuse_is_bound_to_prior_reference(tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-causal-reuse-{tmp_path.name}"
    paths = build.shard_paths(output, "2026-03-10")
    prior_audit = [
        {
            "root": root,
            "candidates": [{"contract": f"{root}H6", "session_volume": 10.0}],
        }
        for root in build.ROOTS
    ]
    reference = {
        "trading_date": "2026-03-09",
        "front_candidate_audit": prior_audit,
        "reference_sha256": build._reference_hash("2026-03-09", prior_audit),
    }
    try:
        paths["raw"].parent.mkdir(parents=True, exist_ok=True)
        paths["features"].parent.mkdir(parents=True, exist_ok=True)
        paths["raw"].write_bytes(b"raw")
        paths["features"].write_bytes(b"features")
        manifest = {
            "trading_date": "2026-03-10",
            "feature_version": build.FEATURE_VERSION,
            "coverage": {
                "ok": True,
                "duplicates": _empty_duplicate_attribution("2026-03-10"),
            },
            "research_eligible": True,
            "front_candidate_audit": prior_audit,
            "front_selection": {
                "mode": "causal_prior_session",
                "source_date": "2026-03-09",
                "reference_sha256": reference["reference_sha256"],
            },
            "checksums": {
                "raw": build.sha256_file(paths["raw"]),
                "features": build.sha256_file(paths["features"]),
            },
        }
        paths["manifest"].parent.mkdir(parents=True, exist_ok=True)
        paths["manifest"].write_text(json.dumps(manifest), encoding="utf-8")
        assert build.valid_existing_shard(
            paths, trading_date=date(2026, 3, 10), prior_reference=reference
        )
        changed = {
            **reference,
            "reference_sha256": "0" * 64,
        }
        assert build.valid_existing_shard(
            paths, trading_date=date(2026, 3, 10), prior_reference=changed
        ) is None
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_parity_declares_tolerances_and_expected_reset(tmp_path: Path) -> None:
    timestamps = pd.date_range("2026-03-10T12:00:00Z", periods=40, freq="1min")
    values = {
        "bucket_ts": timestamps,
        "et_date": "2026-03-10",
        **{feature: np.arange(40, dtype=float) for feature in build.PARITY_TOLERANCES},
        "agg_cum_daily": np.arange(40, dtype=float) + 100,
    }
    legacy = pd.DataFrame(values)
    legacy_path = tmp_path / "legacy.csv"
    legacy.to_csv(legacy_path, index=False)
    fresh = legacy.copy()
    fresh["agg_cum_daily"] += 500
    fresh.set_index("bucket_ts", inplace=True)
    report = build.parity_report(fresh, legacy_path)
    assert report["ok"]
    assert report["features"]["agg_cum_daily"]["ok"]
    assert "18:00 ET reset" in report["expected_reset_differences"]["agg_cum_daily"]


def test_parity_uses_fixed_quote_revision_bounds(tmp_path: Path) -> None:
    fresh, legacy, _ = _synthetic_parity_pair()
    row = 65
    legacy.loc[row, ["size_imbalance_last", "microprice_dev_last", "displayed_depth_last"]] += [
        0.009, 0.00015, 9.0,
    ]
    legacy_path = tmp_path / "legacy-quote-revision.csv"
    legacy.to_csv(legacy_path, index=False)
    report = build.parity_report(fresh.set_index("bucket_ts"), legacy_path)
    assert report["ok"]


def test_root_flow_warmup_is_excluded_but_later_difference_blocks(tmp_path: Path) -> None:
    fresh, legacy, _ = _synthetic_parity_pair()
    legacy.loc[0:4, "TY_agg_5m"] += 100
    legacy_path = tmp_path / "legacy-root-warmup.csv"
    legacy.to_csv(legacy_path, index=False)
    assert build.parity_report(fresh.set_index("bucket_ts"), legacy_path)["ok"]
    legacy.loc[35, "TY_agg_5m"] += 100
    legacy.to_csv(legacy_path, index=False)
    assert not build.parity_report(fresh.set_index("bucket_ts"), legacy_path)["ok"]


def _synthetic_parity_pair() -> tuple[pd.DataFrame, pd.DataFrame, pd.DatetimeIndex]:
    timestamps = pd.date_range("2026-03-10T12:00:00Z", periods=100, freq="1min")
    values = {
        "bucket_ts": timestamps,
        "et_date": "2026-03-10",
        **{feature: np.arange(100, dtype=float) for feature in build.PARITY_TOLERANCES},
        "agg_cum_daily": np.arange(100, dtype=float),
    }
    fresh = pd.DataFrame(values)
    return fresh, fresh.copy(), timestamps


def test_parity_explains_only_declared_dedup_influence(tmp_path: Path) -> None:
    fresh, legacy, timestamps = _synthetic_parity_pair()
    affected = 65
    legacy.loc[affected, ["agg_1m", "trade_intensity", "vol_per_min"]] += [4, 1, 4]
    legacy.loc[affected:affected + 4, ["agg_5m", "FV_agg_5m"]] += 4
    legacy.loc[affected:affected + 29, "agg_30m"] += 4
    legacy.loc[affected:, "buysell_ratio"] += 0.25
    legacy.loc[affected:, "agg_cum_daily"] += 4
    legacy.loc[affected:affected + 1, "agg_accel"] += 4
    legacy_path = tmp_path / "legacy-dedup.csv"
    legacy.to_csv(legacy_path, index=False)
    attribution = [{
        "trading_date": "2026-03-10",
        "removed_row_impact": [{
            "bucket_ts": timestamps[affected], "root": "FV", "removed_rows": 1,
        }],
    }]
    report = build.parity_report(
        fresh.set_index("bucket_ts"), legacy_path, attribution
    )
    assert report["ok"]
    assert report["features"]["agg_1m"]["explained_dedup_failures"] == 1
    assert report["features"]["agg_5m"]["explained_dedup_failures"] == 5
    assert report["features"]["agg_30m"]["explained_dedup_failures"] == 30
    assert report["features"]["buysell_ratio"]["explained_dedup_failures"] == 35
    assert report["features"]["agg_cum_daily"]["explained_dedup_failures"] == 35


@pytest.mark.parametrize(
    ("feature", "row", "legacy_count_delta"),
    [
        ("agg_5m", 70, 0),
        ("price", 65, 0),
        ("agg_1m", 65, -1),
    ],
)
def test_parity_unexplained_or_bad_count_mismatch_blocks(
    tmp_path: Path, feature: str, row: int, legacy_count_delta: int
) -> None:
    fresh, legacy, timestamps = _synthetic_parity_pair()
    legacy.loc[row, feature] += 1
    legacy.loc[row, "trade_intensity"] += legacy_count_delta
    legacy_path = tmp_path / f"legacy-block-{feature}.csv"
    legacy.to_csv(legacy_path, index=False)
    report = build.parity_report(
        fresh.set_index("bucket_ts"),
        legacy_path,
        [{
            "trading_date": "2026-03-10",
            "removed_row_impact": [{
                "bucket_ts": timestamps[65], "root": "FV", "removed_rows": 1,
            }],
        }],
    )
    assert not report["ok"]
    assert report["features"][feature]["unexplained_failures"] >= 1


def test_publication_blocks_failed_dates_and_bad_parity(tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-publish-{tmp_path.name}"
    try:
        failed = [{"trading_date": "2026-03-10", "status": "failed", "coverage": {"ok": False}}]
        with pytest.raises(build.BuildError, match="no causally eligible"):
            build.publish_version(
                output, failed, parity={"ok": True}, version="vfailed",
                min_eligible_dates=1,
            )

        complete = [{
            "trading_date": "2026-03-10", "status": "built", "coverage": {"ok": True},
        }]
        with pytest.raises(build.BuildError, match="parity gate"):
            build.publish_version(output, complete, parity=None, version="vnoparity")
        with pytest.raises(build.BuildError, match="parity"):
            build.publish_version(output, complete, parity={"ok": False}, version="vbadparity")
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_publication_is_versioned_and_immutable(tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-version-{tmp_path.name}"
    feature_path = output / "shards" / "features" / "2026-03-10.parquet"
    feature_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"et_date": ["2026-03-10"], "price": [110.0]},
        index=pd.DatetimeIndex(["2026-03-10T12:00:00Z"], name="bucket_ts"),
    ).to_parquet(feature_path)
    item = {
        "trading_date": "2026-03-10",
        "status": "built",
        "coverage": {"ok": True},
        "research_eligible": True,
        "artifacts": {"features": str(feature_path), "manifest": "date.json"},
        "checksums": {"features": build.sha256_file(feature_path)},
    }
    try:
        result = build.publish_version(
            output, [item], parity={"ok": True}, version="vsynthetic",
            min_eligible_dates=1,
        )
        target = Path(result["path"])
        assert target.joinpath("tbbo_feature_matrix.parquet").exists()
        assert target.joinpath("manifest.json").exists()
        with pytest.raises(build.BuildError, match="already exists"):
            build.publish_version(
                output, [item], parity={"ok": True}, version="vsynthetic",
                min_eligible_dates=1,
            )
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_publication_enforces_minimum_eligible_dates(tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-min-dates-{tmp_path.name}"
    feature_path = tmp_path / "feature.parquet"
    pd.DataFrame(
        {"et_date": ["2026-03-10"], "price": [110.0]},
        index=pd.DatetimeIndex(["2026-03-10T12:00:00Z"], name="bucket_ts"),
    ).to_parquet(feature_path)
    item = {
        "trading_date": "2026-03-10", "status": "built",
        "coverage": {"ok": True}, "research_eligible": True,
        "artifacts": {"features": str(feature_path)},
        "checksums": {"features": build.sha256_file(feature_path)},
    }
    with pytest.raises(build.BuildError, match="requires at least 2"):
        build.publish_version(
            output, [item], parity={"ok": True}, version="vmin",
            min_eligible_dates=2,
        )


def test_publication_excludes_noneligible_seed(tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-seed-{tmp_path.name}"
    seed_path = output / "shards" / "features" / "2026-03-09.parquet"
    causal_path = output / "shards" / "features" / "2026-03-10.parquet"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"et_date": ["2026-03-09"], "price": [109.0]},
        index=pd.DatetimeIndex(["2026-03-09T12:00:00Z"], name="bucket_ts"),
    ).to_parquet(seed_path)
    pd.DataFrame(
        {"et_date": ["2026-03-10"], "price": [110.0]},
        index=pd.DatetimeIndex(["2026-03-10T12:00:00Z"], name="bucket_ts"),
    ).to_parquet(causal_path)
    manifests = [
        {
            "trading_date": "2026-03-09", "status": "built",
            "coverage": {"ok": True}, "research_eligible": False,
            "artifacts": {"features": str(seed_path), "manifest": "seed.json"},
            "checksums": {"features": build.sha256_file(seed_path)},
        },
        {
            "trading_date": "2026-03-10", "status": "built",
            "coverage": {"ok": True}, "research_eligible": True,
            "artifacts": {"features": str(causal_path), "manifest": "causal.json"},
            "checksums": {"features": build.sha256_file(causal_path)},
        },
        {
            "trading_date": "2026-03-11", "status": "failed",
            "coverage": {
                "ok": False, "failures": ["excessive_exact_duplication"],
                "reason": "33.16% exact duplicates",
            },
            "research_eligible": True,
            "artifacts": {"manifest": "failed.json"},
            "exclusion_reasons": [{
                "code": "excessive_exact_duplication",
                "detail": "33.16% exact duplicates",
            }],
        },
        {
            "trading_date": "2026-03-12", "status": "expected_closed",
            "coverage": {
                "ok": True, "schedule": {"reason": "holiday"}, "rows": 0,
            },
            "research_eligible": False,
            "artifacts": {"manifest": "closed.json"},
        },
    ]
    try:
        result = build.publish_version(
            output, manifests, parity={"ok": True}, version="vseedexcluded",
            min_eligible_dates=1,
        )
        published = pd.read_parquet(
            Path(result["path"]) / "tbbo_feature_matrix.parquet"
        )
        assert published["et_date"].tolist() == ["2026-03-10"]
        assert result["research_dates"] == ["2026-03-10"]
        assert result["excluded_noneligible_dates"] == [
            "2026-03-09", "2026-03-11", "2026-03-12"
        ]
        exclusions = {
            item["trading_date"]: item["reasons"] for item in result["excluded_dates"]
        }
        assert exclusions["2026-03-09"][0]["code"] == "noneligible_bootstrap"
        assert exclusions["2026-03-11"][0]["code"] == "excessive_exact_duplication"
        assert exclusions["2026-03-12"][0]["code"] == "expected_closed"
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_output_allowlist_blocks_live_or_external_paths(tmp_path: Path) -> None:
    with pytest.raises(build.BuildError, match="research-only root"):
        build._safe_output_dir(tmp_path / "outside")
    with pytest.raises(build.BuildError, match="research-only root"):
        build._safe_output_dir(build.LEGACY_MATRIX.parent)


def test_range_continues_failed_day_with_last_good_prior(
    monkeypatch, tmp_path: Path
) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-range-{tmp_path.name}"
    prior_audit = [
        {
            "root": root,
            "candidates": [{"contract": f"{root}H6", "session_volume": 10.0}],
        }
        for root in build.ROOTS
    ]
    initial_reference = {
        "trading_date": "2026-03-08",
        "front_candidate_audit": prior_audit,
        "reference_sha256": build._reference_hash("2026-03-08", prior_audit),
    }
    seen_prior_dates: list[str | None] = []
    pacing_sleeps: list[float] = []

    def fake_build_date(trading_date: date, **kwargs):
        prior = kwargs["prior_reference"]
        seen_prior_dates.append(prior["trading_date"] if prior else None)
        if trading_date == date(2026, 3, 9):
            return {
                "trading_date": trading_date.isoformat(), "status": "failed",
                "coverage": {
                    "ok": False, "failures": ["excessive_exact_duplication"],
                },
                "research_eligible": False,
                "exclusion_reasons": [{
                    "code": "excessive_exact_duplication", "detail": "33.16%"
                }],
            }
        return {
            "trading_date": trading_date.isoformat(), "status": "built",
            "coverage": {"ok": True}, "research_eligible": True,
            "front_candidate_audit": prior_audit,
        }

    monkeypatch.setattr(
        build, "load_latest_prior_reference",
        lambda *_args, **_kwargs: initial_reference,
    )
    monkeypatch.setattr(build, "build_date", fake_build_date)
    monkeypatch.setattr(build.time, "sleep", pacing_sleeps.append)
    args = build.parse_args([
        "--start-date", "2026-03-09", "--end-date", "2026-03-10",
        "--request-delay-seconds", "0.25", "--output-dir", str(output),
    ])
    try:
        result = build.run(args)
        assert result["ok"]
        assert len(result["manifests"]) == 2
        assert seen_prior_dates == ["2026-03-08", "2026-03-08"]
        assert pacing_sleeps == [0.25]
        assert result["excluded_dates"][0]["trading_date"] == "2026-03-09"
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_range_propagates_unmanifested_exception(monkeypatch, tmp_path: Path) -> None:
    output = build.CANONICAL_OUTPUT_ROOT / f"pytest-fatal-{tmp_path.name}"
    monkeypatch.setattr(
        build, "load_latest_prior_reference", lambda *_args, **_kwargs: None
    )

    def fatal_build(*_args, **_kwargs):
        raise build.BuildError("HTTP protocol failure without manifest")

    monkeypatch.setattr(build, "build_date", fatal_build)
    args = build.parse_args([
        "--start-date", "2026-03-09", "--end-date", "2026-03-10",
        "--request-delay-seconds", "0", "--output-dir", str(output),
    ])
    try:
        with pytest.raises(build.BuildError, match="without manifest"):
            build.run(args)
    finally:
        if output.exists():
            import shutil
            shutil.rmtree(output)


def test_cli_modes_and_range_validation() -> None:
    one = build.parse_args(["--date", "2026-03-10"])
    assert one.date == "2026-03-10"
    audit = build.parse_args(["--audit-only", "--audit-date", "2026-03-10"])
    assert audit.audit_only
    ranged = build.parse_args(["--start-date", "2026-03-10", "--end-date", "2026-03-12"])
    assert ranged.start_date == "2026-03-10"
    with pytest.raises(build.BuildError, match="on or after"):
        list(build._date_range(date(2026, 3, 12), date(2026, 3, 10)))
