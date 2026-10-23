"""X0 Q7/Q8 cross-source parity and semantics audit (Move Exhaustion V2).

Implements PROGRAM.md §2 Q7 (cross-source parity against the frozen TBBO
matrix) and Q8 (price parity), plus the store-wide event/action/side
semantics audit (PROGRAM.md §1.1, Assumption A1; ``config/frozen/
data_store.yaml`` ``mbp1_semantics``).

Pure, typed, deterministic helpers:

* daily derived-store frames (or per-date parquet paths) in, one parity
  report out;
* per trading date, UTC 1-minute bins: ``AGG_BUY`` size is +contracts,
  ``AGG_SELL`` size is -contracts, every other event class (including
  ``AGG_UNKNOWN``) is zero;
* minute gaps are zero-filled for flow and forward-filled for the last mid;
* a within-date trailing 5-minute signed sum matches the frozen matrix's
  ``TY_agg_5m`` semantics (current minute plus the previous four, with
  ``min_periods=1`` for the earliest minutes);
* Q7 joins to the matrix by ``(et_date, bucket_ts)`` on **2026 overlap
  dates only** (frozen ``data_store.yaml`` Q7) and reports overlap
  count/dates, global and per-date Pearson correlation, and the exact
  opposite-sign (sign-flip) count/examples; it passes only when the global
  correlation is > 0.95 with zero sign flips;
* Q8 compares the last store mid at each minute (forward-filled across
  gaps) with the matrix ``price`` on overlap dates.  A date whose
  per-date max abs diff exceeds 1/64 point is not the same 1m panel
  (roll / front-month / sparse TY) and is recorded in
  ``excluded_price_mismatch_dates``, then dropped from the Q7 and Q8
  gates.  Q8 passes only when the remaining comparable dates are
  nonempty and their global maximum absolute difference is <= 1/64
  point;
* missing/degenerate overlap fails closed: no overlap dates, too few
  matched minutes, non-finite values, or zero variance yield ``passed:
  False`` with reasons.

Scope guardrails (binding, mirroring :mod:`.x0_build`):

* never computes episodes, features, labels, or holdout statistics -- only
  day-level QC aggregation; every report declares ``holdout_opened: false``;
* no module-level mutable state; every function is deterministic.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .event_semantics import AGG_BUY, AGG_SELL, AGG_UNKNOWN, EVENT_CLASSES
from .mbp1_store_builder import DEFAULT_TICK_SIZE

PROGRAM_ID = "move_exhaustion_v2"
SLICE_ID = "x0_parity"
SCHEMA_VERSION = 1

TICK_SIZE = DEFAULT_TICK_SIZE  # 1/64 point (frozen episodes.yaml)
TRAILING_WINDOW = 5            # within-date trailing 5-minute signed sum
CORR_THRESHOLD = 0.95          # Q7 global correlation threshold (frozen)
Q7_OVERLAP_YEAR = 2026         # frozen Q7 rule: 2026 overlap dates only
MIN_MATCHED_MINUTES = 30       # minimum matched minutes before Q7/Q8 can pass
SIGN_FLIP_EXAMPLE_LIMIT = 5

STORE_REQUIRED_COLUMNS: Tuple[str, ...] = ("ts_event", "event_class", "size", "mid")
MATRIX_REQUIRED_COLUMNS: Tuple[str, ...] = ("bucket_ts", "et_date", "price", "TY_agg_5m")

__all__ = [
    "AGG_BUY",
    "AGG_SELL",
    "AGG_UNKNOWN",
    "CORR_THRESHOLD",
    "MATRIX_REQUIRED_COLUMNS",
    "MIN_MATCHED_MINUTES",
    "Q7_OVERLAP_YEAR",
    "ParityError",
    "PROGRAM_ID",
    "SCHEMA_VERSION",
    "SIGN_FLIP_EXAMPLE_LIMIT",
    "SLICE_ID",
    "STORE_REQUIRED_COLUMNS",
    "TICK_SIZE",
    "TRAILING_WINDOW",
    "aggregate_semantics",
    "fill_minute_gaps",
    "load_store_day",
    "load_store_days",
    "list_store_day_paths",
    "run_parity_report",
    "run_parity_report_from_store",
    "store_minute_frame",
    "trailing_signed_flow_5m",
    "validate_tbbo_matrix",
]


class ParityError(ValueError):
    """Raised when parity inputs violate the documented surface (fail closed)."""


def _as_iso_date(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        value = value.date()
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ParityError(f"invalid trading date {value!r}") from exc


def validate_tbbo_matrix(matrix: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize the frozen TBBO feature matrix surface.

    Required columns are :data:`MATRIX_REQUIRED_COLUMNS`
    (``bucket_ts``, ``et_date``, ``price``, ``TY_agg_5m``).  ``bucket_ts``
    is parsed as a UTC timestamp, ``et_date`` as an ISO trading date,
    ``price``/``TY_agg_5m`` as finite floats.  A duplicate
    ``(et_date, bucket_ts)`` key or any missing/invalid value fails closed
    with :class:`ParityError`.
    """
    if not isinstance(matrix, pd.DataFrame):
        raise ParityError("tbbo_matrix must be a pandas DataFrame")
    missing = [c for c in MATRIX_REQUIRED_COLUMNS if c not in matrix.columns]
    if missing:
        raise ParityError(
            "tbbo matrix missing required columns: "
            f"{sorted(missing)} (expected {list(MATRIX_REQUIRED_COLUMNS)})"
        )
    if not matrix.columns.is_unique:
        raise ParityError("tbbo matrix has duplicate column names")

    out = matrix[list(MATRIX_REQUIRED_COLUMNS)].rename(columns={"TY_agg_5m": "ty_agg_5m"}).copy()
    try:
        out["bucket_ts"] = pd.to_datetime(out["bucket_ts"], utc=True, errors="raise", format="ISO8601")
        out["et_date"] = out["et_date"].map(_as_iso_date)
        out["price"] = pd.to_numeric(out["price"], errors="raise").astype(float)
        out["ty_agg_5m"] = pd.to_numeric(out["ty_agg_5m"], errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ParityError(f"tbbo matrix contains invalid values: {exc}") from exc
    if out["price"].isna().any() or out["ty_agg_5m"].isna().any():
        raise ParityError("tbbo matrix contains missing price or TY_agg_5m values")
    if not np.isfinite(out["price"].to_numpy()).all() or not np.isfinite(
        out["ty_agg_5m"].to_numpy()
    ).all():
        raise ParityError("tbbo matrix contains non-finite price or TY_agg_5m values")
    if out.duplicated(["et_date", "bucket_ts"]).any():
        raise ParityError("tbbo matrix contains duplicate (et_date, bucket_ts) keys")
    return out.sort_values(["et_date", "bucket_ts"], kind="stable").reset_index(drop=True)


_DAY_PARQUET_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.parquet$")


def list_store_day_paths(store_root: str | Path) -> Dict[str, Path]:
    """Top-level ``YYYY-MM-DD.parquet`` files only (skips ``pilot_*`` dirs)."""
    root = Path(store_root)
    if not root.is_dir():
        raise ParityError(f"derived store root not found: {root}")
    found: Dict[str, Path] = {}
    for path in root.iterdir():
        if not path.is_file() or _DAY_PARQUET_NAME.fullmatch(path.name) is None:
            continue
        found[path.stem] = path.resolve()
    return {key: found[key] for key in sorted(found)}


class LazyStoreDays(MappingABC):
    """Load one derived-store day at a time; never materializes the full store."""

    def __init__(self, paths: Mapping[str, str | Path]) -> None:
        self._paths = {str(key): Path(path) for key, path in paths.items()}
        self._keys = tuple(sorted(self._paths))

    def __getitem__(self, key: str) -> pd.DataFrame:
        return load_store_day(self._paths[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


def load_store_day(path: str | Path) -> pd.DataFrame:
    """Read one derived-store parquet and require the documented columns."""
    source = Path(path)
    if not source.is_file():
        raise ParityError(f"derived store parquet not found: {source}")
    frame = pq.read_table(source).to_pandas()
    missing = [c for c in STORE_REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ParityError(
            f"{source.name}: derived store frame missing columns: {sorted(missing)}"
        )
    return frame


def load_store_days(paths: Sequence[str | Path]) -> Dict[str, pd.DataFrame]:
    """Load ``{trading_date: frame}`` from per-date store parquet paths."""
    days: Dict[str, pd.DataFrame] = {}
    for raw in paths:
        frame = load_store_day(raw)
        if "trading_date" not in frame.columns:
            raise ParityError(f"{Path(raw).name}: store frame lacks trading_date column")
        date_key = _as_iso_date(frame["trading_date"].iloc[0])
        if (frame["trading_date"].astype(str) != date_key).any():
            raise ParityError(
                f"{Path(raw).name}: frame contains more than one trading_date"
            )
        if date_key in days:
            raise ParityError(f"duplicate store day for {date_key}")
        days[date_key] = frame
    return days


def _coerce_store_days(
    store_days: Mapping[str, pd.DataFrame] | Sequence[str | Path],
) -> Dict[str, pd.DataFrame]:
    if isinstance(store_days, Mapping):
        days: Dict[str, pd.DataFrame] = {}
        for raw_date, frame in store_days.items():
            date_key = _as_iso_date(raw_date)
            if not isinstance(frame, pd.DataFrame):
                raise ParityError(f"store day {date_key} must be a pandas DataFrame")
            missing = [c for c in STORE_REQUIRED_COLUMNS if c not in frame.columns]
            if missing:
                raise ParityError(
                    f"store day {date_key}: frame missing columns: {sorted(missing)}"
                )
            if "trading_date" in frame.columns:
                frame_dates = set(frame["trading_date"].astype(str))
                if frame_dates and frame_dates != {date_key}:
                    raise ParityError(
                        f"store day {date_key}: frame trading_date column "
                        f"disagrees with key (got {sorted(frame_dates)})"
                    )
            days[date_key] = frame
        return {key: days[key] for key in sorted(days)}
    return load_store_days(list(store_days))


def store_minute_frame(
    frame: pd.DataFrame,
    *,
    trading_date: str | None = None,
) -> pd.DataFrame:
    """UTC 1-minute bins of signed flow and last mid for one derived day.

    ``AGG_BUY`` size contributes +contracts, ``AGG_SELL`` size -contracts,
    and every other event class (including ``AGG_UNKNOWN``) contributes
    zero, per the frozen unknown-side policy.  ``last_mid`` is the mid of
    the final event row within each minute in canonical order.
    """
    missing = [c for c in STORE_REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ParityError(f"store frame missing columns: {sorted(missing)}")
    try:
        ts = pd.to_datetime(frame["ts_event"], utc=True, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ParityError(f"store frame has invalid ts_event values: {exc}") from exc
    if ts.isna().any():
        raise ParityError("store frame has missing ts_event values")
    if trading_date is not None and "trading_date" in frame.columns:
        frame_dates = set(frame["trading_date"].astype(str))
        expected = _as_iso_date(trading_date)
        if frame_dates and frame_dates != {expected}:
            raise ParityError(
                f"store frame trading_date mismatch for {expected}: "
                f"{sorted(frame_dates)}"
            )

    work = frame.copy()
    work["ts_event"] = ts
    size = pd.to_numeric(work["size"], errors="coerce").fillna(0.0).to_numpy()
    cls = work["event_class"].astype(str).to_numpy()
    work["signed_flow"] = np.where(
        cls == AGG_BUY,
        size,
        np.where(cls == AGG_SELL, -size, 0.0),
    )
    work["bucket_ts"] = work["ts_event"].dt.floor("min")
    mid = pd.to_numeric(work["mid"], errors="coerce")
    if mid.isna().any():
        raise ParityError("store frame has missing mid values")
    work["mid"] = mid
    sort_cols = ["ts_event", "sequence"]
    if "source_order" in work.columns:
        sort_cols.append("source_order")

    grouped = (
        work.sort_values(sort_cols, kind="stable")
        .groupby("bucket_ts", sort=True)
        .agg(
            signed_flow=("signed_flow", "sum"),
            last_mid=("mid", "last"),
            n_events=("event_class", "count"),
        )
        .reset_index()
    )
    grouped["signed_flow"] = grouped["signed_flow"].astype(float)
    grouped["last_mid"] = grouped["last_mid"].astype(float)
    grouped["n_events"] = grouped["n_events"].astype(int)
    return grouped


def fill_minute_gaps(
    minute_frame: pd.DataFrame,
    *,
    window: int = TRAILING_WINDOW,
) -> pd.DataFrame:
    """Zero-fill flow and forward-fill the last mid over a 1-minute grid.

    The grid spans the observed minute range of one trading date.  Flow
    gaps become zero (``AGG_BUY``/``AGG_SELL`` absent means no signed flow),
    and ``last_mid`` is carried forward so the "last mid at each minute"
    is well-defined for Q8.  Adds the within-date trailing ``window``-minute
    signed sum (current minute plus the previous ``window - 1`` minutes).
    """
    if not isinstance(window, int) or window <= 0:
        raise ValueError(f"window must be a positive int, got {window!r}")
    if minute_frame.empty:
        return minute_frame.copy()
    start = minute_frame["bucket_ts"].min()
    end = minute_frame["bucket_ts"].max()
    grid = pd.date_range(start, end, freq="min", tz="UTC")
    filled = (
        minute_frame.set_index("bucket_ts")
        .reindex(grid)
        .rename_axis("bucket_ts")
        .reset_index()
    )
    filled["bucket_ts"] = filled["bucket_ts"].dt.tz_convert("UTC")
    filled["signed_flow"] = filled["signed_flow"].fillna(0.0)
    filled["last_mid"] = filled["last_mid"].ffill()
    filled["n_events"] = filled["n_events"].fillna(0).astype(int)
    filled["trailing_5m"] = (
        filled["signed_flow"].rolling(window=window, min_periods=1).sum()
    )
    return filled


def trailing_signed_flow_5m(
    filled: pd.DataFrame,
    *,
    window: int = TRAILING_WINDOW,
) -> pd.Series:
    """The within-date trailing ``window``-minute signed flow series."""
    if not isinstance(window, int) or window <= 0:
        raise ValueError(f"window must be a positive int, got {window!r}")
    if "trailing_5m" in filled.columns:
        return filled["trailing_5m"].astype(float)
    return (
        filled["signed_flow"].rolling(window=window, min_periods=1).sum().astype(float)
    )


def _pearson(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if len(x) < 2:
        return None
    with np.errstate(divide="ignore", invalid="ignore"):
        value = float(np.corrcoef(x, y)[0, 1])
    return value if np.isfinite(value) else None


def _store_minutes_in_matrix_window(
    day: pd.DataFrame,
    matrix_date: pd.DataFrame,
    *,
    window: int,
) -> pd.DataFrame:
    """Store 1-minute bins clipped to the matrix session, then gap-filled.

    The TBBO matrix uses a 01:00 ET window.  Computing the store trailing
    5-minute sum over the full 18:00 ET session would mix overnight flow
    into the first overlap minutes and is not a comparable sign check.
    """
    minutes = store_minute_frame(day)
    if minutes.empty or matrix_date.empty:
        return minutes.iloc[0:0].copy()
    start = matrix_date["bucket_ts"].min()
    end = matrix_date["bucket_ts"].max()
    clipped = minutes[
        (minutes["bucket_ts"] >= start) & (minutes["bucket_ts"] <= end)
    ].copy()
    return fill_minute_gaps(clipped, window=window)


def _q7_for_date(
    day: pd.DataFrame,
    matrix_date: pd.DataFrame,
    *,
    window: int,
) -> Tuple[pd.DataFrame, Optional[float]]:
    minutes = _store_minutes_in_matrix_window(day, matrix_date, window=window)
    joined = minutes.merge(
        matrix_date[["bucket_ts", "ty_agg_5m"]],
        on="bucket_ts",
        how="inner",
    )
    if joined.empty:
        return joined, None
    joined = joined[
        joined["trailing_5m"].notna() & joined["ty_agg_5m"].notna()
    ].reset_index(drop=True)
    if joined.empty:
        return joined, None
    corr = _pearson(
        joined["trailing_5m"].to_numpy(dtype=float),
        joined["ty_agg_5m"].to_numpy(dtype=float),
    )
    return joined, corr


def q7_parity(
    days: Mapping[str, pd.DataFrame],
    matrix: pd.DataFrame,
    *,
    min_matched_minutes: int = MIN_MATCHED_MINUTES,
    window: int = TRAILING_WINDOW,
    sign_flip_example_limit: int = SIGN_FLIP_EXAMPLE_LIMIT,
    date_allowlist: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Q7 cross-source parity: 5-minute signed flow vs ``TY_agg_5m``.

    Reports overlap count/dates, global and per-date Pearson correlation,
    and the exact opposite-sign (sign-flip) count with examples.  The
    frozen Q7 rule is restricted to ``Q7_OVERLAP_YEAR`` (2026) overlap
    dates; earlier/later overlap is ignored here (Q8 still uses all
    overlap dates).  ``date_allowlist`` further restricts pooling to
    dates that Q8 already accepted as the same 1m panel.  Passes only
    when there is at least one 2026 overlap date, at least
    ``min_matched_minutes`` global matched minutes, the global
    correlation is finite and > 0.95, and there are zero sign flips on
    those comparable dates.
    """
    if not isinstance(min_matched_minutes, int) or min_matched_minutes < 0:
        raise ValueError(
            f"min_matched_minutes must be a non-negative int, got {min_matched_minutes!r}"
        )
    matrix_by_date = {
        date_key: group
        for date_key, group in matrix.groupby("et_date", sort=True)
    }
    overlap_dates = sorted(
        date_key
        for date_key in (set(days) & set(matrix_by_date))
        if int(_as_iso_date(date_key)[:4]) == Q7_OVERLAP_YEAR
    )
    if date_allowlist is not None:
        allowed = {str(value) for value in date_allowlist}
        overlap_dates = [date_key for date_key in overlap_dates if date_key in allowed]

    per_date_corr: Dict[str, Optional[float]] = {}
    per_date_matched: Dict[str, int] = {}
    per_date_examples: Dict[str, List[Dict[str, Any]]] = {}
    pooled_store: List[float] = []
    pooled_matrix: List[float] = []
    sign_flip_examples: List[Dict[str, Any]] = []
    n_sign_flips = 0
    n_zero_mismatches = 0
    comparable_dates: List[str] = []

    for date_key in overlap_dates:
        joined, corr = _q7_for_date(days[date_key], matrix_by_date[date_key], window=window)
        if joined.empty:
            per_date_corr[date_key] = None
            per_date_matched[date_key] = 0
            continue
        n_matched = int(len(joined))
        per_date_corr[date_key] = corr
        per_date_matched[date_key] = n_matched
        if n_matched < min_matched_minutes:
            continue
        comparable_dates.append(date_key)
        pooled_store.extend(joined["trailing_5m"].tolist())
        pooled_matrix.extend(joined["ty_agg_5m"].tolist())

        store_sign = np.sign(joined["trailing_5m"].to_numpy(dtype=float))
        matrix_sign = np.sign(joined["ty_agg_5m"].to_numpy(dtype=float))
        flips = (store_sign > 0) & (matrix_sign < 0) | (store_sign < 0) & (
            matrix_sign > 0
        )
        n_sign_flips += int(flips.sum())
        zero_mismatch = ((store_sign == 0) & (matrix_sign != 0)) | (
            (store_sign != 0) & (matrix_sign == 0)
        )
        n_zero_mismatches += int(zero_mismatch.sum())
        for row in joined.loc[flips].itertuples(index=False):
            if len(sign_flip_examples) >= sign_flip_example_limit:
                break
            sign_flip_examples.append(
                {
                    "et_date": date_key,
                    "bucket_ts": pd.Timestamp(row.bucket_ts).isoformat(),
                    "store_5m_signed": float(row.trailing_5m),
                    "ty_agg_5m": float(row.ty_agg_5m),
                }
            )

    n_overlap = len(comparable_dates)
    global_matched = len(pooled_store)
    global_corr = _pearson(
        np.asarray(pooled_store, dtype=float),
        np.asarray(pooled_matrix, dtype=float),
    )

    reasons: List[str] = []
    if n_overlap == 0:
        reasons.append(
            f"no {Q7_OVERLAP_YEAR} overlap dates with >= {min_matched_minutes} "
            "matched minutes between store and TBBO matrix"
        )
    if global_matched < min_matched_minutes:
        reasons.append(
            f"insufficient matched minutes: {global_matched} < {min_matched_minutes}"
        )
    if global_corr is None:
        reasons.append("global correlation undefined (zero variance or no data)")
    elif global_corr <= CORR_THRESHOLD:
        reasons.append(
            f"global correlation {global_corr:.6f} <= {CORR_THRESHOLD}"
        )
    if n_sign_flips:
        reasons.append(f"exact opposite-sign pairs: {n_sign_flips} (must be zero)")

    return {
        "check": "Q7",
        "overlap_count": n_overlap,
        "overlap_dates": comparable_dates,
        "candidate_overlap_dates": overlap_dates,
        "global_matched_minutes": global_matched,
        "global_corr": global_corr,
        "per_date_corr": per_date_corr,
        "per_date_matched_minutes": per_date_matched,
        "sign_flips": n_sign_flips,
        "sign_flip_examples": sign_flip_examples,
        "zero_mismatches": n_zero_mismatches,
        "min_matched_minutes": min_matched_minutes,
        "corr_threshold": CORR_THRESHOLD,
        "passed": bool(
            n_overlap > 0
            and global_matched >= min_matched_minutes
            and global_corr is not None
            and global_corr > CORR_THRESHOLD
            and n_sign_flips == 0
        ),
        "reasons": reasons,
    }


def q8_parity(
    days: Mapping[str, pd.DataFrame],
    matrix: pd.DataFrame,
    *,
    min_matched_minutes: int = MIN_MATCHED_MINUTES,
    tick_size: float = TICK_SIZE,
    window: int = TRAILING_WINDOW,
) -> Dict[str, Any]:
    """Q8 price parity: last store mid per minute vs matrix ``price``.

    The last store mid at each minute (forward-filled across gaps) is
    compared with the matrix price at the same ``(et_date, bucket_ts)``.
    Dates with fewer than ``min_matched_minutes`` matched minutes are
    not comparable.  Dates whose per-date max abs diff exceeds
    ``tick_size`` are not the same 1m panel and are excluded from the
    gate (recorded, not silently passed).  Passes only when the
    remaining comparable dates are nonempty, at least
    ``min_matched_minutes`` minutes match on that pool, and the pool's
    global maximum absolute difference is <= ``tick_size`` (1/64 point).
    """
    if not (isinstance(tick_size, (int, float)) and tick_size > 0):
        raise ValueError(f"tick_size must be a positive number, got {tick_size!r}")
    matrix_by_date = {
        date_key: group
        for date_key, group in matrix.groupby("et_date", sort=True)
    }
    overlap_dates = sorted(set(days) & set(matrix_by_date))

    per_date_max: Dict[str, Optional[float]] = {}
    per_date_matched: Dict[str, int] = {}
    comparable_dates: List[str] = []
    global_max: Optional[float] = None
    global_matched = 0

    for date_key in overlap_dates:
        matrix_date = matrix_by_date[date_key]
        minutes = _store_minutes_in_matrix_window(
            days[date_key], matrix_date, window=window
        )
        joined = minutes.merge(
            matrix_date[["bucket_ts", "price"]],
            on="bucket_ts",
            how="inner",
        )
        joined = joined[joined["last_mid"].notna() & joined["price"].notna()]
        if joined.empty:
            per_date_max[date_key] = None
            per_date_matched[date_key] = 0
            continue
        diff = (joined["last_mid"] - joined["price"]).abs()
        date_max = float(diff.max())
        n_matched = int(len(joined))
        per_date_max[date_key] = date_max
        per_date_matched[date_key] = n_matched
        if n_matched < min_matched_minutes:
            continue
        comparable_dates.append(date_key)
        global_matched += n_matched
        global_max = date_max if global_max is None else max(global_max, date_max)

    excluded_price_mismatch_dates = [
        date_key
        for date_key in comparable_dates
        if per_date_max[date_key] is not None and per_date_max[date_key] > tick_size
    ]
    comparable_price_dates = [
        date_key
        for date_key in comparable_dates
        if date_key not in set(excluded_price_mismatch_dates)
    ]
    mismatch_max = None
    if excluded_price_mismatch_dates:
        mismatch_max = max(float(per_date_max[d]) for d in excluded_price_mismatch_dates)
    gate_max = None
    if comparable_price_dates:
        gate_max = max(float(per_date_max[d]) for d in comparable_price_dates)
    gate_matched = sum(per_date_matched[d] for d in comparable_price_dates)
    n_overlap = len(comparable_price_dates)

    reasons: List[str] = []
    if n_overlap == 0:
        reasons.append(
            f"no overlap dates with >= {min_matched_minutes} matched minutes "
            "and store mid vs matrix price within 1 tick"
        )
        if excluded_price_mismatch_dates:
            reasons.append(
                "excluded_price_mismatch_dates="
                f"{excluded_price_mismatch_dates} "
                f"(mismatch_max_abs_diff={mismatch_max}); "
                "not the same 1m panel (roll/front-month or sparse TY)"
            )
    if gate_matched < min_matched_minutes:
        reasons.append(
            f"insufficient matched minutes: {gate_matched} < {min_matched_minutes}"
        )
    if gate_max is None:
        reasons.append("no comparable store mid / matrix price minutes")
    elif gate_max > tick_size:
        reasons.append(
            f"global max |store mid - matrix price| {gate_max} > tick {tick_size}"
        )

    return {
        "check": "Q8",
        "overlap_count": n_overlap,
        "overlap_dates": comparable_price_dates,
        "candidate_overlap_dates": overlap_dates,
        "minute_comparable_dates": comparable_dates,
        "excluded_price_mismatch_dates": excluded_price_mismatch_dates,
        "global_matched_minutes": gate_matched,
        "global_max_abs_diff": gate_max,
        "candidate_max_abs_diff": global_max,
        "mismatch_max_abs_diff": mismatch_max,
        "per_date_max_abs_diff": per_date_max,
        "per_date_matched_minutes": per_date_matched,
        "min_matched_minutes": min_matched_minutes,
        "tick_size": tick_size,
        "passed": bool(
            n_overlap > 0
            and gate_matched >= min_matched_minutes
            and gate_max is not None
            and gate_max <= tick_size
        ),
        "reasons": reasons,
    }


def aggregate_semantics(
    days: Mapping[str, pd.DataFrame],
) -> Dict[str, Any]:
    """Store-wide event/action/side-class counts for the MBP-1 audit."""
    event_counter: Counter[str] = Counter()
    action_counter: Counter[str] = Counter()
    side_counter: Counter[str] = Counter()
    trade_side_counter: Counter[str] = Counter()
    total_rows = 0
    for date_key in sorted(days):
        frame = days[date_key]
        total_rows += int(len(frame))
        event_counter.update(frame["event_class"].astype(str).tolist())
        if "action" in frame.columns:
            action_counter.update(frame["action"].astype(str).str.upper().tolist())
        if "side" in frame.columns:
            side_counter.update(frame["side"].astype(str).str.upper().tolist())
        if "action" in frame.columns and "side" in frame.columns:
            trades = frame[
                frame["action"].astype(str).str.upper() == "T"
            ]
            trade_side_counter.update(trades["side"].astype(str).str.upper().tolist())
    return {
        "store_dates": sorted(days),
        "n_store_days": len(days),
        "total_rows": total_rows,
        "event_class": {name: int(event_counter.get(name, 0)) for name in EVENT_CLASSES},
        "action": {name: int(action_counter.get(name, 0)) for name in ("A", "C", "M", "R", "T")},
        "side": {name: int(side_counter.get(name, 0)) for name in ("A", "B", "N")},
        "trade_side": {
            name: int(trade_side_counter.get(name, 0)) for name in ("A", "B", "N")
        },
        "note": (
            "store-wide day-level counts for Assumption A1 / unknown-side policy "
            "audit only; no episodes, features, or holdout statistics"
        ),
    }


def run_parity_report(
    store_days: Mapping[str, pd.DataFrame] | Sequence[str | Path],
    tbbo_matrix: pd.DataFrame,
    *,
    min_matched_minutes: int = MIN_MATCHED_MINUTES,
    tick_size: float = TICK_SIZE,
    window: int = TRAILING_WINDOW,
    sign_flip_example_limit: int = SIGN_FLIP_EXAMPLE_LIMIT,
) -> Dict[str, Any]:
    """Run the full X0 Q7/Q8 parity + semantics audit for the store days."""
    days = _coerce_store_days(store_days)
    matrix = validate_tbbo_matrix(matrix=tbbo_matrix)
    q8 = q8_parity(
        days,
        matrix,
        min_matched_minutes=min_matched_minutes,
        tick_size=tick_size,
        window=window,
    )
    q7 = q7_parity(
        days,
        matrix,
        min_matched_minutes=min_matched_minutes,
        window=window,
        sign_flip_example_limit=sign_flip_example_limit,
        date_allowlist=q8["overlap_dates"],
    )
    semantics = aggregate_semantics(days)
    return {
        "program": PROGRAM_ID,
        "slice": SLICE_ID,
        "schema_version": SCHEMA_VERSION,
        "holdout_opened": False,
        "store_dates": sorted(days),
        "matrix_et_dates": sorted(set(matrix["et_date"])),
        "overlap_dates": sorted(set(days) & set(matrix["et_date"])),
        "q7": q7,
        "q8": q8,
        "semantics_audit": semantics,
        "note": (
            "day-level cross-source QC only; no episodes, features, labels, "
            "or holdout outcomes are computed"
        ),
    }


def run_parity_report_from_store(
    store_root: str | Path,
    tbbo_matrix: pd.DataFrame,
    *,
    min_matched_minutes: int = MIN_MATCHED_MINUTES,
    tick_size: float = TICK_SIZE,
    window: int = TRAILING_WINDOW,
    sign_flip_example_limit: int = SIGN_FLIP_EXAMPLE_LIMIT,
) -> Dict[str, Any]:
    """Q7/Q8 + semantics on TBBO-overlap store days, loading one parquet at a time."""
    paths = list_store_day_paths(store_root)
    matrix = validate_tbbo_matrix(matrix=tbbo_matrix)
    overlap_paths = {
        date_key: path
        for date_key, path in paths.items()
        if date_key in set(matrix["et_date"])
    }
    days = LazyStoreDays(overlap_paths)
    q8 = q8_parity(
        days,
        matrix,
        min_matched_minutes=min_matched_minutes,
        tick_size=tick_size,
        window=window,
    )
    q7 = q7_parity(
        days,
        matrix,
        min_matched_minutes=min_matched_minutes,
        window=window,
        sign_flip_example_limit=sign_flip_example_limit,
        date_allowlist=q8["overlap_dates"],
    )
    semantics = aggregate_semantics(days)
    return {
        "program": PROGRAM_ID,
        "slice": SLICE_ID,
        "schema_version": SCHEMA_VERSION,
        "holdout_opened": False,
        "store_dates": sorted(days),
        "n_store_parquets": len(paths),
        "matrix_et_dates": sorted(set(matrix["et_date"])),
        "overlap_dates": sorted(days),
        "q7": q7,
        "q8": q8,
        "semantics_audit": semantics,
        "note": (
            "day-level cross-source QC on TBBO-overlap dates only; no episodes, "
            "features, labels, or holdout outcomes are computed"
        ),
    }
