#!/usr/bin/env python3
"""
Lightweight contract checks for unified dashboard endpoints.

Requires the server to be running.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError


def get_json(url: str) -> tuple[int, dict]:
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=5) as resp:
            data = resp.read().decode("utf-8")
            return resp.status, json.loads(data)
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            return e.code, json.loads(body)
        except Exception:
            return e.code, {}
    except URLError:
        return 0, {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify dashboard contracts")
    parser.add_argument("--base-url", default="http://localhost:8800", help="Base URL for server")
    parser.add_argument("--event-type", default="CPI", help="Event type (default: CPI)")
    parser.add_argument("--date", default="2024-06-12", help="Event date to test")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    # 1) /api/candlestick must reject unsupported res
    status, data = get_json(f"{base}/api/candlestick/{args.date}?type={args.event_type}&res=2m")
    if status != 400:
        print("[FAIL] candlestick res=2m should be 400")
        return 2
    print("[OK] candlestick res=2m rejected")

    # 2) /api/event_details should succeed for built L1
    status, data = get_json(f"{base}/api/event_details?type={args.event_type}&date={args.date}")
    if status != 200 or not data.get("success"):
        print("[FAIL] event_details did not succeed")
        return 2
    print("[OK] event_details returned")

    print("[PASS] all contract checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())






