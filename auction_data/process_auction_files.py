#!/usr/bin/env python3
"""
Process raw auction CSV files to generate Nomura-style derived fields.

Scans current directory for *_raw.csv files and generates corresponding
*_processed.csv files with 13 derived columns.

Usage:
    python process_auction_files.py          # Process new/updated files
    python process_auction_files.py --force  # Reprocess all files
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Script is in auction_data/, project root is parent
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data_setup.derive_auction_fields import derive_nomura_fields

# Directory containing auction data files (same as script location)
AUCTION_DATA_DIR = SCRIPT_DIR

# Nomura-style output columns (13 fields)
OUTPUT_FIELDNAMES = [
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


def needs_processing(raw_path: Path, processed_path: Path, force: bool = False) -> bool:
    """Check if raw file needs to be processed.
    
    Returns True if:
    - force is True
    - processed file doesn't exist
    - raw file is newer than processed file
    """
    if force:
        return True
    if not processed_path.exists():
        return True
    return raw_path.stat().st_mtime > processed_path.stat().st_mtime


def process_raw_file(
    input_path: Path,
    percent_decimals: int = 1,
    ratio_decimals: int = 2,
    gap_decimals: int = 1,
) -> Path:
    """Process a single raw auction CSV file and generate processed output.
    
    Args:
        input_path: Path to *_raw.csv file
        percent_decimals: Decimal places for percent fields (default: 1)
        ratio_decimals: Decimal places for ratio fields (default: 2)
        gap_decimals: Decimal places for yield gap fields (default: 1)
    
    Returns:
        Path to the generated *_processed.csv file
    """
    # Build output path: foo_raw.csv -> foo_processed.csv
    stem = input_path.stem
    if stem.endswith("_raw"):
        output_stem = stem[:-4] + "_processed"
    else:
        output_stem = stem + "_processed"
    output_path = input_path.with_name(output_stem + ".csv")
    
    with input_path.open(newline="", encoding="utf-8") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError(f"Input CSV has no header row: {input_path}")
        
        with output_path.open("w", newline="", encoding="utf-8") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=OUTPUT_FIELDNAMES)
            writer.writeheader()
            
            for row in reader:
                derived = derive_nomura_fields(
                    row,
                    percent_decimals=percent_decimals,
                    ratio_decimals=ratio_decimals,
                    gap_decimals=gap_decimals,
                )
                writer.writerow(derived)
    
    return output_path


def process_all(force: bool = False) -> list[Path]:
    """Scan current directory and process all *_raw.csv files.
    
    Args:
        force: If True, reprocess all files regardless of timestamps
    
    Returns:
        List of paths to generated/updated processed files
    """
    processed_files = []
    raw_files = sorted(AUCTION_DATA_DIR.glob("*_raw.csv"))
    
    if not raw_files:
        print(f"No *_raw.csv files found in {AUCTION_DATA_DIR}")
        return []
    
    for raw_path in raw_files:
        # Build expected processed path
        stem = raw_path.stem
        if stem.endswith("_raw"):
            output_stem = stem[:-4] + "_processed"
        else:
            output_stem = stem + "_processed"
        processed_path = raw_path.with_name(output_stem + ".csv")
        
        if needs_processing(raw_path, processed_path, force):
            print(f"Processing: {raw_path.name} -> {processed_path.name}")
            try:
                output = process_raw_file(raw_path)
                processed_files.append(output)
                print(f"  [OK] Generated {output.name}")
            except Exception as e:
                print(f"  [ERROR] {e}")
        else:
            print(f"Skipping: {raw_path.name} (processed file is up to date)")
    
    return processed_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process raw auction CSV files to generate Nomura-style derived fields"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess all files regardless of timestamps",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    print(f"Scanning {AUCTION_DATA_DIR} for *_raw.csv files...")
    processed = process_all(force=args.force)
    
    if processed:
        print(f"\nProcessed {len(processed)} file(s)")
    else:
        print("\nNo files needed processing")


if __name__ == "__main__":
    main()






