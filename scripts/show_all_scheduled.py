#!/usr/bin/env python3
"""Show all scheduled events."""
import sqlite3
from pathlib import Path

db_path = Path(__file__).parent.parent / "workspace" / "scheduled_events.db"
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# First check the table schema
print("\nTable schema:")
cur.execute("PRAGMA table_info(scheduled_events)")
for row in cur.fetchall():
    print(f"  {row['name']}: {row['type']}")

# Then show all events
print("\n" + "=" * 100)
print("ALL SCHEDULED EVENTS".center(100))
print("=" * 100)

cur.execute("SELECT * FROM scheduled_events ORDER BY id")
for row in cur.fetchall():
    print(dict(row))
print("=" * 100 + "\n")

conn.close()
