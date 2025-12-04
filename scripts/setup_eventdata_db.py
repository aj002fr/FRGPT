"""Setup script for economic events database (Trading Economics)."""

import sqlite3
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def setup_eventdata_database():
    """Create economic events database with required tables."""
    
    # Database in workspace directory
    workspace = project_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    db_path = workspace / "economic_events.db"
    
    print(f"Setting up Economic Events database at: {db_path}")
    
    # Connect to database (creates if doesn't exist)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if table exists and needs migration
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='economic_events'")
        table_exists = cursor.fetchone() is not None
        
        if table_exists:
            # Check current schema
            cursor.execute("PRAGMA table_info(economic_events)")
            columns = [col[1] for col in cursor.fetchall()]
            
            # Check if old schema with created_at/updated_at
            has_timestamps = "created_at" in columns or "updated_at" in columns
            has_consensus = "consensus" in columns
            
            if has_timestamps:
                print("Migrating table to remove created_at/updated_at columns...")
                
                # Rename old table
                cursor.execute("ALTER TABLE economic_events RENAME TO economic_events_old")
                
                # Create new table with correct schema
                cursor.execute("""
                    CREATE TABLE economic_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL,
                        event_name TEXT NOT NULL,
                        country TEXT NOT NULL,
                        category TEXT,
                        importance TEXT,
                        event_date TEXT NOT NULL,
                        actual REAL,
                        consensus REAL,
                        forecast REAL,
                        previous REAL,
                        revised REAL,
                        unit TEXT,
                        ticker TEXT,
                        source TEXT DEFAULT 'tradingeconomics',
                        UNIQUE(event_id, event_date)
                    )
                """)
                
                # Copy data from old table (handle missing consensus column)
                if has_consensus:
                    copy_sql = """
                        INSERT INTO economic_events 
                        (event_id, event_name, country, category, importance, event_date,
                         actual, consensus, forecast, previous, revised, unit, ticker, source)
                        SELECT event_id, event_name, country, category, importance, event_date,
                               actual, consensus, forecast, previous, revised, unit, ticker, source
                        FROM economic_events_old
                    """
                else:
                    copy_sql = """
                        INSERT INTO economic_events 
                        (event_id, event_name, country, category, importance, event_date,
                         actual, consensus, forecast, previous, revised, unit, ticker, source)
                        SELECT event_id, event_name, country, category, importance, event_date,
                               actual, NULL, forecast, previous, revised, unit, ticker, source
                        FROM economic_events_old
                    """
                
                cursor.execute(copy_sql)
                rows_migrated = cursor.rowcount
                
                # Drop old table
                cursor.execute("DROP TABLE economic_events_old")
                
                print(f"  Migrated {rows_migrated} events to new schema")
            
            elif not has_consensus:
                # Just add consensus column
                print("Adding consensus column...")
                cursor.execute("ALTER TABLE economic_events ADD COLUMN consensus REAL")
            else:
                print("Table schema is up to date")
        else:
            # Create new table
            print("Creating economic_events table...")
            cursor.execute("""
                CREATE TABLE economic_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    country TEXT NOT NULL,
                    category TEXT,
                    importance TEXT,
                    event_date TEXT NOT NULL,
                    actual REAL,
                    consensus REAL,
                    forecast REAL,
                    previous REAL,
                    revised REAL,
                    unit TEXT,
                    ticker TEXT,
                    source TEXT DEFAULT 'tradingeconomics',
                    UNIQUE(event_id, event_date)
                )
            """)
        
        # Create indices for economic_events
        print("Creating indices for economic_events...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_id 
            ON economic_events (event_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_date 
            ON economic_events (event_date)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_country 
            ON economic_events (country)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_importance 
            ON economic_events (importance)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_name 
            ON economic_events (event_name)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_ticker 
            ON economic_events (ticker)
        """)
        
        # Create live_event_stream table (WebSocket events)
        print("Creating live_event_stream table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS live_event_stream (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                event_name TEXT NOT NULL,
                country TEXT NOT NULL,
                category TEXT,
                importance TEXT,
                event_date TEXT NOT NULL,
                actual REAL,
                consensus REAL,
                forecast REAL,
                previous REAL,
                unit TEXT,
                ticker TEXT,
                received_at TEXT NOT NULL,
                source TEXT DEFAULT 'websocket',
                UNIQUE(event_id, event_date, received_at)
            )
        """)
        
        # Migration: Add consensus column to live_event_stream if it doesn't exist
        cursor.execute("PRAGMA table_info(live_event_stream)")
        live_columns = [col[1] for col in cursor.fetchall()]
        
        if "consensus" not in live_columns:
            print("  Adding 'consensus' column to live_event_stream...")
            cursor.execute("ALTER TABLE live_event_stream ADD COLUMN consensus REAL")
        
        # Create indices for live_event_stream
        print("Creating indices for live_event_stream...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_live_event_id 
            ON live_event_stream (event_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_live_received_at 
            ON live_event_stream (received_at)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_live_event_date 
            ON live_event_stream (event_date)
        """)
        
        # Commit changes
        conn.commit()
        
        print("\n[OK] Database setup complete!")
        print(f"   Location: {db_path}")
        print(f"   Tables: economic_events, live_event_stream")
        
        # Show economic_events schema
        cursor.execute("PRAGMA table_info(economic_events)")
        columns = cursor.fetchall()
        
        print("\n[SCHEMA] economic_events:")
        for col in columns:
            col_id, name, dtype, notnull, default, pk = col
            null_str = "NOT NULL" if notnull else "NULLABLE"
            print(f"   - {name}: {dtype} ({null_str})")
        
        # Show live_event_stream schema
        cursor.execute("PRAGMA table_info(live_event_stream)")
        columns = cursor.fetchall()
        
        print("\n[SCHEMA] live_event_stream:")
        for col in columns:
            col_id, name, dtype, notnull, default, pk = col
            null_str = "NOT NULL" if notnull else "NULLABLE"
            print(f"   - {name}: {dtype} ({null_str})")
        
        # Show indices
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name IN ('economic_events', 'live_event_stream')")
        indices = cursor.fetchall()
        
        print("\n[INDICES]:")
        for idx in indices:
            if idx[0] and not idx[0].startswith("sqlite_"):
                print(f"   - {idx[0]}")
        
    except sqlite3.Error as e:
        print(f"[ERROR] Database error: {e}")
        conn.rollback()
        raise
    
    finally:
        conn.close()


def show_stats():
    """Show database statistics if it exists."""
    workspace = project_root / "workspace"
    db_path = workspace / "economic_events.db"
    
    if not db_path.exists():
        print(f"\n[WARNING] Database not found at {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Count events
        cursor.execute("SELECT COUNT(*) FROM economic_events")
        event_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM live_event_stream")
        live_count = cursor.fetchone()[0]
        
        # Get date range
        cursor.execute("SELECT MIN(event_date), MAX(event_date) FROM economic_events")
        date_range = cursor.fetchone()
        
        # Get country distribution
        cursor.execute("""
            SELECT country, COUNT(*) as cnt 
            FROM economic_events 
            GROUP BY country 
            ORDER BY cnt DESC 
            LIMIT 10
        """)
        countries = cursor.fetchall()
        
        print("\n[STATS] Database Statistics:")
        print(f"   Historical events: {event_count:,}")
        print(f"   Live stream events: {live_count:,}")
        
        if date_range[0]:
            print(f"   Date range: {date_range[0][:10]} to {date_range[1][:10]}")
        
        if countries:
            print("\n[COUNTRIES] Top Countries:")
            for country, count in countries:
                print(f"   - {country}: {count:,} events")
        
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Setup Economic Events database")
    parser.add_argument("--stats", action="store_true", help="Show database statistics")
    args = parser.parse_args()
    
    if args.stats:
        show_stats()
    else:
        setup_eventdata_database()
        show_stats()

