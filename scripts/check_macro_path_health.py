#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.macro_path_navigator.constants import LOCAL_STATUS_DIR
from src.core.telegram_notifier import send_telegram_message

STATE_PATH = LOCAL_STATUS_DIR / "macro_path_health_monitor_state.json"


def fetch_health(url: str, timeout: int) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "status": "down",
            "last_error": f"health HTTP failed: {exc}",
            "feeds": {},
            "state": marker_fallback(),
        }


def marker_fallback() -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for path in LOCAL_STATUS_DIR.glob("macro_path_*.txt"):
        try:
            data[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data[path.stem] = path.read_text(encoding="utf-8", errors="ignore")[:500]
    return data


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


def summarize(health: Dict[str, Any]) -> tuple[str, str]:
    status = str(health.get("status") or "down")
    bad = []
    for name, feed in (health.get("feeds") or {}).items():
        if feed.get("status") != "live":
            age = feed.get("age_seconds")
            age_text = "n/a" if age is None else f"{float(age):.0f}s"
            bad.append(f"{name}: {feed.get('status')} age={age_text}")
    if health.get("last_error"):
        bad.append(f"error={health['last_error']}")
    detail = "; ".join(bad) if bad else "all feeds live"
    return status, detail


def maybe_alert(health: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    state = load_state()
    status, detail = summarize(health)
    previous = state.get("last_status")
    now = datetime.now(timezone.utc).isoformat()
    should_send = status != previous
    if should_send:
        if status == "ok":
            text = f"<b>Macro Path Navigator recovered</b>\n{detail}\n{now}"
        else:
            text = f"<b>Macro Path Navigator {status.upper()}</b>\n{detail}\n{now}"
        if dry_run:
            print(text)
        else:
            send_telegram_message(text)
    state.update({"last_status": status, "last_detail": detail, "updated_at": now})
    save_state(state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Macro Path Navigator health and send deduped Telegram alerts.")
    parser.add_argument("--url", default="http://127.0.0.1:5088/api/health")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    while True:
        health = fetch_health(args.url, args.timeout)
        state = maybe_alert(health, dry_run=args.dry_run)
        print(json.dumps({"health_status": health.get("status"), "monitor_state": state}, indent=2))
        if not args.loop:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

