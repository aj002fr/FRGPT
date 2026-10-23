#!/usr/bin/env python3
"""
Quick Postgres/Timescale status probe for the OHLCV table.

Prints:
- Server version
- Installed extensions (checks for timescaledb)
- Whether public.ohlcv_1m is a hypertable
- Current indexes on public.ohlcv_1m

Usage:
  py scripts/check_ohlcv_timescale_status.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402


def main() -> int:
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed. Install with: pip install psycopg2-binary")
        return 2

    conn = psycopg2.connect(
        host=settings.PG_HOST,
        port=settings.PG_PORT,
        dbname=settings.PG_DATABASE,
        user=settings.PG_USER,
        password=settings.PG_PASSWORD,
    )
    cur = conn.cursor()

    cur.execute("select version()")
    print("server_version:", cur.fetchone()[0])

    cur.execute("select extname from pg_extension order by extname")
    exts = [r[0] for r in cur.fetchall()]
    print("extensions:", exts)

    cur.execute("select to_regclass('public.ohlcv_1m')")
    reg = cur.fetchone()[0]
    print("ohlcv_1m_exists:", reg)

    if "timescaledb" in exts:
        cur.execute(
            """
            select hypertable_schema, hypertable_name, chunk_time_interval
            from timescaledb_information.hypertables
            where hypertable_schema='public' and hypertable_name='ohlcv_1m'
            """
        )
        ht = cur.fetchall()
        print("timescaledb_extension_present: True")
        print("is_hypertable:", bool(ht))
        if ht:
            print("hypertable:", ht[0])
    else:
        print("timescaledb_extension_present: False")
        print("is_hypertable: False")

    cur.execute(
        """
        select indexname, indexdef
        from pg_indexes
        where schemaname='public' and tablename='ohlcv_1m'
        order by indexname
        """
    )
    idx = cur.fetchall()
    print("indexes_count:", len(idx))
    for name, idef in idx:
        print("-", name, "|", idef)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


