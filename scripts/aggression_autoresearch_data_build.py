#!/usr/bin/env python3
"""Research-only, restartable Phase-0 TBBO dataset builder.

This module deliberately owns its constants and feature functions.  It does not
import any live aggression application package and publishes only beneath
``workspace/analysis/aggression_autoresearch_data``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as pa_ipc
import pyarrow.parquet as pq


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_OUTPUT_ROOT = (
    PROJECT_ROOT / "workspace" / "analysis" / "aggression_autoresearch_data"
)
LEGACY_MATRIX = (
    PROJECT_ROOT
    / "workspace"
    / "analysis"
    / "aggression_v2_tbbo"
    / "tbbo_feature_matrix.csv"
)
DV01_PATH = PROJECT_ROOT / "config" / "aggression_dv01.json"
DEFAULT_API_BASE = "http://100.79.30.21:8001"
EASTERN = ZoneInfo("America/New_York")
ROOTS = ("FV", "TU", "TY", "US")
CME_ROOTS = {"FV": "ZF", "TU": "ZT", "TY": "ZN", "US": "ZB"}
MONTH_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}
CODE_MONTHS = {value: key for key, value in MONTH_CODES.items()}
DEFAULT_DV01 = {"FV": 42.0, "TU": 36.0, "TY": 85.0, "US": 125.0}
PRICE_ROOT = "TY"
LARGE_TRADE_SIZE = 100
BUCKET = "1min"
MAX_TY_GAP_MINUTES = 30
START_TOLERANCE_MINUTES = 45
END_TOLERANCE_MINUTES = 20
MAX_EXACT_DUPLICATE_ROW_FRACTION = 0.01
DEFAULT_REQUEST_DELAY_SECONDS = 0.5
DEFAULT_MIN_ELIGIBLE_DATES = 20
MAX_429_BACKOFF_SECONDS = 120.0
FEATURE_VERSION = "phase0_v3"
FRONT_RULE = (
    "For each tenor on trading date D, rank explicitly requested quarterly "
    "outrights by summed trade size from the previous complete session D-1; "
    "break ties by earliest expiry then lexical Bloomberg contract. The first "
    "complete seed may bootstrap from same-session volume but is noneligible."
)

FEATURE_COLUMNS = [
    "agg_cum_daily", "agg_1m", "agg_5m", "agg_30m", "agg_rate",
    "agg_accel", "buysell_ratio", "trade_intensity", "vol_per_min",
    "avg_trade_size", "large_trade_share", "realized_vol", "mom_5m",
    "FV_agg_5m", "TU_agg_5m", "TY_agg_5m", "US_agg_5m",
    "spread_last", "spread_mean", "size_imbalance_last",
    "size_imbalance_mean", "microprice_dev_last", "microprice_dev_mean",
    "displayed_depth_last", "displayed_depth_mean", "trade_size_vs_depth",
    "depth_adjusted_signed_flow", "minutes_since_open", "session_sin",
    "session_cos",
]

PARITY_TOLERANCES: dict[str, dict[str, float]] = {
    "price": {"atol": 1e-12, "rtol": 0.0},
    "agg_1m": {"atol": 1e-9, "rtol": 1e-12},
    "agg_5m": {"atol": 1e-9, "rtol": 1e-12},
    "agg_30m": {"atol": 1e-9, "rtol": 1e-12},
    "agg_rate": {"atol": 1e-9, "rtol": 1e-12},
    "agg_accel": {"atol": 1e-9, "rtol": 1e-12},
    "buysell_ratio": {"atol": 1e-8, "rtol": 1e-10},
    "trade_intensity": {"atol": 0.0, "rtol": 0.0},
    "vol_per_min": {"atol": 0.0, "rtol": 0.0},
    "avg_trade_size": {"atol": 1e-10, "rtol": 1e-10},
    "large_trade_share": {"atol": 1e-10, "rtol": 1e-10},
    "realized_vol": {"atol": 1e-12, "rtol": 1e-9},
    "mom_5m": {"atol": 1e-12, "rtol": 0.0},
    "FV_agg_5m": {"atol": 1e-9, "rtol": 1e-12},
    "TU_agg_5m": {"atol": 1e-9, "rtol": 1e-12},
    "TY_agg_5m": {"atol": 1e-9, "rtol": 1e-12},
    "US_agg_5m": {"atol": 1e-9, "rtol": 1e-12},
    "spread_last": {"atol": 1e-12, "rtol": 0.0},
    "spread_mean": {"atol": 1e-12, "rtol": 1e-10},
    # Fixed economic equivalence bounds for sparse historical-vendor revisions:
    # 0.5% of imbalance's full scale, 1/100 TY tick, and 10 contracts of depth.
    "size_imbalance_last": {"atol": 0.01, "rtol": 0.0},
    "size_imbalance_mean": {"atol": 0.01, "rtol": 0.0},
    "microprice_dev_last": {"atol": 0.00015625, "rtol": 0.0},
    "microprice_dev_mean": {"atol": 0.00015625, "rtol": 0.0},
    "displayed_depth_last": {"atol": 10.0, "rtol": 0.0},
    "displayed_depth_mean": {"atol": 10.0, "rtol": 0.0},
    "trade_size_vs_depth": {"atol": 1e-12, "rtol": 1e-10},
    "depth_adjusted_signed_flow": {"atol": 1e-12, "rtol": 1e-10},
}
EXPECTED_RESET_DIFFERENCES = {
    "agg_cum_daily": "18:00 ET reset versus legacy 01:00 ET reset; compare demeaned overlap",
    "day_open_price": "18:00 ET session open versus legacy first post-01:00 trade",
    "target_price_delta": "different declared day-open anchor",
    "minutes_since_open": "minutes are measured from prior-day 18:00 ET",
    "session_sin": "derived from the new minutes-since-open origin",
    "session_cos": "derived from the new minutes-since-open origin",
    "warmup": "first 30 overlapping minutes may differ because the new shard has pre-01:00 history",
}
DEDUP_PARITY_INFLUENCE = {
    "agg_1m": {"kind": "direct", "minutes": 0},
    "agg_5m": {"kind": "rolling", "minutes": 4},
    "agg_30m": {"kind": "rolling", "minutes": 29},
    "agg_rate": {"kind": "direct", "minutes": 0},
    "agg_accel": {"kind": "rolling", "minutes": 1},
    "buysell_ratio": {"kind": "ewm", "minutes": None},
    "trade_intensity": {"kind": "direct", "minutes": 0},
    "vol_per_min": {"kind": "direct", "minutes": 0},
    "avg_trade_size": {"kind": "direct", "minutes": 0},
    "large_trade_share": {"kind": "direct", "minutes": 0},
    "FV_agg_5m": {"kind": "rolling", "minutes": 4, "root": "FV"},
    "TU_agg_5m": {"kind": "rolling", "minutes": 4, "root": "TU"},
    "TY_agg_5m": {"kind": "rolling", "minutes": 4, "root": "TY"},
    "US_agg_5m": {"kind": "rolling", "minutes": 4, "root": "US"},
    "spread_mean": {"kind": "direct", "minutes": 0, "root": "TY"},
    "size_imbalance_mean": {"kind": "direct", "minutes": 0, "root": "TY"},
    "microprice_dev_mean": {"kind": "direct", "minutes": 0, "root": "TY"},
    "displayed_depth_mean": {"kind": "direct", "minutes": 0, "root": "TY"},
    "trade_size_vs_depth": {"kind": "direct", "minutes": 0, "root": "TY"},
    "depth_adjusted_signed_flow": {"kind": "direct", "minutes": 0, "root": "TY"},
    "agg_cum_daily": {"kind": "cumulative", "minutes": None},
}


class BuildError(RuntimeError):
    """A data-integrity or publication-gate failure."""


class DataQualityError(BuildError):
    """An audited date-level failure that may be excluded from a range."""


@dataclass(frozen=True)
class Contract:
    root: str
    month: int
    year: int

    @property
    def bloomberg(self) -> str:
        return f"{self.root}{MONTH_CODES[self.month]}{self.year % 10}"

    @property
    def cme(self) -> str:
        return f"{CME_ROOTS[self.root]}{MONTH_CODES[self.month]}{self.year % 10}"

    @property
    def expiry_key(self) -> tuple[int, int]:
        return self.year, self.month


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    raise TypeError(f"Cannot JSON encode {type(value).__name__}")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
    os.replace(temp, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_output_dir(path: Path) -> Path:
    root = CANONICAL_OUTPUT_ROOT.resolve()
    candidate = path.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BuildError(f"Output must be under research-only root {root}: {candidate}") from exc
    if candidate == LEGACY_MATRIX.resolve() or LEGACY_MATRIX.resolve() in candidate.parents:
        raise BuildError("The existing live-consumed TBBO matrix is immutable")
    cursor = candidate
    while cursor != root.parent:
        if cursor.exists() and cursor.is_symlink():
            raise BuildError(f"Symlink/junction output path is not allowed: {cursor}")
        if cursor == root:
            break
        cursor = cursor.parent
    return candidate


def classify_aggressor(side: Any) -> int:
    value = str(side).strip().upper()
    if value in {"B", "BID"}:
        return 1
    if value in {"A", "ASK"}:
        return -1
    return 0


def load_dv01(path: Path = DV01_PATH) -> tuple[dict[str, float], dict[str, Any]]:
    values = dict(DEFAULT_DV01)
    source = "built_in"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        unknown = sorted(set(raw) - set(ROOTS))
        if unknown:
            raise BuildError(f"Unknown DV01 roots: {unknown}")
        values.update({root: float(value) for root, value in raw.items()})
        source = str(path.resolve())
    if any(not np.isfinite(value) or value <= 0 for value in values.values()):
        raise BuildError("All DV01 values must be finite and positive")
    return values, {
        "values": values,
        "source": source,
        "sha256": sha256_file(path) if path.exists() else None,
    }


def session_bounds(trading_date: date | str) -> tuple[datetime, datetime]:
    value = date.fromisoformat(trading_date) if isinstance(trading_date, str) else trading_date
    start_et = datetime.combine(value - timedelta(days=1), dt_time(18), tzinfo=EASTERN)
    end_et = datetime.combine(value, dt_time(17), tzinfo=EASTERN)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    cursor = (date(year, month, 1) + pd.offsets.MonthEnd(0)).date()
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _good_friday(year: int) -> date:
    # Anonymous Gregorian computus, then subtract two days.
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    easter = date(year, month, (h + l - 7 * m + 114) % 31 + 1)
    return easter - timedelta(days=2)


def expected_session(trading_date: date | str) -> dict[str, Any]:
    value = date.fromisoformat(trading_date) if isinstance(trading_date, str) else trading_date
    start, end = session_bounds(value)
    if value.weekday() >= 5:
        return {"kind": "closed", "reason": "weekend", "start_utc": start, "end_utc": end}
    if value == _good_friday(value.year):
        return {"kind": "closed", "reason": "Good Friday", "start_utc": start, "end_utc": end}
    holidays = {
        _observed(date(value.year, 1, 1)): "New Year",
        _nth_weekday(value.year, 1, 0, 3): "MLK Day",
        _nth_weekday(value.year, 2, 0, 3): "Presidents Day",
        _last_weekday(value.year, 5, 0): "Memorial Day",
        _observed(date(value.year, 6, 19)): "Juneteenth",
        _observed(date(value.year, 7, 4)): "Independence Day",
        _nth_weekday(value.year, 9, 0, 1): "Labor Day",
        _nth_weekday(value.year, 11, 3, 4): "Thanksgiving",
        _observed(date(value.year, 12, 25)): "Christmas",
    }
    if value in holidays:
        close = datetime.combine(value, dt_time(13), tzinfo=EASTERN).astimezone(timezone.utc)
        return {
            "kind": "partial", "reason": holidays[value], "start_utc": start,
            "end_utc": end, "expected_last_trade_utc": close,
        }
    return {
        "kind": "full", "reason": None, "start_utc": start, "end_utc": end,
        "expected_last_trade_utc": end,
    }


def candidate_contracts(trading_date: date | str, quarters_each_side: int = 2) -> list[Contract]:
    value = date.fromisoformat(trading_date) if isinstance(trading_date, str) else trading_date
    quarter_index = value.year * 4 + (value.month - 1) // 3
    contracts: list[Contract] = []
    for root in ROOTS:
        for offset in range(-quarters_each_side, quarters_each_side + 1):
            index = quarter_index + offset
            year, quarter = divmod(index, 4)
            contracts.append(Contract(root, (quarter + 1) * 3, year))
    return contracts


_CONTRACT_RE = re.compile(r"^(?P<root>[A-Z]{2})(?P<month>[HMUZ])(?P<year>\d)(?:\s+COMDTY)?$")


def parse_contract(value: Any) -> Contract | None:
    cleaned = str(value or "").strip().upper()
    match = _CONTRACT_RE.match(cleaned)
    if not match:
        return None
    raw_root = match.group("root")
    reverse_cme = {cme: root for root, cme in CME_ROOTS.items()}
    root = raw_root if raw_root in ROOTS else reverse_cme.get(raw_root)
    if root is None:
        return None
    digit = int(match.group("year"))
    # Phase-0 requested span is 2024 onward; nearest decade interpretation is auditable.
    now_year = datetime.now(timezone.utc).year
    decade = (now_year // 10) * 10
    year = decade + digit
    if year < now_year - 6:
        year += 10
    if year > now_year + 6:
        year -= 10
    return Contract(root, CODE_MONTHS[match.group("month")], year)


def verify_and_filter_symbols(
    frame: pd.DataFrame, requested: Sequence[Contract]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    required = {"symbol", "bloomberg_symbol"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise BuildError(f"TBBO response lacks symbol-integrity columns: {missing}")
    allowed = {(c.root, c.month, c.year) for c in requested}
    keep: list[bool] = []
    ignored: set[str] = set()
    mappings: dict[str, set[str]] = {}
    mismatches: list[dict[str, str]] = []
    canonical: list[str | None] = []
    roots: list[str | None] = []
    for _, row in frame.iterrows():
        cme = parse_contract(row["symbol"])
        bbg = parse_contract(row["bloomberg_symbol"])
        if cme is None or bbg is None or cme != bbg:
            mismatches.append({
                "symbol": str(row["symbol"]),
                "bloomberg_symbol": str(row["bloomberg_symbol"]),
            })
            keep.append(False)
            canonical.append(None)
            roots.append(None)
            continue
        key = (cme.root, cme.month, cme.year)
        mappings.setdefault(cme.cme, set()).add(bbg.bloomberg)
        is_requested = key in allowed
        if not is_requested:
            ignored.add(f"{row['symbol']}|{row['bloomberg_symbol']}")
        keep.append(is_requested)
        canonical.append(cme.bloomberg if is_requested else None)
        roots.append(cme.root if is_requested else None)
    if mismatches:
        raise BuildError(f"Returned symbol/root mapping mismatch: {mismatches[:5]}")
    ambiguous = {key: sorted(values) for key, values in mappings.items() if len(values) != 1}
    if ambiguous:
        raise BuildError(f"Returned symbols do not map uniquely: {ambiguous}")
    out = frame.loc[keep].copy()
    out["contract"] = [value for value, flag in zip(canonical, keep) if flag]
    out["dashboard_symbol"] = [value for value, flag in zip(roots, keep) if flag]
    return out, {
        "requested": sorted(c.bloomberg for c in requested),
        "returned_pairs": {
            key: sorted(values) for key, values in sorted(mappings.items())
        },
        "ignored_unrequested_pairs": sorted(ignored),
        "ignored_unrequested_rows": int(len(frame) - len(out)),
        "retained_rows": int(len(out)),
    }


def contract_volume_audit(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    work = frame.copy()
    work["size"] = pd.to_numeric(work["size"], errors="coerce")
    volumes = (
        work.groupby(["dashboard_symbol", "contract"], as_index=False)["size"]
        .sum().rename(columns={"size": "session_volume"})
    )
    audit: list[dict[str, Any]] = []
    for root, group in volumes.groupby("dashboard_symbol", sort=True):
        ranked = group.copy()
        ranked["_expiry"] = ranked["contract"].map(
            lambda value: parse_contract(value).expiry_key if parse_contract(value) else (9999, 12)
        )
        ranked.sort_values(
            ["session_volume", "_expiry", "contract"],
            ascending=[False, True, True],
            inplace=True,
        )
        audit.append({
            "root": str(root),
            "candidates": [
                {"contract": str(row["contract"]), "session_volume": float(row["session_volume"])}
                for _, row in ranked.iterrows()
            ],
        })
    return audit


def _reference_hash(trading_date: str, audit: Sequence[Mapping[str, Any]]) -> str:
    payload = {"trading_date": trading_date, "front_candidate_audit": list(audit)}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def front_reference_from_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    trading_date = str(manifest.get("trading_date") or "")
    audit = manifest.get("front_candidate_audit")
    if (
        not trading_date
        or not isinstance(audit, list)
        or not manifest.get("coverage", {}).get("ok")
        or manifest.get("status") == "expected_closed"
    ):
        raise BuildError("Prior manifest is not a complete front-volume reference")
    return {
        "trading_date": trading_date,
        "front_candidate_audit": audit,
        "reference_sha256": _reference_hash(trading_date, audit),
    }


def select_front_contracts(
    frame: pd.DataFrame,
    *,
    trading_date: date | str | None = None,
    prior_reference: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if frame.empty:
        return frame.copy(), [], [], {
            "mode": "bootstrap_same_session", "source_date": None,
            "research_eligible": False, "rule": FRONT_RULE,
        }
    work = frame.copy()
    current_audit = contract_volume_audit(frame)
    current_by_root = {entry["root"]: entry["candidates"] for entry in current_audit}
    if prior_reference is None:
        source_date = str(trading_date) if trading_date is not None else None
        selection_audit = current_audit
        mode = "bootstrap_same_session"
        research_eligible = False
        reference_sha256 = (
            _reference_hash(source_date, current_audit) if source_date else None
        )
    else:
        source_date = str(prior_reference.get("trading_date") or "")
        selection_audit = prior_reference.get("front_candidate_audit")
        if not source_date or not isinstance(selection_audit, list):
            raise BuildError("Prior front reference lacks date/candidate volumes")
        if trading_date is not None and source_date >= str(trading_date):
            raise BuildError("Prior front reference must predate the trading date")
        mode = "causal_prior_session"
        research_eligible = True
        reference_sha256 = _reference_hash(source_date, selection_audit)
        supplied_hash = prior_reference.get("reference_sha256")
        if supplied_hash and str(supplied_hash) != reference_sha256:
            raise BuildError("Prior front reference hash does not match its volume table")

    prior_by_root = {entry["root"]: entry["candidates"] for entry in selection_audit}
    chosen: set[tuple[str, str]] = set()
    selected: list[dict[str, Any]] = []
    for root in ROOTS:
        candidates = prior_by_root.get(root) or []
        if not candidates:
            raise DataQualityError(f"Selection reference lacks candidates for {root}")
        winner = candidates[0]
        contract = str(winner["contract"])
        current_contracts = {
            str(item["contract"]) for item in current_by_root.get(root, [])
        }
        if contract not in current_contracts:
            raise DataQualityError(
                f"Prior-selected contract {contract} for {root} is absent on {trading_date}"
            )
        chosen.add((root, contract))
        selected.append({
            "root": root,
            "contract": contract,
            "selection_volume": float(winner["session_volume"]),
            "selection_source_date": source_date,
            "selection_mode": mode,
            "prior_candidates": candidates,
            "current_candidates": current_by_root.get(root, []),
            "rule": FRONT_RULE,
        })
    pairs = pd.MultiIndex.from_frame(work[["dashboard_symbol", "contract"]])
    mask = pairs.isin(pd.MultiIndex.from_tuples(sorted(chosen)))
    out = work.loc[mask].copy().sort_values("ts_event")
    selection_meta = {
        "mode": mode,
        "source_date": source_date,
        "reference_sha256": reference_sha256,
        "research_eligible": research_eligible,
        "rule": FRONT_RULE,
        "prior_volume_table": selection_audit,
    }
    return out, selected, current_audit, selection_meta


def _response_headers(response: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in response.headers.items()}


def _read_stream_response(response: Any) -> tuple[bytes, dict[str, str]]:
    headers = _response_headers(response)
    chunks: list[bytes] = []
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    raw = b"".join(chunks)
    length = headers.get("content-length")
    if length and int(length) != len(raw):
        raise BuildError(f"Truncated response: Content-Length={length}, received={len(raw)}")
    return raw, headers


def decode_tbbo_response(raw: bytes, content_type: str) -> pd.DataFrame:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or "data" not in payload:
            raise BuildError("Unexpected JSON response envelope")
        return pd.DataFrame(payload["data"])
    if media_type not in {
        "application/vnd.apache.arrow.stream", "application/octet-stream",
    }:
        raise BuildError(f"Unexpected historical content-type: {content_type!r}")
    # Arrow's reader accepts a stream missing its EOS marker, so assert it
    # explicitly to distinguish a clean response from a server-side truncation.
    if not raw.endswith(b"\xff\xff\xff\xff\x00\x00\x00\x00"):
        raise BuildError("Incomplete or invalid Arrow stream: missing EOS marker")
    try:
        return pa_ipc.open_stream(io.BytesIO(raw)).read_all().to_pandas()
    except (pa.ArrowInvalid, OSError) as exc:
        raise BuildError(f"Incomplete or invalid Arrow stream: {exc}") from exc


def _retry_delay_seconds(exc: Exception, attempt: int) -> float:
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        header_delay = 0.0
        if retry_after:
            try:
                header_delay = max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    header_delay = max(
                        0.0, (retry_at - datetime.now(timezone.utc)).total_seconds()
                    )
                except (TypeError, ValueError, OverflowError):
                    header_delay = 0.0
        longer_backoff = 5.0 * (2 ** (attempt - 1))
        return min(max(header_delay, longer_backoff), MAX_429_BACKOFF_SECONDS)
    return min(float(2 ** (attempt - 1)), 8.0)


def fetch_tbbo(
    *,
    contracts: Sequence[Contract],
    start: datetime,
    end: datetime,
    api_base: str,
    timeout: int,
    retries: int = 3,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    params = {
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "symbols": ",".join(sorted(contract.bloomberg for contract in contracts)),
        "source": "database",
        "schema": "tbbo",
        "format": "arrow",
    }
    url = f"{api_base.rstrip('/')}/historical/trades?{urllib.parse.urlencode(params)}"
    failures: list[str] = []
    for attempt in range(1, retries + 1):
        try:
            with opener(url, timeout=timeout) as response:
                raw, headers = _read_stream_response(response)
            frame = decode_tbbo_response(raw, headers.get("content-type", ""))
            return frame, {
                "endpoint": f"{api_base.rstrip('/')}/historical/trades",
                "parameters": params,
                "attempts": attempt,
                "content_type": headers.get("content-type"),
                "content_length": len(raw),
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "failures": failures,
            }
        except Exception as exc:
            delay = _retry_delay_seconds(exc, attempt)
            failures.append(
                f"{type(exc).__name__}: {exc}; retry_delay_seconds={delay:g}"
            )
            if attempt == retries:
                raise BuildError(f"Historical request failed after {retries} attempts: {exc}") from exc
            sleep_fn(delay)
    raise AssertionError("unreachable")


def clean_tbbo(frame: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    required = {
        "ts_event", "price", "size", "side", "bid_price", "ask_price",
        "bid_size", "ask_size", "dashboard_symbol", "contract",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise BuildError(f"TBBO response lacks required fields: {missing}")
    out = frame.copy()
    out["ts_event"] = pd.to_datetime(out["ts_event"], utc=True, errors="coerce")
    numeric = ("price", "size", "bid_price", "ask_price", "bid_size", "ask_size")
    for column in numeric:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out[
        out["ts_event"].notna()
        & (out["ts_event"] >= start)
        & (out["ts_event"] < end)
        & out["price"].between(80, 150, inclusive="neither")
        & (out["size"] > 0)
        & (out["bid_price"] > 0)
        & (out["ask_price"] >= out["bid_price"])
        & (out["bid_size"] > 0)
        & (out["ask_size"] > 0)
    ].copy()
    out.sort_values("ts_event", inplace=True)
    return out


def deduplicate_trade_records(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Remove exact records while rejecting ambiguous event identities.

    Event identity uses receive time, instrument IDs, and sequence/publisher IDs
    whenever the source supplies them.  Exactness uses every returned/derived
    column, while conflicts are limited to materially different trade/quote
    fields for the same event identity.
    """
    exact_keys = sorted(frame.columns)
    identity_candidates = [
        "ts_event", "ts_recv", "publisher_id", "instrument_id",
        "symbol", "bloomberg_symbol", "contract", "dashboard_symbol",
    ]
    identity_keys = [
        column for column in identity_candidates
        if column in frame and frame[column].notna().any()
    ]
    if "ts_event" not in identity_keys or not {
        "symbol", "bloomberg_symbol"
    }.intersection(identity_keys):
        raise BuildError("Cannot construct a safe duplicate event identity")
    sequence_keys = [
        column for column in ("sequence",)
        if column in frame and frame[column].notna().any()
    ]
    if sequence_keys:
        identity_mode = "source_sequence"
        identity_keys.extend(sequence_keys)
        conflict_candidates = (
            "price", "size", "side", "bid_price", "ask_price", "bid_size", "ask_size"
        )
        identity_limitation = None
        conflict_detection = "enabled_by_source_sequence"
    else:
        # AccessAPI's TBBO projection can contain legitimate distinct trades
        # sharing ts_event, ts_recv, and trade values while quote depth advances
        # between fills. Only a full-record fingerprint is safe in that case.
        identity_mode = "fallback_full_record_fingerprint"
        identity_keys = exact_keys
        conflict_candidates: tuple[str, ...] = ()
        identity_limitation = (
            "Source supplied no sequence. AccessAPI can emit legitimate distinct "
            "fills sharing ts_event/ts_recv and trade values while quote depth "
            "changes, so conflicting identities cannot be inferred safely. Exact "
            "full-record duplicates are still removed and threshold-gated."
        )
        conflict_detection = "unavailable_without_source_sequence"
    pre_rows = int(len(frame))
    exact_mask = frame.duplicated(exact_keys, keep=False)
    exact_rows = int(exact_mask.sum())
    exact_groups = (
        int(frame.loc[exact_mask].groupby(exact_keys, dropna=False).ngroups)
        if exact_rows else 0
    )

    material_fields = [
        column for column in conflict_candidates if column in frame
    ]
    conflict_rows = 0
    conflict_groups = 0
    conflict_examples: list[dict[str, Any]] = []
    identity_mask = frame.duplicated(identity_keys, keep=False)
    if material_fields and identity_mask.any():
        candidates = frame.loc[identity_mask]
        for identity, group in candidates.groupby(identity_keys, dropna=False, sort=False):
            signatures = group[material_fields].copy()
            for column in ("price", "bid_price", "ask_price"):
                if column in signatures:
                    signatures[column] = pd.to_numeric(
                        signatures[column], errors="coerce"
                    ).round(12)
            if len(signatures.drop_duplicates()) <= 1:
                continue
            conflict_groups += 1
            conflict_rows += int(len(group))
            if len(conflict_examples) < 5:
                identity_values = identity if isinstance(identity, tuple) else (identity,)
                conflict_examples.append({
                    "identity": {
                        key: _json_default(value) if isinstance(
                            value, (datetime, date, pd.Timestamp, Path, np.integer, np.floating)
                        ) else value
                        for key, value in zip(identity_keys, identity_values)
                    },
                    "rows": int(len(group)),
                    "material_variants": int(len(signatures.drop_duplicates())),
                    "variants": group[material_fields].drop_duplicates().head(3).to_dict(
                        orient="records"
                    ),
                })

    # Stable hash order makes which exact copy is retained independent of input
    # order. Hash ties are harmless because all exact-key values are identical.
    work = frame.copy()
    work["_dedup_hash"] = pd.util.hash_pandas_object(
        work[exact_keys], index=False, categorize=True
    ).astype("uint64")
    work.sort_values([*identity_keys, "_dedup_hash"], kind="mergesort", inplace=True)
    removed_mask = work.duplicated(exact_keys, keep="first")
    removed_records = work.loc[removed_mask].copy()
    deduped = work.loc[~removed_mask].drop(columns="_dedup_hash")
    deduped.sort_values("ts_event", kind="mergesort", inplace=True)
    deduped.reset_index(drop=True, inplace=True)
    removed = pre_rows - int(len(deduped))
    exact_fraction = exact_rows / pre_rows if pre_rows else 0.0
    removed_impact: list[dict[str, Any]] = []
    if not removed_records.empty:
        removed_records["bucket_ts"] = pd.to_datetime(
            removed_records["ts_event"], utc=True
        ).dt.floor(BUCKET)
        removed_records["_sign"] = removed_records["side"].map(classify_aggressor)
        removed_records["_signed"] = removed_records["_sign"] * removed_records["size"]
        for (bucket_ts, root), group in removed_records.groupby(
            ["bucket_ts", "dashboard_symbol"], sort=True, dropna=False
        ):
            sign = group["_sign"]
            size = group["size"]
            removed_impact.append({
                "bucket_ts": pd.Timestamp(bucket_ts),
                "root": str(root),
                "removed_rows": int(len(group)),
                "removed_contracts": float(size.sum()),
                "removed_signed_contracts": float(group["_signed"].sum()),
                "removed_buy_contracts": float(size.where(sign == 1, 0).sum()),
                "removed_sell_contracts": float(size.where(sign == -1, 0).sum()),
                "removed_large_contracts": float(
                    size.where(size >= LARGE_TRADE_SIZE, 0).sum()
                ),
            })
    failures: list[str] = []
    if conflict_groups:
        failures.append("conflicting_duplicate_identity")
    if exact_fraction > MAX_EXACT_DUPLICATE_ROW_FRACTION:
        failures.append("excessive_exact_duplication")
    report = {
        "ok": not failures,
        "pre_dedup_rows": pre_rows,
        "post_dedup_rows": int(len(deduped)),
        "exact_duplicate_rows": exact_rows,
        "exact_duplicate_groups": exact_groups,
        "removed_exact_rows": removed,
        "affected_bucket_minutes": sorted({
            item["bucket_ts"].isoformat() for item in removed_impact
        }),
        "removed_row_impact": removed_impact,
        "exact_duplicate_row_fraction": exact_fraction,
        "removed_exact_fraction": removed / pre_rows if pre_rows else 0.0,
        "max_exact_duplicate_row_fraction": MAX_EXACT_DUPLICATE_ROW_FRACTION,
        "threshold_metric": "exact_duplicate_rows / pre_dedup_rows",
        "conflicting_identity_rows": conflict_rows,
        "conflicting_identity_groups": conflict_groups,
        "conflict_examples": conflict_examples,
        "conflict_detection": conflict_detection,
        "identity_mode": identity_mode,
        "identity_limitation": identity_limitation,
        "identity_keys": identity_keys,
        "exact_keys": exact_keys,
        "material_fields": material_fields,
        "price_materiality_decimals": 12,
        "failures": failures,
    }
    return deduped, report


