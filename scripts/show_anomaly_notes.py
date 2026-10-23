#!/usr/bin/env python3
"""Display anomaly notes from the database."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.servers.anomaly_detection.storage import query_anomaly_notes

print("="*80)
print("MARKET ANOMALY NOTES - 2025")
print("="*80)

result = query_anomaly_notes(
    start_date="2025-01-01",
    end_date="2025-12-31",
    symbol="TY",
    limit=20,  # Show top 20 by z-score
)

if not result["success"]:
    print(f"Error: {result.get('error')}")
    sys.exit(1)

print(f"\nShowing top {len(result['notes'])} notes (by z-score):\n")
print("-"*80)

for note in result["notes"]:
    print(f"\n{note['date']} | {note['anomaly_type']} | z={note['z_score']:.2f} | {note['value_bps']:.1f} bps")
    print(f"  Source: {note['source']}")
    print(f"  Note: {note['note'][:200]}..." if len(note['note']) > 200 else f"  Note: {note['note']}")

print("\n" + "="*80)
print(f"Total notes in database: {result['count']}")
print("="*80)






