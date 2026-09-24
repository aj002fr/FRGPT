#!/usr/bin/env python3
"""
View live pre-event dashboard logs for verification.

Usage:
    python scripts/view_live_logs.py               # View recent logs
    python scripts/view_live_logs.py --last 50     # View last 50 entries
    python scripts/view_live_logs.py --tail        # Tail the logs live
    python scripts/view_live_logs.py --export      # Export to CSV
    python scripts/view_live_logs.py --clear       # Clear all logs
"""

import sqlite3
import argparse
from pathlib import Path
from datetime import datetime
import time
import csv

# Log database path
LOG_DB = Path(__file__).resolve().parent.parent / "workspace" / "scenarios" / "live_preevent_log.db"


def get_logs(limit: int = 20, offset: int = 0):
    """Fetch logs from the database."""
    if not LOG_DB.exists():
        print(f"No log database found at: {LOG_DB}")
        return []
    
    conn = sqlite3.connect(str(LOG_DB))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM live_data_log 
        ORDER BY id DESC 
        LIMIT ? OFFSET ?
    ''', (limit, offset))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


def display_logs(logs, verbose=False):
    """Display logs in a formatted table."""
    if not logs:
        print("No logs found.")
        return
    
    print("\n" + "=" * 120)
    print(f"{'ID':>6} | {'Log Time':>19} | {'Event':>4} | {'TY Price':>10} | {'Ref Price':>10} | {'High':>10} | {'Low':>10} | {'Move Up':>8} | {'Move Dn':>8} | {'B/E':>8} | {'Rows':>4}")
    print("-" * 120)
    
    for log in reversed(logs):  # Show oldest first
        log_time = log['log_timestamp'][:19] if log['log_timestamp'] else '-'
        ty_price = f"{log['ty_price']:.6f}" if log['ty_price'] else '-'
        ref_price = f"{log['price_ref']:.6f}" if log['price_ref'] else '-'
        high = f"{log['high_since_ref']:.6f}" if log['high_since_ref'] else '-'
        low = f"{log['low_since_ref']:.6f}" if log['low_since_ref'] else '-'
        move_up = f"{log['move_up_bps']:.2f}" if log['move_up_bps'] else '-'
        move_dn = f"{log['move_down_bps']:.2f}" if log['move_down_bps'] else '-'
        be = f"{log['be_realized_bps']:.2f}" if log['be_realized_bps'] else '-'
        rows = str(log['new_rows_count']) if log['new_rows_count'] else '0'
        
        error_flag = " [ERR]" if log.get('error') else ""
        
        print(f"{log['id']:>6} | {log_time} | {log['event_type'] or '-':>4} | {ty_price:>10} | {ref_price:>10} | {high:>10} | {low:>10} | {move_up:>8} | {move_dn:>8} | {be:>8} | {rows:>4}{error_flag}")
        
        if verbose and log.get('error'):
            print(f"       ERROR: {log['error']}")
    
    print("=" * 120)
    print(f"Showing {len(logs)} log entries")


def tail_logs(interval=2.0):
    """Continuously display new logs."""
    print(f"Tailing logs (refresh every {interval}s)... Press Ctrl+C to stop.\n")
    
    last_id = 0
    
    try:
        while True:
            if not LOG_DB.exists():
                print(f"Waiting for log database at: {LOG_DB}")
                time.sleep(interval)
                continue
            
            conn = sqlite3.connect(str(LOG_DB))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT * FROM live_data_log 
                WHERE id > ?
                ORDER BY id ASC
            ''', (last_id,))
            
            rows = cursor.fetchall()
            conn.close()
            
            for row in rows:
                log = dict(row)
                last_id = log['id']
                
                log_time = log['log_timestamp'][:19] if log['log_timestamp'] else '-'
                ty_price = f"{log['ty_price']:.6f}" if log['ty_price'] else '-'
                ref_price = f"{log['price_ref']:.6f}" if log['price_ref'] else '-'
                high = f"{log['high_since_ref']:.6f}" if log['high_since_ref'] else '-'
                low = f"{log['low_since_ref']:.6f}" if log['low_since_ref'] else '-'
                move_up = f"{log['move_up_bps']:.2f}" if log['move_up_bps'] else '-'
                move_dn = f"{log['move_down_bps']:.2f}" if log['move_down_bps'] else '-'
                be = f"{log['be_realized_bps']:.2f}" if log['be_realized_bps'] else '-'
                
                print(f"[{log['id']:>5}] {log_time} | {log['event_type'] or '-':>4} | Price: {ty_price} | Ref: {ref_price} | High: {high} | Low: {low} | B/E: {be} bps")
                
                if log.get('error'):
                    print(f"        ERROR: {log['error']}")
            
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print("\n\nTailing stopped.")