def duplicate_report(frame: pd.DataFrame) -> dict[str, Any]:
    """Compatibility wrapper returning the full deterministic dedup audit."""
    _, report = deduplicate_trade_records(frame)
    return report


def coverage_report(
    frame: pd.DataFrame, trading_date: date | str, duplicates: Mapping[str, Any]
) -> dict[str, Any]:
    schedule = expected_session(trading_date)
    if frame.empty:
        return {
            "ok": schedule["kind"] == "closed",
            "schedule": schedule,
            "rows": 0,
            "reason": "expected_closed" if schedule["kind"] == "closed" else "no_rows",
            "duplicates": dict(duplicates),
        }
    ty = frame[frame["dashboard_symbol"] == PRICE_ROOT].copy()
    if ty.empty:
        return {
            "ok": False, "schedule": schedule, "rows": int(len(frame)),
            "reason": "missing_TY", "duplicates": dict(duplicates),
        }
    minute_index = (
        pd.DatetimeIndex(ty["ts_event"]).floor("min").drop_duplicates().sort_values()
    )
    gaps = minute_index.to_series().diff().dt.total_seconds().div(60).dropna()
    start_lag = (minute_index.min().to_pydatetime() - schedule["start_utc"]).total_seconds() / 60
    expected_last = schedule.get("expected_last_trade_utc", schedule["end_utc"])
    end_lag = (expected_last - minute_index.max().to_pydatetime()).total_seconds() / 60
    max_gap = float(gaps.max()) if not gaps.empty else 0.0
    failures: list[str] = []
    if schedule["kind"] == "closed":
        failures.append("rows_on_expected_closed_session")
    if start_lag > START_TOLERANCE_MINUTES:
        failures.append("late_session_start")
    if end_lag > END_TOLERANCE_MINUTES:
        failures.append("early_session_end")
    if max_gap > MAX_TY_GAP_MINUTES:
        failures.append("excessive_TY_minute_gap")
    failures.extend(str(value) for value in duplicates.get("failures", []))
    return {
        "ok": not failures,
        "schedule": schedule,
        "rows": int(len(frame)),
        "ty_rows": int(len(ty)),
        "first_ts": frame["ts_event"].min(),
        "last_ts": frame["ts_event"].max(),
        "ty_first_minute": minute_index.min(),
        "ty_last_minute": minute_index.max(),
        "start_lag_minutes": start_lag,
        "end_lag_minutes": end_lag,
        "max_ty_gap_minutes": max_gap,
        "gap_count_over_5m": int((gaps > 5).sum()),
        "failures": failures,
        "duplicates": dict(duplicates),
    }


