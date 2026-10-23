"""Append-only journal for the Move Exhaustion V2 autoresearch loop.

Usage:
    py -3 scripts/move_exhaustion_v2/tools/loop_journal.py append \
        --actor advisor --event brief --cycle C001 \
        --summary "Opened C001 on BL-2 impact decay" --ref research/loop/cycles/C001_impact_decay/BRIEF.md

    py -3 scripts/move_exhaustion_v2/tools/loop_journal.py verify

Rules enforced here and in tests/test_loop_integrity.py:
- strictly increasing ``seq`` starting at 1;
- valid actor/event vocabulary;
- append-only (verify recomputes nothing destructive; editing history is a
  protocol violation and will show up as seq/schema breakage).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
JOURNAL_PATH = PKG_ROOT / "research" / "loop" / "journal" / "loop_journal.jsonl"

ACTORS = {"advisor", "worker", "human"}
EVENTS = {
    "governance", "backlog", "brief", "launch", "result",
    "feedback", "decision", "escalation", "human_input", "correction",
    "retro", "process_change",
}
REQUIRED_KEYS = {"seq", "ts_utc", "actor", "event", "cycle", "summary", "refs"}


def read_entries() -> list[dict]:
    if not JOURNAL_PATH.exists():
        return []
    entries = []
    for i, line in enumerate(JOURNAL_PATH.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"journal line {i} is not valid JSON: {exc}") from exc
    return entries


def validate(entries: list[dict]) -> list[str]:
    problems: list[str] = []
    for i, e in enumerate(entries):
        missing = REQUIRED_KEYS - set(e)
        if missing:
            problems.append(f"entry {i + 1}: missing keys {sorted(missing)}")
            continue
        if e["seq"] != i + 1:
            problems.append(f"entry {i + 1}: seq={e['seq']} (must be strictly sequential from 1)")
        if e["actor"] not in ACTORS:
            problems.append(f"entry {i + 1}: bad actor {e['actor']!r}")
        if e["event"] not in EVENTS:
            problems.append(f"entry {i + 1}: bad event {e['event']!r}")
        if not isinstance(e["refs"], list):
            problems.append(f"entry {i + 1}: refs must be a list")
        if e["cycle"] is not None and not str(e["cycle"]).startswith("C"):
            problems.append(f"entry {i + 1}: cycle must be null or 'C###'")
    return problems


def append_entry(actor: str, event: str, summary: str, cycle: str | None, refs: list[str]) -> dict:
    entries = read_entries()
    problems = validate(entries)
    if problems:
        raise ValueError("journal invalid; fix via correction entries first: " + "; ".join(problems))
    entry = {
        "seq": len(entries) + 1,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actor": actor,
        "event": event,
        "cycle": cycle,
        "summary": summary,
        "refs": refs,
    }
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_PATH, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ap_append = sub.add_parser("append")
    ap_append.add_argument("--actor", required=True, choices=sorted(ACTORS))
    ap_append.add_argument("--event", required=True, choices=sorted(EVENTS))
    ap_append.add_argument("--cycle", default=None)
    ap_append.add_argument("--summary", required=True)
    ap_append.add_argument("--ref", action="append", default=[], dest="refs")
    sub.add_parser("verify")

    args = ap.parse_args()
    if args.cmd == "append":
        entry = append_entry(args.actor, args.event, args.summary, args.cycle, args.refs)
        print(f"appended seq={entry['seq']}")
    else:
        problems = validate(read_entries())
        if problems:
            print("JOURNAL INVALID:")
            for p in problems:
                print(f"  - {p}")
            sys.exit(1)
        print(f"journal valid ({len(read_entries())} entries)")


if __name__ == "__main__":
    main()
