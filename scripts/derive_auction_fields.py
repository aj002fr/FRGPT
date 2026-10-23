#!/usr/bin/env python3
"""
Derive auction table fields from Treasury auction CSV exports.

Default mode adds basic fields derived from Securities.csv.
Nomura mode uses exact Nomura-style column labels.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Optional


def parse_money(value: str) -> Optional[float]:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    raw = raw.replace("$", "").replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_float(value: str) -> Optional[float]:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def parse_percent(value: str) -> Optional[float]:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    raw = raw.replace("%", "")
    try:
        return float(raw)
    except ValueError:
        return None


def fmt_percent(value: Optional[float], decimals: int) -> str:
    if value is None:
        return ""
    return f"{value * 100:.{decimals}f}%"


def fmt_ratio(value: Optional[float], decimals: int) -> str:
    if value is None:
        return ""
    return f"{value:.{decimals}f}"


def fmt_gap(value: Optional[float], decimals: int) -> str:
    if value is None:
        return ""
    return f"{value:.{decimals}f}"


def safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def derive_fields(
    row: dict,
    percent_decimals: int,
    ratio_decimals: int,
    include_dealer_percent: bool,
) -> dict:
    competitive_accepted = parse_money(row.get("Competitive Accepted", ""))
    direct_accepted = parse_money(row.get("Direct Bidder Accepted", ""))
    direct_tendered = parse_money(row.get("Direct Bidder Tendered", ""))
    indirect_accepted = parse_money(row.get("Indirect Bidder Accepted", ""))
    indirect_tendered = parse_money(row.get("Indirect Bidder Tendered", ""))

    bid_cover = parse_float(row.get("Bid-to-Cover Ratio", ""))

    direct_pct = safe_div(direct_accepted, competitive_accepted)
    indirect_pct = safe_div(indirect_accepted, competitive_accepted)
    dir_hit = safe_div(direct_accepted, direct_tendered)
    indir_hit = safe_div(indirect_accepted, indirect_tendered)

    dealer_pct = None
    if include_dealer_percent and direct_pct is not None and indirect_pct is not None:
        dealer_pct = max(0.0, 1.0 - direct_pct - indirect_pct)

    derived = {
        "Auction": row.get("Auction Date", ""),
        "Bid/Cover": fmt_ratio(bid_cover, ratio_decimals),
        "Direct%": fmt_percent(direct_pct, percent_decimals),
        "Indirect%": fmt_percent(indirect_pct, percent_decimals),
        "Dir Hit": fmt_percent(dir_hit, percent_decimals),
        "Indir Hit": fmt_percent(indir_hit, percent_decimals),
    }

    if include_dealer_percent:
        derived["Dealer%"] = fmt_percent(dealer_pct, percent_decimals)

    return derived


def derive_nomura_fields(
    row: dict,
    percent_decimals: int,
    ratio_decimals: int,
    gap_decimals: int,
) -> dict:
    competitive_accepted = parse_money(row.get("Competitive Accepted", ""))
    direct_accepted = parse_money(row.get("Direct Bidder Accepted", ""))
    direct_tendered = parse_money(row.get("Direct Bidder Tendered", ""))
    indirect_accepted = parse_money(row.get("Indirect Bidder Accepted", ""))
    indirect_tendered = parse_money(row.get("Indirect Bidder Tendered", ""))
    dealer_accepted = parse_money(row.get("Primary Dealer Accepted", ""))
    dealer_tendered = parse_money(row.get("Primary Dealer Tendered", ""))

    bid_cover = parse_float(row.get("Bid-to-Cover Ratio", ""))
    high_yield = parse_percent(row.get("High Yield", ""))
    avg_med_yield = parse_percent(row.get("Average / Median Yield", ""))

    direct_pct = safe_div(direct_accepted, competitive_accepted)
    indirect_pct = safe_div(indirect_accepted, competitive_accepted)
    dealer_pct = safe_div(dealer_accepted, competitive_accepted)

    dlr_hit = safe_div(dealer_accepted, dealer_tendered)
    dir_hit = safe_div(direct_accepted, direct_tendered)
    indir_hit = safe_div(indirect_accepted, indirect_tendered)

    yld_gap = None
    if high_yield is not None and avg_med_yield is not None:
        yld_gap = (high_yield - avg_med_yield) * 100.0

    return {
        "Auction": row.get("Auction Date", ""),
        "Size": row.get("Offering Amount", ""),
        "Coupon": row.get("Interest Rate", ""),
        "Stop": row.get("High Yield", ""),
        "Bid/Cover": fmt_ratio(bid_cover, ratio_decimals),
        "Tail": "",
        "Yld Gap": fmt_gap(yld_gap, gap_decimals),
        "Dealer%": fmt_percent(dealer_pct, percent_decimals),
        "Direct%": fmt_percent(direct_pct, percent_decimals),
        "Indirect%": fmt_percent(indirect_pct, percent_decimals),
        "Dlr Hit": fmt_percent(dlr_hit, percent_decimals),
        "Dir Hit": fmt_percent(dir_hit, percent_decimals),
        "Indir Hit": fmt_percent(indir_hit, percent_decimals),
    }


def build_output_path(input_path: Path, output_path: Optional[Path]) -> Path:
    if output_path is not None:
        return output_path
    return input_path.with_name(f"{input_path.stem}_derived.csv")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Derive auction fields from Securities.csv")
    parser.add_argument("--input", required=True, help="Path to Securities.csv")
    parser.add_argument("--output", help="Output CSV path (default: <input>_derived.csv)")
    parser.add_argument("--percent-decimals", type=int, default=1, help="Decimal places for percent fields")
    parser.add_argument("--ratio-decimals", type=int, default=2, help="Decimal places for ratio fields")
    parser.add_argument("--gap-decimals", type=int, default=1, help="Decimal places for yield gap fields")
    parser.add_argument(
        "--include-dealer-percent",
        action="store_true",
        help="Include Dealer% = 100 - Direct% - Indirect%",
    )
    parser.add_argument(
        "--nomura-labels",
        action="store_true",
        help="Use Nomura-style labels (Auction, Size, Coupon, Stop, ...)",
    )
    parser.add_argument(
        "--nomura-only",
        action="store_true",
        help="Write only Nomura-style columns (implies --nomura-labels)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).expanduser()
    output_path = build_output_path(input_path, Path(args.output).expanduser() if args.output else None)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open(newline="", encoding="utf-8") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError("Input CSV has no header row.")

        use_nomura = args.nomura_labels or args.nomura_only
        if use_nomura:
            derived_fieldnames = [
                "Auction",
                "Size",
                "Coupon",
                "Stop",
                "Bid/Cover",
                "Tail",
                "Yld Gap",
                "Dealer%",
                "Direct%",
                "Indirect%",
                "Dlr Hit",
                "Dir Hit",
                "Indir Hit",
            ]
        else:
            derived_fieldnames = [
                "Auction",
                "Bid/Cover",
                "Direct%",
                "Indirect%",
                "Dir Hit",
                "Indir Hit",
            ]
            if args.include_dealer_percent:
                derived_fieldnames.append("Dealer%")

        if args.nomura_only:
            fieldnames = list(derived_fieldnames)
        else:
            fieldnames = list(reader.fieldnames) + derived_fieldnames

        with output_path.open("w", newline="", encoding="utf-8") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                if use_nomura:
                    derived = derive_nomura_fields(
                        row,
                        percent_decimals=args.percent_decimals,
                        ratio_decimals=args.ratio_decimals,
                        gap_decimals=args.gap_decimals,
                    )
                else:
                    derived = derive_fields(
                        row,
                        percent_decimals=args.percent_decimals,
                        ratio_decimals=args.ratio_decimals,
                        include_dealer_percent=args.include_dealer_percent,
                    )
                if args.nomura_only:
                    writer.writerow(derived)
                else:
                    row.update(derived)
                    writer.writerow(row)

    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()

