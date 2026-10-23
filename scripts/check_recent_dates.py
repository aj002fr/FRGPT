"""Check recent dates in database."""
from src.servers.marketdata.run_query import run_query

# Check data for recent dates
result = run_query(
    template='custom', 
    columns=['timestamp', 'close'], 
    params={'conditions': "symbol ILIKE 'TY%' AND timestamp >= '2026-01-10'"},
    limit=5000
)

data = result.get('data', [])
dates = set([x['timestamp'][:10] for x in data])

print("=" * 50)
print("Recent dates with TY data in database:")
print("=" * 50)
for d in sorted(dates):
    count = sum(1 for x in data if x['timestamp'][:10] == d)
    print(f"  {d}: {count} rows")
print(f"\nTotal: {len(data)} rows across {len(dates)} dates")
print("=" * 50)