def _bucket_symbol(root: str, frame: pd.DataFrame, dv01: float) -> pd.DataFrame:
    work = frame.copy()
    work["sign"] = work["side"].map(classify_aggressor)
    work["signed_flow"] = work["sign"] * work["size"] * dv01
    work["buy_flow"] = (work["sign"] == 1).astype(float) * work["size"] * dv01
    work["sell_flow"] = (work["sign"] == -1).astype(float) * work["size"] * dv01
    work["large_volume"] = np.where(work["size"] >= LARGE_TRADE_SIZE, work["size"], 0)
    work["spread"] = work["ask_price"] - work["bid_price"]
    denominator = work["bid_size"] + work["ask_size"]
    work["size_imbalance"] = (work["bid_size"] - work["ask_size"]) / denominator
    work["microprice"] = (
        work["ask_price"] * work["bid_size"] + work["bid_price"] * work["ask_size"]
    ) / denominator
    work["mid"] = (work["bid_price"] + work["ask_price"]) / 2
    work["microprice_dev"] = work["microprice"] - work["mid"]
    work["displayed_depth"] = denominator
    work["trade_size_vs_depth"] = work["size"] / denominator
    work["depth_adjusted_signed_flow"] = work["signed_flow"] / denominator
    work.set_index("ts_event", inplace=True)
    grouped = pd.DataFrame({
        f"{root}_signed_flow": work["signed_flow"].resample(BUCKET).sum().fillna(0.0),
        f"{root}_buy_flow": work["buy_flow"].resample(BUCKET).sum().fillna(0.0),
        f"{root}_sell_flow": work["sell_flow"].resample(BUCKET).sum().fillna(0.0),
        f"{root}_contracts": work["size"].resample(BUCKET).sum().fillna(0.0),
        f"{root}_trade_count": work["size"].resample(BUCKET).count().fillna(0.0),
        f"{root}_avg_trade_size": work["size"].resample(BUCKET).mean(),
        f"{root}_large_volume": work["large_volume"].resample(BUCKET).sum().fillna(0.0),
    })
    if root == PRICE_ROOT:
        grouped["price"] = work["price"].resample(BUCKET).last().ffill()
        quote_columns = [
            "spread", "size_imbalance", "microprice_dev", "displayed_depth",
            "trade_size_vs_depth", "depth_adjusted_signed_flow",
        ]
        quotes = work[quote_columns].resample(BUCKET).agg(["last", "mean"])
        quotes.columns = [f"{name}_{aggregation}" for name, aggregation in quotes.columns]
        grouped = grouped.join(quotes)
        grouped["trade_size_vs_depth"] = grouped["trade_size_vs_depth_mean"]
        grouped["depth_adjusted_signed_flow"] = grouped["depth_adjusted_signed_flow_mean"]
    return grouped


