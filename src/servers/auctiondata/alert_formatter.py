"""Telegram alert formatting for Treasury auction results."""

from __future__ import annotations

from html import escape

from data_setup.derive_auction_fields import derive_nomura_fields, parse_money


def _short_money(value: str) -> str:
    amount = parse_money(value)
    if amount is None:
        return value or ""
    if abs(amount) >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    return f"${amount:,.0f}"


def derive_alert_fields(csv_row: dict[str, str]) -> dict[str, str]:
    """Compute the same Nomura-style fields used in processed CSV files."""

    return derive_nomura_fields(csv_row, percent_decimals=1, ratio_decimals=2, gap_decimals=1)


def format_auction_alert(csv_row: dict[str, str], term_label: str) -> str:
    """Return an HTML Telegram message for a new auction result."""

    derived = derive_alert_fields(csv_row)
    auction_date = csv_row.get("Auction Date", "")
    cusip = csv_row.get("CUSIP", "")
    reopening = csv_row.get("Reopening", "")
    series = csv_row.get("Series", "")
    size = _short_money(derived.get("Size", ""))

    lines = [
        f"<b>{escape(term_label)} Auction Result</b>",
        f"<i>{escape(auction_date)}</i>",
        "",
        f"<b>CUSIP:</b> {escape(cusip)}"
        + (f" | <b>Series:</b> {escape(series)}" if series else "")
        + (f" | <b>Reopening:</b> {escape(reopening)}" if reopening else ""),
        f"<b>Size:</b> {escape(size)} | <b>Coupon:</b> {escape(derived.get('Coupon', ''))} | <b>Stop:</b> {escape(derived.get('Stop', ''))}",
        f"<b>Bid/Cover:</b> {escape(derived.get('Bid/Cover', ''))} | <b>Yld Gap:</b> {escape(derived.get('Yld Gap', ''))}",
        "",
        f"<b>Dealer:</b> {escape(derived.get('Dealer%', ''))} (Hit {escape(derived.get('Dlr Hit', ''))})",
        f"<b>Direct:</b> {escape(derived.get('Direct%', ''))} (Hit {escape(derived.get('Dir Hit', ''))})",
        f"<b>Indirect:</b> {escape(derived.get('Indirect%', ''))} (Hit {escape(derived.get('Indir Hit', ''))})",
    ]
    return "\n".join(lines)

