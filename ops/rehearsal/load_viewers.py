#!/usr/bin/env python3
"""Simulate dashboard viewers at their real polling cadence and report latency and errors.

Per app, N viewer threads poll the endpoints the pages poll (5064 /api/today every 15 s,
5065 /api/live every 15 s, V2.2/V2.7 /api/live_signal every 5 s); V3 viewers hold the SSE
stream open. Prints per-endpoint p50/p95/max latency and error counts as JSON.

    python ops/rehearsal/load_viewers.py --viewers 20 --seconds 180 --base-port 6000
"""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict

ENDPOINTS = [
    ("flow", 64, "/api/today?symbol=TY", 15.0),
    ("supply", 65, "/api/live", 15.0),
    ("v22", 58, "/api/live_signal", 5.0),
    ("v27", 63, "/api/live_signal", 5.0),
]


def poll(url: str, every: float, stop: float, samples: dict, errors: dict, key: str) -> None:
    offset = (hash(threading.current_thread().name) % 1000) / 1000.0 * every
    time.sleep(offset)
    while time.time() < stop:
        started = time.perf_counter()
        try:
            request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(request, timeout=60) as response:
                response.read()
                if response.status >= 400:
                    errors[key] += 1
        except urllib.error.HTTPError as exc:
            if exc.code not in (202, 304):
                errors[key] += 1
        except Exception:
            errors[key] += 1
        samples[key].append((time.perf_counter() - started) * 1000.0)
        time.sleep(max(0.0, every - (time.perf_counter() - started)))


def stream(url: str, stop: float, counts: dict, errors: dict) -> None:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            while time.time() < stop:
                line = response.readline()
                if not line:
                    break
                if line.startswith(b"event: "):
                    counts[line[7:].strip().decode()] += 1
    except Exception:
        errors["v3_stream"] += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--viewers", type=int, default=20)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--base-port", type=int, default=6000, help="6000 -> 6053..6065 (rehearsal)")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    stop = time.time() + args.seconds
    samples: dict = defaultdict(list)
    errors: dict = defaultdict(int)
    events: dict = defaultdict(int)
    threads = []
    for name, suffix, path, every in ENDPOINTS:
        url = f"http://{args.host}:{args.base_port + suffix}{path}"
        for i in range(args.viewers):
            threads.append(threading.Thread(target=poll, name=f"{name}-{i}", args=(url, every, stop, samples, errors, name), daemon=True))
    for i in range(args.viewers):
        threads.append(threading.Thread(target=stream, name=f"v3-{i}", args=(f"http://{args.host}:{args.base_port + 53}/api/live/stream", stop, events, errors), daemon=True))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=args.seconds + 90)
    report = {"viewers_per_app": args.viewers, "seconds": args.seconds, "errors": dict(errors), "v3_events": dict(events), "latency_ms": {}}
    for key, values in samples.items():
        ordered = sorted(values)
        report["latency_ms"][key] = {"n": len(ordered), "p50": round(statistics.median(ordered), 1),
                                     "p95": round(ordered[int(0.95 * (len(ordered) - 1))], 1), "max": round(ordered[-1], 1)}
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
