#!/usr/bin/env python3
"""Create market_anomaly_notes table in PostgreSQL."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import PG_HOST, PG_PORT, PG_DATABASE, PG_USER, PG_PASSWORD
import psycopg2

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS market_anomaly_notes (
    id SERIAL PRIMARY KEY,
    date DATE NOT NULL,
    symbol VARCHAR(20) NOT NULL DEFAULT 'TY',
    anomaly_type VARCHAR(50) NOT NULL,
    z_score FLOAT NOT NULL,
    value_bps FLOAT NOT NULL,
    threshold_bps FLOAT,
    event_name VARCHAR(200),
    news_summary TEXT,
    note TEXT NOT NULL,
    source VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(date, symbol, anomaly_type)
);

CREATE INDEX IF NOT EXISTS idx_anomaly_notes_date ON market_anomaly_notes(date);
CREATE INDEX IF NOT EXISTS idx_anomaly_notes_symbol ON market_anomaly_notes(symbol);
CREATE INDEX IF NOT EXISTS idx_anomaly_notes_type ON market_anomaly_notes(anomaly_type);
"""

def create_table():
    """Create the market_anomaly_notes table."""
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD
    )
    
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            conn.commit()
            print("Created market_anomaly_notes table successfully!")
            
            # Verify table exists
            cur.execute("""
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'market_anomaly_notes'
                ORDER BY ordinal_position
            """)
            columns = cur.fetchall()
            print("\nTable schema:")
            for col_name, col_type in columns:
                print(f"  {col_name}: {col_type}")
                
    finally:
        conn.close()

if __name__ == "__main__":
    create_table()






