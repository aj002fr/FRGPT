#!/usr/bin/env python3
"""Read V3's SSE stream for a while and report what arrived and how late trade prints were.

Lag is receive time minus the print's own time (``x`` in ET), so it includes feed delay. Only
live deltas count (trade messages that carry ``strip``); replays of already-sent prints (after a
subscribe or a rebuild) are reported separately.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5053/api/live/stream")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--stop-after-picture", action="store_true", help="return as soon as the first picture arrives")
    args = parser.parse_args()

    started = time.monotonic()
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    lags: list[float] = []
    first_picture = None
    replayed = 0
    event = None
    with urllib.request.urlopen(args.url, timeout=30) as response:
        while time.monotonic() - started < args.seconds:
            raw = response.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("event: "):
                event = line[7:]
                continue
            if not line.startswith("data: ") or event is None:
                continue
            counts[event] = counts.get(event, 0) + 1
            sizes[event] = sizes.get(event, 0) + len(raw)
            if event in ("snapshot", "history", "beta") and first_picture is None:
                first_picture = time.monotonic() - started
                if args.stop_after_picture:
                    break
            if event == "trade":
                now_et = datetime.now(EASTERN).replace(tzinfo=None)
                message = json.loads(line[6:])
                if "strip" not in message:
                    replayed += len(message.get("prints") or [])
                    event = None
                    continue
                for point in message.get("prints") or []:
                    try:
                        stamp = datetime.fromisoformat(str(point["x"]).replace("T", " ")[:26])
                    except (KeyError, ValueError):
                        continue
                    lags.append((now_et - stamp.replace(tzinfo=None)).total_seconds())
            event = None
    report = {
        "seconds": round(time.monotonic() - started, 1),
        "events": counts,
        "bytes": sizes,
        "first_picture_sec": round(first_picture, 2) if first_picture is not None else None,
        "prints": len(lags),
        "replayed_prints": replayed,
    }
    if lags:
        ordered = sorted(lags)
        report["lag_sec"] = {
            "median": round(statistics.median(ordered), 3),
            "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
            "max": round(ordered[-1], 3),
        }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
