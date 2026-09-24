#!/usr/bin/env python3
"""Run anomaly annotation for all of 2025."""
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.servers.anomaly_detection.annotate import annotate_date_range

print("="*70)
print("MARKET ANOMALY ANNOTATION - 2025")
print("="*70)
print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print()
print("Parameters:")
print("  - Date range: 2025-01-01 to 2025-12-31")
print("  - Symbol: TY")
print("  - Z-score threshold: 1.5")
print("  - Use Perplexity for unexplained moves: Yes")
print()
print("Running annotation (this may take a few minutes due to Perplexity API calls)...")
print()

result = annotate_date_range(
    start_date="2025-01-01",
    end_date="2025-12-31",
    symbol="TY",
    z_threshold=1.5,
    use_perplexity=True,
)

print("="*70)
print("RESULTS")
print("="*70)

if result["success"]:
    print(f"SUCCESS!")
    print()
    print(f"  Total trading days analyzed: {result['total_days']}")
    print(f"  Days with anomalies: {result['anomaly_days']}")
    print(f"  Notes created: {result['notes_created']}")
    print()
    print("  Notes by source:")
    for source, count in result['notes_by_source'].items():
        print(f"    - {source}: {count}")
    print()
    print(f"  Perplexity API calls made: {result['perplexity_calls']}")
else:
    print(f"FAILED: {result.get('error', 'Unknown error')}")

print()
print(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*70)






