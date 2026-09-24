"""Server-side smoke check for the V2.4 dashboard.

Confirms routes serve, static assets load, and every field the front-end reads
actually exists in the API payload. Read-only.

Run (with the app already running): py -3 -m scripts.aggression_dashboard_v24.smoke_check
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:5060"
STATIC = Path(__file__).resolve().parent / "static"


def fetch(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=90) as resp:
            return resp.status, resp.read()
    except Exception as exc:  # noqa: BLE001
        return getattr(exc, "code", 0) or 0, str(exc).encode()


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK ' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}", flush=True)
    return ok


def main() -> None:
    results: list[bool] = []

    status, body = fetch("/")
    html = body.decode("utf-8", errors="replace")
    results.append(check("GET / returns 200", status == 200, f"status={status}"))
    for needle in ("Aggression V2.4", "paper-banner", "id=\"books\"", "dashboard.js"):
        results.append(check(f"index.html contains {needle!r}", needle in html))

    for asset in ("dashboard.css", "dashboard.js"):
        status, body = fetch(f"/static/{asset}")
        results.append(check(f"GET /static/{asset}", status == 200 and len(body) > 500, f"{len(body)} bytes"))

    status, body = fetch("/api/config")
    config = json.loads(body) if status == 200 else {}
    results.append(check("GET /api/config returns 200", status == 200))
    for key in ("strategy_status", "research_context", "specs", "isolation"):
        results.append(check(f"config has {key}", key in config))
    results.append(check("config marks non-executable", config.get("isolation", {}).get("executable") is False))
    results.append(check("config marks v2 untouched", config.get("isolation", {}).get("v2_untouched") is True))

    status, body = fetch("/api/live_signal")
    payload = json.loads(body) if status in (200, 202) else {}
    results.append(check("GET /api/live_signal responds", status in (200, 202), f"status={status}"))

    books = (payload.get("paper") or {}).get("books") or []
    results.append(check("payload has two books", len(books) == 2, f"n={len(books)}"))

    # Every field dashboard.js reads off a book must exist in the payload.
    live_fields = ["state", "side", "entry_price", "stop_price", "target_price",
                   "deadline_ts", "unrealized_ticks", "mae_ticks", "mfe_ticks",
                   "instruction", "spec_text"]
    stats_fields = ["n_trades", "n_dates", "mean_gross_ticks", "mean_net_ticks",
                    "total_net_ticks", "hit_rate", "mean_mae_ticks",
                    "median_minutes_held", "exit_mix"]
    for book in books:
        missing_live = [f for f in live_fields if f not in (book.get("live") or {})]
        missing_stats = [f for f in stats_fields if f not in (book.get("stats") or {})]
        results.append(check(f"{book.get('name')} live fields complete", not missing_live, str(missing_live)))
        results.append(check(f"{book.get('name')} stats fields complete", not missing_stats, str(missing_stats)))
        results.append(check(f"{book.get('name')} has equity list", isinstance(book.get("equity"), list)))

    # Research context keys referenced by loadConfig().
    ctx = config.get("research_context") or {}
    for key in ("verdict", "cells_scored", "cells_passed", "explore_holdout_corr",
                "specs_positive_after_cost_both_samples", "live_v2_gross_ticks_30m", "note"):
        results.append(check(f"research_context has {key}", key in ctx))

    status, body = fetch("/api/paper?book=DISLOCATION_ONLY&scope=today")
    paper = json.loads(body) if status == 200 else {}
    results.append(check("GET /api/paper returns 200", status == 200))
    results.append(check("paper payload has log_rows", "log_rows" in paper))

    # Cross-check: any el("...") id the JS touches must exist in the template.
    js = (STATIC / "dashboard.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'el\("([\w-]+)"\)', js))
    missing_ids = sorted(i for i in ids if f'id="{i}"' not in html)
    results.append(check("all JS element ids exist in template", not missing_ids, str(missing_ids)))

    print(f"\n{sum(results)}/{len(results)} checks passed", flush=True)
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
