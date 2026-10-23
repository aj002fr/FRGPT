#!/usr/bin/env python3
"""
Alert when economic_events.db copies have gone stale.

This is intended for a lightweight daily scheduled task that can still alert
even when the main overnight BBG loader task is skipped or exits early.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.telegram_notifier import load_telegram_chat_id, send_telegram_message


DEFAULT_DB_PATHS = [
    PROJECT_ROOT / "workspace" / "economic_events.db",
    Path(r"D:\FRDBs\workspace\economic_events.db"),
]
DEFAULT_STATE_FILE = Path.home() / ".market_data" / "alerts" / "economic_events_db_staleness_state.json"


@dataclass(frozen=True)
class DbStatus:
    path: str
    exists: bool
    stale: bool
    age_hours: float | None
    last_write_utc: str | None
    reason: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _status_for_path(path: Path, max_age_hours: float, now_utc: datetime) -> DbStatus:
    if not path.exists():
        return DbStatus(
            path=str(path),
            exists=False,
            stale=True,
            age_hours=None,
            last_write_utc=None,
            reason="missing",
        )

    modified_utc = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_hours = (now_utc - modified_utc).total_seconds() / 3600.0
    stale = age_hours > max_age_hours
    return DbStatus(
        path=str(path),
        exists=True,
        stale=stale,
        age_hours=round(age_hours, 2),
        last_write_utc=modified_utc.isoformat(),
        reason="stale" if stale else "fresh",
    )


def _build_alert_key(statuses: Iterable[DbStatus], max_age_hours: float) -> str:
    stale_items: list[str] = []
    for status in statuses:
        if not status.stale:
            continue
        stale_items.append(
            "|".join(
                [
                    status.path,
                    status.reason,
                    status.last_write_utc or "none",
                    str(max_age_hours),
                ]
            )
        )
    return "||".join(sorted(stale_items))


def _format_age(age_hours: float | None) -> str:
    if age_hours is None:
        return "n/a"
    return f"{age_hours:.2f}h"


def _build_message(statuses: list[DbStatus], max_age_hours: float, now_utc: datetime) -> str:
    stale_statuses = [status for status in statuses if status.stale]
    lines = [
        "<b>economic_events.db stale alert</b>",
        "",
        f"<b>Checked (UTC):</b> {now_utc.strftime('%Y-%m-%d %H:%M:%S')}",
        f"<b>Threshold:</b> {max_age_hours:.1f} hours",
        "",
    ]
    for status in stale_statuses:
        lines.extend(
            [
                f"<b>Path:</b> <code>{status.path}</code>",
                f"<b>Status:</b> {status.reason}",
                f"<b>Age:</b> {_format_age(status.age_hours)}",
                f"<b>Last write (UTC):</b> {status.last_write_utc or 'missing'}",
                "",
            ]
        )
    lines.append("Check the BBGDataDaily scheduler/logs and rerun the loader if needed.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Alert when economic_events.db copies are stale")
    parser.add_argument(
        "--db",
        action="append",
        dest="db_paths",
        help="SQLite path to check; repeat to override the default pair",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=30.0,
        help="Maximum allowed DB age before alerting",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help="Path to JSON file used to suppress duplicate stale alerts",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print status and skip Telegram send/state updates",
    )
    args = parser.parse_args()

    db_paths = [Path(path) for path in (args.db_paths or DEFAULT_DB_PATHS)]
    now_utc = _utc_now()
    statuses = [_status_for_path(path, args.max_age_hours, now_utc) for path in db_paths]

    print("=" * 60)
    print("economic_events.db staleness check")
    print("=" * 60)
    print(f"Checked at (UTC): {now_utc.isoformat()}")
    print(f"Threshold (hours): {args.max_age_hours:.1f}")
    for status in statuses:
        print(
            " - {path}: {reason} | age={age} | last_write_utc={last_write}".format(
                path=status.path,
                reason=status.reason,
                age=_format_age(status.age_hours),
                last_write=status.last_write_utc or "missing",
            )
        )

    stale_statuses = [status for status in statuses if status.stale]
    if not stale_statuses:
        print("[OK] All monitored DB files are fresh.")
        if not args.dry_run and args.state_file.exists():
            args.state_file.unlink()
        return 0

    alert_key = _build_alert_key(statuses, args.max_age_hours)
    previous_state = _load_state(args.state_file)
    already_alerted = previous_state.get("alert_key") == alert_key
    if already_alerted:
        print("[INFO] Stale condition unchanged; suppressing duplicate Telegram alert.")
        return 0

    message = _build_message(statuses, args.max_age_hours, now_utc)
    print("[ALERT] One or more monitored DB files are stale.")
    if args.dry_run:
        print("[DRY RUN] Telegram send skipped.")
        return 0

    alert_chat_id = load_telegram_chat_id("BBG_ALERT_CHAT_ID", "TELEGRAM_CHAT_ID")
    result = send_telegram_message(message, chat_id=alert_chat_id)
    if not result.get("success"):
        print(f"[ERROR] Failed to send Telegram alert: {result.get('error')}")
        return 1

    _save_state(
        args.state_file,
        {
            "alert_key": alert_key,
            "alerted_at_utc": now_utc.isoformat(),
            "max_age_hours": args.max_age_hours,
            "statuses": [asdict(status) for status in statuses],
        },
    )
    print(f"[OK] Telegram alert sent to {result.get('chat_id')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
