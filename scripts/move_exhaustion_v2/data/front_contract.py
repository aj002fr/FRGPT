"""Causal front-contract selection for the ZN (10Y T-Note) complex (X0).

PROGRAM.md §1.2 / ``config/frozen/data_store.yaml``:

* Filter each raw MBP-1 pack to ZN quarterly **outrights**, excluding
  calendar spreads by symbol pattern.
* For trading date D, choose the front contract **only** from the previous
  complete session's T-volume (same convention as
  ``aggression_autoresearch_data_build.py``); roll decisions never use
  same-day information.
* The first complete session is a non-eligible bootstrap: no front contract
  is chosen for it (it cannot be selected from same-day information).
* If a prior complete session exists but has no T-volume winner, selection
  fails closed with :class:`FrontContractError`.

Everything here is pure (DataFrame in -> dict/DataFrame out); no archive or
store I/O.  All functions are chunk-friendly: they take one day's frame (or
one prior-session audit) and keep no cross-day state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

ROOT = "ZN"      # CME root for the 10-year T-Note complex
BBG_ROOT = "TY"  # Bloomberg root alias for the same outrights

MONTH_CODES: Dict[int, str] = {3: "H", 6: "M", 9: "U", 12: "Z"}
CODE_MONTHS: Dict[str, int] = {value: key for key, value in MONTH_CODES.items()}

# Authorized replacement batch is 2022-01-01 .. 2026-04-01; single-digit
# contract years are resolved against this deterministic midpoint.
DEFAULT_REFERENCE_DATE = date(2024, 1, 1)

_OUTRIGHT_RE = re.compile(
    r"^(?P<root>ZN|TY)[\s-]*(?P<month>[HMUZ])(?P<year>\d{1,2})(?:\s+COMDTY)?$",
    re.IGNORECASE,
)
_CALENDAR_SPREAD_MARKER = "-"


class FrontContractError(RuntimeError):
    """Raised when a front contract cannot be determined (fail closed)."""


def _as_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(frozen=True)
class Contract:
    """A ZN quarterly outright (March/June/September/December)."""

    month: int
    year: int
    root: str = ROOT

    def __post_init__(self) -> None:
        if self.month not in MONTH_CODES:
            raise ValueError(f"not a quarterly month: {self.month}")
        if not (2000 <= self.year <= 2100):
            raise ValueError(f"unreasonable contract year: {self.year}")

    @property
    def month_code(self) -> str:
        return MONTH_CODES[self.month]

    @property
    def expiry_key(self) -> Tuple[int, int]:
        return (self.year, self.month)

    @property
    def symbol(self) -> str:
        """Databento-style raw symbol, e.g. ``ZNH6``."""
        return f"{self.root}{self.month_code}{self.year % 10}"

    @property
    def symbol_yy(self) -> str:
        """Two-digit-year form, e.g. ``ZNH26`` (some vendors)."""
        return f"{self.root}{self.month_code}{self.year % 100:02d}"

    @property
    def bloomberg(self) -> str:
        return f"{BBG_ROOT}{self.month_code}{self.year % 10} COMDTY"


def parse_contract(
    value: Any, *, reference_date: date | str | None = None
) -> Optional[Contract]:
    """Parse one ZN quarterly outright symbol (or None).

    Accepts CME (``ZNH6``, ``ZNH26``), Bloomberg (``TYH6 COMDTY``) and
    whitespace-separated forms.  Calendar spreads (``ZNZ6-ZNH7``),
    non-quarterly months (``ZNF6``), other roots (``ZBM6``) and unparseable
    values return None.  Single-digit years are resolved against
    ``reference_date`` (default: the authorized-batch midpoint) using the
    audited ``aggression_autoresearch_data_build`` ±6-year heuristic; if the
    resolution is still ambiguous the symbol is rejected rather than guessed.
    """
    cleaned = str(value or "").strip().upper()
    match = _OUTRIGHT_RE.match(cleaned)
    if match is None:
        return None
    month = CODE_MONTHS[match.group("month")]
    digits = match.group("year")
    ref = _as_date(reference_date) if reference_date is not None else DEFAULT_REFERENCE_DATE
    if len(digits) == 2:
        year = (ref.year // 100) * 100 + int(digits)
        if year < ref.year - 6:
            year += 100
        elif year > ref.year + 6:
            year -= 100
    else:
        year = (ref.year // 10) * 10 + int(digits)
        if year < ref.year - 6:
            year += 10
        elif year > ref.year + 6:
            year -= 10
    if abs(year - ref.year) > 6:
        return None  # ambiguous decade/century -> fail closed
    return Contract(month=month, year=year, root=ROOT)


def is_quarterly_outright(
    value: Any, *, reference_date: date | str | None = None
) -> bool:
    """True when the value parses as a ZN quarterly outright."""
    return parse_contract(value, reference_date=reference_date) is not None


def filter_outrights(
    frame: pd.DataFrame,
    *,
    symbol_col: str = "symbol",
    reference_date: date | str | None = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Keep ZN quarterly outright rows; report everything dropped.

    Returns ``(kept, report)``.  ``kept`` carries ``contract`` (canonical
    ``ZNH6`` form), ``expiry_year`` and ``expiry_month`` columns.  The report
    classifies dropped rows as calendar spreads vs everything else.
    """
    if symbol_col not in frame.columns:
        raise FrontContractError(f"frame missing symbol column {symbol_col!r}")
    if frame.empty:
        return frame.copy(), {
            "rows_in": 0,
            "rows_out": 0,
            "rows_dropped": 0,
            "kept_symbols": [],
            "excluded_by_reason": {
                "calendar_spread": 0,
                "non_quarterly_or_foreign": 0,
                "unparseable": 0,
            },
        }
    symbol_values = frame[symbol_col].astype("string")
    parsed_lookup = {
        symbol: parse_contract(symbol, reference_date=reference_date)
        for symbol in symbol_values.dropna().drop_duplicates()
    }
    parsed = symbol_values.map(parsed_lookup)
    valid = parsed.notna().to_numpy()
    kept = frame.loc[valid].copy()
    contracts = [c for c in parsed if c is not None]
    kept["contract"] = [c.symbol for c in contracts]
    kept["expiry_year"] = [c.year for c in contracts]
    kept["expiry_month"] = [c.month for c in contracts]

    dropped = frame.loc[~valid]
    dropped_symbols = dropped[symbol_col].astype("string").fillna("").str.strip()
    is_missing = dropped_symbols.eq("")
    is_spread = dropped_symbols.str.contains(_CALENDAR_SPREAD_MARKER, regex=False)
    report = {
        "rows_in": int(len(frame)),
        "rows_out": int(len(kept)),
        "rows_dropped": int(len(dropped)),
        "kept_symbols": sorted(str(s) for s in kept[symbol_col].astype(str).unique()),
        "excluded_by_reason": {
            "calendar_spread": int(is_spread.sum()),
            "non_quarterly_or_foreign": int((~is_spread & ~is_missing).sum()),
            "unparseable": int(is_missing.sum()),
        },
    }
    return kept, report


