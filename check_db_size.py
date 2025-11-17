"""Check market data database size and performance."""

import sqlite3
import sys
from pathlib import Path

# Database path
db_path = Path("market_data.db")

if not db_path.exists():
    print(f"❌ Database not found: {db_path}")
    sys.exit(1)

print("\n" + "="*70)
print("DATABASE ANALYSIS")
print("="*70)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Total rows
cursor.execute("SELECT COUNT(*) FROM market_data")
total_rows = cursor.fetchone()[0]
print(f"\n📊 Total rows: {total_rows:,}")

# ZN rows
cursor.execute("SELECT COUNT(*) FROM market_data WHERE symbol LIKE '%ZN%'")
zn_rows = cursor.fetchone()[0]
print(f"📊 ZN rows: {zn_rows:,}")

# Sample query timing
import time

print("\n⏱️  Query Performance Tests:")

# Test 1: Get all data (slow)
start = time.time()
cursor.execute("SELECT * FROM market_data WHERE symbol LIKE '%ZN%'")
rows = cursor.fetchall()
duration = (time.time() - start) * 1000
print(f"  1. All ZN data: {len(rows):,} rows in {duration:.2f}ms")

# Test 2: Limited data (fast)
start = time.time()
cursor.execute("SELECT * FROM market_data WHERE symbol LIKE '%ZN%' LIMIT 10")
rows = cursor.fetchall()
duration = (time.time() - start) * 1000
print(f"  2. Limited (10): {len(rows)} rows in {duration:.2f}ms")

# Test 3: With ORDER BY (might be slower)
start = time.time()
cursor.execute("SELECT * FROM market_data WHERE symbol LIKE '%ZN%' ORDER BY file_date DESC LIMIT 10")
rows = cursor.fetchall()
duration = (time.time() - start) * 1000
print(f"  3. Ordered + Limited: {len(rows)} rows in {duration:.2f}ms")

# Check for indexes
cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='market_data'")
indexes = cursor.fetchall()
print(f"\n📇 Indexes: {len(indexes)}")
for idx in indexes:
    print(f"  - {idx[0]}")

# Sample ZN symbols
cursor.execute("SELECT DISTINCT symbol FROM market_data WHERE symbol LIKE '%ZN%' LIMIT 10")
symbols = cursor.fetchall()
print(f"\n📝 Sample ZN symbols:")
for sym in symbols:
    print(f"  - {sym[0]}")

conn.close()

print("\n" + "="*70)
print("RECOMMENDATIONS")
print("="*70)
print("\n✅ Always use LIMIT in queries for testing")
print("✅ Use ORDER BY + LIMIT for 'most recent' queries")
print("✅ Consider adding indexes on: symbol, file_date, price")
print("\n" + "="*70 + "\n")