def build_feature_matrix(
    frame: pd.DataFrame, trading_date: date | str, dv01: Mapping[str, float]
) -> pd.DataFrame:
    date_text = str(trading_date)
    frames = [
        _bucket_symbol(root, frame[frame["dashboard_symbol"] == root], float(dv01[root]))
        for root in ROOTS if not frame[frame["dashboard_symbol"] == root].empty
    ]
    if not frames:
        return pd.DataFrame()
    matrix = pd.concat(frames, axis=1).sort_index()
    signed = [f"{root}_signed_flow" for root in ROOTS]
    buys = [f"{root}_buy_flow" for root in ROOTS]
    sells = [f"{root}_sell_flow" for root in ROOTS]
    contracts = [f"{root}_contracts" for root in ROOTS]
    counts = [f"{root}_trade_count" for root in ROOTS]
    averages = [f"{root}_avg_trade_size" for root in ROOTS]
    large = [f"{root}_large_volume" for root in ROOTS]
    for column in signed + buys + sells + contracts + counts + large + averages:
        if column not in matrix:
            matrix[column] = 0.0
        else:
            matrix[column] = matrix[column].fillna(0.0)
    for column in [
        "price", "spread_last", "spread_mean", "size_imbalance_last",
        "size_imbalance_mean", "microprice_dev_last", "microprice_dev_mean",
        "displayed_depth_last", "displayed_depth_mean",
    ]:
        if column in matrix:
            matrix[column] = matrix[column].ffill()
    for column in ("trade_size_vs_depth", "depth_adjusted_signed_flow"):
        if column in matrix:
            matrix[column] = matrix[column].fillna(0.0)
    matrix = matrix[matrix["price"].notna()].copy()
    matrix["agg_1m"] = matrix[signed].sum(axis=1)
    matrix["agg_5m"] = matrix["agg_1m"].rolling(5, min_periods=1).sum()
    matrix["agg_30m"] = matrix["agg_1m"].rolling(30, min_periods=1).sum()
    matrix["agg_cum_daily"] = matrix["agg_1m"].cumsum()
    matrix["agg_cum_daily"] -= matrix["agg_cum_daily"].iloc[0]
    matrix["agg_rate"] = matrix["agg_cum_daily"].diff().fillna(0.0)
    matrix["agg_accel"] = matrix["agg_rate"].diff().fillna(0.0)
    buy_flow, sell_flow = matrix[buys].sum(axis=1), matrix[sells].sum(axis=1)
    denominator = buy_flow + sell_flow
    matrix["buysell_ratio"] = (
        (100 * matrix["agg_1m"] / denominator).where(denominator != 0)
        .ewm(span=10, ignore_na=True, min_periods=1).mean()
    )
    matrix["trade_intensity"] = matrix[counts].sum(axis=1)
    matrix["vol_per_min"] = matrix[contracts].sum(axis=1)
    average_numerator = matrix[averages].multiply(matrix[counts].to_numpy()).sum(axis=1)
    matrix["avg_trade_size"] = (
        average_numerator / matrix["trade_intensity"].replace(0, np.nan)
    ).fillna(0.0)
    matrix["large_trade_share"] = (
        matrix[large].sum(axis=1) / matrix["vol_per_min"].replace(0, np.nan)
    ).fillna(0.0)
    for root in ROOTS:
        matrix[f"{root}_agg_5m"] = matrix[f"{root}_signed_flow"].rolling(
            5, min_periods=1
        ).sum()
    matrix["price_return_1m"] = matrix["price"].diff()
    matrix["realized_vol"] = matrix["price_return_1m"].rolling(30, min_periods=5).std()
    matrix["mom_5m"] = matrix["price"] - matrix["price"].shift(5)
    start, _ = session_bounds(date_text)
    minutes = (
        (pd.DatetimeIndex(matrix.index) - pd.Timestamp(start)).total_seconds() / 60
    ).astype(int)
    matrix["et_date"] = date_text
    matrix["day_open_price"] = matrix["price"].iloc[0]
    matrix["target_price_delta"] = matrix["price"] - matrix["day_open_price"]
    matrix["minutes_since_open"] = minutes
    matrix["session_sin"] = np.sin(2 * np.pi * minutes / (24 * 60))
    matrix["session_cos"] = np.cos(2 * np.pi * minutes / (24 * 60))
    matrix.index.name = "bucket_ts"
    return matrix.reindex(
        columns=["price", "day_open_price", "target_price_delta", "et_date", *FEATURE_COLUMNS]
    )


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    frame.to_parquet(temp, index=True, compression="zstd")
    os.replace(temp, path)


