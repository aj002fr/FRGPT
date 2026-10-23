"""Causal X0 volume prepass and front-selection day planning (Move Exhaustion V2).

This is the *volume prepass / front-selection planning* slice of the X0 data
build (PROGRAM.md §1.2, §2 Q5; ``config/frozen/data_store.yaml``
``builder.front_contract_selection``).  It is deliberately split from the
per-day store builder so the causal front-contract plan can be computed once,
cheaply, over the full archive before any per-day derived frames are built.

Four pieces:

1. :func:`scan_monthly_volume_csv` — DuckDB-side scan of one Databento
   monthly CSV (``.csv`` or ``.csv.zst``).  Only ZN/TY quarterly outrights
   (via :func:`front_contract.parse_contract`) survive.  Each event is
   assigned an ET trading date: events at or after 18:00 ET belong to the
   **next** calendar date (matching ``session.session_bounds``:
   trading date D = prior day 18:00 ET → D 17:00 ET), everything before
   18:00 ET belongs to its own calendar date.  The output is one row per
   ``(trading_date, contract)`` with ``trade_volume`` = sum of ``size`` over
   ``action == 'T'`` rows and ``event_count`` = count of **all** filtered
   MBP-1 rows (any action).  No full raw frame is ever materialized in
   Python: DuckDB streams the filter/aggregation and only the small
   aggregate result crosses into pandas.

2. :func:`merge_monthly_aggregates` — deterministic merge of monthly scans,
   because a trading date can span adjacent monthly files (events from
   day 17:00–18:00 ET of month M and 18:00–23:59 ET of month M−1 belong to
   the same trading date).

3. :func:`build_day_plan` — the causal day plan.  For each expected open
   session D, the front contract is chosen **only** from the immediately
   prior expected open session's T-volume table (roll decisions never use
   same-day information).  Bootstrap/no-prior days are ineligible; the
   selected contract must be present on the current day; the prior winner's
   share must be strictly greater than ``prior_min_share`` (default 0.50);
   volume ties break deterministically by earliest expiry, then canonical
   contract symbol.  No lookahead: every row depends only on rows for dates
   strictly before its trading date.

4. :func:`documented_roll_window_dates` — pure helper returning the calendar
   dates on which an **adjacent-quarter** roll (previous front → next
   quarterly contract) is documented as permitted: 21 calendar days before
   through 10 calendar days after the new contract's delivery-month start
   (inclusive).  A non-adjacent roll gets an empty window (never permitted).

All functions are pure and typed; no module-level state is kept.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import duckdb
import pandas as pd

from .front_contract import Contract, parse_contract

VOLUME_COLUMNS: tuple[str, ...] = (
    "trading_date",
    "contract",
    "expiry_year",
    "expiry_month",
    "trade_volume",
    "event_count",
)

PLAN_COLUMNS: tuple[str, ...] = (
    "trading_date",
    "selection_source_date",
    "selected_contract",
    "selection_share",
    "previous_selected_contract",
    "contract_changed",
    "research_eligible",
    "reason",
)

DEFAULT_PRIOR_MIN_SHARE = 0.50  # Q5: chosen contract share strictly > 50%
ROLL_BEFORE_DAYS = 21           # 21 calendar days before delivery-month start
ROLL_AFTER_DAYS = 10            # 10 calendar days after delivery-month start

_ET_ZONE = "America/New_York"
_ET_SESSION_HOUR = 18  # events at or after 18:00 ET belong to the next date
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_VOLUME_DTYPES: Dict[str, str] = {
    "trading_date": "object",
    "contract": "object",
    "expiry_year": "int64",
    "expiry_month": "int64",
    "trade_volume": "int64",
    "event_count": "int64",
}


class VolumePrepassError(RuntimeError):
    """Raised when a volume scan/merge/plan cannot be built (fail closed)."""


def _as_date(value: date | str | pd.Timestamp) -> date:
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _quote_ident(name: str, *, label: str) -> str:
    """Validate + double-quote a SQL identifier from caller-supplied columns."""
    if not isinstance(name, str) or _IDENT_RE.fullmatch(name) is None:
        raise VolumePrepassError(
            f"{label} must be a plain SQL identifier, got {name!r}"
        )
    return f'"{name}"'


def databento_timestamp_sql(quoted_ident: str) -> str:
    """UTC timestamp from Databento epoch-ns integers or ISO timestamp strings.

    Authorized batch CSV is ``pretty_ts=False`` (integer nanoseconds). Synthetic
    tests use ISO-8601 strings. Both must yield a naive UTC TIMESTAMP.
    """
    return (
        f"CASE WHEN regexp_matches(trim(CAST({quoted_ident} AS VARCHAR)), '^[0-9]+$') "
        f"THEN to_timestamp(CAST({quoted_ident} AS DOUBLE) / 1000000000.0) "
        f"ELSE CAST({quoted_ident} AS TIMESTAMP) END"
    )


def databento_price_sql(quoted_ident: str) -> str:
    """Human prices from Databento fixed-point integers or already-scaled doubles.

    Authorized batch CSV is ``pretty_px=False`` (price * 1e9). Values with
    ``abs >= 1e6`` are divided by 1e9; ordinary test prices (~100) are kept.
    """
    return (
        f"CASE WHEN abs(CAST({quoted_ident} AS DOUBLE)) >= 1000000.0 "
        f"THEN CAST({quoted_ident} AS DOUBLE) / 1000000000.0 "
        f"ELSE CAST({quoted_ident} AS DOUBLE) END"
    )


def _read_csv_options(ts_col: str, symbol_col: str, action_col: str, size_col: str) -> str:
    """Explicit ``read_csv_auto`` types so DuckDB never re-infers our columns."""
    ts_q = _quote_ident(ts_col, label="ts_col")
    symbol_q = _quote_ident(symbol_col, label="symbol_col")
    action_q = _quote_ident(action_col, label="action_col")
    size_q = _quote_ident(size_col, label="size_col")
    types = (
        f"{{"
        f"{ts_q}: 'VARCHAR', {symbol_q}: 'VARCHAR', "
        f"{action_q}: 'VARCHAR', {size_q}: 'BIGINT'"
        f"}}"
    )
    return (
        f"read_csv_auto(?, header=true, types={types})"
    )


def _empty_volume_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {column: pd.Series(dtype=_VOLUME_DTYPES[column]) for column in VOLUME_COLUMNS}
    )


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise VolumePrepassError(f"{label} missing columns: {sorted(missing)}")
    if not frame.columns.is_unique:
        raise VolumePrepassError(f"{label} has duplicate column names")


def scan_monthly_volume_csv(
    path: str | Path,
    *,
    reference_date: date | str | None = None,
    ts_col: str = "ts_event",
    symbol_col: str = "symbol",
    action_col: str = "action",
    size_col: str = "size",
) -> pd.DataFrame:
    """DuckDB scan of one Databento monthly CSV (``.csv`` or ``.csv.zst``).

    Filters to ZN/TY quarterly outrights, assigns the ET trading date
    (events at or after 18:00 ET belong to the next date), and aggregates
    per ``(trading_date, contract)``:

    * ``trade_volume`` — sum of ``size`` where ``action == 'T'``;
    * ``event_count`` — count of all filtered MBP-1 rows (any action).

    Only the per-day/per-raw-symbol aggregate crosses into Python, where the
    canonical symbol parser is applied. The raw frame is never materialized.

    Returns
    -------
    pd.DataFrame
        Sorted by ``(trading_date, contract)`` with :data:`VOLUME_COLUMNS`
        and fixed dtypes.  Empty (schema only) when no ZN/TY quarterly
        outright exists in the file.

    Raises
    ------
    VolumePrepassError
        Missing/unreadable file, unsupported suffix, invalid column names,
        or an unparseable MBP-1 value in the projected columns.
    """
    source = Path(path)
    if not source.is_file():
        raise VolumePrepassError(f"monthly CSV not found: {source}")
    lowered = source.name.lower()
    if not (lowered.endswith(".csv") or lowered.endswith(".csv.zst")):
        raise VolumePrepassError(
            f"unsupported monthly CSV suffix: {source.name!r} "
            "(expected .csv or .csv.zst)"
        )
    ts_q = _quote_ident(ts_col, label="ts_col")
    symbol_q = _quote_ident(symbol_col, label="symbol_col")
    action_q = _quote_ident(action_col, label="action_col")
    size_q = _quote_ident(size_col, label="size_col")

    con = duckdb.connect()
    try:
        con.execute("SET TimeZone = 'UTC'")  # naive Databento ts_event is UTC

        # One archive pass: aggregate plausible ZN/TY symbols in DuckDB, then
        # apply the canonical Python parser to the small aggregate only.
        volume_query = f"""
            WITH base AS (
                SELECT {databento_timestamp_sql(ts_q)} AS ts_event,
                       UPPER(TRIM(CAST({symbol_q} AS VARCHAR))) AS raw_symbol,
                       UPPER(TRIM(CAST({action_q} AS VARCHAR))) AS action,
                       {size_q} AS size
                FROM {_read_csv_options(ts_col, symbol_col, action_col, size_col)}
                WHERE LEFT(UPPER(TRIM(CAST({symbol_q} AS VARCHAR))), 2)
                      IN ('ZN', 'TY')
            ),
            local AS (
                SELECT raw_symbol, action, size,
                       (ts_event::TIMESTAMPTZ) AT TIME ZONE '{_ET_ZONE}' AS local_ts
                FROM base
            ),
            dated AS (
                SELECT raw_symbol, action, size,
                       strftime(
                           CASE
                               WHEN EXTRACT(HOUR FROM local_ts) >= {_ET_SESSION_HOUR}
                               THEN CAST(local_ts AS DATE) + INTERVAL '1 day'
                               ELSE CAST(local_ts AS DATE)
                           END,
                           '%Y-%m-%d'
                       ) AS trading_date
                FROM local
            )
            SELECT trading_date, raw_symbol,
                   SUM(CASE WHEN action IN ('T', 'F') THEN COALESCE(size, 0) ELSE 0 END)
                       AS trade_volume,
                   SUM(CASE WHEN action IN ('T', 'F') AND (size IS NULL OR size <= 0)
                            THEN 1 ELSE 0 END) AS invalid_trade_sizes,
                   COUNT(*) AS event_count
            FROM dated
            GROUP BY trading_date, raw_symbol
            ORDER BY trading_date, raw_symbol
        """
        try:
            out = con.execute(volume_query, [str(source)]).df()
        except Exception as exc:
            raise VolumePrepassError(
                f"{source.name}: DuckDB volume scan failed: {exc}"
            ) from exc
    finally:
        con.close()

    if out.empty:
        return _empty_volume_frame()

    raw_to_contract = {
        raw: parse_contract(raw, reference_date=reference_date)
        for raw in out["raw_symbol"].drop_duplicates()
    }
    out = out[out["raw_symbol"].map(raw_to_contract).notna()].copy()
    if out.empty:
        return _empty_volume_frame()
    invalid = int(out["invalid_trade_sizes"].sum())
    if invalid:
        raise VolumePrepassError(
            f"{source.name}: {invalid} trade rows have missing/nonpositive size"
        )
    canonical_to_contract: Dict[str, Contract] = {
        contract.symbol: contract
        for contract in raw_to_contract.values()
        if contract is not None
    }

    # Canonicalize raw symbols (e.g. "TYH6 COMDTY" -> "ZNH6") and re-fold
    # aliases of the same contract on the same date.
    result = out.rename(columns={"raw_symbol": "contract"})
    result["contract"] = result["contract"].map(
        lambda raw: raw_to_contract[raw].symbol
    )
    result["expiry_year"] = result["contract"].map(
        lambda canonical: canonical_to_contract[canonical].year
    )
    result["expiry_month"] = result["contract"].map(
        lambda canonical: canonical_to_contract[canonical].month
    )
    result = (
        result.groupby(["trading_date", "contract"], as_index=False)
        .agg(
            trade_volume=("trade_volume", "sum"),
            event_count=("event_count", "sum"),
            expiry_year=("expiry_year", "first"),
            expiry_month=("expiry_month", "first"),
        )
    )
    result = (
        result.sort_values(["trading_date", "contract"], kind="stable")
        .reset_index(drop=True)
    )
    return result[list(VOLUME_COLUMNS)].astype(_VOLUME_DTYPES)


def merge_monthly_aggregates(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Deterministically merge per-month volume aggregates.

    A trading date can span adjacent monthly files (the 18:00 ET boundary
    splits calendar days), so the same ``(trading_date, contract)`` may
    appear in two scans.  Volumes and event counts are summed; expiry
    metadata must agree for the same canonical contract across all inputs
    (fail closed otherwise).  Output is sorted by
    ``(trading_date, contract)`` and is invariant to input order.
    """
    collected = [frame for frame in frames]
    for frame in collected:
        _require_columns(frame, VOLUME_COLUMNS, "volume aggregate")
    if not collected:
        return _empty_volume_frame()

    combined = pd.concat(collected, ignore_index=True)
    combined["trading_date"] = combined["trading_date"].map(_as_iso_date)
    combined["contract"] = combined["contract"].astype(str)
    for column in ("expiry_year", "expiry_month"):
        combined[column] = pd.to_numeric(combined[column], errors="raise")

    expiry_nunique = combined.groupby("contract")[
        ["expiry_year", "expiry_month"]
    ].nunique()
    inconsistent = expiry_nunique[
        (expiry_nunique["expiry_year"] > 1)
        | (expiry_nunique["expiry_month"] > 1)
    ]
    if not inconsistent.empty:
        bad = ", ".join(sorted(inconsistent.index))
        raise VolumePrepassError(
            f"inconsistent expiry metadata for contract(s): {bad}"
        )

    merged = (
        combined.groupby(["trading_date", "contract"], as_index=False)
        .agg(
            trade_volume=("trade_volume", "sum"),
            event_count=("event_count", "sum"),
            expiry_year=("expiry_year", "first"),
            expiry_month=("expiry_month", "first"),
        )
    )
    merged = (
        merged.sort_values(["trading_date", "contract"], kind="stable")
        .reset_index(drop=True)
    )
    return merged[list(VOLUME_COLUMNS)].astype(_VOLUME_DTYPES)


