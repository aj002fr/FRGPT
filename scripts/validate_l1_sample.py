#!/usr/bin/env python3
"""
Validate a small sample of L1 event feature files for CI gating.
Checks for required meta keys and presence of price_1m/price_5m files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_META_KEYS = ("event_type", "event_date", "event_ts_utc", "event_ts_et", "build_version")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate L1 event feature samples.")
    parser.add_argument(
        "--l1-root",
        type=Path,
        default=Path("tests/fixtures/l1_event_features"),
        help="Root directory containing L1 event feature files.",
    )
    parser.add_argument(
        "--event-types",
        type=str,
        default="NFP,CPI,PPI,JOLTS",
        help="Comma-separated event types to validate.",
    )
    parser.add_argument(
        "--min-per-type",
        type=int,
        default=1,
        help="Minimum number of events required per type.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require non-empty price files.",
    )
    return parser.parse_args()


def validate_event_dir(event_dir: Path, strict: bool) -> list[str]:
    errors: list[str] = []
    meta_files = sorted(event_dir.glob("*.meta.json"))
    if not meta_files:
        errors.append(f"{event_dir}: no meta files found")
        return errors

    for meta_path in meta_files:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{meta_path}: invalid json ({exc})")
            continue

        missing = [key for key in REQUIRED_META_KEYS if key not in meta]
        if missing:
            errors.append(f"{meta_path}: missing keys {missing}")
            continue

        base = meta_path.with_suffix("")
        price_1m = base.with_suffix(".price_1m.arrow")
        price_5m = base.with_suffix(".price_5m.arrow")
        for path in (price_1m, price_5m):
            if not path.exists():
                errors.append(f"{meta_path}: missing {path.name}")
            elif strict and path.stat().st_size == 0:
                errors.append(f"{meta_path}: empty {path.name}")
    return errors


def main() -> int:
    args = parse_args()
    l1_root = args.l1_root
    event_types = [e.strip().upper() for e in args.event_types.split(",") if e.strip()]

    if not l1_root.exists():
        print(f"L1 root not found: {l1_root}")
        return 1

    errors: list[str] = []
    for event_type in event_types:
        event_dir = l1_root / event_type
        if not event_dir.exists():
            errors.append(f"{event_type}: directory missing ({event_dir})")
            continue

        meta_files = list(event_dir.glob("*.meta.json"))
        if len(meta_files) < args.min_per_type:
            errors.append(f"{event_type}: expected >= {args.min_per_type} meta files, found {len(meta_files)}")

        errors.extend(validate_event_dir(event_dir, args.strict))

    if errors:
        print("L1 sample validation failed:")
        for err in errors:
            print(f"- {err}")
        return 1

    print("L1 sample validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

