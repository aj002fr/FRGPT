#!/usr/bin/env python3
"""Follow a V3 live stream like a browser and report TotalAggress steps between consecutive prints.

Snapshot/history/beta events replace the rows (compact payloads are expanded); trade events append.
A step larger than --max-step between two consecutive prints is reported with its timestamps.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.aggression_dashboard_v3.panels import expand_payload  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:7053/api/live/stream")
    parser.add_argument("--seconds", type=float, default=360.0)
    parser.add_argument("--max-step", type=float, default=1_000_000.0)
    args = parser.parse_args()
    deadline = time.time() + args.seconds
    resp = urllib.request.urlopen(args.url, timeout=300)
    last = None
    steps = 0
    counts: dict[str, int] = {}
    event, data = None, []
    for raw in resp:
        if time.time() > deadline:
            break
        line = raw.decode("utf-8").rstrip("\n")
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
            continue
        if line or not event:
            continue
        payload = json.loads("\n".join(data)) if data else {}
        counts[event] = counts.get(event, 0) + 1
        stamp = time.strftime("%H:%M:%S")
        if event in ("snapshot", "history", "beta") and payload.get("panels"):
            rows = expand_payload(payload)["panels"]["p1"]["data"]
            last = rows[-1] if rows else None
            print(f"{stamp} {event}: {len(rows):,} rows, last {last['x'] if last else '-'} "
                  f"{last['total_aggress'] if last else float('nan'):,.0f}", flush=True)
        elif event == "trade":
            for point in payload.get("prints") or []:
                value = point.get("total_aggress")
                if last is not None and value is not None and last.get("total_aggress") is not None:
                    step = value - last["total_aggress"]
                    if abs(step) > args.max_step:
                        steps += 1
                        print(f"{stamp} STEP {step:+,.0f}: {last['x']} {last['total_aggress']:,.0f} -> "
                              f"{point['x']} {value:,.0f}", flush=True)
                last = point
        event, data = None, []
    print(f"done: events {counts}; steps over {args.max_step:,.0f}: {steps}; last "
          f"{last['x'] if last else '-'} {last['total_aggress'] if last else float('nan'):,.0f}")
    return 1 if steps else 0


if __name__ == "__main__":
    raise SystemExit(main())
