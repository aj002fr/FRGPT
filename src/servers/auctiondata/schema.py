"""Schema constants and formatters for Treasury auction data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AUCTION_DATA_DIR = PROJECT_ROOT / "auction_data"
WORKSPACE_STATE_DIR = PROJECT_ROOT / "workspace" / "state"
AUCTION_PULLER_STATE_PATH = WORKSPACE_STATE_DIR / "auction_puller_state.json"

FISCAL_DATA_BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
AUCTIONS_QUERY_PATH = "/v1/accounting/od/auctions_query"


@dataclass(frozen=True)
class AuctionTermConfig:
    """Coupon auction term configuration."""

    security_type: str
    security_term: str
    csv_filename: str
    label: str


# Keep the legacy 20Y filename even though FiscalData classifies it as a Bond.
AUCTION_TERM_MAP: tuple[AuctionTermConfig, ...] = (
    AuctionTermConfig("Note", "2-Year", "2y_note_raw.csv", "2Y Note"),
    AuctionTermConfig("Note", "3-Year", "3y_note_raw.csv", "3Y Note"),
    AuctionTermConfig("Note", "5-Year", "5y_note_raw.csv", "5Y Note"),
    AuctionTermConfig("Note", "7-Year", "7y_note_raw.csv", "7Y Note"),
    AuctionTermConfig("Note", "10-Year", "10y_note_raw.csv", "10Y Note"),
    AuctionTermConfig("Bond", "20-Year", "20y_note_raw.csv", "20Y Bond"),
    AuctionTermConfig("Bond", "30-Year", "30y_note_raw.csv", "30Y Bond"),
)


def is_nullish(value: object) -> bool:
    return value is None or str(value).strip().lower() in {"", "null", "none"}


def parse_decimal(value: object) -> Optional[Decimal]:
    if is_nullish(value):
        return None
    try:
        return Decimal(str(value).strip().replace("$", "").replace(",", "").replace("%", ""))
    except (InvalidOperation, ValueError):
        return None


def format_date(value: object) -> str:
    if is_nullish(value):
        return ""
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return raw


def format_money(value: object) -> str:
    amount = parse_decimal(value)
    if amount is None:
        return ""
    return f"${int(amount):,}"


def format_decimal(value: object, decimals: int = 2, trim: bool = True) -> str:
    number = parse_decimal(value)
    if number is None:
        return ""
    text = f"{number:.{decimals}f}"
    if trim:
        text = text.rstrip("0").rstrip(".")
    return text


def format_percent(value: object, decimals: int = 3) -> str:
    number = parse_decimal(value)
    if number is None:
        return ""
    text = f"{number:.{decimals}f}".rstrip("0").rstrip(".")
    return f"{text}%"


def format_text(value: object) -> str:
    if is_nullish(value):
        return ""
    return str(value).strip()


Formatter = Callable[[object], str]


DATE_FIELDS = {
    "record_date",
    "auction_date",
    "issue_date",
    "maturity_date",
    "announcemt_date",
    "back_dated_date",
    "call_date",
    "called_date",
    "dated_date",
    "first_int_payment_date",
    "frn_index_determination_date",
    "mat_date",
    "original_dated_date",
    "original_issue_date",
}

MONEY_FIELDS = {
    "comp_accepted",
    "comp_tendered",
    "direct_bidder_accepted",
    "direct_bidder_tendered",
    "est_pub_held_mat_by_type_amt",
    "fima_noncomp_accepted",
    "fima_noncomp_tendered",
    "max_comp_award",
    "max_noncomp_award",
    "max_single_bid",
    "min_bid_amt",
    "min_strip_amt",
    "min_to_issue",
    "multiples_to_bid",
    "multiples_to_issue",
    "nlp_exclusion_amt",
    "nlp_reporting_threshold",
    "noncomp_accepted",
    "offering_amt",
    "primary_dealer_accepted",
    "primary_dealer_tendered",
    "soma_accepted",
    "soma_holdings",
    "soma_tendered",
    "total_accepted",
    "total_tendered",
    "treas_retail_accepted",
}

PERCENT_FIELDS = {
    "allocation_pctage",
    "avg_med_discnt_rate",
    "avg_med_investment_rate",
    "avg_med_discnt_margin",
    "avg_med_yield",
    "high_discnt_rate",
    "high_investment_rate",
    "high_discnt_margin",
    "high_yield",
    "int_rate",
    "low_discnt_rate",
    "low_investment_rate",
    "low_discnt_margin",
    "low_yield",
}

DECIMAL_FIELDS = {
    "price_per100",
    "accrued_int_per100",
    "accrued_int_per1000",
    "adj_accrued_int_per1000",
    "adj_price",
    "allocation_pctage_decimals",
    "avg_med_price",
    "bid_to_cover_ratio",
    "comp_bid_decimals",
    "currently_outstanding",
    "frn_index_determination_rate",
    "high_price",
    "index_ratio_on_issue_date",
    "low_price",
    "ref_cpi_on_dated_date",
    "ref_cpi_on_issue_date",
    "spread",
    "std_int_payment_per1000",
    "tiin_conversion_factor_per1000",
    "unadj_accrued_int_per1000",
    "unadj_price",
}


API_TO_CSV_FIELD_MAP: dict[str, str] = {
    "record_date": "Record Date",
    "cusip": "CUSIP",
    "security_type": "Security Type",
    "security_term": "Security Term",
    "auction_date": "Auction Date",
    "issue_date": "Issue Date",
    "maturity_date": "Maturity Date",
    "price_per100": "Price per $100",
    "accrued_int_per100": "Accrued Interest per $100",
    "accrued_int_per1000": "Accrued Interest per $1,000",
    "adj_accrued_int_per1000": "Adjusted Accrued Interest per $1,000",
    "adj_price": "Adjusted Price",
    "allocation_pctage": "Allocation Percentage",
    "allocation_pctage_decimals": "Allocation Percentage Decimals",
    "announcemtd_cusip": "Announced CUSIP",
    "announcemt_date": "Announcement Date",
    "auction_format": "Auction Format",
    "avg_med_discnt_rate": "Average / Median Discount Rate",
    "avg_med_investment_rate": "Average / Median Investment Rate",
    "avg_med_price": "Average / Median Price",
    "avg_med_discnt_margin": "Average / Median Discount Margin",
    "avg_med_yield": "Average / Median Yield",
    "back_dated": "Back Dated",
    "back_dated_date": "Back Dated Date",
    "bid_to_cover_ratio": "Bid-to-Cover Ratio",
    "callable": "Callable",
    "call_date": "Call Date",
    "called_date": "Called Date",
    "cash_management_bill_cmb": "Cash Management Bill (CMB)",
    "closing_time_comp": "Closing Time (ET) - Competitive",
    "closing_time_noncomp": "Closing Time (ET) - Noncompetitive",
    "comp_accepted": "Competitive Accepted",
    "comp_bid_decimals": "Competitive Bid Decimals",
    "comp_tendered": "Competitive Tendered",
    "comp_tenders_accepted": "Competitive Tenders Accepted",
    "corpus_cusip": "Corpus CUSIP",
    "cpi_base_reference_period": "CPI Base Reference Period",
    "currently_outstanding": "Currently Outstanding",
    "dated_date": "Dated Date",
    "direct_bidder_accepted": "Direct Bidder Accepted",
    "direct_bidder_tendered": "Direct Bidder Tendered",
    "est_pub_held_mat_by_type_amt": "Estimated Amount of Publicly Held Maturing Securities by Type",
    "fima_included": "FIMA Included",
    "fima_noncomp_accepted": "FIMA Noncompetitive Accepted",
    "fima_noncomp_tendered": "FIMA Noncompetitive Tendered",
    "first_int_period": "First Interest Period",
    "first_int_payment_date": "First Interest Payment Date",
    "floating_rate": "Floating Rate",
    "frn_index_determination_date": "FRN Index Determination Date",
    "frn_index_determination_rate": "FRN Index Determination Rate",
    "high_discnt_rate": "High Discount Rate",
    "high_investment_rate": "High Investment Rate",
    "high_price": "High Price",
    "high_discnt_margin": "High Discount Margin",
    "high_yield": "High Yield",
    "index_ratio_on_issue_date": "Index Ratio On Issue Date",
    "indirect_bidder_accepted": "Indirect Bidder Accepted",
    "indirect_bidder_tendered": "Indirect Bidder Tendered",
    "int_payment_frequency": "Interest Payment Frequency",
    "int_rate": "Interest Rate",
    "low_discnt_rate": "Low Discount Rate",
    "low_investment_rate": "Low Investment Rate",
    "low_price": "Low Price",
    "low_discnt_margin": "Low Discount Margin",
    "low_yield": "Low Yield",
    "mat_date": "Maturing Date",
    "max_comp_award": "Maximum Competitive Award",
    "max_noncomp_award": "Maximum Noncompetitive Award",
    "max_single_bid": "Maximum Single Bid",
    "min_bid_amt": "Minimum Bid Amount",
    "min_strip_amt": "Minimum Strip Amount",
    "min_to_issue": "Minimum To Issue",
    "multiples_to_bid": "Multiples To Bid",
    "multiples_to_issue": "Multiples To Issue",
    "nlp_exclusion_amt": "NLP Exclusion Amount",
    "nlp_reporting_threshold": "NLP Reporting Threshold",
    "noncomp_accepted": "Noncompetitive Accepted",
    "noncomp_tenders_accepted": "Noncompetitive Tenders Accepted",
    "offering_amt": "Offering Amount",
    "original_cusip": "Original CUSIP",
    "original_dated_date": "Original Dated Date",
    "original_issue_date": "Original Issue Date",
    "original_security_term": "Original Security Term",
    "pdf_filenm_announcemt": "PDF Filename - Announcement",
    "pdf_filenm_comp_results": "PDF Filename - Competitive Results",
    "pdf_filenm_noncomp_results": "PDF Filename - Noncompetitive Results",
    "primary_dealer_accepted": "Primary Dealer Accepted",
    "primary_dealer_tendered": "Primary Dealer Tendered",
    "ref_cpi_on_dated_date": "Ref CPI On Dated Date",
    "ref_cpi_on_issue_date": "Ref CPI On Issue Date",
    "reopening": "Reopening",
    "security_term_day_month": "Security Term Day Month",
    "security_term_week_year": "Security Term Week Year",
    "series": "Series",
    "soma_accepted": "SOMA Accepted",
    "soma_holdings": "SOMA Holdings",
    "soma_included": "SOMA Included",
    "soma_tendered": "SOMA Tendered",
    "spread": "Spread",
    "std_int_payment_per1000": "Standard Interest Payment per $1,000",
    "strippable": "Strippable",
    "tiin_conversion_factor_per1000": "TIIN Conversion Factor per $1,000",
    "inflation_index_security": "TIPS",
    "total_accepted": "Total Accepted",
    "total_tendered": "Total Tendered",
    "treas_retail_accepted": "Treasury Retail Accepted",
    "treas_retail_tenders_accepted": "Treasury Retail Tenders Accepted",
    "unadj_accrued_int_per1000": "Unadjusted Accrued Interest per $1,000",
    "unadj_price": "Unadjusted Price",
    "xml_filenm_announcemt": "XML Filename - Announcement",
    "xml_filenm_comp_results": "XML Filename - Competitive Results",
}

API_FIELDS = tuple(API_TO_CSV_FIELD_MAP.keys())


def formatter_for_api_field(api_field: str) -> Formatter:
    if api_field in DATE_FIELDS:
        return format_date
    if api_field in MONEY_FIELDS:
        return format_money
    if api_field in PERCENT_FIELDS:
        return format_percent
    if api_field in DECIMAL_FIELDS:
        return format_decimal
    return format_text


def get_term_config(security_type: str, security_term: str) -> Optional[AuctionTermConfig]:
    for config in AUCTION_TERM_MAP:
        if config.security_type == security_type and config.security_term == security_term:
            return config
    return None