def _as_iso_date(value: Any) -> str:
    return _as_date(value).isoformat()


def _rank_candidates(grouped: pd.DataFrame) -> pd.DataFrame:
    """Deterministic front-candidate ranking for one session's T-volume."""
    return grouped.sort_values(
        ["trade_volume", "expiry_year", "expiry_month", "contract"],
        ascending=[False, True, True, True],
        kind="stable",
    ).reset_index(drop=True)


def _prior_winner(
    rows: Optional[pd.DataFrame],
    prior_date: str,
    *,
    prior_min_share: float,
) -> Tuple[Optional[str], Optional[float], Optional[str]]:
    """Winner + share from the prior session's volume table, or a failure reason."""
    if rows is None or rows.empty:
        return None, None, (
            f"prior expected open session {prior_date} has no ZN volume aggregate"
        )
    grouped = (
        rows.groupby("contract", as_index=False)
        .agg(
            trade_volume=("trade_volume", "sum"),
            event_count=("event_count", "sum"),
            expiry_year=("expiry_year", "first"),
            expiry_month=("expiry_month", "first"),
        )
    )
    total = float(grouped["trade_volume"].sum())
    if total <= 0.0:
        return None, None, (
            f"prior expected open session {prior_date} has zero ZN T-volume"
        )
    winner = _rank_candidates(grouped).iloc[0]
    share = float(winner["trade_volume"]) / total
    if share <= prior_min_share:
        return None, None, (
            f"prior-session front share {share:.6f} is not strictly greater "
            f"than {prior_min_share:.2f}"
        )
    return str(winner["contract"]), share, None


