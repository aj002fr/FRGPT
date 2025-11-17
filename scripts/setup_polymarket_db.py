"""Setup script for Polymarket markets database."""

import sqlite3
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def setup_polymarket_database():
    """Create Polymarket database with prediction_queries table."""
    
    db_path = project_root / "polymarket_markets.db"
    
    print(f"Setting up Polymarket database at: {db_path}")
    
    # Connect to database (creates if doesn't exist)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Create prediction_queries table
        print("Creating prediction_queries table...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prediction_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_query TEXT NOT NULL,
                expanded_keywords TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                results TEXT NOT NULL,
                platform TEXT DEFAULT 'polymarket',
                market_ids TEXT,
                avg_probability REAL,
                total_volume INTEGER,
                result_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create indices for performance
        print("Creating indices...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_id 
            ON prediction_queries (session_id)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp 
            ON prediction_queries (timestamp)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_platform 
            ON prediction_queries (platform)
        """)
        
        # Commit changes
        conn.commit()
        
        print("✅ Database setup complete!")
        print(f"   Location: {db_path}")
        print(f"   Table: prediction_queries")
        print(f"   Indices: idx_session_id, idx_timestamp, idx_platform")
        
        # Show table schema
        cursor.execute("PRAGMA table_info(prediction_queries)")
        columns = cursor.fetchall()
        
        print("\n📋 Table Schema:")
        for col in columns:
            col_id, name, dtype, notnull, default, pk = col
            print(f"   - {name}: {dtype}")
        
    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
        conn.rollback()
        raise
    
    finally:
        conn.close()


if __name__ == "__main__":
    setup_polymarket_database()