def contract_volume_audit(
    frame: pd.DataFrame,
    *,
    symbol_col: str = "symbol",
    volume_col: str = "size",
    action_col: str = "action",
    reference_date: date | str | None = None,
) -> Dict[str, Any]:
    """T-volume audit of one complete session's ZN quarterly outrights.

    Only trade rows (``action == 'T'``) contribute: front selection is
    T-volume based and must never use same-day information for date D.
    Candidates are ranked by descending T-volume, then earliest expiry, then
    lexical contract symbol.  The first candidate is the session's front
    winner.
    """
    if frame.empty:
        return {
            "candidates": [],
            "total_t_volume": 0.0,
            "rows": 0,
            "trade_rows": 0,
        }
    missing = [c for c in (symbol_col, volume_col, action_col) if c not in frame.columns]
    if missing:
        raise FrontContractError(f"frame missing audit columns: {sorted(missing)}")
    symbol_values = frame[symbol_col].astype("string")
    parsed_lookup = {
        symbol: parse_contract(symbol, reference_date=reference_date)
        for symbol in symbol_values.dropna().drop_duplicates()
    }
    parsed = symbol_values.map(parsed_lookup)
    valid = parsed.notna()
    is_trade = frame[action_col].astype(str).str.upper() == "T"
    volume = pd.to_numeric(frame[volume_col], errors="coerce").fillna(0.0)
    work = pd.DataFrame(
        {
            "contract": parsed,
            "volume": volume,
        }
    ).loc[valid & is_trade]
    if work.empty:
        return {
            "candidates": [],
            "total_t_volume": 0.0,
            "rows": int(len(frame)),
            "trade_rows": 0,
        }
    grouped = (
        work.groupby("contract", sort=False)["volume"]
        .sum()
        .reset_index()
    )
    grouped["expiry_key"] = grouped["contract"].map(lambda c: c.expiry_key)
    grouped["symbol"] = grouped["contract"].map(lambda c: c.symbol)
    grouped = grouped.sort_values(
        ["volume", "expiry_key", "symbol"], ascending=[False, True, True]
    ).reset_index(drop=True)
    candidates = [
        {
            "contract": row["symbol"],
            "expiry_key": list(row["expiry_key"]),
            "t_volume": float(row["volume"]),
        }
        for _, row in grouped.iterrows()
    ]
    return {
        "candidates": candidates,
        "total_t_volume": float(work["volume"].sum()),
        "rows": int(len(frame)),
        "trade_rows": int(len(work)),
    }