def build_day_plan(
    volume: pd.DataFrame,
    sessions: Sequence[date | str],
    *,
    prior_min_share: float = DEFAULT_PRIOR_MIN_SHARE,
) -> pd.DataFrame:
    """Causal front-selection day plan over expected open sessions.

    For every expected open session date ``D`` (ascending), the front
    contract is selected only from the **immediately prior** expected open
    session's volume table:

    * bootstrap/no-prior days are ``research_eligible=False``;
    * the prior winner's share must be strictly > ``prior_min_share``
      (default 0.50);
    * the selected contract must be present on ``D`` itself;
    * volume ties break by earliest expiry, then canonical contract symbol.

    Output columns are :data:`PLAN_COLUMNS`.  ``selection_share`` is the
    prior winner's share of the prior session's total T-volume (NaN when no
    selection).  ``previous_selected_contract`` is the immediately prior
    session's selection, so ``contract_changed`` is True exactly when a
    different contract is selected.  The plan is a single forward pass: no
    row uses information from its own date or any later date.
    """
    _require_columns(volume, VOLUME_COLUMNS, "volume")
    if not (0.0 < float(prior_min_share) < 1.0):
        raise ValueError(f"prior_min_share must be in (0, 1), got {prior_min_share!r}")

    dates = [_as_iso_date(value) for value in sessions]
    if not dates:
        raise ValueError("sessions must contain at least one expected open session")
    if any(current <= previous for previous, current in zip(dates, dates[1:])):
        raise ValueError("sessions must be strictly ascending and unique")

    work = volume.copy()
    work["trading_date"] = work["trading_date"].map(_as_iso_date)
    work["contract"] = work["contract"].astype(str)
    for column in ("expiry_year", "expiry_month", "trade_volume", "event_count"):
        work[column] = pd.to_numeric(work[column], errors="raise")

    volume_by_date: Dict[str, pd.DataFrame] = {
        trading_date: group
        for trading_date, group in work.groupby("trading_date", sort=True)
    }

    records: List[Dict[str, Any]] = []
    previous_selected: Optional[str] = None
    for index, trading_date in enumerate(dates):
        prior_date = dates[index - 1] if index > 0 else None
        if prior_date is None:
            records.append(
                {
                    "trading_date": trading_date,
                    "selection_source_date": None,
                    "selected_contract": None,
                    "selection_share": float("nan"),
                    "previous_selected_contract": previous_selected,
                    "contract_changed": False,
                    "research_eligible": False,
                    "reason": (
                        "no prior expected open session "
                        "(bootstrap day; ineligible by design)"
                    ),
                }
            )
            previous_selected = None
            continue

        winner, share, fail_reason = _prior_winner(
            volume_by_date.get(prior_date),
            prior_date,
            prior_min_share=float(prior_min_share),
        )
        selected: Optional[str]
        reason: str
        if winner is None:
            selected = None
            eligible = False
            reason = fail_reason or "prior winner could not be determined"
        else:
            current_rows = volume_by_date.get(trading_date)
            current_contracts = (
                set(current_rows["contract"]) if current_rows is not None else set()
            )
            if winner not in current_contracts:
                selected = None
                eligible = False
                reason = (
                    f"selected contract {winner} is not present on trading "
                    f"date {trading_date}"
                )
            else:
                selected = winner
                eligible = True
                reason = (
                    f"prior-session volume winner {winner} with share "
                    f"{share:.6f} > {prior_min_share:.2f}"
                )

        contract_changed = (
            previous_selected is not None
            and selected is not None
            and selected != previous_selected
        )
        records.append(
            {
                "trading_date": trading_date,
                "selection_source_date": prior_date,
                "selected_contract": selected,
                "selection_share": share if eligible else float("nan"),
                "previous_selected_contract": previous_selected,
                "contract_changed": contract_changed,
                "research_eligible": eligible,
                "reason": reason,
            }
        )
        previous_selected = selected

    plan = pd.DataFrame(records, columns=list(PLAN_COLUMNS))
    plan["selection_share"] = plan["selection_share"].astype("float64")
    plan["contract_changed"] = plan["contract_changed"].astype("bool")
    plan["research_eligible"] = plan["research_eligible"].astype("bool")
    return plan


