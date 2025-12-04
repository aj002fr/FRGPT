"""Verify predictive markets setup."""

import sqlite3
from pathlib import Path

def verify():
    """Verify database and files."""
    project_root = Path(__file__).parent.parent
    db_path = project_root / "market_data.db"
    
    print("=== Predictive Markets Setup Verification ===\n")
    
    # Check database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='prediction_queries'
    """)
    
    table_exists = cursor.fetchone() is not None
    print(f"Database table 'prediction_queries': {'EXISTS' if table_exists else 'MISSING'}")
    
    if table_exists:
        cursor.execute("PRAGMA table_info(prediction_queries)")
        columns = cursor.fetchall()
        print(f"  Columns: {len(columns)}")
        for col in columns:
            print(f"    - {col[1]} ({col[2]})")
    
    conn.close()
    
    # Check files
    print("\nAgent files:")
    agent_path = project_root / "src" / "agents" / "predictive_markets_agent"
    if agent_path.exists():
        files = list(agent_path.glob("*.py")) + list(agent_path.glob("*.md"))
        for f in files:
            print(f"  - {f.name}")
    else:
        print("  MISSING")
    
    print("\nTest files:")
    test_path = project_root / "tests" / "e2e"
    test_file = test_path / "test_predictions_e2e.py"
    print(f"  - test_predictions_e2e.py: {'EXISTS' if test_file.exists() else 'MISSING'}")
    
    print("\nScript files:")
    scripts_path = project_root / "scripts"
    script_file = scripts_path / "test_predictions.py"
    print(f"  - test_predictions.py: {'EXISTS' if script_file.exists() else 'MISSING'}")
    
    print("\n=== Verification Complete ===")

if __name__ == "__main__":
    verify()