def select_front_contract(
    prior_audit: Optional[Mapping[str, Any]],
    *,
    trading_date: date | str | None = None,
) -> Dict[str, Any]:
    """Choose the front contract for date D from the PRIOR complete session.

    Fail-closed behavior:
    * ``prior_audit is None`` -> bootstrap mode: no contract is chosen and
      ``research_eligible`` is False (the first complete session cannot use
      same-day information to pick a front contract).
    * Prior audit with no candidates or zero T-volume ->
      :class:`FrontContractError`.
    """
    if prior_audit is None:
        return {
            "mode": "bootstrap_no_prior_session",
            "selected": None,
            "research_eligible": False,
            "source_date": None,
            "prior_share": None,
            "reason": (
                "first complete session is a non-eligible bootstrap: a front "
                "contract may not be chosen from same-day information"
            ),
        }
    candidates = list(prior_audit.get("candidates") or [])
    if not candidates:
        raise FrontContractError(
            f"prior session audit has no candidates (trading_date={trading_date}); "
            "cannot choose a front contract"
        )
    total = float(prior_audit.get("total_t_volume") or 0.0)
    if total <= 0.0:
        raise FrontContractError(
            f"prior session audit has zero T-volume (trading_date={trading_date}); "
            "cannot choose a front contract"
        )
    winner = candidates[0]
    year, month = winner["expiry_key"]
    return {
        "mode": "prior_session_volume",
        "selected": winner["contract"],
        "contract": Contract(month=month, year=year),
        "research_eligible": True,
        "source_date": prior_audit.get("trading_date"),
        "prior_t_volume": float(winner["t_volume"]),
        "prior_share": float(winner["t_volume"]) / total,
        "candidates": candidates,
        "total_t_volume": total,
    }


def candidate_contracts(
    trading_date: date | str, *, quarters_each_side: int = 2
) -> List[Contract]:
    """Quarterly ZN contracts surrounding the trading date (audit helper)."""
    trading = _as_date(trading_date)
    quarter_index = trading.year * 4 + (trading.month - 1) // 3
    contracts: List[Contract] = []
    for offset in range(-quarters_each_side, quarters_each_side + 1):
        index = quarter_index + offset
        year, quarter = divmod(index, 4)
        contracts.append(Contract(month=(quarter + 1) * 3, year=year))
    return contracts