def _code_identity() -> dict[str, Any]:
    source = Path(__file__).resolve()
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True,
            capture_output=True, check=False, timeout=10,
        ).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {
        "source": str(source),
        "source_sha256": sha256_file(source),
        "git_commit": commit,
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": {
            "numpy": np.__version__, "pandas": pd.__version__,
            "pyarrow": pa.__version__,
        },
    }


def shard_paths(output_dir: Path, trading_date: date | str) -> dict[str, Path]:
    stamp = str(trading_date)
    return {
        "raw": output_dir / "shards" / "raw" / f"{stamp}.parquet",
        "features": output_dir / "shards" / "features" / f"{stamp}.parquet",
        "manifest": output_dir / "manifests" / "dates" / f"{stamp}.json",
    }


def load_latest_prior_reference(
    output_dir: Path, before_date: date, explicit_manifest: Path | None = None
) -> dict[str, Any] | None:
    if explicit_manifest is not None:
        manifest = json.loads(explicit_manifest.read_text(encoding="utf-8"))
        reference = front_reference_from_manifest(manifest)
        if reference["trading_date"] >= before_date.isoformat():
            raise BuildError("Explicit prior manifest must predate the build")
        return reference
    manifest_dir = output_dir / "manifests" / "dates"
    if not manifest_dir.exists():
        return None
    for path in sorted(manifest_dir.glob("*.json"), reverse=True):
        if path.stem >= before_date.isoformat():
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("feature_version") != FEATURE_VERSION:
                continue
            return front_reference_from_manifest(manifest)
        except (BuildError, json.JSONDecodeError, OSError):
            continue
    return None


def _has_dedup_attribution_schema(
    manifest: Mapping[str, Any], trading_date: date
) -> bool:
    duplicates = manifest.get("coverage", {}).get("duplicates")
    if not isinstance(duplicates, Mapping):
        return False
    impacts = duplicates.get("removed_row_impact")
    affected = duplicates.get("affected_bucket_minutes")
    if (
        duplicates.get("trading_date") != trading_date.isoformat()
        or not isinstance(impacts, list)
        or not isinstance(affected, list)
    ):
        return False
    required_impact = {
        "bucket_ts", "root", "removed_rows", "removed_contracts",
        "removed_signed_contracts", "removed_buy_contracts",
        "removed_sell_contracts", "removed_large_contracts",
    }
    impact_minutes: set[str] = set()
    removed_rows = 0
    for impact in impacts:
        if not isinstance(impact, Mapping) or not required_impact.issubset(impact):
            return False
        timestamp = pd.to_datetime(impact["bucket_ts"], utc=True, errors="coerce")
        if pd.isna(timestamp) or impact["root"] not in ROOTS:
            return False
        try:
            count = int(impact["removed_rows"])
        except (TypeError, ValueError):
            return False
        if count <= 0:
            return False
        removed_rows += count
        impact_minutes.add(pd.Timestamp(timestamp).isoformat())
    return (
        sorted(affected) == sorted(impact_minutes)
        and duplicates.get("removed_exact_rows") == removed_rows
    )