def _next_quarter_index(contract: Contract) -> Tuple[int, int]:
    """(year, quarter-index) of the contract's delivery quarter."""
    return contract.year * 4 + contract.month // 3


def documented_roll_window_dates(
    previous_contract: Contract | str,
    new_contract: Contract | str,
    *,
    days_before: int = ROLL_BEFORE_DAYS,
    days_after: int = ROLL_AFTER_DAYS,
    reference_date: date | str | None = None,
) -> List[date]:
    """Calendar dates on which an adjacent-quarter roll is documented.

    A roll from ``previous_contract`` to ``new_contract`` is permitted only
    when ``new_contract`` is the immediately following quarterly expiry
    (adjacent quarter).  The window is inclusive:

        [new delivery-month start - ``days_before``,
         new delivery-month start + ``days_after``]

    with the frozen defaults ``days_before=21`` and ``days_after=10``
    (PROGRAM.md §2 Q5 roll windows).  Non-adjacent pairs return an empty
    list: the roll is never documented/permitted.

    Raises
    ------
    VolumePrepassError
        Either contract cannot be parsed as a ZN/TY quarterly outright.
    ValueError
        Non-negative ``days_before``/``days_after`` required.
    """
    if (
        not isinstance(days_before, int)
        or days_before < 0
        or not isinstance(days_after, int)
        or days_after < 0
    ):
        raise ValueError("days_before and days_after must be non-negative integers")

    previous = (
        previous_contract
        if isinstance(previous_contract, Contract)
        else parse_contract(previous_contract, reference_date=reference_date)
    )
    new = (
        new_contract
        if isinstance(new_contract, Contract)
        else parse_contract(new_contract, reference_date=reference_date)
    )
    if previous is None or new is None:
        raise VolumePrepassError(
            f"cannot resolve quarterly outrights "
            f"{previous_contract!r} -> {new_contract!r}"
        )
    if _next_quarter_index(new) != _next_quarter_index(previous) + 1:
        return []  # non-adjacent roll: never permitted

    delivery_start = date(new.year, new.month, 1)
    first = delivery_start - timedelta(days=days_before)
    last = delivery_start + timedelta(days=days_after)
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