def export_to_csv(output_path=None):
    """Export all logs to CSV."""
    if not LOG_DB.exists():
        print(f"No log database found at: {LOG_DB}")
        return
    
    if output_path is None:
        output_path = LOG_DB.parent / f"live_preevent_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    conn = sqlite3.connect(str(LOG_DB))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM live_data_log ORDER BY id ASC')
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        print("No logs to export.")
        return
    
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    
    print(f"Exported {len(rows)} logs to: {output_path}")


def clear_logs():
    """Clear all logs from the database."""
    if not LOG_DB.exists():
        print("No log database to clear.")
        return
    
    confirm = input("Are you sure you want to clear all logs? (yes/no): ")
    if confirm.lower() != 'yes':
        print("Cancelled.")
        return
    
    conn = sqlite3.connect(str(LOG_DB))
    cursor = conn.cursor()
    cursor.execute('DELETE FROM live_data_log')
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    
    print(f"Cleared {deleted} log entries.")


def get_stats():
    """Get summary statistics from logs."""
    if not LOG_DB.exists():
        print(f"No log database found at: {LOG_DB}")
        return
    
    conn = sqlite3.connect(str(LOG_DB))
    cursor = conn.cursor()
    
    # Count total logs
    cursor.execute('SELECT COUNT(*) FROM live_data_log')
    total = cursor.fetchone()[0]
    
    # Count by event type
    cursor.execute('SELECT event_type, COUNT(*) FROM live_data_log GROUP BY event_type')
    by_event = cursor.fetchall()
    
    # Get time range
    cursor.execute('SELECT MIN(log_timestamp), MAX(log_timestamp) FROM live_data_log')
    time_range = cursor.fetchone()
    
    # Get error count
    cursor.execute("SELECT COUNT(*) FROM live_data_log WHERE error IS NOT NULL AND error != ''")
    errors = cursor.fetchone()[0]
    
    # Get price range
    cursor.execute('SELECT MIN(ty_price), MAX(ty_price), AVG(ty_price) FROM live_data_log WHERE ty_price IS NOT NULL')
    price_stats = cursor.fetchone()
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("LIVE DASHBOARD LOG STATISTICS")
    print("=" * 60)
    print(f"Total log entries:    {total}")
    print(f"By event type:        {dict(by_event) if by_event else 'N/A'}")
    print(f"Time range:           {time_range[0][:19] if time_range[0] else 'N/A'} to {time_range[1][:19] if time_range[1] else 'N/A'}")
    print(f"Errors logged:        {errors}")
    if price_stats[0]:
        print(f"Price range:          {price_stats[0]:.6f} - {price_stats[1]:.6f} (avg: {price_stats[2]:.6f})")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View live pre-event dashboard logs")
    parser.add_argument('--last', type=int, default=20, help='Number of recent entries to show')
    parser.add_argument('--tail', action='store_true', help='Tail logs continuously')
    parser.add_argument('--export', action='store_true', help='Export logs to CSV')
    parser.add_argument('--clear', action='store_true', help='Clear all logs')
    parser.add_argument('--stats', action='store_true', help='Show log statistics')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show verbose output')
    
    args = parser.parse_args()
    
    print(f"Log database: {LOG_DB}")
    
    if args.tail:
        tail_logs()
    elif args.export:
        export_to_csv()
    elif args.clear:
        clear_logs()
    elif args.stats:
        get_stats()
    else:
        logs = get_logs(limit=args.last)
        display_logs(logs, verbose=args.verbose)