def valid_existing_shard(
    paths: Mapping[str, Path],
    *,
    trading_date: date,
    prior_reference: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not all(paths[key].exists() for key in ("raw", "features", "manifest")):
        return None
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    checksums = manifest.get("checksums", {})
    selection = manifest.get("front_selection", {})
    expected_mode = "causal_prior_session" if prior_reference else "bootstrap_same_session"
    expected_source = (
        str(prior_reference.get("trading_date")) if prior_reference
        else trading_date.isoformat()
    )
    if prior_reference:
        expected_hash = _reference_hash(
            str(prior_reference.get("trading_date")),
            prior_reference.get("front_candidate_audit", []),
        )
        supplied_hash = prior_reference.get("reference_sha256")
        if supplied_hash and str(supplied_hash) != expected_hash:
            return None
    else:
        expected_hash = _reference_hash(
            trading_date.isoformat(), manifest.get("front_candidate_audit", [])
        )
    if (
        checksums.get("raw") == sha256_file(paths["raw"])
        and checksums.get("features") == sha256_file(paths["features"])
        and manifest.get("feature_version") == FEATURE_VERSION
        and manifest.get("coverage", {}).get("ok")
        and selection.get("mode") == expected_mode
        and selection.get("source_date") == expected_source
        and selection.get("reference_sha256") == expected_hash
        and bool(manifest.get("research_eligible")) == bool(prior_reference)
        and isinstance(manifest.get("front_candidate_audit"), list)
        and _has_dedup_attribution_schema(manifest, trading_date)
    ):
        return manifest
    return None


def build_date(
    trading_date: date,
    *,
    output_dir: Path,
    api_base: str,
    timeout: int,
    retries: int,
    contracts: Sequence[Contract] | None = None,
    fetcher: Callable[..., tuple[pd.DataFrame, dict[str, Any]]] = fetch_tbbo,
    write: bool = True,
    prior_reference: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    paths = shard_paths(output_dir, trading_date)
    if write:
        existing = valid_existing_shard(
            paths, trading_date=trading_date, prior_reference=prior_reference
        )
        if existing:
            return {**existing, "status": "reused"}
    schedule = expected_session(trading_date)
    if schedule["kind"] == "closed":
        manifest = {
            "trading_date": trading_date.isoformat(), "status": "expected_closed",
            "coverage": {"ok": True, "schedule": schedule, "rows": 0},
            "feature_version": FEATURE_VERSION,
            "research_only": True,
            "research_eligible": False,
            "exclusion_reasons": [
                {"code": "expected_closed", "detail": schedule.get("reason")}
            ],
            "artifacts": {"manifest": str(paths["manifest"])},
        }
        if write:
            _atomic_json(paths["manifest"], manifest)
        return manifest
    requested = list(contracts or candidate_contracts(trading_date))
    start, end = session_bounds(trading_date)
    raw, request = fetcher(
        contracts=requested, start=start, end=end, api_base=api_base,
        timeout=timeout, retries=retries,
    )
    if raw.empty:
        manifest = {
            "trading_date": trading_date.isoformat(),
            "status": "failed",
            "research_only": True,
            "feature_version": FEATURE_VERSION,
            "research_eligible": False,
            "request": request,
            "front_contract_rule": FRONT_RULE,
            "coverage": {
                "ok": False,
                "schedule": schedule,
                "rows": 0,
                "failures": ["empty_api_response"],
                "reason": "AccessAPI returned no TBBO rows",
            },
            "exclusion_reasons": [{
                "code": "empty_api_response",
                "detail": "AccessAPI returned no TBBO rows",
            }],
            "artifacts": {"manifest": str(paths["manifest"])},
            "code": _code_identity(),
        }
        if write:
            _atomic_json(paths["manifest"], manifest)
        return manifest
    filtered, symbol_audit = verify_and_filter_symbols(raw, requested)
    current_candidate_audit = contract_volume_audit(filtered)
    try:
        selected, front_audit, current_candidate_audit, front_selection = select_front_contracts(
            filtered, trading_date=trading_date, prior_reference=prior_reference
        )
    except DataQualityError as exc:
        manifest = {
            "trading_date": trading_date.isoformat(),
            "status": "failed",
            "research_only": True,
            "feature_version": FEATURE_VERSION,
            "research_eligible": False,
            "request": request,
            "symbol_audit": symbol_audit,
            "front_candidate_audit": current_candidate_audit,
            "front_contract_rule": FRONT_RULE,
            "coverage": {
                "ok": False,
                "schedule": schedule,
                "rows": int(len(filtered)),
                "failures": ["front_selection_data_quality"],
                "reason": str(exc),
            },
            "exclusion_reasons": [
                {"code": "front_selection_data_quality", "detail": str(exc)}
            ],
            "artifacts": {"manifest": str(paths["manifest"])},
            "code": _code_identity(),
        }
        if write:
            _atomic_json(paths["manifest"], manifest)
        return manifest
    clean_pre_dedup = clean_tbbo(selected, start, end)
    clean, duplicates = deduplicate_trade_records(clean_pre_dedup)
    duplicates["trading_date"] = trading_date.isoformat()
    coverage = coverage_report(clean, trading_date, duplicates)
    dv01, dv01_meta = load_dv01()
    features = build_feature_matrix(clean, trading_date, dv01) if coverage["ok"] else pd.DataFrame()
    required_roots = {entry["root"] for entry in front_audit}
    if required_roots != set(ROOTS):
        coverage["ok"] = False
        coverage.setdefault("failures", []).append(
            f"missing_front_roots:{sorted(set(ROOTS) - required_roots)}"
        )
    manifest: dict[str, Any] = {
        "trading_date": trading_date.isoformat(),
        "status": "built" if coverage["ok"] else "failed",
        "research_only": True,
        "feature_version": FEATURE_VERSION,
        "session_rule": "prior calendar day 18:00 ET inclusive to trading date 17:00 ET exclusive",
        "request": request,
        "symbol_audit": symbol_audit,
        "front_contract_selection": front_audit,
        "front_candidate_audit": current_candidate_audit,
        "front_selection": front_selection,
        "front_contract_rule": FRONT_RULE,
        "research_eligible": bool(front_selection["research_eligible"]),
        "coverage": coverage,
        "dv01": dv01_meta,
        "rows": {
            "raw_selected_pre_dedup": int(len(clean_pre_dedup)),
            "raw_selected": int(len(clean)),
            "features": int(len(features)),
        },
        "artifacts": {"manifest": str(paths["manifest"])},
        "code": _code_identity(),
    }
    if not coverage["ok"]:
        manifest["exclusion_reasons"] = [
            {"code": str(reason), "detail": coverage.get("reason")}
            for reason in coverage.get("failures", ["coverage_failed"])
        ]
    if write:
        if coverage["ok"]:
            _write_parquet_atomic(clean.set_index("ts_event"), paths["raw"])
            _write_parquet_atomic(features, paths["features"])
            manifest["checksums"] = {
                "raw": sha256_file(paths["raw"]),
                "features": sha256_file(paths["features"]),
            }
            manifest["artifacts"].update({
                "raw": str(paths["raw"]), "features": str(paths["features"])
            })
        _atomic_json(paths["manifest"], manifest)
    return manifest


def _dedup_affected_minutes(
    attribution: Sequence[Mapping[str, Any]] | None,
) -> dict[str, dict[pd.Timestamp, set[str]]]:
    affected: dict[str, dict[pd.Timestamp, set[str]]] = {}
    for report in attribution or ():
        trading_date = str(report.get("trading_date") or "")
        if not trading_date:
            continue
        for impact in report.get("removed_row_impact", []):
            timestamp = pd.to_datetime(impact.get("bucket_ts"), utc=True, errors="coerce")
            root = str(impact.get("root") or "")
            if pd.isna(timestamp) or root not in ROOTS:
                continue
            affected.setdefault(trading_date, {}).setdefault(timestamp, set()).add(root)
    return affected


def _dedup_explains(
    feature: str,
    timestamp: pd.Timestamp,
    trading_date: str,
    affected: Mapping[str, Mapping[pd.Timestamp, set[str]]],
) -> bool:
    declaration = DEDUP_PARITY_INFLUENCE.get(feature)
    if declaration is None:
        return False
    required_root = declaration.get("root")
    for affected_at, roots in affected.get(trading_date, {}).items():
        delta = (timestamp - affected_at).total_seconds() / 60
        if delta < 0 or (required_root and required_root not in roots):
            continue
        minutes = declaration["minutes"]
        if minutes is None or delta <= minutes:
            return True
    return False


def parity_report(
    new_frame: pd.DataFrame,
    legacy_path: Path = LEGACY_MATRIX,
    dedup_attribution: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not legacy_path.exists():
        return {"ok": False, "blocking_reason": f"Legacy matrix not found: {legacy_path}"}
    legacy = pd.read_csv(legacy_path, parse_dates=["bucket_ts"])
    legacy["bucket_ts"] = pd.to_datetime(legacy["bucket_ts"], utc=True)
    fresh = new_frame.reset_index()
    fresh["bucket_ts"] = pd.to_datetime(fresh["bucket_ts"], utc=True)
    common = fresh.merge(
        legacy, on=["bucket_ts", "et_date"], suffixes=("_new", "_legacy"), how="inner"
    ).sort_values(["et_date", "bucket_ts"])
    if common.empty:
        return {"ok": False, "blocking_reason": "No overlapping symbol-minute parity rows"}
    affected = _dedup_affected_minutes(dedup_attribution)
    count_guard_available = {
        "trade_intensity_new", "trade_intensity_legacy"
    }.issubset(common.columns)

    def classify_bad(feature: str, rows: pd.DataFrame, bad: pd.Series) -> tuple[int, int]:
        explained = 0
        unexplained_count = 0
        for index in rows.index[bad]:
            row = common.loc[index]
            legacy_count_not_lower = (
                count_guard_available
                and pd.notna(row["trade_intensity_new"])
                and pd.notna(row["trade_intensity_legacy"])
                and row["trade_intensity_legacy"] >= row["trade_intensity_new"]
            )
            attributed = _dedup_explains(
                feature, pd.Timestamp(row["bucket_ts"]), str(row["et_date"]), affected
            )
            if attributed and legacy_count_not_lower:
                explained += 1
            else:
                unexplained_count += 1
        return explained, unexplained_count

    results: dict[str, Any] = {}
    unexplained = 0
    for feature, tolerance in PARITY_TOLERANCES.items():
        left, right = f"{feature}_new", f"{feature}_legacy"
        if left not in common or right not in common:
            results[feature] = {"ok": False, "reason": "missing_column"}
            unexplained += 1
            continue
        usable = common[[left, right]].dropna()
        if feature in {
            "agg_5m", "agg_30m", "realized_vol", "mom_5m",
            "FV_agg_5m", "TU_agg_5m", "TY_agg_5m", "US_agg_5m",
        }:
            rank = common.groupby("et_date").cumcount()
            usable = common.loc[rank >= 30, [left, right]].dropna()
        elif feature in {"agg_rate", "agg_accel"}:
            rank = common.groupby("et_date").cumcount()
            usable = common.loc[rank >= 2, [left, right]].dropna()
        elif feature == "buysell_ratio":
            rank = common.groupby("et_date").cumcount()
            usable = common.loc[rank >= 60, [left, right]].dropna()
        if usable.empty:
            results[feature] = {"ok": True, "compared": 0, "reason": "no_non_null_overlap"}
            continue
        difference = (usable[left] - usable[right]).abs()
        allowed = tolerance["atol"] + tolerance["rtol"] * usable[right].abs()
        bad = difference > allowed
        explained_bad, unexplained_bad = classify_bad(feature, usable, bad)
        ok = unexplained_bad == 0
        unexplained += int(not ok)
        results[feature] = {
            "ok": ok, "compared": int(len(usable)),
            "failures": int(bad.sum()),
            "explained_dedup_failures": explained_bad,
            "unexplained_failures": unexplained_bad,
            "max_abs_difference": float(difference.max()),
            "tolerance": tolerance,
        }
    # Cumulative flow is compared after removing the known per-session constant.
    cumulative_bad = 0
    cumulative_explained = 0
    cumulative_unexplained = 0
    cumulative_max = 0.0
    for _, group in common.groupby("et_date"):
        left = group["agg_cum_daily_new"] - group["agg_cum_daily_new"].iloc[0]
        right = group["agg_cum_daily_legacy"] - group["agg_cum_daily_legacy"].iloc[0]
        delta = (left - right).abs()
        bad = delta > 1e-9
        explained_bad, unexplained_bad = classify_bad("agg_cum_daily", group, bad)
        cumulative_bad += int(bad.sum())
        cumulative_explained += explained_bad
        cumulative_unexplained += unexplained_bad
        cumulative_max = max(cumulative_max, float(delta.max()))
    results["agg_cum_daily"] = {
        "ok": cumulative_unexplained == 0, "failures": cumulative_bad,
        "explained_dedup_failures": cumulative_explained,
        "unexplained_failures": cumulative_unexplained,
        "max_abs_difference_after_offset": cumulative_max,
        "expected_difference": EXPECTED_RESET_DIFFERENCES["agg_cum_daily"],
    }
    unexplained += int(cumulative_unexplained > 0)
    return {
        "ok": unexplained == 0,
        "overlap_rows": int(len(common)),
        "overlap_dates": sorted(common["et_date"].astype(str).unique()),
        "declared_tolerances": PARITY_TOLERANCES,
        "expected_reset_differences": EXPECTED_RESET_DIFFERENCES,
        "dedup_attribution": {
            "affected_bucket_minutes": {
                trading_date: [timestamp.isoformat() for timestamp in sorted(minutes)]
                for trading_date, minutes in sorted(affected.items())
            },
            "declared_feature_influence": DEDUP_PARITY_INFLUENCE,
            "legacy_count_guard": "trade_intensity_legacy >= trade_intensity_new",
        },
        "features": results,
        "unexplained_failed_features": unexplained,
    }


def publish_version(
    output_dir: Path,
    manifests: Sequence[Mapping[str, Any]],
    *,
    parity: Mapping[str, Any] | None,
    version: str | None = None,
    min_eligible_dates: int = DEFAULT_MIN_ELIGIBLE_DATES,
) -> dict[str, Any]:
    if min_eligible_dates <= 0:
        raise BuildError("Minimum eligible-date count must be positive")
    if parity is None:
        raise BuildError("Publication blocked until the parity gate is run")
    if not parity.get("ok"):
        raise BuildError("Publication blocked by unexplained parity discrepancies")
    eligible = [
        item for item in manifests
        if item.get("status") != "expected_closed"
        and item.get("research_eligible") is True
        and item.get("coverage", {}).get("ok") is True
        and item.get("artifacts", {}).get("features")
        and item.get("checksums", {}).get("features")
    ]
    if not eligible:
        raise BuildError("Publication blocked: no causally eligible session shards")
    if len(eligible) < min_eligible_dates:
        raise BuildError(
            f"Publication requires at least {min_eligible_dates} eligible dates; "
            f"found {len(eligible)}"
        )
    excluded_dates: list[dict[str, Any]] = []
    for item in manifests:
        if item in eligible:
            continue
        coverage = item.get("coverage", {})
        reasons = item.get("exclusion_reasons")
        if not reasons:
            if item.get("status") == "expected_closed":
                reasons = [{"code": "expected_closed", "detail": coverage.get("schedule", {}).get("reason")}]
            elif not coverage.get("ok"):
                reasons = [
                    {"code": str(code), "detail": coverage.get("reason")}
                    for code in coverage.get("failures", ["coverage_failed"])
                ]
            else:
                reasons = [{"code": "noneligible_bootstrap", "detail": "same-session seed"}]
        excluded_dates.append({
            "trading_date": str(item.get("trading_date")),
            "status": item.get("status"),
            "research_eligible": bool(item.get("research_eligible")),
            "manifest": item.get("artifacts", {}).get("manifest"),
            "manifest_sha256": (
                sha256_file(Path(item["artifacts"]["manifest"]))
                if item.get("artifacts", {}).get("manifest")
                and Path(item["artifacts"]["manifest"]).exists()
                else None
            ),
            "reasons": reasons,
            "manifest_coverage": coverage,
            "front_selection": item.get("front_selection"),
        })
    version_name = version or datetime.now(timezone.utc).strftime("v%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"v[0-9A-Za-z._-]+", version_name):
        raise BuildError("Version must start with 'v' and contain only safe filename characters")
    target = output_dir / "versions" / version_name
    if target.exists():
        raise BuildError(f"Immutable publication already exists: {target}")
    staging = output_dir / "versions" / f".{version_name}.tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        feature_frames = []
        for item in eligible:
            feature_path = Path(item["artifacts"]["features"])
            if sha256_file(feature_path) != item["checksums"]["features"]:
                raise BuildError(f"Shard changed after manifest: {feature_path}")
            feature_frames.append(pd.read_parquet(feature_path))
        combined = pd.concat(feature_frames).sort_index()
        if combined.index.duplicated().any():
            raise BuildError("Publication matrix has duplicate bucket timestamps")
        matrix_path = staging / "tbbo_feature_matrix.parquet"
        combined.to_parquet(matrix_path, compression="zstd")
        publication = {
            "version": version_name,
            "created_at": datetime.now(timezone.utc),
            "research_only": True,
            "immutable": True,
            "feature_version": FEATURE_VERSION,
            "date_manifests": [
                {
                    "trading_date": item["trading_date"],
                    "manifest": item.get("artifacts", {}).get("manifest"),
                    "checksums": item.get("checksums"),
                }
                for item in manifests
            ],
            "rows": int(len(combined)),
            "date_range": [
                min(item["trading_date"] for item in eligible),
                max(item["trading_date"] for item in eligible),
            ],
            "research_dates": [str(item["trading_date"]) for item in eligible],
            "minimum_eligible_dates": min_eligible_dates,
            "eligible_date_count": len(eligible),
            "excluded_dates": excluded_dates,
            "excluded_noneligible_dates": [
                item["trading_date"] for item in excluded_dates
            ],
            "matrix_sha256": sha256_file(matrix_path),
            "parity": parity,
            "code": _code_identity(),
        }
        _atomic_json(staging / "manifest.json", publication)
        os.replace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {**publication, "path": str(target)}


def _date_range(start: date, end: date) -> Iterable[date]:
    if end < start:
        raise BuildError("--end-date must be on or after --start-date")
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def latest_complete_trading_date(now: datetime | None = None) -> date:
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise BuildError("latest-complete calculation requires a timezone-aware datetime")
    candidate = instant.astimezone(EASTERN).date()
    while True:
        schedule = expected_session(candidate)
        if schedule["kind"] == "closed":
            candidate -= timedelta(days=1)
            continue
        completion = schedule.get("expected_last_trade_utc", schedule["end_utc"])
        if instant.astimezone(timezone.utc) >= completion:
            return candidate
        candidate -= timedelta(days=1)


def _parse_contract_list(value: str) -> list[Contract] | None:
    if not value:
        return None
    parsed = [parse_contract(part) for part in value.split(",")]
    if any(contract is None for contract in parsed):
        raise BuildError("--contracts must be comma-separated specific quarterly contracts")
    contracts = [contract for contract in parsed if contract is not None]
    if {contract.root for contract in contracts} != set(ROOTS):
        raise BuildError("--contracts must include specific contracts for FV,TU,TY,US")
    return contracts


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit-only", action="store_true", help="Fetch and validate one date without writing shards")
    mode.add_argument("--date", help="Build one ET trading date (YYYY-MM-DD)")
    mode.add_argument("--start-date", help="Build an inclusive ET trading-date range")
    parser.add_argument("--end-date", help="Inclusive range end; required with --start-date")
    parser.add_argument("--contracts", default="", help="Optional explicit Bloomberg/CME contracts for all four roots")
    parser.add_argument("--audit-date", help="Date used with --audit-only; defaults to latest complete weekday")
    parser.add_argument(
        "--prior-manifest",
        help="Optional complete prior-date manifest supplying causal contract volumes",
    )
    parser.add_argument("--output-dir", default=str(CANONICAL_OUTPUT_ROOT))
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help="Sequential delay between date requests (default: 0.5 seconds)",
    )
    parser.add_argument("--publish", action="store_true", help="Publish an immutable version after all gates pass")
    parser.add_argument("--version", help="Explicit immutable publication version, beginning with v")
    parser.add_argument(
        "--min-eligible-dates",
        type=int,
        default=DEFAULT_MIN_ELIGIBLE_DATES,
        help=f"Minimum causally eligible dates required to publish (default: {DEFAULT_MIN_ELIGIBLE_DATES})",
    )
    parser.add_argument("--parity", action="store_true", help="Run parity against the existing read-only matrix")
    parser.add_argument("--parity-start-date", help="Optional first date of a contract-stable parity benchmark")
    parser.add_argument("--parity-end-date", help="Optional last date of a contract-stable parity benchmark")
    parser.add_argument("--legacy-matrix", default=str(LEGACY_MATRIX), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = _safe_output_dir(Path(args.output_dir))
    if args.timeout <= 0 or args.retries <= 0 or args.min_eligible_dates <= 0:
        raise BuildError("--timeout, --retries, and --min-eligible-dates must be positive")
    if args.request_delay_seconds < 0:
        raise BuildError("--request-delay-seconds must be nonnegative")
    if args.publish and not args.parity:
        raise BuildError("--publish requires --parity")
    if bool(args.parity_start_date) != bool(args.parity_end_date):
        raise BuildError("--parity-start-date and --parity-end-date must be supplied together")
    explicit_contracts = _parse_contract_list(args.contracts)
    explicit_prior = Path(args.prior_manifest) if args.prior_manifest else None
    if args.audit_only:
        audit_date = (
            date.fromisoformat(args.audit_date)
            if args.audit_date else latest_complete_trading_date()
        )
        prior_reference = (
            load_latest_prior_reference(output_dir, audit_date, explicit_prior)
            if explicit_prior else None
        )
        manifest = build_date(
            audit_date, output_dir=output_dir, api_base=args.api_base,
            timeout=args.timeout, retries=args.retries, contracts=explicit_contracts,
            write=False, prior_reference=prior_reference,
        )
        return {"ok": bool(manifest["coverage"]["ok"]), "mode": "audit-only", "manifest": manifest}
    if args.date:
        dates = [date.fromisoformat(args.date)]
    else:
        if not args.end_date:
            raise BuildError("--end-date is required with --start-date")
        dates = list(_date_range(date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)))
    output_dir.mkdir(parents=True, exist_ok=True)
    prior_reference = load_latest_prior_reference(
        output_dir, dates[0], explicit_prior
    )
    manifests: list[dict[str, Any]] = []
    nonclosed_dates_seen = 0
    for trading_date in dates:
        if expected_session(trading_date)["kind"] != "closed":
            if nonclosed_dates_seen and args.request_delay_seconds:
                time.sleep(float(args.request_delay_seconds))
            nonclosed_dates_seen += 1
        manifest = build_date(
            trading_date, output_dir=output_dir, api_base=args.api_base,
            timeout=args.timeout, retries=args.retries, contracts=explicit_contracts,
            prior_reference=prior_reference,
        )
        manifests.append(manifest)
        if manifest.get("status") == "expected_closed":
            continue
        if not manifest.get("coverage", {}).get("ok"):
            continue
        prior_reference = front_reference_from_manifest(manifest)
    parity: dict[str, Any] | None = None
    if args.parity:
        parity_start = date.fromisoformat(args.parity_start_date) if args.parity_start_date else dates[0]
        parity_end = date.fromisoformat(args.parity_end_date) if args.parity_end_date else dates[-1]
        if parity_start > parity_end or parity_start < dates[0] or parity_end > dates[-1]:
            raise BuildError("Parity benchmark must be ordered and contained in the build range")
        parity_manifests = [
            item for item in manifests
            if parity_start <= date.fromisoformat(str(item["trading_date"])) <= parity_end
            and item.get("coverage", {}).get("ok")
            and item.get("research_eligible")
            and item.get("artifacts", {}).get("features")
        ]
        frames = [
            pd.read_parquet(item["artifacts"]["features"])
            for item in parity_manifests
        ]
        if not frames:
            raise BuildError("Parity requires at least one complete feature shard")
        parity = parity_report(
            pd.concat(frames).sort_index(),
            Path(args.legacy_matrix),
            dedup_attribution=[
                item.get("coverage", {}).get("duplicates", {})
                for item in parity_manifests
            ],
        )
        parity_path = output_dir / "parity" / f"parity_{parity_start}_{parity_end}.json"
        _atomic_json(parity_path, parity)
    publication = (
        publish_version(
            output_dir, manifests, parity=parity, version=args.version,
            min_eligible_dates=args.min_eligible_dates,
        )
        if args.publish else None
    )
    excluded_dates = [
        {
            "trading_date": item.get("trading_date"),
            "status": item.get("status"),
            "reasons": item.get("exclusion_reasons")
            or item.get("coverage", {}).get("failures")
            or (["noneligible_bootstrap"] if not item.get("research_eligible") else []),
        }
        for item in manifests
        if item.get("status") == "expected_closed"
        or not item.get("coverage", {}).get("ok")
        or not item.get("research_eligible")
    ]
    ok = len(manifests) == len(dates) and (parity is None or parity.get("ok", False))
    return {
        "ok": ok, "mode": "build", "dates": [value.isoformat() for value in dates],
        "manifests": manifests, "parity": parity, "publication": publication,
        "excluded_dates": excluded_dates,
        "output_dir": str(output_dir),
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = run(parse_args(argv))
        print(json.dumps(result, indent=2, default=_json_default))
        return 0 if result.get("ok") else 2
    except (BuildError, ValueError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
