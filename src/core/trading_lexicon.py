"""Trading lexicon builder for Telegram chats.

Aggregates domain-specific and potentially ambiguous terms from Telegram
messages into an in-memory lexicon that can be exported to JSON.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Set

from config.settings import TRADING_LEXICON_PATH
from src.bus.file_bus import write_atomic
from src.core.text_normalization import NormalizedMessage, normalize_message

logger = logging.getLogger(__name__)


AMBIGUOUS_TERMS: Set[str] = {
    # Position / direction
    "short",
    "long",
    "flat",
    # Curve / structure
    "curve",
    "roll",
    "roll-down",
    "butterfly",
    "fly",
    "belly",
    "wings",
    # Pricing / basis
    "basis",
    "spread",
    "box",
    "front",
    "back",
}

# Very common non-domain words we always ignore as terms
STOPWORDS: Set[str] = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "at",
    "by",
    "from",
    "this",
    "that",
    "is",
    "are",
    "was",
    "were",
    "be",
    "as",
    "it",
    "we",
    "you",
    "they",
    "i",
}

# Tokens that strongly indicate a financial trading context when they appear
FINANCE_MARKERS: Set[str] = {
    "futures",
    "future",
    "bond",
    "bonds",
    "swap",
    "swaps",
    "option",
    "options",
    "gamma",
    "delta",
    "vega",
    "theta",
    "dv01",
    "duration",
    "convexity",
    "ytm",
    "yield",
    "yields",
    "spread",
    "spreads",
    "curve",
    "flatten",
    "steepen",
    "tenor",
    "tenors",
    "leg",
    "legs",
    "basis",
    "roll",
    "pnl",
    "p&l",
    "bid",
    "ask",
    "offer",
    "mid",
    "notional",
    "size",
    "strike",
    "expiry",
    "expiration",
    "coupon",
}


@dataclass
class TermStats:
    frequency: int = 0
    first_seen: Optional[int] = None
    last_seen: Optional[int] = None
    example_sentences: List[str] = None  # type: ignore[assignment]
    finance_hits: int = 0

    def __post_init__(self) -> None:
        if self.example_sentences is None:
            self.example_sentences = []


@dataclass
class LexiconEntry:
    term: str
    kind: str
    is_ambiguous: bool
    meaning_shift: bool
    stats: Dict[str, object]
    example_sentences: List[str]
    notes: Optional[str] = None


def _classify_kind(token: str) -> str:
    """Classify term kind based on simple heuristics."""
    if not token:
        return "other"

    # Futures-style tickers: e.g., ZNZ5, ESU4
    if len(token) <= 6 and token.isupper() and any(ch.isdigit() for ch in token):
        return "ticker"

    # Dot-separated symbols (e.g., XCME.OZN.AUG25.113.C)
    if "." in token and any(ch.isdigit() for ch in token):
        return "ticker"

    # Curve / spread syntax like 2s10s, 5s30s, 2s5s10s
    if "s" in token and any(ch.isdigit() for ch in token):
        return "syntax"

    # Acronyms: mostly uppercase letters/digits, short length
    if len(token) <= 6 and token.upper() == token and any(ch.isalpha() for ch in token):
        return "acronym"

    return "shorthand" if token.islower() else "other"


def _is_ambiguous(token: str) -> bool:
    return token.lower() in AMBIGUOUS_TERMS


def _epoch_to_iso(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def build_lexicon_from_messages(
    messages: Iterable[object],
    min_frequency: int = 2,
    max_examples: int = 3,
) -> List[LexiconEntry]:
    """Aggregate a trading lexicon from Telegram messages."""
    stats: Dict[str, TermStats] = defaultdict(TermStats)

    for msg in messages:
        # Support both dicts (from JSON) and objects with attributes (if used directly)
        text = getattr(msg, "text", None)
        if text is None and isinstance(msg, dict):
            text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        date = getattr(msg, "date", None)
        if date is None and isinstance(msg, dict):
            date = msg.get("date")
        if not isinstance(date, int):
            continue

        norm: NormalizedMessage = normalize_message(text)

        # Determine whether this message clearly occurs in a trading context
        tokens_lower = [t.lower() for t in norm.tokens]
        has_fin_marker = any(t in FINANCE_MARKERS for t in tokens_lower)
        has_ticker_like = any(_classify_kind(t) in {"ticker", "syntax"} for t in norm.tokens)
        in_financial_context = has_fin_marker or has_ticker_like

        for token in norm.tokens:
            # Filter trivial tokens
            if len(token) <= 1 or token.isdigit():
                continue
            if token.lower() in STOPWORDS:
                continue

            # If message is not clearly financial and token is not in our
            # ambiguous trading set, skip it to avoid generic chat vocabulary.
            if not in_financial_context and token.lower() not in AMBIGUOUS_TERMS:
                continue

            term_stats = stats[token]
            term_stats.frequency += 1
            if term_stats.first_seen is None or date < int(term_stats.first_seen):
                term_stats.first_seen = date
            if term_stats.last_seen is None or date > int(term_stats.last_seen):
                term_stats.last_seen = date
            if in_financial_context:
                term_stats.finance_hits += 1

            if len(term_stats.example_sentences) < max_examples and norm.cleaned_text not in term_stats.example_sentences:
                term_stats.example_sentences.append(norm.cleaned_text)

    entries: List[LexiconEntry] = []
    for term, s in stats.items():
        if s.frequency < min_frequency:
            continue

        # Drop terms that almost never appear in clear financial context,
        # unless they are explicitly in our ambiguous trading list.
        lower_term = term.lower()
        if s.finance_hits == 0 and lower_term not in AMBIGUOUS_TERMS:
            continue

        kind = _classify_kind(term)
        ambiguous = _is_ambiguous(term)
        meaning_shift = ambiguous  # heuristic: all ambiguous domain terms shift meaning

        entry = LexiconEntry(
            term=term,
            kind=kind,
            is_ambiguous=ambiguous,
            meaning_shift=meaning_shift,
            stats={
                "frequency": s.frequency,
                "first_seen": _epoch_to_iso(s.first_seen),
                "last_seen": _epoch_to_iso(s.last_seen),
            },
            example_sentences=s.example_sentences,
            notes=None,
        )
        entries.append(entry)

    # Sort by descending frequency then lexicographically
    entries.sort(key=lambda e: (-int(e.stats["frequency"]), e.term))
    logger.info("Built lexicon with %s terms (min_frequency=%s)", len(entries), min_frequency)
    return entries


def export_lexicon_to_json(entries: List[LexiconEntry], path: Optional[str] = None) -> None:
    """Export lexicon entries to TRADING_LEXICON_PATH (or provided path)."""
    target = TRADING_LEXICON_PATH if path is None else path

    serializable = [asdict(e) for e in entries]
    write_atomic(target, serializable)
    logger.info("Wrote trading lexicon JSON with %s terms to %s", len(entries), target)


