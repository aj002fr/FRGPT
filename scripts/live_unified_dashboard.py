#!/usr/bin/env python3
"""
Live Unified Event Dashboard Generator
---------------------------------------
Creates an interactive HTML dashboard with TABBED interface for analyzing
both pre-event and post-event price paths with statistical overlays.

Two tabs:
- Pre-Event Analysis: 3pm T-1 to 8:30am event day (-17.5h to 0h from release)
- Post-Event Analysis: 8:30am T to 3pm T (0h to +6.5h from release)

Usage:
    python scripts/live_unified_dashboard.py --event CPI --serve
    python scripts/live_unified_dashboard.py --event NFP --fwd_be 6.5
"""

# Pre-event window constants (hours from 8:30am release)
PREEVENT_WINDOW_START_HOURS = -17.5  # 3pm T-1 relative to 8:30am release
PREEVENT_WINDOW_END_HOURS = 0        # 8:30am event time (release)

# Post-event window constants (hours from 8:30am release)
POSTEVENT_WINDOW_START_HOURS = 0     # 8:30am release time
POSTEVENT_WINDOW_END_HOURS = 6.5     # 3pm T = 6.5 hours after 8:30am


def get_current_hours_from_event() -> float:
    """
    Calculate current time as hours from event release (8:30am).
    Returns negative value pre-event, positive post-event.
    """
    now = datetime.now()
    current_hour = now.hour + now.minute / 60 + now.second / 3600
    
    # Release is at 8:30am (8.5 decimal hours)
    release_hour = 8.5
    
    if current_hour < release_hour:
        # Before 8:30am on event day - hours until release
        hours_from_event = current_hour - release_hour  # e.g., 6am = -2.5h
    else:
        # After 8:30am - calculate hours since release or hours until tomorrow's
        hours_from_event = current_hour - release_hour  # e.g., 3pm = +6.5h
    
    return hours_from_event


import functools
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sys
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
import uuid

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PLOTLY_CDN_URL = "https://cdn.plot.ly/plotly-2.27.0.min.js"
PLOTLY_CACHE_PATH = PROJECT_ROOT / "workspace" / "scenarios" / "_assets" / "plotly-2.27.0.min.js"

# Import treasury futures conversion utilities
from rules.treasury_price_conversion import (
    points_to_bps as decimal_to_bps,
    bps_to_points as bps_to_decimal,
    ticks_to_bps,
    bps_to_ticks,
    decimal_to_ticks,
    ticks_to_decimal,
)
from src.core.dashboard_helpers import (
    be_multiplier as calc_be_multiplier,
    normalize_price_to_bps,
    percentile,
    window_move_bps,
)

# ============================================================================
# LIVE DATA SERVER CONFIGURATION
# ============================================================================
SNAPSHOTS_DIR = Path(r"X:\uikitxv2\data\snapshot_replicas")
LIVE_SERVER_PORT = 8800  # Changed port to avoid conflicts
TY_SYMBOL_PREFIX = "TY"  # Look for TYH6, TYM6, etc.


# ============================================================================
# LIVE DATA LOGGING
# ============================================================================
LIVE_LOG_DB = Path(__file__).resolve().parent.parent / "workspace" / "scenarios" / "live_unified_log.db"

def init_live_log_db():
    """Initialize the live logging database."""
    LIVE_LOG_DB.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(LIVE_LOG_DB))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS live_data_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_timestamp TEXT NOT NULL,
            event_type TEXT,
            source_db TEXT,
            snapshot_timestamp TEXT,
            ty_price REAL,
            price_ref REAL,
            price_ref_timestamp TEXT,
            ref_locked INTEGER,
            high_since_ref REAL,
            low_since_ref REAL,
            move_up_bps REAL,
            move_down_bps REAL,
            be_realized_bps REAL,
            new_rows_count INTEGER,
            last_seen_id INTEGER,
            error TEXT
        )
    ''')
    conn.commit()
    conn.close()
    print(f"[LiveLog] Database initialized at: {LIVE_LOG_DB}")


def log_live_data(event_type: str, data: Dict, server_state: Dict):
    """Log live data point to database for verification."""
    try:
        conn = sqlite3.connect(str(LIVE_LOG_DB))
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO live_data_log (
                log_timestamp, event_type, source_db,
                snapshot_timestamp, ty_price,
                price_ref, price_ref_timestamp, ref_locked,
                high_since_ref, low_since_ref,
                move_up_bps, move_down_bps, be_realized_bps,
                new_rows_count, last_seen_id, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(),
            event_type,
            data.get('db_file'),
            data.get('timestamp_iso'),
            data.get('ty_price'),
            data.get('ty_price_3pm'),
            data.get('ty_price_3pm_timestamp'),
            1 if server_state.get('ref_locked') else 0,
            data.get('high_since_3pm'),
            data.get('low_since_3pm'),
            data.get('move_up_bps'),
            data.get('move_down_bps'),
            data.get('be_realized_bps'),
            data.get('new_rows_processed', 0),
            server_state.get('last_seen_id', 0),
            data.get('error')
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[LiveLog] Error logging: {e}")


# ============================================================================
# LIVE DATA SERVER CLASS (Unified for both pre and post event)
# ============================================================================
class LiveDataServer:
    """
    Flask server that provides live TY price data from snapshot database.
    Supports both pre-event and post-event windows.
    """
    
    def __init__(self, event_type: str = "NFP"):
        self.event_type = event_type
        self.conn = None
        self.db_path = None
        self.price_ref = None
        self.price_ref_timestamp = None
        self.ref_locked = False
        self.high_since_ref = None
        self.low_since_ref = None
        self.last_known_price = None
        self.last_seen_id = 0
        self._lock = threading.Lock()
        self._current_db_date = None
        self._ref_init_inflight = False
        
        init_live_log_db()
        self._kickoff_reference_init()

    def _kickoff_reference_init(self):
        """Initialize reference price in the background to avoid blocking startup."""
        if self._ref_init_inflight:
            return
        self._ref_init_inflight = True

        def _run():
            try:
                self._initialize_reference_price()
            finally:
                self._ref_init_inflight = False

        threading.Thread(target=_run, daemon=True).start()
    
    def _get_latest_db_path(self) -> Optional[Path]:
        """Find the latest snapshot database file."""
        if not SNAPSHOTS_DIR.exists():
            return None
        
        now = datetime.now()
        current_hour = now.hour + now.minute / 60
        today = now.strftime("%Y-%m-%d")
        
        if current_hour >= 17:  # 5pm CST = 6pm EST
            tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            tomorrow_db = SNAPSHOTS_DIR / f"snapshots_{tomorrow}.db"
            if tomorrow_db.exists():
                return tomorrow_db
        
        today_db = SNAPSHOTS_DIR / f"snapshots_{today}.db"
        if today_db.exists():
            return today_db
        
        db_files = sorted(SNAPSHOTS_DIR.glob("snapshots_*.db"), reverse=True)
        return db_files[0] if db_files else None
    
    def _reset_session_state(self, new_db_date: str):
        """Reset session state when switching to a new database."""
        old_date = self._current_db_date
        self._current_db_date = new_db_date
        
        self.price_ref = None
        self.price_ref_timestamp = None
        self.ref_locked = False
        self.high_since_ref = None
        self.low_since_ref = None
        self.last_known_price = None
        self.last_seen_id = 0
        
        print(f"[LiveServer] Session reset: switched from {old_date} to {new_db_date} database")
        self._kickoff_reference_init()
    
    def _initialize_reference_price(self):
        """Set reference price to the first snapshot AT or AFTER 3pm EST."""
        db_path = self._get_latest_db_path()
        if db_path is None:
            print("[LiveServer] No snapshot DB found for reference price")
            return
        
        now = datetime.now()
        current_hour = now.hour + now.minute / 60
        db_date = db_path.stem.replace("snapshots_", "")
        
        today = now.strftime("%Y-%m-%d")
        if current_hour >= 17:
            today_db = SNAPSHOTS_DIR / f"snapshots_{today}.db"
            if today_db.exists():
                ref_db_path = today_db
                ref_db_date = today
                print(f"[LiveServer] Looking for 3pm reference in today's DB: {today_db.name}")
            else:
                ref_db_path = db_path
                ref_db_date = db_date
        else:
            ref_db_path = db_path
            ref_db_date = db_date
        
        try:
            max_retries = 3
            conn = None
            for attempt in range(max_retries):
                try:
                    conn = sqlite3.connect(str(ref_db_path), timeout=10.0)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=10000")
                    break
                except sqlite3.OperationalError as e:
                    if attempt < max_retries - 1:
                        time.sleep(0.5)
                        continue
                    raise
            
            if conn is None:
                print(f"[LiveServer] Could not connect to {ref_db_path} after {max_retries} attempts")
                return
            
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp_iso, snapshot_json 
                FROM positions_snapshots 
                WHERE timestamp_iso LIKE ?
                ORDER BY timestamp_iso ASC
            """, (f"{ref_db_date}%",))
            
            rows = cursor.fetchall()
            
            if rows:
                for row in rows:
                    timestamp_iso = row[0]
                    try:
                        hour = int(timestamp_iso[11:13])
                        minute = int(timestamp_iso[14:16])
                        time_minutes = hour * 60 + minute
                        target_minutes = 14 * 60  # 3pm EST = 2pm CST = 14:00
                        
                        if time_minutes >= target_minutes:
                            snapshot_json = row[1]
                            price = self._get_ty_price_from_snapshot(snapshot_json)
                            if price:
                                self.price_ref = price
                                self.price_ref_timestamp = timestamp_iso
                                self.high_since_ref = price
                                self.low_since_ref = price
                                self.ref_locked = True
                                print(f"[LiveServer] 3pm Reference price: {price} at {timestamp_iso}")
                                conn.close()
                                return
                    except:
                        continue
            
            cursor.execute("""
                SELECT timestamp_iso, snapshot_json 
                FROM positions_snapshots 
                ORDER BY id DESC LIMIT 1
            """)
            row = cursor.fetchone()
            
            if row:
                timestamp_iso, snapshot_json = row
                price = self._get_ty_price_from_snapshot(snapshot_json)
                if price:
                    self.price_ref = price
                    self.price_ref_timestamp = timestamp_iso
                    self.high_since_ref = price
                    self.low_since_ref = price
                    self.ref_locked = False
                    print(f"[LiveServer] Pre-3pm: using latest as temp reference: {price} at {timestamp_iso}")
            
            conn.close()
        except Exception as e:
            print(f"[LiveServer] Error initializing reference price: {e}")
    
    def _get_ty_price_from_snapshot(self, snapshot_json: str) -> Optional[float]:
        """Extract TY futures price from snapshot JSON."""
        try:
            data = json.loads(snapshot_json)
            rows = data.get('rows', [])
            
            for row in rows:
                symbol = row.get('symbol', '')
                if symbol.startswith(TY_SYMBOL_PREFIX) and 'Comdty' in symbol:
                    return row.get('live_px')
            
            return None
        except Exception as e:
            print(f"[LiveServer] Error parsing snapshot: {e}")
            return None
    
    def _ensure_connection(self) -> bool:
        """Ensure we have a valid read-only connection to the latest DB."""
        current_db = self._get_latest_db_path()
        
        if current_db is None:
            return False
        
        db_date = current_db.stem.replace("snapshots_", "")
        
        if self._current_db_date is not None and db_date != self._current_db_date:
            self._reset_session_state(db_date)
        elif self._current_db_date is None:
            self._current_db_date = db_date
        
        if self.conn is None or str(current_db) != self.db_path:
            if self.conn:
                try:
                    self.conn.close()
                except:
                    pass
            
            try:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        self.conn = sqlite3.connect(
                            str(current_db),
                            check_same_thread=False,
                            timeout=10.0
                        )
                        self.conn.execute("PRAGMA journal_mode=WAL")
                        self.conn.execute("PRAGMA busy_timeout=10000")
                        self.conn.execute("PRAGMA query_only = ON")
                        break
                    except sqlite3.OperationalError as e:
                        if attempt < max_retries - 1:
                            time.sleep(0.5)
                            continue
                        raise
                
                self.db_path = str(current_db)
                print(f"[LiveServer] Connected to {current_db.name}")
                return True
            except Exception as e:
                print(f"[LiveServer] Connection error: {e}")
                return False
        
        return True
    
    def get_live_data(self) -> Dict:
        """Get the latest live price data."""
        with self._lock:
            current_db = self._get_latest_db_path()
            if current_db is None:
                return {
                    "error": "No database found",
                    "timestamp_iso": datetime.now().isoformat()
                }
            
            conn = None
            try:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        conn = sqlite3.connect(str(current_db), timeout=10.0)
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("PRAGMA busy_timeout=10000")
                        break
                    except sqlite3.OperationalError as e:
                        if attempt < max_retries - 1:
                            time.sleep(0.3)
                            continue
                        raise
                
                cursor = conn.cursor()
                
                if self.price_ref is None:
                    self._initialize_reference_price()
                
                if not self.ref_locked:
                    self._initialize_reference_price()
                
                cursor.execute("""
                    SELECT id, timestamp_iso, snapshot_json 
                    FROM positions_snapshots 
                    WHERE id > ?
                    ORDER BY id ASC
                """, (self.last_seen_id,))
                
                rows = cursor.fetchall()
                
                if not rows:
                    if self.last_known_price is not None:
                        pass
                    else:
                        return {
                            "error": "No data in database",
                            "timestamp_iso": datetime.now().isoformat()
                        }
                
                timestamp_iso = None
                ty_price = None
                new_rows_count = len(rows) if rows else 0
                
                for row in rows:
                    row_id, row_timestamp, row_snapshot = row
                    price = self._get_ty_price_from_snapshot(row_snapshot)
                    
                    if price is not None and 90 < price < 140:
                        if self.high_since_ref is None or price > self.high_since_ref:
                            self.high_since_ref = price
                        if self.low_since_ref is None or price < self.low_since_ref:
                            self.low_since_ref = price
                    
                    self.last_seen_id = row_id
                    timestamp_iso = row_timestamp
                    ty_price = price
                
                if ty_price is not None:
                    self.last_known_price = ty_price
                
                if timestamp_iso is None and self.last_known_price is not None:
                    ty_price = self.last_known_price
                    timestamp_iso = datetime.now().isoformat()
                
                be_realized_bps = None
                move_up_bps = None
                move_down_bps = None
                if self.price_ref is not None and self.high_since_ref is not None and self.low_since_ref is not None:
                    move_up_bps = decimal_to_bps(self.high_since_ref - self.price_ref)
                    move_down_bps = decimal_to_bps(self.low_since_ref - self.price_ref)
                    be_realized_bps = max(move_up_bps, abs(move_down_bps))
                
                result = {
                    "timestamp_iso": timestamp_iso,
                    "ty_price": ty_price,
                    "ty_price_3pm": self.price_ref,
                    "ty_price_3pm_timestamp": self.price_ref_timestamp,
                    "high_since_3pm": self.high_since_ref,
                    "low_since_3pm": self.low_since_ref,
                    "move_up_bps": move_up_bps,
                    "move_down_bps": move_down_bps,
                    "be_realized_bps": be_realized_bps,
                    "db_file": current_db.name,
                    "new_rows_processed": new_rows_count
                }
                
                log_live_data(self.event_type, result, {
                    'ref_locked': self.ref_locked,
                    'last_seen_id': self.last_seen_id
                })
                
                return result
                
            except sqlite3.DatabaseError as e:
                error_msg = str(e)
                if "malformed" in error_msg.lower() or "locked" in error_msg.lower():
                    error_msg = "Database busy (being written to) - will retry"
                return {
                    "error": error_msg,
                    "timestamp_iso": datetime.now().isoformat(),
                    "ty_price": getattr(self, 'last_known_price', self.price_ref),
                    "ty_price_3pm": self.price_ref,
                    "ty_price_3pm_timestamp": self.price_ref_timestamp,
                    "high_since_3pm": self.high_since_ref,
                    "low_since_3pm": self.low_since_ref,
                    "move_up_bps": (self.high_since_ref - self.price_ref) * 16 if self.high_since_ref and self.price_ref else 0.0,
                    "move_down_bps": (self.price_ref - self.low_since_ref) * 16 if self.low_since_ref and self.price_ref else 0.0,
                    "be_realized_bps": 0.0,
                    "db_file": current_db.name if current_db else None
                }
            except Exception as e:
                return {
                    "error": str(e),
                    "timestamp_iso": datetime.now().isoformat()
                }
            finally:
                if conn:
                    try:
                        conn.close()
                    except:
                        pass
    
    def close(self):
        """Close the database connection."""
        if self.conn:
            try:
                self.conn.close()
            except:
                pass
            self.conn = None


def get_events_for_date(event_date: str) -> List[Dict]:
    """
    Query economic_events.db for all significant events on a specific date.
    Returns events with their times for display on candlestick charts.
    """
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    
    try:
        conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT event_name, importance, category, actual, consensus, country, event_date
            FROM economic_events 
            WHERE DATE(event_date) = ?
              AND (LOWER(importance) = 'high' OR LOWER(importance) = 'medium')
              AND country = 'United States'
            ORDER BY event_date ASC, importance DESC
        """, (event_date,))
        
        rows = cursor.fetchall()
        conn.close()
        
        events = []
        for row in rows:
            # Parse the event_date to get time
            date_str = row['event_date']
            time_str = "08:30"  # Default time if not available
            if 'T' in str(date_str):
                time_str = str(date_str).split('T')[1][:5]
            elif ' ' in str(date_str):
                time_str = str(date_str).split(' ')[1][:5]
            
            events.append({
                'name': row['event_name'],
                'importance': row['importance'],
                'category': row['category'],
                'actual': row['actual'],
                'consensus': row['consensus'],
                'date': event_date,
                'time': time_str,
                'datetime': f"{event_date}T{time_str}:00",
            })
        
        return events
    except Exception as e:
        print(f"Warning: Could not query events for {event_date}: {e}")
        return []


def create_dashboard_app(html_path: str, event_type: str = "NFP", enable_socketio: bool = True):
    """
    Application factory for creating the Flask app.
    Can be used by Gunicorn/Waitress for multi-worker deployment.
    
    Args:
        html_path: Path to the generated HTML dashboard
        event_type: "NFP" or "CPI"
        enable_socketio: Whether to enable WebSocket support (default: True)
    
    Returns:
        Flask app instance (or SocketIO app if enable_socketio=True)
    """
    try:
        from flask import Flask, jsonify, make_response, send_file, request
        from flask_cors import CORS
    except ImportError:
        print("\n[App] Flask not installed. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "flask", "flask-cors", "-q"])
        from flask import Flask, jsonify, make_response, send_file, request
        from flask_cors import CORS
    
    # Import TTLCache for shared caching across requests
    try:
        from cachetools import TTLCache
    except ImportError:
        print("\n[App] cachetools not installed. Installing...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "cachetools", "-q"])
        from cachetools import TTLCache
    
    # Import flask-socketio for WebSocket support
    socketio = None
    if enable_socketio:
        try:
            from flask_socketio import SocketIO, emit, join_room, leave_room
        except ImportError:
            print("\n[App] flask-socketio not installed. Installing...")
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "flask-socketio", "eventlet", "-q"])
            from flask_socketio import SocketIO, emit, join_room, leave_room
    
    app = Flask(__name__)
    CORS(app)

    @app.errorhandler(Exception)
    def _handle_api_exception(exc):
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "error": str(exc)}), 500
        raise exc
    
    # Initialize SocketIO if enabled
    if enable_socketio:
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
    
    # Shared caches - reduce redundant DB queries across users
    # TTL of 5 seconds for live data, 60 seconds for event data
    live_cache = TTLCache(maxsize=10, ttl=5)
    events_cache = TTLCache(maxsize=50, ttl=60)
    event_detail_cache = TTLCache(maxsize=200, ttl=300)
    l2_cache = TTLCache(maxsize=500, ttl=900)
    meta_cache = TTLCache(maxsize=500, ttl=300)
    telegram_chat_cache = TTLCache(maxsize=64, ttl=300)
    supported_events = {e.upper() for e in get_supported_events()}
    preload_inflight = set()
    preload_lock = threading.Lock()

    def _resolve_event_type_from_path(path: str, default_type: str) -> str:
        if not path:
            return default_type
        suffix = path.strip("/").split("/")[-1]
        if not suffix:
            return default_type
        candidate = suffix.upper()
        return candidate if candidate in supported_events else default_type

    def _build_light_events_response(req_event_type: str, events: List[Dict], cons_std_stats: Optional[Dict] = None,
                                     all_std_surprises: Optional[List[float]] = None) -> Dict:
        light_events = [build_light_event(e) for e in events]
        return {
            "success": True,
            "event_type": req_event_type,
            "light": True,
            "events": light_events,
            "cons_std_stats": cons_std_stats or compute_cons_std_stats(light_events),
            "historical_std_surprises": all_std_surprises or extract_std_surprises(light_events),
            "adhoc_trading_days": [],
        }

    def _get_meta_cached(req_event_type: str, event_date: str) -> dict:
        cache_key = f"meta:{req_event_type}:{event_date}"
        if cache_key in meta_cache:
            return meta_cache[cache_key]
        from scripts.l1_event_store import load_event_meta
        meta = load_event_meta(req_event_type, event_date)
        meta_cache[cache_key] = meta
        return meta

    def _hash_payload(payload: dict) -> str:
        import hashlib
        import json
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.md5(raw).hexdigest()

    def _warm_events_cache(req_event_type: str) -> None:
        cache_key = f"events:{req_event_type}"
        if cache_key in events_cache:
            return
        with preload_lock:
            if req_event_type in preload_inflight:
                return
            preload_inflight.add(req_event_type)

        def _load():
            try:
                cached = load_cached_dashboard_data(req_event_type)
                if cached:
                    events_cache[cache_key] = _build_light_events_response(
                        req_event_type,
                        cached.get("events", []),
                        cons_std_stats=cached.get("cons_std_stats", {}),
                        all_std_surprises=cached.get("all_std_surprises", []),
                    )
                    return
                raw_data = load_event_data(req_event_type)
                if not raw_data:
                    return
                events_cache[cache_key] = _build_light_events_response(req_event_type, raw_data)
            finally:
                with preload_lock:
                    preload_inflight.discard(req_event_type)

        threading.Thread(target=_load, daemon=True).start()
    
    # Create live data server instance (snapshot DB fallback)
    live_server = LiveDataServer(event_type)

    # Databento live overlay (optional)
    databento_state = None
    try:
        from scripts.databento_live_state import DatabentoLiveState, get_databento_env
        env = get_databento_env()
        if env.get("api_key"):
            databento_state = DatabentoLiveState(
                api_key=env["api_key"],
                dataset=env["dataset"],
                schema=env["schema"],
                symbol=env["symbol"],
                stype_in=env["stype_in"],
            )
            databento_state.start()
    except Exception as e:
        print(f"[LiveServer] Databento overlay not active: {e}")
    
    # Store socketio instance in app for access by background thread
    app.socketio = socketio
    app.live_server = live_server
    app.databento_state = databento_state
    app.live_cache = live_cache
    
    @app.route('/plotly.min.js')
    def serve_plotly():
        """Serve Plotly.js locally to avoid CDN issues."""
        plotly_path = OUTPUT_DIR / "plotly.min.js"
        if plotly_path.exists():
            return send_file(str(plotly_path), mimetype='application/javascript')
        # Fallback to CDN redirect
        from flask import redirect
        return redirect("https://cdn.plot.ly/plotly-2.27.0.min.js")
    
    @app.route('/')
    def serve_dashboard():
        try:
            latest_path = _get_latest_dashboard_file(event_type, html_path)
            response = make_response(send_file(str(latest_path), mimetype='text/html'))
            response.headers['Cache-Control'] = 'no-store, max-age=0'
            return response
        except Exception as e:
            return f"Error loading dashboard: {e}", 500

    @app.route('/<event_suffix>', strict_slashes=False)
    def serve_dashboard_with_suffix(event_suffix: str):
        req_event_type = _resolve_event_type_from_path(f"/{event_suffix}", event_type)
        if req_event_type not in supported_events:
            return f"Unknown event type '{event_suffix}'", 404
        _warm_events_cache(req_event_type)
        latest_path = _get_latest_dashboard_file(req_event_type, html_path)
        response = make_response(send_file(str(latest_path), mimetype='text/html'))
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        return response
    
    @app.route('/api/live')
    def get_live():
        """Get live price data with caching to reduce DB load."""
        cache_key = 'live_data'
        if cache_key in live_cache:
            return jsonify(live_cache[cache_key])
        
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                # Databento-only: no fallback to snapshot DB.
                if databento_state is None:
                    data = {
                        "error": "Databento not configured (set DATABENTO_API_KEY)",
                        "timestamp_iso": datetime.now().isoformat(),
                        "ty_price": None,
                        "source": "databento",
                    }
                else:
                    future = executor.submit(databento_state.get_snapshot)
                    try:
                        data = future.result(timeout=5.0)  # 5 second timeout
                        data = data or {}
                        data.setdefault("source", "databento")
                        data.setdefault("timestamp_iso", datetime.now().isoformat())
                        if data.get("ty_price") is None:
                            data.setdefault("error", "Live price unavailable from Databento")
                    except concurrent.futures.TimeoutError:
                        data = {
                            "error": "Databento timeout",
                            "timestamp_iso": datetime.now().isoformat(),
                            "ty_price": None,
                            "source": "databento",
                        }
        except Exception as e:
            data = {
                "error": f"Databento live error: {e}",
                "timestamp_iso": datetime.now().isoformat(),
                "ty_price": None,
                "source": "databento",
            }
        
        live_cache[cache_key] = data
        return jsonify(data)
    
    # WebSocket event handlers
    if enable_socketio:
        @socketio.on('connect')
        def handle_connect():
            print(f"[WebSocket] Client connected")
            emit('connected', {'status': 'ok'})
        
        @socketio.on('disconnect')
        def handle_disconnect():
            print(f"[WebSocket] Client disconnected")
        
        @socketio.on('subscribe')
        def handle_subscribe(data):
            """Client subscribes to a data channel (e.g., 'live', 'TY')."""
            channel = data.get('channel', 'live')
            join_room(channel)
            print(f"[WebSocket] Client subscribed to {channel}")
            emit('subscribed', {'channel': channel})
        
        @socketio.on('unsubscribe')
        def handle_unsubscribe(data):
            """Client unsubscribes from a channel."""
            channel = data.get('channel', 'live')
            leave_room(channel)
            print(f"[WebSocket] Client unsubscribed from {channel}")
        
        @socketio.on('request_live')
        def handle_request_live():
            """Client requests current live data."""
            cache_key = 'live_data'
            if cache_key in live_cache:
                data = live_cache[cache_key]
            else:
                if databento_state is None:
                    data = {
                        "error": "Databento not configured (set DATABENTO_API_KEY)",
                        "timestamp_iso": datetime.now().isoformat(),
                        "ty_price": None,
                        "source": "databento",
                    }
                else:
                    try:
                        data = databento_state.get_snapshot() or {}
                        data.setdefault("source", "databento")
                        data.setdefault("timestamp_iso", datetime.now().isoformat())
                        if data.get("ty_price") is None:
                            data.setdefault("error", "Live price unavailable from Databento")
                    except Exception as e:
                        data = {
                            "error": f"Databento live error: {e}",
                            "timestamp_iso": datetime.now().isoformat(),
                            "ty_price": None,
                            "source": "databento",
                        }
                live_cache[cache_key] = data
            emit('live_update', data)
    
    @app.route('/api/refresh_ref')
    def refresh_ref():
        live_server._initialize_reference_price()
        return jsonify({"status": "refreshed", "price_ref": live_server.price_ref})
    
    @app.route('/api/events')
    def get_events():
        """API endpoint to fetch lightweight event list from L1 + SQLite."""
        req_event_type = (request.args.get("event_type") or request.args.get("type") or event_type).upper()
        from config.unified_dashboard_event_config import load_unified_dashboard_event_config
        cfg = load_unified_dashboard_event_config()
        start_year = request.args.get("start_year") or cfg.get("default_start_year")
        try:
            from scripts.build_l1_event_features import load_event_instances
            from scripts.l1_event_store import get_event_paths, load_event_meta
            from datetime import datetime, timezone
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

        start_date = None
        if start_year:
            try:
                start_date = f"{int(start_year)}-01-01"
            except ValueError:
                return jsonify({"success": False, "error": "Invalid start_year"}), 400

        cache_key = f"events:{req_event_type}:{start_year or ''}"
        if cache_key in events_cache:
            return jsonify(events_cache[cache_key])

        try:
            # Prefer cached dashboard data for richer event metadata (cons_std/surp_pct/etc)
            cached = load_cached_dashboard_data(req_event_type) or load_dashboard_cache_raw(req_event_type)
            if cached and isinstance(cached.get("events"), list):
                uer_by_date = {}
                if req_event_type == "NFP":
                    try:
                        from scripts.nfp_data_loader import get_nfp_data_combined
                        for row in get_nfp_data_combined():
                            date_key = row.get("date")
                            if not date_key:
                                continue
                            uer_by_date[date_key] = {
                                "uer_cons_std": row.get("uer_std"),
                                "uer_actual": row.get("uer_actual"),
                                "uer_cons": row.get("uer_cons"),
                                "uer_surprise": row.get("uer_surprise"),
                                "uer_std_surprise": row.get("uer_std_surprise"),
                            }
                    except Exception as e:
                        print(f"Warning: UER fallback load failed: {e}")
                out = []
                for ev in cached["events"]:
                    date_str = ev.get("date")
                    if not date_str:
                        continue
                    if start_date and date_str < start_date:
                        continue
                    uer_fallback = uer_by_date.get(date_str, {})
                    out.append(
                        {
                            "date": date_str,
                            "event_time": ev.get("event_time"),
                            "fwd": ev.get("fwd"),
                            "cons_std": ev.get("cons_std"),
                            "uer_cons_std": ev.get("uer_cons_std") or uer_fallback.get("uer_cons_std"),
                            "surp_pct": ev.get("surp_pct"),
                            "surprise": ev.get("surprise"),
                            "standardized_surprise": ev.get("standardized_surprise"),
                            "post_be": ev.get("post_be"),
                            "be_by_time": ev.get("be_by_time"),
                            "bps_by_time": ev.get("bps_by_time"),
                            "nfp_actual": ev.get("nfp_actual"),
                            "nfp_cons": ev.get("nfp_cons"),
                            "uer_actual": ev.get("uer_actual") or uer_fallback.get("uer_actual"),
                            "uer_cons": ev.get("uer_cons") or uer_fallback.get("uer_cons"),
                            "uer_surprise": ev.get("uer_surprise") or uer_fallback.get("uer_surprise"),
                            "uer_std_surprise": ev.get("uer_std_surprise") or uer_fallback.get("uer_std_surprise"),
                        }
                    )
                result = {"success": True, "event_type": req_event_type, "events": out, "light": True}
                events_cache[cache_key] = result
                return jsonify(result)

            # Fallback to L1 + SQLite when cache is unavailable
            events = load_event_instances(req_event_type, start_date, None)
            out = []
            for ev in events:
                date_str = ev["event_date"]
                paths = get_event_paths(req_event_type, date_str)
                has_l1 = paths.price_1m.exists() and paths.meta.exists()
                annotations = []
                event_ts_utc = None
                if has_l1:
                    meta = load_event_meta(req_event_type, date_str)
                    annotations = meta.get("annotations_same_day", []) or []
                    event_ts_utc = meta.get("event_ts_utc")
                if event_ts_utc is None:
                    event_ts_utc = int(ev["event_dt_et"].astimezone(timezone.utc).timestamp() * 1_000_000_000)
                out.append(
                    {
                        "date": date_str,
                        "event_ts_utc": event_ts_utc,
                        "label": ev.get("event_time"),
                        "has_l1": has_l1,
                        "annotations": annotations,
                    }
                )

            result = {"success": True, "event_type": req_event_type, "events": out, "light": True}
            events_cache[cache_key] = result
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route('/api/event_details')
    def get_event_details():
        """Fetch full price paths and candlesticks for a single event date."""
        req_event_type = (request.args.get('event_type') or request.args.get('type') or event_type).upper()
        event_date = request.args.get('date')
        if not event_date:
            return jsonify({"error": "Missing required 'date' parameter", "success": False}), 400

        mode = request.args.get("mode")
        pre_start_hhmm = request.args.get("pre_start_hhmm")
        post_end_hhmm = request.args.get("post_end_hhmm")
        build_version = None
        try:
            meta = _get_meta_cached(req_event_type, event_date)
            build_version = meta.get("build_version")
        except Exception:
            meta = None
        cache_key = f"event_detail:{req_event_type}:{event_date}:{build_version}:{mode}:{pre_start_hhmm}:{post_end_hhmm}"
        if cache_key in l2_cache:
            return jsonify(l2_cache[cache_key])

        try:
            cached_event = None
            cached = load_cached_dashboard_data(req_event_type) or load_dashboard_cache_raw(req_event_type)
            if cached and isinstance(cached.get("events"), list):
                cached_event = next((e for e in cached["events"] if e.get("date") == event_date), None)
                # Only fast-path cached events that are already hydrated.
                if _event_has_detail_payload(cached_event):
                    if cached_event.get("cons_std") is None:
                        try:
                            from scripts.build_l1_event_features import EVENT_CONFIG as L1_EVENT_CONFIG
                            db_event_name = L1_EVENT_CONFIG.get(req_event_type, {}).get("db_event_name")
                            if db_event_name:
                                conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
                                cur = conn.cursor()
                                cur.execute(
                                    """
                                    SELECT consensus_std_dev
                                    FROM bbg_data
                                    WHERE db_event_name = ?
                                      AND timestamp LIKE ?
                                    LIMIT 1
                                    """,
                                    (db_event_name, f"{event_date}%"),
                                )
                                row = cur.fetchone()
                                conn.close()
                                if row:
                                    cached_event["cons_std"] = row[0]
                        except Exception:
                            pass
                    result = {
                        "success": True,
                        "event_type": req_event_type,
                        "event": cached_event,
                    }
                    l2_cache[cache_key] = result
                    return jsonify(result)

            # Serve from L1 store only (no Postgres in request handlers)
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            from scripts.l1_event_store import load_event_meta, load_price_table, slice_by_ts_range
            from scripts.build_l1_event_features import EVENT_CONFIG
            import sqlite3

            meta = meta or load_event_meta(req_event_type, event_date)
            event_ts_et = meta.get("event_ts_et")
            if not event_ts_et:
                return jsonify({"success": False, "error": "Missing event_ts_et in meta"}), 500

            dt = datetime.fromisoformat(str(event_ts_et))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))

            # Determine pre/post window bounds
            def _parse_hhmm(s: str) -> tuple[int, int]:
                h, m = s.split(":")
                return int(h), int(m)

            pre_start = pre_start_hhmm or "15:00"
            post_end = post_end_hhmm or "15:00"

            pre_h, pre_m = _parse_hhmm(pre_start)
            post_h, post_m = _parse_hhmm(post_end)
            pre_start_dt = dt.replace(hour=pre_h, minute=pre_m, second=0, microsecond=0)
            if pre_start_dt.time() > dt.time():
                pre_start_dt = pre_start_dt - timedelta(days=1)
            post_end_dt = dt.replace(hour=post_h, minute=post_m, second=0, microsecond=0)

            table = load_price_table(req_event_type, event_date, "1m")
            pre_slice = slice_by_ts_range(
                table,
                int(pre_start_dt.timestamp() * 1_000_000_000),
                int(dt.timestamp() * 1_000_000_000),
            )
            post_slice = slice_by_ts_range(
                table,
                int(dt.timestamp() * 1_000_000_000),
                int(post_end_dt.timestamp() * 1_000_000_000),
            )

            def _build_prices(sliced_table) -> list[dict]:
                cols = {name: sliced_table.column(name).to_pylist() for name in sliced_table.schema.names}
                ts_utc = cols.get("ts_utc") or []
                close_dec = cols.get("close_dec") or cols.get("close") or []
                high_dec = cols.get("high_dec") or cols.get("high") or []
                low_dec = cols.get("low_dec") or cols.get("low") or []
                if not ts_utc or not close_dec:
                    return []
                ref = close_dec[0]
                bps_moves = normalize_price_to_bps(close_dec, ref)
                out = []
                for idx, ts_ns in enumerate(ts_utc):
                    ts_dt = datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=ZoneInfo("America/New_York"))
                    out.append(
                        {
                            "ts": ts_dt.isoformat(),
                            "bps": round(bps_moves[idx], 4),
                            "price": close_dec[idx],
                            "high": high_dec[idx] if idx < len(high_dec) else close_dec[idx],
                            "low": low_dec[idx] if idx < len(low_dec) else close_dec[idx],
                        }
                    )
                return out

            prices = _build_prices(pre_slice)
            postevent_prices = _build_prices(post_slice)

            precomputed = meta.get("precomputed", {}) if meta else {}

            # Lookup consensus std dev from economic_events DB
            cons_std = (meta.get("economic") or {}).get("consensus_std_dev") if isinstance(meta, dict) else None
            if cons_std is None:
                try:
                    db_event_name = EVENT_CONFIG.get(req_event_type, {}).get("db_event_name")
                    if not db_event_name:
                        raise ValueError(f"Missing db_event_name for {req_event_type}")
                    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
                    cur = conn.cursor()
                    cur.execute(
                        """
                        SELECT consensus_std_dev
                        FROM economic_events
                        WHERE event_name = ?
                          AND event_date LIKE ?
                        LIMIT 1
                        """,
                        (db_event_name, f"{event_date}%"),
                    )
                    row = cur.fetchone()
                    if row:
                        cons_std = row[0]
                    if cons_std is None:
                        cur.execute(
                            """
                            SELECT consensus_std_dev
                            FROM bbg_data
                            WHERE db_event_name = ?
                              AND timestamp LIKE ?
                            LIMIT 1
                            """,
                            (db_event_name, f"{event_date}%"),
                        )
                        row = cur.fetchone()
                        if row:
                            cons_std = row[0]
                    if cons_std is None and isinstance(cached_event, dict):
                        cons_std = cached_event.get("cons_std")
                    conn.close()
                except Exception:
                    cons_std = cached_event.get("cons_std") if isinstance(cached_event, dict) else None

            if cons_std is None and isinstance(cached_event, dict):
                cons_std = cached_event.get("cons_std")
            if cons_std is None:
                try:
                    db_event_name = EVENT_CONFIG.get(req_event_type, {}).get("db_event_name")
                    if db_event_name:
                        conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
                        cur = conn.cursor()
                        cur.execute(
                            """
                            SELECT consensus_std_dev
                            FROM bbg_data
                            WHERE db_event_name = ?
                              AND timestamp LIKE ?
                            LIMIT 1
                            """,
                            (db_event_name, f"{event_date}%"),
                        )
                        row = cur.fetchone()
                        conn.close()
                        if row:
                            cons_std = row[0]
                except Exception:
                    pass

            response_cons_std = cons_std
            if response_cons_std is None and isinstance(meta, dict):
                response_cons_std = (meta.get("economic") or {}).get("consensus_std_dev")
            if response_cons_std is None and isinstance(cached_event, dict):
                response_cons_std = cached_event.get("cons_std")

            result = {
                "success": True,
                "event_type": req_event_type,
                "event": {
                    "date": event_date,
                    "event_time": meta.get("event_time"),
                    "cons_std": response_cons_std,
                    "be_by_time": precomputed.get("be_by_time") or {},
                    "bps_by_time": precomputed.get("bps_by_time") or {},
                    "prices": prices or [],
                    "postevent_prices": postevent_prices or [],
                },
            }
            l2_cache[cache_key] = result
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e), "success": False}), 500
    
    @app.route('/api/query')
    def custom_query():
        """
        API endpoint for user-specific queries with custom parameters.
        Allows each user to filter/customize their view.
        
        Query params:
            - event: Event type (NFP, CPI) - default: current event_type
            - bucket: Bucket width for grouping - default: 25
            - percentile: Surprise percentile filter (0-100) - default: None (all)
            - percentile_min: Min percentile - default: 0
            - percentile_max: Max percentile - default: 100
            - include_outliers: Whether to include outliers - default: true
            - date_from: Filter events from date (YYYY-MM-DD)
            - date_to: Filter events to date (YYYY-MM-DD)
        """
        req_event_type = request.args.get('event', event_type).upper()
        bucket_width = request.args.get('bucket', 25, type=int)
        percentile = request.args.get('percentile', None, type=int)
        percentile_min = request.args.get('percentile_min', 0, type=int)
        percentile_max = request.args.get('percentile_max', 100, type=int)
        include_outliers = request.args.get('include_outliers', 'true').lower() == 'true'
        date_from = request.args.get('date_from', None)
        date_to = request.args.get('date_to', None)
        
        # Build cache key from all params
        cache_key = f"query:{req_event_type}:{bucket_width}:{percentile}:{percentile_min}:{percentile_max}:{include_outliers}:{date_from}:{date_to}"
        
        # Check cache (shorter TTL for custom queries)
        if cache_key in events_cache:
            return jsonify(events_cache[cache_key])
        
        try:
            # Get base events data
            base_cache_key = f"events:{req_event_type}"
            if base_cache_key in events_cache:
                base_data = events_cache[base_cache_key]
                events = base_data['events']
            else:
                cached = load_cached_dashboard_data(req_event_type)
                if cached:
                    events = cached.get('events', [])
                else:
                    raw_data = load_event_data(req_event_type)
                    if not raw_data:
                        return jsonify({"error": f"No data for {req_event_type}", "success": False}), 404
                    
                    dashboard_data = build_dashboard_data(raw_data, event_type=req_event_type)
                    events = dashboard_data['events']
            
            # Apply filters
            filtered_events = events
            
            # Date range filter
            if date_from:
                filtered_events = [e for e in filtered_events if e['date'] >= date_from]
            if date_to:
                filtered_events = [e for e in filtered_events if e['date'] <= date_to]
            
            # Percentile filter
            if percentile is not None:
                # Filter to events near the target percentile
                filtered_events = [
                    e for e in filtered_events 
                    if e.get('surp_pct') is not None and
                    abs((e['surp_pct'] * 100) - percentile) <= bucket_width / 2
                ]
            elif percentile_min > 0 or percentile_max < 100:
                # Range filter
                filtered_events = [
                    e for e in filtered_events
                    if e.get('surp_pct') is not None and
                    percentile_min <= (e['surp_pct'] * 100) <= percentile_max
                ]
            
            # Outlier filter (remove events with extreme BE values)
            if not include_outliers:
                be_values = [e.get('post_be') for e in filtered_events if e.get('post_be') is not None]
                if be_values:
                    lower_bound = percentile(be_values, 5)
                    upper_bound = percentile(be_values, 95)
                    filtered_events = [
                        e for e in filtered_events
                        if e.get('post_be') is None or lower_bound <= e['post_be'] <= upper_bound
                    ]
            
            # Calculate bucket statistics
            buckets = []
            for i in range(0, 100, bucket_width):
                bucket_min = i
                bucket_max = min(i + bucket_width, 100)
                bucket_events = [
                    e for e in filtered_events
                    if e.get('surp_pct') is not None and
                    bucket_min <= (e['surp_pct'] * 100) < bucket_max
                ]
                
                be_values = [e.get('post_be') for e in bucket_events if e.get('post_be') is not None]
                
                if be_values:
                    import statistics
                    buckets.append({
                        'label': f'P{bucket_min}-{bucket_max}',
                        'min': bucket_min,
                        'max': bucket_max,
                        'count': len(be_values),
                        'mean_be': round(statistics.mean(be_values), 3),
                        'median_be': round(statistics.median(be_values), 3),
                        'min_be': round(min(be_values), 3),
                        'max_be': round(max(be_values), 3),
                    })
            
            result = {
                "success": True,
                "event_type": req_event_type,
                "total_events": len(events),
                "filtered_events": len(filtered_events),
                "filters": {
                    "bucket_width": bucket_width,
                    "percentile": percentile,
                    "percentile_range": [percentile_min, percentile_max],
                    "include_outliers": include_outliers,
                    "date_range": [date_from, date_to],
                },
                "buckets": buckets,
                "events": filtered_events,
            }
            
            # Cache the result
            events_cache[cache_key] = result
            return jsonify(result)
            
        except Exception as e:
            import traceback
            return jsonify({"error": str(e), "traceback": traceback.format_exc(), "success": False}), 500
    
    @app.route('/api/adhoc_session/<date_str>')
    def get_adhoc_session(date_str: str):
        """API endpoint to fetch a single trading session (6pm prior day → 5pm selected day)."""
        # Check cache first (adhoc sessions don't change)
        cache_key = f"adhoc:{date_str}"
        if cache_key in events_cache:
            return jsonify(events_cache[cache_key])
        
        try:
            prices = fetch_trading_session_prices(date_str)
            if not prices or len(prices) < 5:
                return jsonify({"error": f"No data for {date_str}", "success": False}), 404
            
            price_ref = prices[0]['close']
            if not price_ref or price_ref <= 0:
                return jsonify({"error": "Invalid reference price", "success": False}), 400
            
            session_end_dt = datetime.strptime(date_str, "%Y-%m-%d")
            session_start_dt = session_end_dt - timedelta(days=1)
            session_start_date_str = session_start_dt.strftime("%Y-%m-%d")
            session_start = datetime.strptime(f"{session_start_date_str} 18:00:00", "%Y-%m-%d %H:%M:%S")
            
            normalized = []
            close_prices = [p['close'] for p in prices]
            bps_moves = normalize_price_to_bps(close_prices, price_ref)
            for idx, p in enumerate(prices):
                move_bps = bps_moves[idx]
                ts_str = p['timestamp']
                if 'T' in ts_str:
                    ts_dt = datetime.fromisoformat(ts_str.replace('Z', '').replace('+00:00', ''))
                else:
                    ts_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                
                if ts_dt.tzinfo is not None:
                    ts_dt = ts_dt.replace(tzinfo=None)
                
                hours_from_open = (ts_dt - session_start).total_seconds() / 3600
                
                normalized.append({
                    'hours': round(hours_from_open, 3),
                    'bps': round(move_bps, 4),
                    'close': p['close'],
                    'ts': ts_str,
                })
            
            candle_data_1 = fetch_candlestick_data(session_start_date_str, days_around=0) or []
            candle_data_2 = fetch_candlestick_data(date_str, days_around=0) or []
            
            session_candles = []
            session_start_str = f"{session_start_date_str}T18:00:00"
            session_end_str = f"{date_str}T17:00:00"
            
            for c in candle_data_1 + candle_data_2:
                if c.get('ts') and session_start_str <= c['ts'] <= session_end_str:
                    session_candles.append(c)
            
            session_candles.sort(key=lambda x: x['ts'])
            
            events_day1 = get_events_for_date(session_start_date_str)
            events_day2 = get_events_for_date(date_str)
            session_events = events_day1 + events_day2
            
            result = {
                'success': True,
                'date': date_str,
                'session_label': f"{session_start_date_str} 6pm -> {date_str} 5pm",
                'prices': normalized,
                'candlestick_5m': session_candles,
                'ref_price': price_ref,
                'events': session_events,
            }
            
            # Cache the result
            events_cache[cache_key] = result
            return jsonify(result)
            
        except ValueError as e:
            return jsonify({"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD", "success": False}), 400
        except Exception as e:
            return jsonify({"error": str(e), "success": False}), 500
    
    @app.route('/api/adhoc_sessions_batch')
    def get_adhoc_sessions_batch():
        """
        Batch API endpoint to fetch multiple trading sessions in one request.
        Much faster than calling /api/adhoc_session/<date> N times.
        
        Query params:
            - dates: Comma-separated date strings (YYYY-MM-DD)
            - skip_candles: If "1", skip candlestick data for faster response
        
        Returns:
            {
                "success": true,
                "sessions": [...],  // Array of session data
                "errors": {...}     // Map of date -> error message
            }
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from zoneinfo import ZoneInfo
        from datetime import timezone
        
        dates_param = request.args.get('dates', '')
        skip_candles = request.args.get('skip_candles', '0') == '1'
        
        if not dates_param:
            return jsonify({"error": "No dates provided. Use ?dates=2023-01-01,2023-02-01", "success": False}), 400
        
        date_list = [d.strip() for d in dates_param.split(',') if d.strip()]
        if not date_list:
            return jsonify({"error": "No valid dates provided", "success": False}), 400
        
        if len(date_list) > 50:
            return jsonify({"error": "Maximum 50 dates per request", "success": False}), 400
        
        batch_cache_key = f"adhoc_batch:{','.join(date_list)}:{int(skip_candles)}"
        if batch_cache_key in l2_cache:
            return jsonify(l2_cache[batch_cache_key])

        def process_single_date(date_str: str) -> dict:
            """Process a single date - returns session data or error dict."""
            # Check cache first
            cache_key = f"adhoc:{date_str}:{int(skip_candles)}"
            if cache_key in l2_cache:
                return l2_cache[cache_key]
            
            try:
                prices = fetch_trading_session_prices(date_str)
                if not prices or len(prices) < 5:
                    return {"date": date_str, "error": f"No data for {date_str}", "success": False}
                
                price_ref = prices[0]['close']
                if not price_ref or price_ref <= 0:
                    return {"date": date_str, "error": "Invalid reference price", "success": False}
                
                session_end_dt = datetime.strptime(date_str, "%Y-%m-%d")
                session_start_dt = session_end_dt - timedelta(days=1)
                session_start_date_str = session_start_dt.strftime("%Y-%m-%d")
                session_start = datetime.strptime(f"{session_start_date_str} 18:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/New_York"))
                session_end = datetime.strptime(f"{date_str} 17:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/New_York"))
                
                ts_utc = []
                price_dec = []
                price_bps_from_ref = []
                close_prices = [p['close'] for p in prices]
                bps_moves = normalize_price_to_bps(close_prices, price_ref)
                for idx, p in enumerate(prices):
                    move_bps = bps_moves[idx]
                    ts_str = p['timestamp']
                    if 'T' in ts_str:
                        ts_dt = datetime.fromisoformat(ts_str.replace('Z', '').replace('+00:00', ''))
                    else:
                        ts_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    
                    if ts_dt.tzinfo is not None:
                        ts_dt = ts_dt.replace(tzinfo=None)
                    ts_dt = ts_dt.replace(tzinfo=ZoneInfo("America/New_York"))
                    ts_utc.append(int(ts_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000))
                    price_dec.append(float(p['close']))
                    price_bps_from_ref.append(round(move_bps, 4))
                
                # Optionally skip candlestick data for faster response
                session_candles = []
                if not skip_candles:
                    candle_data_1 = fetch_candlestick_data(session_start_date_str, days_around=0) or []
                    candle_data_2 = fetch_candlestick_data(date_str, days_around=0) or []
                    
                    session_start_str = f"{session_start_date_str}T18:00:00"
                    session_end_str = f"{date_str}T17:00:00"
                    
                    for c in candle_data_1 + candle_data_2:
                        if c.get('ts') and session_start_str <= c['ts'] <= session_end_str:
                            ts_dt = datetime.fromisoformat(c['ts'].replace('Z', '').replace('+00:00', ''))
                            if ts_dt.tzinfo is None:
                                ts_dt = ts_dt.replace(tzinfo=ZoneInfo("America/New_York"))
                            session_candles.append({
                                "ts_utc": int(ts_dt.astimezone(timezone.utc).timestamp() * 1_000_000_000),
                                "open_dec": float(c["open"]),
                                "high_dec": float(c["high"]),
                                "low_dec": float(c["low"]),
                                "close_dec": float(c["close"]),
                                "volume": float(c.get("volume") or 0),
                            })
                    
                    session_candles.sort(key=lambda x: x['ts_utc'])
                
                events_day1 = get_events_for_date(session_start_date_str)
                events_day2 = get_events_for_date(date_str)
                session_events = events_day1 + events_day2
                
                stats = {
                    "open_dec": float(price_dec[0]) if price_dec else None,
                    "high_dec": float(max(price_dec)) if price_dec else None,
                    "low_dec": float(min(price_dec)) if price_dec else None,
                    "close_dec": float(price_dec[-1]) if price_dec else None,
                    "move_bps": float(price_bps_from_ref[-1]) if price_bps_from_ref else None,
                }
                result = {
                    'success': True,
                    'date': date_str,
                    'session_start_utc': int(session_start.astimezone(timezone.utc).timestamp() * 1_000_000_000),
                    'session_end_utc': int(session_end.astimezone(timezone.utc).timestamp() * 1_000_000_000),
                    'paths': {
                        'ts_utc': ts_utc,
                        'price_dec': price_dec,
                        'price_bps_from_ref': price_bps_from_ref,
                    },
                    'candles': session_candles if not skip_candles else [],
                    'stats': stats,
                    'events': session_events,
                }
                
                # Cache the result
                l2_cache[cache_key] = result
                return result
                
            except ValueError:
                return {"date": date_str, "error": f"Invalid date format: {date_str}", "success": False}
            except Exception as e:
                return {"date": date_str, "error": str(e), "success": False}
        
        # Process dates in parallel using ThreadPoolExecutor
        sessions = []
        errors = {}
        
        # Use up to 8 threads for parallel processing
        max_workers = min(8, len(date_list))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_date = {executor.submit(process_single_date, d): d for d in date_list}
            
            for future in as_completed(future_to_date):
                date_str = future_to_date[future]
                try:
                    result = future.result()
                    if result.get('success'):
                        sessions.append(result)
                    else:
                        errors[date_str] = result.get('error', 'Unknown error')
                except Exception as e:
                    errors[date_str] = str(e)
        
        # Sort sessions by date
        sessions.sort(key=lambda x: x['date'])
        
        payload = {
            "success": True,
            "sessions": sessions,
            "errors": errors,
            "total_requested": len(date_list),
            "total_success": len(sessions),
        }
        l2_cache[batch_cache_key] = payload
        return jsonify(payload)
    
    @app.route('/api/candlestick')
    def get_candlestick_query():
        """Query-style candlestick endpoint (Appendix C shape)."""
        date_str = request.args.get('date')
        if not date_str:
            return jsonify({"success": False, "error": "Missing required 'date' parameter"}), 400
        return get_candlestick_data(date_str)

    @app.route('/api/candlestick/<date_str>')
    def get_candlestick_data(date_str: str):
        """
        API endpoint to fetch candlestick data for a specific event date.
        Used for lazy loading - only fetched when user clicks on an event.
        
        Query params:
            - days: Number of days around event (default: 5)
            - type/event_type: Event type (NFP/CPI) (default: server event_type)
            - res: Candle resolution ("1m" or "5m") (default: "5m")
        """
        days_around = request.args.get('days', 5, type=int)
        req_event_type = (request.args.get('event_type') or request.args.get('type') or event_type).upper()
        res = (request.args.get("res") or "5m").lower()

        # Enforce "no dynamic OHLC" rule: only allow precomputed resolutions.
        if res not in {"1m", "5m"}:
            return jsonify(
                {
                    "success": False,
                    "error": f"Unsupported resolution '{res}'. Allowed: 1m, 5m",
                }
            ), 400
        
        build_version = None
        try:
            meta = _get_meta_cached(req_event_type, date_str)
            build_version = meta.get("build_version")
        except Exception:
            meta = None
        cache_key = f"candlestick:{req_event_type}:{date_str}:{build_version}:{res}:{days_around}"
        if cache_key in l2_cache:
            return jsonify(l2_cache[cache_key])
        
        try:
            # Load precomputed candles from L1 store
            from scripts.l1_event_store import load_event_meta, load_price_table, slice_by_days_around, table_to_candles_payload

            meta = meta or load_event_meta(req_event_type, date_str)
            event_ts_et = meta.get("event_ts_et")
            if not event_ts_et:
                return jsonify({"success": False, "error": "Missing event_ts_et in meta", "date": date_str}), 500

            # meta stores ISO; convert to ns epoch in ET for slicing
            # (use Python parse to keep consistent with builder)
            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                dt = datetime.fromisoformat(str(event_ts_et))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
                event_ts_et_ns = int(dt.timestamp() * 1_000_000_000)
            except Exception as e:
                return jsonify({"success": False, "error": f"Bad event_ts_et: {e}", "date": date_str}), 500

            table = load_price_table(req_event_type, date_str, res)  # "1m" | "5m"
            sliced = slice_by_days_around(table, event_ts_et_ns=event_ts_et_ns, days_around=days_around)
            candles = table_to_candles_payload(sliced)

            # Events/annotations come from meta (preloaded at build time)
            result = {
                "success": True,
                "event_type": req_event_type,
                "date": date_str,
                "res": res,
                "days": days_around,
                "candles": candles,
                "count": len(candles),
                "events": meta.get("annotations_same_day", []),
                "validation": {
                    "is_sorted": True,
                    "build_version": meta.get("build_version"),
                },
            }
            
            # Cache the result
            l2_cache[cache_key] = result
            return jsonify(result)
            
        except ValueError as e:
            return jsonify({"error": f"Invalid date format: {date_str}", "success": False}), 400
        except FileNotFoundError as e:
            # L1 not built yet for this event date
            return jsonify(
                {
                    "success": False,
                    "error": f"L1 file missing for {req_event_type} {date_str}. Run: py scripts/build_l1_event_features.py --event {req_event_type}",
                    "details": str(e),
                }
            ), 503
        except ImportError as e:
            return jsonify({"success": False, "error": str(e)}), 503
        except Exception as e:
            return jsonify({"error": str(e), "success": False}), 500
    
    @app.route('/api/config')
    def get_config():
        """API endpoint to fetch dashboard configuration (from unified config mapping)."""
        from config.unified_dashboard_event_config import load_unified_dashboard_event_config

        cfg = load_unified_dashboard_event_config()
        return jsonify({
            "success": True,
            "version": cfg.get("version"),
            "default_event_type": cfg.get("default_event_type"),
            "event_time_by_type": cfg.get("event_time_by_type", {}),
            "default_windows_by_type": cfg.get("default_windows_by_type", {}),
            "default_start_year": cfg.get("default_start_year"),
            "supported_resolutions": cfg.get("supported_resolutions", []),
            "outlier_rule_default": cfg.get("outlier_rule_default"),
            "live_poll_interval_ms": cfg.get("live_poll_interval_ms", 3000),
            "live_stale_threshold_ms": cfg.get("live_stale_threshold_ms", 10000),
        })
    
    @app.route('/api/event_types')
    def get_event_types():
        """API endpoint to get available event types and component metadata."""
        from config.unified_dashboard_event_config import load_unified_dashboard_event_config

        cfg = load_unified_dashboard_event_config()
        return jsonify({
            "success": True,
            "event_types": cfg.get("event_types", []),
            "components_by_type": cfg.get("components_by_type", {}),
            "default_components_by_type": cfg.get("default_components_by_type", {}),
            "component_input_constraints_by_type": cfg.get("component_input_constraints_by_type", {}),
            "bucket_schemes_by_type": cfg.get("bucket_schemes_by_type", {}),
        })

    @app.route('/api/impulse/ecos', methods=['POST'])
    def parse_impulse_ecos():
        """Parse a Bloomberg ECOS estimates workbook for the current event setup."""
        try:
            use_default = request.form.get("use_default", "").lower() in {"1", "true", "yes"}
            if use_default:
                result = parse_ecos_estimates(Path(r"Z:\Aastha\ECOS_Estimates1.xlsx"))
            else:
                if 'file' not in request.files:
                    return jsonify({"success": False, "error": "No file uploaded"}), 400
                file = request.files['file']
                if not file.filename:
                    return jsonify({"success": False, "error": "No file selected"}), 400
                result = parse_ecos_estimates(file.read())
            result["success"] = True
            return jsonify(result)
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route('/api/impulse/history')
    def get_impulse_history():
        """Historical pre-event drift relationship for prior-vs-consensus setup."""
        req_event_type = (request.args.get("event_type") or request.args.get("type") or event_type).upper()
        start_day = request.args.get("start_day", default=-2, type=int)
        start_hhmm = request.args.get("start_hhmm", default="15:00", type=str)
        if start_day is None:
            start_day = -2
        start_day = max(-4, min(-1, int(start_day)))
        try:
            payload = build_impulse_history(req_event_type, start_day=start_day, start_hhmm=start_hhmm)
            return jsonify({"success": True, **payload})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route('/api/impulse/scenarios', methods=['POST'])
    def get_impulse_scenarios():
        """Map hypothetical actuals into historical surprise-percentile bucket stats."""
        data = request.get_json(silent=True) or {}
        req_event_type = (data.get("event_type") or event_type or "NFP").upper()
        consensus = _safe_float(data.get("consensus"))
        std_dev = _safe_float(data.get("std"))
        bucket_width = _safe_float(data.get("bucket_width")) or 20.0
        start_day = data.get("start_day", -2)
        start_hhmm = data.get("start_hhmm", "15:00")
        actuals = data.get("actuals") or []
        if consensus is None or std_dev is None or std_dev <= 0:
            return jsonify({"success": False, "error": "consensus and positive std are required"}), 400
        if not isinstance(actuals, list) or not actuals:
            return jsonify({"success": False, "error": "actuals list is required"}), 400
        try:
            stats = build_surprise_bucket_stats(
                req_event_type,
                pre_start_day=int(start_day),
                pre_start_hhmm=str(start_hhmm or "15:00"),
                bucket_width=float(bucket_width),
            )
            buckets_by_idx = {b["bucket_idx"]: b for b in stats.get("buckets", [])}
            std_surprises = stats.get("std_surprises", [])
            rows = []
            for item in actuals:
                if not isinstance(item, dict):
                    continue
                actual = _safe_float(item.get("actual"))
                label = str(item.get("label") or "").strip() or f"Actual {actual:g}" if actual is not None else "Actual"
                if actual is None:
                    continue
                z = (actual - consensus) / std_dev
                pct_val = _impulse_percentile_rank(std_surprises, z)
                idx = _bucket_index(pct_val, stats.get("bucket_width", bucket_width))
                bucket = buckets_by_idx.get(idx) if idx is not None else None
                rows.append(
                    {
                        "label": label,
                        "actual": actual,
                        "z": z,
                        "surprise_pct": pct_val,
                        "bucket_idx": idx,
                        "bucket_min": bucket.get("bucket_min") if bucket else None,
                        "bucket_max": bucket.get("bucket_max") if bucket else None,
                        "bucket_n": bucket.get("count") if bucket else 0,
                        "pre": bucket.get("pre") if bucket else None,
                        "post_2h": bucket.get("post_2h") if bucket else None,
                        "post_eod": bucket.get("post_eod") if bucket else None,
                    }
                )
            return jsonify(
                {
                    "success": True,
                    "event_type": req_event_type,
                    "consensus": consensus,
                    "std": std_dev,
                    "bucket_width": stats.get("bucket_width", bucket_width),
                    "pre_start_day": stats.get("pre_start_day"),
                    "pre_start_hhmm": stats.get("pre_start_hhmm"),
                    "rows": rows,
                    "bucket_stats": stats.get("buckets", []),
                }
            )
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500
    
    # ========================================================================
    # COMPONENTS & COMMENTS API ROUTES
    # ========================================================================
    
    @app.route('/api/components')
    def get_components():
        """API endpoint to get available components for an event type."""
        event_type = request.args.get('event', '').upper()
        if not event_type:
            return jsonify({"success": False, "error": "Event type required"}), 400
        from config.unified_dashboard_event_config import load_unified_dashboard_event_config
        cfg = load_unified_dashboard_event_config()
        components = cfg.get("components_by_type", {}).get(event_type, [])
        return jsonify({
            "success": True,
            "event_type": event_type,
            "components": components,
        })
    
    @app.route('/api/comments')
    def get_comments():
        """API endpoint to get comments for an event type."""
        event_type = request.args.get('event', '').upper()
        if not event_type:
            return jsonify({"success": False, "error": "Event type required"}), 400
        try:
            from scripts.event_data_loader import get_event_comments
            comments = get_event_comments(event_type)
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc), "event_type": event_type, "comments": []}), 500
        return jsonify({
            "success": True,
            "event_type": event_type,
            "comments": comments,
        })
    
    @app.route('/api/comments', methods=['POST'])
    def post_comment():
        """API endpoint to add/update a comment for an event date."""
        from scripts.event_data_loader import upsert_event_comment
        
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "JSON body required"}), 400
        
        event_type = data.get('event_type', '').upper()
        event_date = data.get('event_date', '')
        comment = data.get('comment', '')
        
        if not event_type or not event_date:
            return jsonify({"success": False, "error": "event_type and event_date required"}), 400
        
        success = upsert_event_comment(event_type, event_date, comment)
        return jsonify({
            "success": success,
            "message": "Comment saved" if success else "Failed to save comment",
        })
    
    @app.route('/api/comments', methods=['DELETE'])
    def delete_comment():
        """API endpoint to delete a comment for an event date."""
        from scripts.event_data_loader import delete_event_comment
        
        event_type = request.args.get('event_type', '').upper()
        event_date = request.args.get('event_date', '')
        
        if not event_type or not event_date:
            return jsonify({"success": False, "error": "event_type and event_date required"}), 400
        
        success = delete_event_comment(event_type, event_date)
        return jsonify({
            "success": success,
            "message": "Comment deleted" if success else "Failed to delete comment",
        })

    @app.route('/api/telegram/chats')
    def telegram_chats():
        """List allowed Telegram chats with human-readable names."""
        bot_token, allowed_chat_ids = _telegram_config()
        if not bot_token or not allowed_chat_ids:
            return jsonify({"success": False, "error": "Telegram bot not configured", "chats": []}), 503
        chats = []
        for chat_id in allowed_chat_ids:
            cache_key = f"chat:{chat_id}"
            if cache_key in telegram_chat_cache:
                chats.append(telegram_chat_cache[cache_key])
                continue
            name = str(chat_id)
            chat_type = "unknown"
            try:
                info = _telegram_api_post_json(bot_token, "getChat", {"chat_id": int(chat_id)})
                result = info.get("result", {}) if isinstance(info, dict) else {}
                chat_type = str(result.get("type") or "unknown")
                name = (
                    result.get("title")
                    or ("@" + result["username"] if result.get("username") else None)
                    or " ".join(x for x in [result.get("first_name"), result.get("last_name")] if x)
                    or str(chat_id)
                )
            except Exception:
                name = str(chat_id)
            item = {"chat_id": int(chat_id), "name": str(name), "type": chat_type}
            telegram_chat_cache[cache_key] = item
            chats.append(item)
        chats_sorted = sorted(chats, key=lambda x: x.get("name", "").lower())
        return jsonify({"success": True, "chats": chats_sorted})

    @app.route('/api/telegram/send_snapshot', methods=['POST'])
    def telegram_send_snapshot():
        """Send a captured dashboard snapshot directly to a Telegram chat via bot."""
        bot_token, allowed_chat_ids = _telegram_config()
        if not bot_token or not allowed_chat_ids:
            return jsonify({"success": False, "error": "Telegram bot not configured"}), 503

        chat_id_raw = request.form.get("chat_id")
        if not chat_id_raw:
            return jsonify({"success": False, "error": "chat_id is required"}), 400
        try:
            chat_id = int(chat_id_raw)
        except Exception:
            return jsonify({"success": False, "error": "chat_id must be integer"}), 400
        if chat_id not in set(int(x) for x in allowed_chat_ids):
            return jsonify({"success": False, "error": "chat_id not allowed"}), 403

        image = request.files.get("image")
        if image is None:
            return jsonify({"success": False, "error": "image file is required"}), 400
        image_bytes = image.read()
        if not image_bytes:
            return jsonify({"success": False, "error": "image payload is empty"}), 400
        if len(image_bytes) > 15 * 1024 * 1024:
            return jsonify({"success": False, "error": "image exceeds 15MB limit"}), 400

        caption = (request.form.get("caption") or "").strip()
        try:
            sent = _telegram_send_photo(bot_token, chat_id, image_bytes, caption=caption)
            msg = sent.get("result", {}) if isinstance(sent, dict) else {}
            return jsonify(
                {
                    "success": True,
                    "chat_id": chat_id,
                    "message_id": msg.get("message_id"),
                }
            )
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 500
    
    @app.route('/api/ref_price')
    def get_ref_price():
        """API endpoint to get reference price at a specific time."""
        time_str = request.args.get('time', '')  # Format: HH:MM
        date_str = request.args.get('date', '')  # Optional: YYYY-MM-DD
        
        if not time_str:
            return jsonify({"success": False, "error": "time parameter required (HH:MM)"}), 400
        
        try:
            # Parse time
            hour, minute = map(int, time_str.split(':'))
            
            # Use provided date or today
            if date_str:
                target_date = datetime.strptime(date_str, '%Y-%m-%d')
            else:
                target_date = datetime.now()
            
            target_dt = target_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # Query price at this time from live_server
            # For now, return placeholder - actual implementation depends on data availability
            return jsonify({
                "success": True,
                "time": time_str,
                "date": target_date.strftime('%Y-%m-%d'),
                "ref_price": None,  # Would be fetched from DB
                "message": "Reference price lookup not yet implemented",
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 400
    
    # ========================================================================
    # POLYMARKET + SCENARIO API ROUTES
    # ========================================================================

    @app.route('/api/polymarket/resolve')
    def polymarket_resolve():
        kind = (request.args.get('kind') or '').strip().lower()
        if kind not in SCENARIO_COMPONENTS:
            return jsonify({"success": False, "error": "kind must be nfp|uer|cpi"}), 400
        markets = resolve_polymarket_markets_from_db(kind)
        if not markets:
            return jsonify({"success": False, "error": f"No tracked market resolved for kind={kind}"}), 404
        outcomes = get_polymarket_outcomes_with_latest_mid(kind)
        primary = markets[0]
        return jsonify({
            "success": True,
            "kind": kind,
            "condition_id": primary.get("condition_id"),
            "market_slug": primary.get("market_slug"),
            "market_title": primary.get("title"),
            "market_count": len(markets),
            "market_titles": [m.get("title") for m in markets[:20]],
            "outcomes": outcomes,
        })

    @app.route('/api/polymarket/latest')
    def polymarket_latest():
        token_ids_raw = (request.args.get("token_ids") or "").strip()
        token_ids = [t.strip() for t in token_ids_raw.split(",") if t.strip()]
        if not token_ids:
            return jsonify({"success": False, "error": "token_ids is required"}), 400
        return jsonify({"success": True, "rows": get_latest_mid_by_token_ids(token_ids)})

    @app.route('/api/scenarios/table')
    def scenario_table():
        component = (request.args.get("component") or "").strip().lower()
        if component not in SCENARIO_COMPONENTS:
            return jsonify({"success": False, "error": "component must be nfp|uer|cpi"}), 400
        consensus = request.args.get("consensus", type=float)
        cons_std = request.args.get("cons_std", type=float)
        if consensus is None or cons_std is None or cons_std <= 0:
            return jsonify({"success": False, "error": "consensus and positive cons_std are required"}), 400
        rows, expected = build_scenario_table(component, consensus, cons_std)
        return jsonify({
            "success": True,
            "component": component,
            "rows": rows,
            "expected": expected,
        })

    @app.route('/api/scenarios/cpi_yoy_odds')
    def scenario_cpi_yoy_odds():
        outcomes = get_polymarket_outcomes_with_latest_mid("cpi")
        rows = []
        for out in outcomes:
            label = out.get("label") or out.get("token_id")
            if not _is_cpi_annual_label(label):
                continue
            rows.append(
                {
                    "token_id": out.get("token_id"),
                    "actual_range_label": label,
                    "polymarket_odds": _safe_float(out.get("mid")),
                    "observed_ts": out.get("observed_ts"),
                }
            )
        return jsonify({"success": True, "rows": rows, "row_count": len(rows)})

    @app.route('/api/scenarios/hist')
    def scenario_hist():
        component = (request.args.get("component") or "").strip().lower()
        if component not in SCENARIO_COMPONENTS:
            return jsonify({"success": False, "error": "component must be nfp|uer|cpi"}), 400
        bucket_min = request.args.get("bucket_min", type=float)
        bucket_max = request.args.get("bucket_max", type=float)
        start_h = request.args.get("start_h", default=-12.0, type=float)
        end_h = request.args.get("end_h", default=12.0, type=float)
        if bucket_min is None or bucket_max is None:
            return jsonify({"success": False, "error": "bucket_min and bucket_max are required"}), 400
        rows = get_bucket_window_distribution(component, bucket_min, bucket_max, start_h, end_h)
        values = [float(r["value"]) for r in rows if r.get("value") is not None]
        stats = compute_distro_stats(values)
        stats_lines = build_distro_stats_lines(stats)
        last_two = build_last_two_markers(rows)
        if last_two:
            stats_lines = stats_lines + [" ", "Last 2 events:"] + [m["label"] for m in last_two]
        return jsonify({
            "success": True,
            "component": component,
            "bucket_min": bucket_min,
            "bucket_max": bucket_max,
            "start_h": start_h,
            "end_h": end_h,
            "values": values,
            "rows": rows,
            "stats": stats,
            "stats_lines": stats_lines,
            "last_two": last_two,
        })

    @app.route('/api/scenarios/bin_paths')
    def scenario_bin_paths():
        component = (request.args.get("component") or "").strip().lower()
        if component not in SCENARIO_COMPONENTS:
            return jsonify({"success": False, "error": "component must be nfp|uer|cpi"}), 400
        bucket_min = request.args.get("bucket_min", type=float)
        bucket_max = request.args.get("bucket_max", type=float)
        start_h = request.args.get("start_h", default=-12.0, type=float)
        end_h = request.args.get("end_h", default=12.0, type=float)
        bin_start = request.args.get("bin_start", type=float)
        bin_end = request.args.get("bin_end", type=float)
        if bucket_min is None or bucket_max is None or bin_start is None or bin_end is None:
            return jsonify({"success": False, "error": "bucket_min, bucket_max, bin_start, bin_end are required"}), 400

        rows = get_bucket_window_distribution(component, bucket_min, bucket_max, start_h, end_h)
        selected_rows = []
        for r in rows:
            val = _safe_float(r.get("value"))
            if val is None:
                continue
            if bin_start <= val < bin_end:
                selected_rows.append(r)
        selected_rows.sort(key=lambda r: str(r.get("timestamp")))

        paths = []
        for r in selected_rows:
            path = _window_path_for_event(str(r.get("timestamp")), start_h, end_h)
            if path is None:
                continue
            path["value"] = _safe_float(r.get("value"))
            paths.append(path)

        return jsonify(
            {
                "success": True,
                "component": component,
                "bucket_min": bucket_min,
                "bucket_max": bucket_max,
                "start_h": start_h,
                "end_h": end_h,
                "bin_start": bin_start,
                "bin_end": bin_end,
                "path_count": len(paths),
                "paths": paths,
            }
        )

    # ========================================================================
    # WEEK MATCH API ROUTES
    # ========================================================================
    
    @app.route('/api/weekmatch/parse', methods=['POST'])
    def parse_weekmatch_calendar():
        """Parse uploaded Excel calendar and extract events with tier info."""
        if 'file' not in request.files:
            return jsonify({"success": False, "error": "No file uploaded"}), 400
        
        file = request.files['file']
        if not file.filename:
            return jsonify({"success": False, "error": "No file selected"}), 400
        
        # Get date range filter from form data
        date_from = request.form.get('date_from', '')
        date_to = request.form.get('date_to', '')
        
        # Parse filter dates
        filter_from = None
        filter_to = None
        if date_from:
            try:
                filter_from = datetime.strptime(date_from, '%Y-%m-%d')
            except:
                pass
        if date_to:
            try:
                filter_to = datetime.strptime(date_to, '%Y-%m-%d')
            except:
                pass
        
        try:
            import openpyxl
            from io import BytesIO
            import calendar
            
            # Load workbook from uploaded bytes
            wb = openpyxl.load_workbook(BytesIO(file.read()))
            
            # Find Calendar sheet
            sheet_name = 'Calendar' if 'Calendar' in wb.sheetnames else wb.sheetnames[0]
            ws = wb[sheet_name]
            
            events = []
            current_day = None
            current_date = None  # Full date parsed from header
            
            # Month name to number mapping
            month_map = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
            month_map.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
            
            for row in range(1, ws.max_row + 1):
                cell_b = ws.cell(row, 2)  # Column B: dates/times
                cell_c = ws.cell(row, 3)  # Column C: country
                cell_d = ws.cell(row, 4)  # Column D: event name
                
                b_val = cell_b.value
                c_val = cell_c.value
                d_val = cell_d.value
                
                if not b_val:
                    continue
                
                b_str = str(b_val).strip()
                
                # Check if this is a date header (e.g., "Monday August 11 2025")
                day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                for day_name in day_names:
                    if b_str.startswith(day_name):
                        current_day = day_name
                        # Parse full date from header
                        try:
                            # Format: "Monday August 11 2025" or "Monday August 11, 2025"
                            parts = b_str.replace(',', '').split()
                            if len(parts) >= 4:
                                month_name = parts[1].lower()
                                day_num = int(parts[2])
                                year = int(parts[3])
                                month_num = month_map.get(month_name, 1)
                                current_date = datetime(year, month_num, day_num)
                        except:
                            current_date = None
                        break
                else:
                    # This is a time row with an event
                    if current_day and d_val:
                        # Parse time - handle various formats
                        time_str = None
                        try:
                            if hasattr(b_val, 'strftime'):
                                # datetime.time object
                                time_str = b_val.strftime('%H:%M')
                            elif hasattr(b_val, 'hour'):
                                # datetime object
                                time_str = f"{b_val.hour:02d}:{b_val.minute:02d}"
                            elif isinstance(b_val, (int, float)):
                                # Excel time as fraction of day (e.g., 0.5 = 12:00)
                                total_minutes = int(b_val * 24 * 60)
                                hours = total_minutes // 60
                                minutes = total_minutes % 60
                                time_str = f"{hours:02d}:{minutes:02d}"
                            elif ':' in b_str:
                                # String like "08:30:00" or "08:30"
                                parts = b_str.split(':')
                                time_str = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
                            else:
                                # Skip rows that don't have valid times
                                continue
                        except (ValueError, TypeError, AttributeError):
                            # Skip rows with unparseable times
                            continue
                        
                        if not time_str:
                            continue
                        
                        # Get country
                        country = str(c_val).strip() if c_val else 'US'
                        
                        # Get event name (clean up special chars)
                        event_name = str(d_val).strip().replace('\t', '').replace('\u0192', '')
                        
                        # Determine tier from cell color
                        tier = _get_event_tier(cell_d, event_name)
                        
                        # Apply date range filter
                        if current_date:
                            if filter_from and current_date < filter_from:
                                continue
                            if filter_to and current_date > filter_to:
                                continue
                        
                        events.append({
                            'date': current_date.strftime('%Y-%m-%d') if current_date else None,
                            'time_et': time_str,
                            'name': event_name,
                            'tier': tier,
                            'country': country,
                        })
            
            week_start = None
            week_end = None
            dates = [e["date"] for e in events if e.get("date")]
            if dates:
                min_date = min(dates)
                dt_min = datetime.strptime(min_date, "%Y-%m-%d")
                week_start = (dt_min - timedelta(days=dt_min.weekday())).strftime("%Y-%m-%d")
                week_end = (datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=4)).strftime("%Y-%m-%d")

            return jsonify({
                "success": True,
                "events": events,
                "week_start": week_start,
                "week_end": week_end,
                "count": len(events),
                "filter_applied": bool(filter_from or filter_to),
                "date_from": date_from,
                "date_to": date_to
            })
            
        except Exception as e:
            import traceback
            return jsonify({
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }), 500
    
    @app.route('/api/weekmatch/candidates', methods=['POST'])
    def find_weekmatch_candidates():
        """Find historical weeks matching the uploaded calendar events."""
        data = request.get_json()
        if not data or 'events' not in data:
            return jsonify({"success": False, "error": "No events provided"}), 400
        
        target_events = data['events']
        cache_key = f"weekmatch:candidates:{_hash_payload({'events': target_events})}"
        if cache_key in l2_cache:
            return jsonify(l2_cache[cache_key])
        
        try:
            candidates = _find_matching_weeks(target_events)
            payload = {
                "success": True,
                "candidates": candidates,
                "count": len(candidates)
            }
            l2_cache[cache_key] = payload
            return jsonify(payload)
        except Exception as e:
            import traceback
            return jsonify({
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }), 500
    
    @app.route('/api/weekmatch/pricepaths', methods=['POST'])
    def get_weekmatch_pricepaths():
        """Get price paths for selected weeks, normalized to week start."""
        data = request.get_json()
        if not data or 'weeks' not in data:
            return jsonify({"success": False, "error": "No weeks provided"}), 400
        
        weeks = data['weeks']
        cache_key = f"weekmatch:pricepaths:{_hash_payload({'weeks': weeks})}"
        if cache_key in l2_cache:
            return jsonify(l2_cache[cache_key])
        
        try:
            from zoneinfo import ZoneInfo
            price_paths = []
            for week_start in weeks:
                prices = _fetch_week_prices(week_start)
                if prices:
                    anchor_dt = datetime.strptime(week_start, "%Y-%m-%d").replace(hour=18, minute=0, second=0, microsecond=0)
                    anchor_dt = anchor_dt.replace(tzinfo=ZoneInfo("America/New_York"))
                    anchor_ts_utc = int(anchor_dt.timestamp() * 1_000_000_000)
                    ts_utc = []
                    price_dec = []
                    anchor_price = prices[0].get("close") if prices else None
                    for p in prices:
                        ts_str = p.get("timestamp")
                        if not ts_str:
                            continue
                        ts_dt = datetime.fromisoformat(ts_str.replace('Z', '').replace('+00:00', ''))
                        if ts_dt.tzinfo is None:
                            ts_dt = ts_dt.replace(tzinfo=ZoneInfo("America/New_York"))
                        ts_utc.append(int(ts_dt.timestamp() * 1_000_000_000))
                        price_dec.append(float(p["close"]))
                    if anchor_price is not None:
                        price_bps_from_anchor = [
                            round(v, 4) for v in normalize_price_to_bps(price_dec, float(anchor_price))
                        ]
                    else:
                        price_bps_from_anchor = [0.0 for _ in price_dec]
                    price_paths.append({
                        "week_start": week_start,
                        "anchor_ts_utc": anchor_ts_utc,
                        "paths": {
                            "ts_utc": ts_utc,
                            "price_dec": price_dec,
                            "price_bps_from_anchor": price_bps_from_anchor,
                        },
                    })
            
            payload = {
                "success": True,
                "weeks": price_paths
            }
            l2_cache[cache_key] = payload
            return jsonify(payload)
        except Exception as e:
            import traceback
            return jsonify({
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }), 500
    
    @app.route('/api/weekmatch/candlestick/<week_start>')
    def get_weekmatch_candlestick(week_start: str):
        """Get candlestick data and events for a specific week."""
        try:
            cache_key = f"weekmatch:candlestick:{week_start}"
            if cache_key in l2_cache:
                return jsonify(l2_cache[cache_key])
            candles = _fetch_week_candlestick(week_start)
            events = _fetch_week_events(week_start)
            from zoneinfo import ZoneInfo
            candles_out = []
            for c in candles:
                ts_dt = datetime.fromisoformat(c["ts"].replace("Z", ""))
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=ZoneInfo("America/New_York"))
                candles_out.append({
                    "ts_utc": int(ts_dt.timestamp() * 1_000_000_000),
                    "open_dec": float(c["open"]),
                    "high_dec": float(c["high"]),
                    "low_dec": float(c["low"]),
                    "close_dec": float(c["close"]),
                })
            payload = {
                "success": True,
                "week_start": week_start,
                "candles": candles_out,
                "events": events
            }
            l2_cache[cache_key] = payload
            return jsonify(payload)
        except Exception as e:
            import traceback
            return jsonify({
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }), 500
    
    # Return socketio-wrapped app if enabled, otherwise just the app
    if enable_socketio and socketio is not None:
        return app, socketio
    return app


def start_live_server(html_path: str, event_type: str = "NFP"):
    """
    Start the Flask server to serve live data (development mode).
    For production, use run_server.py with Gunicorn/Waitress.
    """
    result = create_dashboard_app(html_path, event_type, enable_socketio=True)
    
    # Handle both return types (with or without socketio)
    if isinstance(result, tuple):
        app, socketio = result
        use_socketio = True
    else:
        app = result
        socketio = None
        use_socketio = False
    
    print(f"\n{'='*60}")
    print(f"  LIVE UNIFIED DASHBOARD SERVER (Dev Mode)")
    print(f"{'='*60}")
    print(f"Dashboard:  http://localhost:{LIVE_SERVER_PORT}/")
    print(f"Live API:   http://localhost:{LIVE_SERVER_PORT}/api/live")
    print(f"WebSocket:  {'Enabled' if use_socketio else 'Disabled'}")
    print(f"Reset Ref:  http://localhost:{LIVE_SERVER_PORT}/api/refresh_ref")
    print(f"{'='*60}")
    print("For multi-user production use: python scripts/run_server.py")
    print("Press Ctrl+C to stop.\n")
    
    # Use socketio.run if available, otherwise fall back to app.run
    if use_socketio and socketio is not None:
        socketio.run(app, host='0.0.0.0', port=LIVE_SERVER_PORT, debug=False)
    else:
        app.run(host='0.0.0.0', port=LIVE_SERVER_PORT, debug=False, threaded=True)


# ============================================================================
# DATA LOADING AND PROCESSING (continued in next section)
# ============================================================================
from src.servers.marketdata.run_query import run_query
from scripts.dashboard_tabs import preevent_tab, postevent_tab, adhoc_tab, weekmatch_tab, polymarket_scenarios_tab, impulse_tab
from scripts.event_data_loader import (
    get_nfp_data_combined,
    get_event_data_combined,
    get_event_config,
    get_supported_events,
    calculate_surprise_percentile,
    EVENT_CONFIG,
)

OUTPUT_DIR = PROJECT_ROOT / "workspace" / "scenarios"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ECONOMIC_EVENTS_DB = PROJECT_ROOT / "workspace" / "economic_events.db"


# ============================================================================
# WEEK MATCH HELPER FUNCTIONS
# ============================================================================

# Tier reference list (from Event list.xlsx analysis)
TIER_1_EVENTS = [
    "NFP", "UER", "Average Hourly Earnings", "Change in Private Payrolls",
    "Change in Manufact. Payrolls", "Labor Force Participation",
    "CPI MoM", "Core CPI MoM", "CPI YoY", "Core CPI YoY",
    "PPI MoM", "Core PPI MoM", "Supercore PPI", "PPI YoY", "Core PPI YoY",
    "PCE MoM", "PCE YoY", "Core PCE MoM", "Core PCE YoY",
    "GDP Price Index", "Core PCE Price Index",
    "PCE Personal Income", "PCE Personal Spending", "PCE Real Personal Spending",
    "GDP Annualized", "Personal Consumption",
    "Fed", "FOMC", "Powell",
]

TIER_2_EVENTS = [
    "Retail Sales", "Retail Sales Control Group",
    "ISM Manufacturing", "ISM Manufacturing New Orders",
    "ISM Services", "ISM Services New Orders",
]

TIER_3_EVENTS = [
    "Initial Jobless Claims", "Continuing Claims", "Initial Claims 4-Wk",
    "ADP Employment Change", "ADP Weekly",
    "JOLTS Job Openings", "JOLTS Quits", "JOLTS Layoffs",
    "Challenger Job Cuts",
    "ISM Manufacturing Employment", "ISM Services Employment",
    "ISM Manufacturing Prices Paid", "ISM Services Prices Paid",
    "U-Mich", "Michigan", "Consumer Sentiment",
    "New Home Sales",
    "S&P Global", "PMI",
    "Durable Goods", "Durables Ex Transportation",
    "NY Fed Empire State", "Philly Fed",
]

DAY_TO_IDX = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
IDX_TO_DAY = {v: k for k, v in DAY_TO_IDX.items()}


def _get_event_tier(cell, event_name: str) -> int:
    """Determine event tier from cell color or event name matching."""
    # First try to match by event name against reference lists
    name_lower = event_name.lower()
    
    for t1_keyword in TIER_1_EVENTS:
        if t1_keyword.lower() in name_lower:
            return 1
    
    for t2_keyword in TIER_2_EVENTS:
        if t2_keyword.lower() in name_lower:
            return 2
    
    for t3_keyword in TIER_3_EVENTS:
        if t3_keyword.lower() in name_lower:
            return 3
    
    # Fallback: check cell fill color
    if cell and cell.fill and cell.fill.patternType == 'solid':
        fg = cell.fill.fgColor
        if fg:
            if fg.type == 'rgb' and fg.rgb in ('FFFFFF00', 'FFFFC000'):
                return 1  # Yellow/Orange = Tier 1
            elif fg.type == 'theme':
                if fg.theme == 5:  # Pink theme
                    return 2
                elif fg.theme == 3:  # Blue theme
                    return 3
    
    return 4  # Default tier


def _normalize_event_name(name: str) -> str:
    """Normalize event name for fuzzy matching."""
    import re
    s = name.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    # Remove month names and common suffixes
    month_words = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
                   "january", "february", "march", "april", "june", "july", "august", "september",
                   "october", "november", "december"}
    drop_words = {"prel", "prelim", "preliminary", "flash", "final", "rate", "index", "yoy", "mom",
                  "q1", "q2", "q3", "q4", "sa", "nsa"}
    tokens = [t for t in s.split() if t and t not in month_words and t not in drop_words]
    return " ".join(tokens)


def _event_similarity(a: str, b: str) -> float:
    """Calculate similarity between two event names."""
    na = _normalize_event_name(a)
    nb = _normalize_event_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    ta = set(na.split())
    tb = set(nb.split())
    if not ta or not tb:
        return 0.0
    jaccard = len(ta.intersection(tb)) / len(ta.union(tb))
    return jaccard


def _find_matching_weeks(target_events: list, top_n: int = 10) -> list:
    """
    Find historical weeks that best match the target event list.
    Uses ordered matching - events must appear in chronological sequence.
    """
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    cursor = conn.cursor()
    
    # Fetch all events
    cursor.execute("SELECT event_name, country, event_date FROM economic_events WHERE event_date IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()
    
    # Group events by week
    from collections import defaultdict
    weeks = defaultdict(list)
    
    for event_name, country, event_date in rows:
        if not event_date:
            continue
        try:
            dt = datetime.fromisoformat(event_date.replace("Z", ""))
        except:
            continue
        
        # Week start = Monday
        week_start = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        weeks[week_start].append({
            "name": event_name,
            "country": country,
            "datetime": dt,
            "day_idx": dt.weekday(),
            "time": dt.strftime("%H:%M")
        })
    
    # Convert target events to comparable format
    target_list = []
    for evt in target_events:
        day_idx = DAY_TO_IDX.get(evt.get('day', 'Monday'), 0)
        target_list.append({
            "day_idx": day_idx,
            "time": evt.get('time', '00:00'),
            "country": evt.get('country', 'US'),
            "name": evt.get('name', ''),
            "tier": evt.get('tier', 4)
        })
    
    # Sort targets by day, time
    target_list.sort(key=lambda x: (x["day_idx"], x["time"]))
    
    def _ordered_match_count(target_events_list, week_events_list) -> int:
        """Count ordered matches between target list and week events."""
        week_idx = 0
        matched_count = 0
        for t in target_events_list:
            for i in range(week_idx, len(week_events_list)):
                w = week_events_list[i]
                if w["day_idx"] == t["day_idx"]:
                    t_mins = int(t["time"].split(":")[0]) * 60 + int(t["time"].split(":")[1])
                    w_mins = int(w["time"].split(":")[0]) * 60 + int(w["time"].split(":")[1])
                    if abs(t_mins - w_mins) <= 120:
                        if _event_similarity(t["name"], w["name"]) >= 0.4:
                            matched_count += 1
                            week_idx = i + 1
                            break
        return matched_count

    # Sort week events chronologically (once)
    sorted_weeks = {}
    for week_start, week_events in weeks.items():
        week_events.sort(key=lambda e: (e["day_idx"], e["time"]))
        sorted_weeks[week_start] = week_events

    # Tiered candidate pruning (partial matches allowed)
    candidates = list(sorted_weeks.items())
    for tier in [1, 2, 3, 4]:
        tier_targets = [t for t in target_list if t["tier"] == tier]
        if not tier_targets:
            continue
        tier_matches = []
        for week_start, week_events in candidates:
            cnt = _ordered_match_count(tier_targets, week_events)
            if cnt > 0:
                tier_matches.append((week_start, week_events))
        # Only prune if we found any matches at this tier
        if tier_matches:
            candidates = tier_matches

    # Score each historical week (only candidates after pruning)
    results = []
    for week_start, week_events in candidates:
        # week_events already sorted
        
        matched = []
        missing = []
        matched_by_tier = {1: 0, 2: 0, 3: 0, 4: 0}
        missing_by_tier = {1: 0, 2: 0, 3: 0, 4: 0}
        
        # Ordered matching
        week_idx = 0
        for t in target_list:
            found = False
            # Search forward in week events
            for i in range(week_idx, len(week_events)):
                w = week_events[i]
                # Check day and approximate time match
                if w["day_idx"] == t["day_idx"]:
                    # Time within 2 hours
                    t_mins = int(t["time"].split(":")[0]) * 60 + int(t["time"].split(":")[1])
                    w_mins = int(w["time"].split(":")[0]) * 60 + int(w["time"].split(":")[1])
                    if abs(t_mins - w_mins) <= 120:
                        # Check name similarity
                        if _event_similarity(t["name"], w["name"]) >= 0.4:
                            matched.append({"target": t, "historical": w})
                            matched_by_tier[t["tier"]] += 1
                            week_idx = i + 1
                            found = True
                            break
            
            if not found:
                missing.append(t)
                missing_by_tier[t["tier"]] += 1
        
        # Calculate extra events (in historical week but not in target)
        matched_hist_names = {_normalize_event_name(m["historical"]["name"]) for m in matched}
        extra = [e for e in week_events if _normalize_event_name(e["name"]) not in matched_hist_names]
        extra_by_tier = {1: 0, 2: 0, 3: 0, 4: 0}
        for e in extra:
            tier = _get_event_tier(None, e["name"])
            extra_by_tier[tier] += 1
        
        total_matched = len(matched)
        match_pct = (total_matched / len(target_list) * 100) if target_list else 0
        
        week_start_str = week_start.strftime("%Y-%m-%d")
        week_end_str = (week_start + timedelta(days=4)).strftime("%Y-%m-%d")
        matched_events = []
        for m in matched:
            hist = m["historical"]
            matched_events.append({
                "name": hist["name"],
                "date": hist["datetime"].strftime("%Y-%m-%d"),
                "time": hist["time"],
                "tier": m["target"]["tier"],
            })

        results.append({
            "week_start": week_start_str,
            "week_end": week_end_str,
            "match_score": round(match_pct / 100.0, 4),
            "total_matched": total_matched,
            "match_pct": match_pct,
            "matched_by_tier": matched_by_tier,
            "missing_by_tier": missing_by_tier,
            "extra_by_tier": extra_by_tier,
            "matched_events": matched_events,
            "missing_events": [{"day": IDX_TO_DAY.get(m["day_idx"], "?"), "time": m["time"], "name": m["name"], "tier": m["tier"]} for m in missing],
        })
    
    # Sort by match count descending
    results.sort(key=lambda x: (-x["total_matched"], x["week_start"]))
    
    return results[:top_n]


def _fetch_week_prices(week_start: str) -> list:
    """Fetch price data for a week: Monday 8am to Friday 6pm.
    
    Returns raw prices with timestamps and close values.
    Normalization is done client-side for dynamic window adjustment.
    """
    try:
        week_dt = datetime.strptime(week_start, "%Y-%m-%d")
    except:
        return []
    
    # Week runs Monday 8am to Friday 6pm
    start_dt = week_dt.replace(hour=8, minute=0, second=0, microsecond=0)
    end_dt = (week_dt + timedelta(days=4)).replace(hour=18, minute=0, second=0, microsecond=0)
    
    all_rows = []
    
    # Fetch day-by-day for Monday through Friday
    for day_offset in range(5):
        current_date = week_dt + timedelta(days=day_offset)
        current_str = current_date.strftime('%Y-%m-%d')
        
        # Day session (8am - 6pm)
        start_time = "08:00:00"
        end_time = "18:00:00"
        
        result = run_query(
            template="custom",
            columns=["timestamp", "close"],
            params={
                "conditions": (
                    f"symbol = 'TY' AND "
                    f"timestamp >= '{current_str} {start_time}' AND "
                    f"timestamp <= '{current_str} {end_time}' AND "
                    f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
                )
            },
            limit=3000
        )
        
        all_rows.extend(result.get("data") or [])
    
    if not all_rows:
        return []
    
    # Parse and sort by timestamp
    parsed = []
    for row in all_rows:
        try:
            ts = row.get('timestamp', '')
            close = row.get('close', 0)
            if not ts or not close:
                continue
            # Parse timestamp and remove timezone info for consistency
            dt_str = ts.replace("Z", "").replace("+00:00", "")
            if "-05:00" in dt_str:
                dt_str = dt_str.split("-05:00")[0]
            if "-04:00" in dt_str:
                dt_str = dt_str.split("-04:00")[0]
            dt = datetime.fromisoformat(dt_str)
            parsed.append({"dt": dt, "close": close, "ts": dt.isoformat()})
        except:
            continue
    
    parsed.sort(key=lambda x: x["dt"])
    
    if not parsed:
        return []

    # Filter out bad ticks (near-zero/negative or unrealistic jumps)
    filtered = []
    prev_close = None
    for p in parsed:
        close = p["close"]
        # Treasury futures should stay within a reasonable band
        if close < 80 or close > 200:
            continue
        if prev_close is not None and abs(close - prev_close) > 1.0:
            # Drop unrealistic 1-minute jump (>1.0 point = 64 bps)
            continue
        filtered.append(p)
        prev_close = close

    if not filtered:
        return []

    # Get reference price (first price at Monday 8am)
    ref_price = filtered[0]["close"]
    
    # Normalize to bps from window start using shared helper
    prices = []
    close_prices = [p["close"] for p in filtered]
    bps_moves = normalize_price_to_bps(close_prices, ref_price)
    for idx, p in enumerate(filtered):
        prices.append({
            "ts": p["ts"],
            "close": p["close"],
            "bps": round(bps_moves[idx], 2)
        })
    
    return prices


def _fetch_week_candlestick(week_start: str) -> list:
    """Fetch 5-minute candlestick data for a week (Mon 8am - Fri 6pm)."""
    try:
        week_dt = datetime.strptime(week_start, "%Y-%m-%d")
    except:
        return []
    
    # Week runs Monday 8am to Friday 6pm
    start_dt = week_dt.replace(hour=8, minute=0)
    end_dt = (week_dt + timedelta(days=4)).replace(hour=18, minute=0)
    
    start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    
    result = run_query(
        template="custom",
        columns=["timestamp", "open", "high", "low", "close"],
        params={
            "conditions": f"symbol = 'TY' AND timestamp >= '{start_str}' AND timestamp <= '{end_str}' AND close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}",
            "order_by": "timestamp ASC",
        }
    )
    
    if not result or "data" not in result:
        return []
    
    # Aggregate to 5-minute bars
    from collections import defaultdict
    bars = defaultdict(lambda: {"open": None, "high": float("-inf"), "low": float("inf"), "close": None})
    prev_close = None
    for row in result["data"]:
        try:
            # Data is returned as dict with 'timestamp', 'open', 'high', 'low', 'close' keys
            ts = row.get('timestamp', '')
            o = row.get('open', 0)
            h = row.get('high', 0)
            l = row.get('low', 0)
            c = row.get('close', 0)
            # Filter bad ticks (erroneous near-zero prices)
            if c is None or not is_valid_ty_price(c):
                continue
            if prev_close is not None and abs(c - prev_close) > 1.0:
                continue

            dt = datetime.fromisoformat(ts.replace("Z", ""))
            # 5-minute bucket
            bucket = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
            bucket_key = bucket.isoformat()
            
            bar = bars[bucket_key]
            if bar["open"] is None:
                bar["open"] = o
            bar["high"] = max(bar["high"], h)
            bar["low"] = min(bar["low"], l)
            bar["close"] = c
            prev_close = c
        except:
            continue
    
    candles = []
    for ts_key in sorted(bars.keys()):
        bar = bars[ts_key]
        if bar["open"] is not None:
            candles.append({
                "ts": ts_key,
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"]
            })
    
    return candles


def _fetch_week_events(week_start: str) -> list:
    """Fetch economic events for a specific week from the database."""
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    
    try:
        week_dt = datetime.strptime(week_start, "%Y-%m-%d")
    except:
        return []
    
    week_end = week_dt + timedelta(days=7)
    
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT event_name, country, event_date FROM economic_events WHERE event_date >= ? AND event_date < ?",
        (week_dt.isoformat(), week_end.isoformat())
    )
    rows = cursor.fetchall()
    conn.close()
    
    events = []
    for name, country, event_date in rows:
        tier = _get_event_tier(None, name)
        events.append({
            "name": name,
            "country": country,
            "datetime": event_date,
            "tier": tier
        })
    
    return events


def get_next_event_date_and_time(event_type: str) -> Optional[Tuple[str, str]]:
    """
    Get the next upcoming event date and time from the economic_events database.
    
    Args:
        event_type: "NFP" or "CPI"
    
    Returns:
        Tuple of (date_str, time_str) or None if not found
        date_str: YYYY-MM-DD format
        time_str: HH:MM format (24-hour)
    """
    if not ECONOMIC_EVENTS_DB.exists():
        return None
    
    try:
        from scripts.event_data_loader import EVENT_CONFIG
        config = EVENT_CONFIG.get(event_type)
        if not config:
            return None
        
        db_event_name = config["db_event_name"]
        conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
        cursor = conn.cursor()
        
        # Get the next event that hasn't happened yet (no actual or future date)
        today_str = datetime.now().strftime('%Y-%m-%d')
        cursor.execute('''
            SELECT event_date
            FROM economic_events 
            WHERE event_name = ?
            AND country LIKE '%United States%'
            AND event_date >= ?
            ORDER BY event_date ASC
            LIMIT 1
        ''', (db_event_name, f"{today_str}T00:00:00"))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        event_date_str = row[0]
        
        # Parse the event_date (format: 2025-01-10T08:30:00 or 2025-01-10 08:30:00)
        try:
            if 'T' in str(event_date_str):
                date_part, time_part = str(event_date_str).split('T')
                time_str = time_part[:5]  # Get HH:MM
            elif ' ' in str(event_date_str):
                date_part, time_part = str(event_date_str).split(' ')
                time_str = time_part[:5]
            else:
                # No time in string, default to 08:30
                date_part = str(event_date_str)[:10]
                time_str = "08:30"
            
            # Ensure date is in YYYY-MM-DD format
            if len(date_part) == 10:
                return (date_part, time_str)
            else:
                # Try to parse as datetime and format
                event_dt = datetime.fromisoformat(event_date_str.replace('Z', ''))
                return (event_dt.strftime('%Y-%m-%d'), time_str)
        except Exception as e:
            print(f"Warning: Could not parse event_date '{event_date_str}': {e}")
            # Fallback: extract date part
            date_str = str(event_date_str)[:10]
            return (date_str, "08:30")  # Default time
            
    except Exception as e:
        print(f"Warning: Could not query next event time: {e}")
        return None


def get_event_defaults(event_type: str) -> Dict[str, float]:
    """Return UI defaults for a given event type."""
    event_type = event_type.upper()
    if event_type == "CPI":
        return {
            "cons_std_default": 0.05,
            "cons_std_step": 0.01,
            "cons_std_min": 0.01,
            "cons_std_max": 0.2,
            "bucket_width_default": 0.02,
            "bucket_step": 0.005,
            "bucket_min": 0.005,
            "bucket_max": 0.1,
            "bucket_precision": 3,
            "fwd_be_default": 8.0,
        }
    if event_type == "PPI":
        return {
            "cons_std_default": 0.1,
            "cons_std_step": 0.01,
            "cons_std_min": 0.01,
            "cons_std_max": 0.5,
            "bucket_width_default": 0.05,
            "bucket_step": 0.01,
            "bucket_min": 0.01,
            "bucket_max": 0.2,
            "bucket_precision": 2,
            "fwd_be_default": 8.0,
            "post_event_end_time": "17:00",  # 5pm ET for PPI
        }
    # NFP defaults (also fallback for other events)
    return {
        "cons_std_default": 26.1,
        "cons_std_step": 0.5,
        "cons_std_min": 10,
        "cons_std_max": 60,
        "bucket_width_default": 7,
        "bucket_step": 1,
        "bucket_min": 1,
        "bucket_max": 20,
        "bucket_precision": 0,
        "fwd_be_default": 6.384,
    }


def get_other_events_on_date(event_date: str, exclude_event: str = "NFP", event_type: str = "NFP") -> List[Dict]:
    """Query economic_events.db for other tier 1/2 events on the same date."""
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    
    try:
        conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT event_name, importance, category, actual, consensus, country
            FROM economic_events 
            WHERE DATE(event_date) = ?
              AND LOWER(event_name) NOT LIKE ?
              AND (LOWER(importance) = 'high' OR LOWER(importance) = 'medium')
              AND country = 'United States'
            ORDER BY importance DESC, event_name ASC
        """, (event_date, f"%{exclude_event.lower()}%"))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [{
            'name': row['event_name'],
            'importance': row['importance'],
            'category': row['category'],
            'actual': row['actual'],
            'consensus': row['consensus'],
        } for row in rows]
    except Exception as e:
        print(f"Warning: Could not query other events: {e}")
        return []


def get_front_month_symbol(event_date: str) -> str:
    """Determine the front-month TY contract for a given event date."""
    event_dt = datetime.strptime(event_date, "%Y-%m-%d")
    month = event_dt.month
    year = event_dt.year % 10
    
    if month <= 3:
        return f"TYH{year}"
    elif month <= 6:
        return f"TYM{year}"
    elif month <= 9:
        return f"TYU{year}"
    else:
        return f"TYZ{year}"


def load_event_data(event_type: str = "NFP") -> List[Dict]:
    """Load event data (NFP or CPI) from the Excel sheet via event_data_loader."""
    event_type = event_type.upper()

    data = None
    if event_type == "NFP":
        try:
            from scripts.nfp_data_loader import get_nfp_data_combined
            data = get_nfp_data_combined()
        except Exception as e:
            print(f"Warning: NFP combined loader failed, falling back to generic loader: {e}")

    if data is None:
        data = get_event_data_combined(event_type)

    if not data:
        print(f"No data returned for event type: {event_type}")
        return []

    events = []
    for row in data:
        date = row.get('date')
        if not date:
            continue

        cons_std = row.get('cons_std') or row.get('stdev')
        if cons_std is None:
            continue

        fwd = row.get('fwd_bps') or row.get('fwd') or row.get('fwd_be')

        events.append({
            'date': date if isinstance(date, str) else date.strftime('%Y-%m-%d'),
            'cons_std': float(cons_std),
            'surp_pct': row.get('surp_pct'),
            'surprise': row.get('surprise'),
            'standardized_surprise': row.get('standardized_surprise'),
            'nfp_actual': row.get('nfp_actual') or row.get('actual'),
            'nfp_cons': row.get('nfp_cons') or row.get('consensus'),
            'fwd': float(fwd) if fwd else None,
            'uer_actual': row.get('uer_actual'),
            'uer_cons': row.get('uer_cons'),
            'uer_std': row.get('uer_std') or row.get('uer_cons_std'),
            'uer_surprise': row.get('uer_surprise'),
            'uer_std_surprise': row.get('uer_std_surprise'),
        })

    print(f"Loaded {len(events)} {event_type} events from Excel sheet")
    return events


def build_light_event(event: Dict) -> Dict:
    """Return a lightweight event payload for initial /api/events responses."""
    return {
        "date": event.get("date"),
        "cons_std": event.get("cons_std"),
        "surp_pct": event.get("surp_pct"),
        "surprise": event.get("surprise"),
        "standardized_surprise": event.get("standardized_surprise"),
        "nfp_actual": event.get("nfp_actual") or event.get("actual"),
        "nfp_cons": event.get("nfp_cons") or event.get("consensus"),
        "fwd": event.get("fwd"),
        "post_be": event.get("post_be"),
        "uer_actual": event.get("uer_actual"),
        "uer_cons": event.get("uer_cons"),
        "uer_cons_std": event.get("uer_cons_std"),
        "uer_surprise": event.get("uer_surprise"),
        "uer_std_surprise": event.get("uer_std_surprise"),
    }


def _event_has_detail_payload(event: Optional[Dict]) -> bool:
    """True when an event payload already contains hydrated pre/post path data."""
    if not isinstance(event, dict):
        return False
    prices = event.get("prices")
    postevent_prices = event.get("postevent_prices")
    return isinstance(prices, list) and isinstance(postevent_prices, list)


def compute_cons_std_stats(events: List[Dict]) -> Dict:
    """Compute simple consensus std dev stats for lightweight payloads."""
    cons_stds = [e.get("cons_std") for e in events if e.get("cons_std") is not None]
    if not cons_stds:
        return {"min": 20, "max": 40, "mean": 25, "median": 25}
    cons_stds_sorted = sorted(cons_stds)
    return {
        "min": round(cons_stds_sorted[0], 1),
        "max": round(cons_stds_sorted[-1], 1),
        "mean": round(sum(cons_stds) / len(cons_stds), 1),
        "median": round(cons_stds_sorted[len(cons_stds_sorted) // 2], 1),
    }


def extract_std_surprises(events: List[Dict]) -> List[float]:
    """Extract standardized surprises from events for lightweight payloads."""
    return [
        e.get("standardized_surprise")
        for e in events
        if e.get("standardized_surprise") is not None
    ]


def load_nfp_data() -> List[Dict]:
    """Backwards compatible: Load NFP event data."""
    return load_event_data("NFP")


@functools.lru_cache(maxsize=256)
def _fetch_preevent_prices_cached(event_date: str) -> Optional[Tuple[Tuple, ...]]:
    """Internal cached version - returns tuple of tuples for hashability."""
    event_dt = datetime.strptime(event_date, "%Y-%m-%d")
    prev_day = event_dt - timedelta(days=1)
    
    while prev_day.weekday() >= 5:
        prev_day -= timedelta(days=1)
    
    prev_day_str = prev_day.strftime('%Y-%m-%d')
    
    # Database uses 'TY' as continuous front-month symbol
    # Filter out erroneous near-zero prices that cause extreme bps spikes
    result_evening = run_query(
        template="custom",
        columns=["timestamp", "high", "low", "close"],
        params={
            "conditions": (
                f"symbol = 'TY' AND "
                f"timestamp >= '{prev_day_str} 15:00:00' AND "
                f"timestamp <= '{prev_day_str} 23:59:59' AND "
                f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
            )
        },
        limit=2000
    )
    
    result_morning = run_query(
        template="custom",
        columns=["timestamp", "high", "low", "close"],
        params={
            "conditions": (
                f"symbol = 'TY' AND "
                f"timestamp >= '{event_date} 00:00:00' AND "
                f"timestamp <= '{event_date} 08:30:00' AND "
                f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
            )
        },
        limit=2000
    )
    
    rows_evening = result_evening.get("data") or result_evening.get("rows") or []
    rows_morning = result_morning.get("data") or result_morning.get("rows") or []
    
    all_rows = rows_evening + rows_morning
    if not all_rows:
        return None
    
    prices = []
    for row in all_rows:
        ts = row.get('timestamp')
        high = row.get('high')
        low = row.get('low')
        close = row.get('close')
        if ts and close:
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '').replace('+00:00', ''))
                except:
                    continue
            prices.append((
                ts.isoformat() if isinstance(ts, datetime) else str(ts),
                float(high) if high else float(close),
                float(low) if low else float(close),
                float(close),
            ))
    
    prices.sort(key=lambda x: x[0])  # Sort by timestamp
    return tuple(prices) if prices else None


def fetch_preevent_prices(event_date: str) -> Optional[List[Dict]]:
    """Fetch minute-by-minute prices from 3pm previous day to 8:30am event day.
    Uses LRU cache internally for performance."""
    cached = _fetch_preevent_prices_cached(event_date)
    if cached is None:
        return None
    # Convert tuple of tuples back to list of dicts
    return [
        {'timestamp': t[0], 'high': t[1], 'low': t[2], 'close': t[3]}
        for t in cached
    ]


@functools.lru_cache(maxsize=256)
def _fetch_postevent_prices_cached(event_date: str) -> Optional[Tuple[Tuple, ...]]:
    """Internal cached version - returns tuple of tuples for hashability."""
    
    # Database uses 'TY' as continuous front-month symbol
    # Filter out erroneous near-zero prices that cause extreme bps spikes
    result = run_query(
        template="custom",
        columns=["timestamp", "high", "low", "close"],
        params={
            "conditions": (
                f"symbol = 'TY' AND "
                f"timestamp >= '{event_date} 08:30:00' AND "
                f"timestamp <= '{event_date} 15:00:00' AND "
                f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
            )
        },
        limit=2000
    )
    
    rows = result.get("data") or result.get("rows") or []
    
    if not rows:
        return None
    
    prices = []
    for row in rows:
        ts = row.get('timestamp')
        high = row.get('high')
        low = row.get('low')
        close = row.get('close')
        if ts and close:
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '').replace('+00:00', ''))
                except:
                    continue
            prices.append((
                ts.isoformat() if isinstance(ts, datetime) else str(ts),
                float(high) if high else float(close),
                float(low) if low else float(close),
                float(close),
            ))
    
    prices.sort(key=lambda x: x[0])  # Sort by timestamp
    return tuple(prices) if prices else None


def fetch_postevent_prices(event_date: str) -> Optional[List[Dict]]:
    """Fetch minute-by-minute prices from 8:30am to 3pm on event day (post-release).
    Uses LRU cache internally for performance."""
    cached = _fetch_postevent_prices_cached(event_date)
    if cached is None:
        return None
    # Convert tuple of tuples back to list of dicts
    return [
        {'timestamp': t[0], 'high': t[1], 'low': t[2], 'close': t[3]}
        for t in cached
    ]


def calculate_postevent_be(prices: List[Dict], fwd: float) -> Optional[float]:
    """
    Calculate post-event B/E multiplier from 8:30am to 3pm prices.
    B/E = max(high - open, open - low) converted to bps / FWD
    """
    if not prices or fwd <= 0:
        return None
    
    price_830 = prices[0]['close']
    high_price = max(p.get('high', p['close']) for p in prices)
    low_price = min(p.get('low', p['close']) for p in prices)
    
    up_move = abs(high_price - price_830)
    down_move = abs(price_830 - low_price)
    max_extent = max(up_move, down_move)
    move_bps = window_move_bps(price_830, price_830 + max_extent)
    return calc_be_multiplier(move_bps, fwd)


@functools.lru_cache(maxsize=256)
def _fetch_candlestick_data_cached(event_date: str, days_around: int = 5) -> Optional[Tuple[Tuple, ...]]:
    """Internal cached version - returns tuple of tuples for hashability."""
    event_dt = datetime.strptime(event_date, "%Y-%m-%d")
    
    start_dt = event_dt - timedelta(days=days_around)
    end_dt = event_dt + timedelta(days=days_around)
    
    while start_dt.weekday() >= 5:
        start_dt -= timedelta(days=1)
    while end_dt.weekday() >= 5:
        end_dt += timedelta(days=1)
    
    start_str = start_dt.strftime('%Y-%m-%d')
    end_str = end_dt.strftime('%Y-%m-%d')
    
    # Database uses 'TY' as continuous front-month symbol
    # Filter out erroneous near-zero prices that cause extreme bps spikes
    result = run_query(
        template="custom",
        columns=["timestamp", "open", "high", "low", "close", "volume"],
        params={
            "conditions": (
                f"symbol = 'TY' AND "
                f"timestamp >= '{start_str} 00:00:00' AND "
                f"timestamp <= '{end_str} 23:59:59' AND "
                f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
            )
        },
        limit=50000
    )
    
    rows = result.get("data") or result.get("rows") or []
    if not rows:
        return None
    
    parsed_rows = []
    for row in rows:
        ts = row.get('timestamp')
        if not ts:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace('Z', '').replace('+00:00', ''))
            except:
                continue
        
        parsed_rows.append({
            'ts': ts,
            'open': float(row.get('open') or row.get('close') or 0),
            'high': float(row.get('high') or row.get('close') or 0),
            'low': float(row.get('low') or row.get('close') or 0),
            'close': float(row.get('close') or 0),
            'volume': int(row.get('volume') or 0),
        })
    
    if not parsed_rows:
        return None
    
    parsed_rows.sort(key=lambda x: x['ts'])
    
    five_min_bars = []
    current_bucket = None
    bucket_data = None
    
    for row in parsed_rows:
        bucket_ts = row['ts'].replace(
            minute=(row['ts'].minute // 5) * 5,
            second=0,
            microsecond=0
        )
        
        if bucket_ts != current_bucket:
            if bucket_data is not None:
                five_min_bars.append((
                    current_bucket.isoformat(),
                    bucket_data['open'],
                    bucket_data['high'],
                    bucket_data['low'],
                    bucket_data['close'],
                    bucket_data['volume'],
                ))
            
            current_bucket = bucket_ts
            bucket_data = {
                'open': row['open'],
                'high': row['high'],
                'low': row['low'],
                'close': row['close'],
                'volume': row['volume'],
            }
        else:
            bucket_data['high'] = max(bucket_data['high'], row['high'])
            bucket_data['low'] = min(bucket_data['low'], row['low'])
            bucket_data['close'] = row['close']
            bucket_data['volume'] += row['volume']
    
    if bucket_data is not None and current_bucket is not None:
        five_min_bars.append((
            current_bucket.isoformat(),
            bucket_data['open'],
            bucket_data['high'],
            bucket_data['low'],
            bucket_data['close'],
            bucket_data['volume'],
        ))
    
    return tuple(five_min_bars) if five_min_bars else None


def fetch_candlestick_data(event_date: str, days_around: int = 5) -> Optional[List[Dict]]:
    """Fetch OHLC data for +/- days around an event and aggregate to 5-minute bars.
    Uses LRU cache internally for performance."""
    cached = _fetch_candlestick_data_cached(event_date, days_around)
    if cached is None:
        return None
    # Convert tuple of tuples back to list of dicts
    return [
        {'ts': t[0], 'open': t[1], 'high': t[2], 'low': t[3], 'close': t[4], 'volume': t[5]}
        for t in cached
    ]


def calculate_be_for_endtime(prices: List[Dict], prev_day: str, event_date: str, 
                              end_hour: int, end_minute: int, fwd: float) -> Optional[Tuple[float, float]]:
    """Calculate B/E multiplier and bps move for a specific end time."""
    if not prices or fwd <= 0:
        return None
    
    start_ts_space = f"{prev_day} 15:"
    start_ts_t = f"{prev_day}T15:"
    
    price_3pm = None
    for p in prices:
        ts = p['timestamp']
        if ts.startswith(start_ts_space) or ts.startswith(start_ts_t):
            price_3pm = p['close']
            break
    
    if price_3pm is None and prices:
        price_3pm = prices[0]['close']
    
    if price_3pm is None:
        return None
    
    end_ts_space = f"{event_date} {end_hour:02d}:{end_minute:02d}"
    end_ts_t = f"{event_date}T{end_hour:02d}:{end_minute:02d}"
    
    start_bound_space = f"{prev_day} 15:00"
    start_bound_t = f"{prev_day}T15:00"
    
    high_price = None
    low_price = None
    for p in prices:
        ts = p['timestamp']
        
        in_window = False
        if ' ' in ts:
            in_window = start_bound_space <= ts <= end_ts_space + ":59"
        else:
            in_window = start_bound_t <= ts <= end_ts_t + ":59"
        
        if in_window:
            price_high = p.get('high', p['close'])
            price_low = p.get('low', p['close'])
            if high_price is None or price_high > high_price:
                high_price = price_high
            if low_price is None or price_low < low_price:
                low_price = price_low
    
    if high_price is None or low_price is None:
        return None
    
    up_move = abs(high_price - price_3pm)
    down_move = abs(price_3pm - low_price)
    max_extent = max(up_move, down_move)
    
    move_bps = window_move_bps(price_3pm, price_3pm + max_extent)
    return (calc_be_multiplier(move_bps, fwd), move_bps)


# Valid price range for TY (10-year Treasury futures)
# Historical range is approximately 85-145; values outside this are erroneous data
TY_PRICE_MIN = 80.0
TY_PRICE_MAX = 150.0


def is_valid_ty_price(price: float) -> bool:
    """Check if a TY price is within valid range (filters erroneous data like 0.05)."""
    return TY_PRICE_MIN <= price <= TY_PRICE_MAX


def fetch_trading_session_prices(session_end_date: str) -> Optional[List[Dict]]:
    """
    Fetch prices for a full trading session ending on session_end_date.
    Session window: 6pm prior day → 5pm session_end_date (23-hour futures session).
    
    Note: Filters out erroneous price data (values outside 80-150 range).
    The database contains ~110k bad rows with near-zero prices that would
    cause extreme bps spikes if included.
    
    Args:
        session_end_date: The date when the session ENDS (at 5pm)
    
    Returns:
        List of price dicts with timestamp, high, low, close
    """
    session_end_dt = datetime.strptime(session_end_date, "%Y-%m-%d")
    session_start_dt = session_end_dt - timedelta(days=1)
    session_start_str = session_start_dt.strftime('%Y-%m-%d')
    session_end_str = session_end_date
    
    # Evening session: 6pm to midnight on session_start_str
    # Added price filter to exclude erroneous data points
    result_evening = run_query(
        template="custom",
        columns=["timestamp", "high", "low", "close"],
        params={
            "conditions": (
                f"symbol = 'TY' AND "
                f"timestamp >= '{session_start_str} 18:00:00' AND "
                f"timestamp <= '{session_start_str} 23:59:59' AND "
                f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
            )
        },
        limit=2000
    )
    
    # Overnight + day session: midnight to 5pm on session_end_str
    result_day = run_query(
        template="custom",
        columns=["timestamp", "high", "low", "close"],
        params={
            "conditions": (
                f"symbol = 'TY' AND "
                f"timestamp >= '{session_end_str} 00:00:00' AND "
                f"timestamp <= '{session_end_str} 17:00:00' AND "
                f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
            )
        },
        limit=2000
    )
    
    rows_evening = result_evening.get("data") or result_evening.get("rows") or []
    rows_day = result_day.get("data") or result_day.get("rows") or []
    
    all_rows = rows_evening + rows_day
    if not all_rows:
        return None
    
    prices = []
    for row in all_rows:
        ts = row.get('timestamp')
        high = row.get('high')
        low = row.get('low')
        close = row.get('close')
        if ts and close:
            # Double-check price validity (belt and suspenders)
            if not is_valid_ty_price(float(close)):
                continue
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace('Z', '').replace('+00:00', ''))
                except:
                    continue
            prices.append({
                'timestamp': ts.isoformat() if isinstance(ts, datetime) else str(ts),
                'high': float(high) if high and is_valid_ty_price(float(high)) else float(close),
                'low': float(low) if low and is_valid_ty_price(float(low)) else float(close),
                'close': float(close),
            })
    
    prices.sort(key=lambda x: x['timestamp'])
    
    return prices if prices else None


def load_adhoc_trading_days(start_date: str, end_date: str) -> List[Dict]:
    """
    Load price paths for all trading days in a date range (for ad-hoc analysis).
    
    Trading day = 6pm prior day → 5pm on the selected date (23-hour futures session).
    Returns list of {date, prices: [{hours, bps, close}], candlestick_5m} for each trading day.
    
    The x-axis 'hours' is relative to session start (6pm prior day = 0h, 5pm selected day = 23h).
    """
    from datetime import datetime, timedelta
    
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    
    trading_days = []
    current = start_dt
    while current <= end_dt:
        # Skip weekends (no session starts on Sat/Sun)
        # Friday 6pm session runs into Saturday 5pm but we label by start date
        if current.weekday() < 5:  # Mon-Fri
            trading_days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    
    print(f"\nLoading adhoc price data for {len(trading_days)} trading sessions (6pm->5pm)...")
    
    adhoc_data = []
    for i, date_str in enumerate(trading_days):
        if i % 20 == 0:
            print(f"  [{i+1}/{len(trading_days)}] {date_str}...")

        prices = fetch_trading_session_prices(date_str)
        if not prices or len(prices) < 5:
            continue

        price_ref = prices[0]["close"]
        if not price_ref or price_ref <= 0:
            continue

        session_start_date = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
        session_start = datetime.strptime(
            f"{session_start_date.strftime('%Y-%m-%d')} 18:00:00",
            "%Y-%m-%d %H:%M:%S",
        )

        normalized = []
        for p in prices:
            ts_str = p.get("timestamp")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "").replace("+00:00", ""))
            except Exception:
                continue
            hours = (ts - session_start).total_seconds() / 3600.0
            bps = window_move_bps(price_ref, p["close"])
            normalized.append(
                {
                    "timestamp": ts.isoformat(),
                    "hours": hours,
                    "close": p["close"],
                    "bps": bps,
                }
            )

        if not normalized:
            continue
        adhoc_data.append({"date": date_str, "prices": normalized})

    return adhoc_data


POLYMARKET_DB_PATH = PROJECT_ROOT / "polymarket_markets.db"
PM_KIND_KEYWORDS = {
    "nfp": ("jobs", "added", "january"),
    "uer": ("unemployment", "rate", "january"),
    "cpi": ("inflation", "january"),
}
SCENARIO_COMPONENTS = {"nfp", "uer", "cpi"}
SCENARIO_COMPONENT_EVENT_NAMES = {
    "nfp": ("NFP",),
    "uer": ("UER",),
    # DB labels can vary by ingestion pipeline.
    "cpi": ("CPI", "Core CPI MoM", "Core Inflation Rate MoM"),
}


def _dashboard_cache_path(event_type: str) -> Path:
    return OUTPUT_DIR / f"live_unified_{event_type.lower()}_cache.json"


def load_cached_dashboard_data(event_type: str) -> Optional[Dict]:
    cache_path = _dashboard_cache_path(event_type)
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_dashboard_cache_raw(event_type: str) -> Optional[Dict]:
    return load_cached_dashboard_data(event_type)


def save_dashboard_cache(event_type: str, dashboard_data: Dict) -> None:
    cache_path = _dashboard_cache_path(event_type)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(dashboard_data, ensure_ascii=True), encoding="utf-8")


def _get_latest_dashboard_file(event_type: str, html_path: str) -> Path:
    direct = Path(html_path)
    if direct.exists():
        # Only use direct path when it matches requested event type.
        # This keeps /cpi, /nfp, etc. serving their own HTML files.
        expected_tag = f"live_unified_{event_type.lower()}_dashboard_"
        if expected_tag in direct.name.lower():
            return direct
    candidates = sorted(
        OUTPUT_DIR.glob(f"live_unified_{event_type.lower()}_dashboard_*.html"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    # Final fallback: any live unified dashboard
    fallback = sorted(
        OUTPUT_DIR.glob("live_unified_*_dashboard_*.html"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if fallback:
        return fallback[0]
    raise FileNotFoundError(f"No dashboard HTML found for event_type={event_type}")


def _safe_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_estimate_number(value) -> Optional[float]:
    """Parse ECOS/Bloomberg estimate values like '85k' into 85.0."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            if value != value:
                return None
        except Exception:
            pass
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "n/a"}:
        return None
    multiplier = 1.0
    if text.lower().endswith("k"):
        text = text[:-1]
        multiplier = 1.0
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _parse_rank_value(value) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text == "nan":
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def _percentile_rank(values: List[float], target: Optional[float]) -> Optional[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals or target is None:
        return None
    below_or_equal = sum(1 for v in vals if v <= target)
    return (below_or_equal / len(vals)) * 100.0


def _weighted_average(pairs: List[tuple[float, float]]) -> Optional[float]:
    total_w = sum(w for _, w in pairs if w > 0)
    if total_w <= 0:
        return None
    return sum(v * w for v, w in pairs if w > 0) / total_w


def parse_ecos_estimates(source) -> Dict:
    """Parse a Bloomberg ECOS estimate export for the current event."""
    from io import BytesIO

    import pandas as pd

    excel_source = BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    df = pd.read_excel(excel_source, sheet_name=0, header=None)

    summary = {}
    for _, row in df.iterrows():
        label = str(row.iloc[1]).strip() if len(row) > 1 and row.iloc[1] == row.iloc[1] else ""
        if not label:
            continue
        key = label.lower().replace(" ", "_").replace("/", "_")
        if key in {
            "release_date",
            "time",
            "country_region",
            "event",
            "period",
            "actual",
            "prior",
            "revised",
            "median_estimate",
            "average_estimate",
            "high_estimate",
            "low_estimate",
            "number_of_estimates",
            "qualified_economists",
            "standard_deviation",
            "custom_estimate",
        }:
            summary[key] = row.iloc[2] if len(row) > 2 else None

    estimates = []
    header_idx = None
    for idx, row in df.iterrows():
        if str(row.iloc[1]).strip().lower() == "economist" and str(row.iloc[2]).strip().lower() == "firm":
            header_idx = idx
            break
    if header_idx is not None:
        for _, row in df.iloc[header_idx + 1 :].iterrows():
            estimate = _parse_estimate_number(row.iloc[3] if len(row) > 3 else None)
            firm = None if len(row) <= 2 or str(row.iloc[2]).strip().lower() == "nan" else str(row.iloc[2]).strip()
            economist = None if len(row) <= 1 or str(row.iloc[1]).strip().lower() == "nan" else str(row.iloc[1]).strip()
            if estimate is None and not firm and not economist:
                continue
            as_of_raw = row.iloc[4] if len(row) > 4 else None
            as_of = None
            try:
                if as_of_raw == as_of_raw:
                    as_of = pd.to_datetime(as_of_raw).strftime("%Y-%m-%d")
            except Exception:
                as_of = str(as_of_raw) if as_of_raw is not None else None
            estimates.append(
                {
                    "economist": economist,
                    "firm": firm,
                    "estimate": estimate,
                    "as_of": as_of,
                    "rank": _parse_rank_value(row.iloc[5] if len(row) > 5 else None),
                }
            )

    estimate_values = [e["estimate"] for e in estimates if e.get("estimate") is not None]
    prior = _parse_estimate_number(summary.get("prior"))
    median = _parse_estimate_number(summary.get("median_estimate"))
    average = _parse_estimate_number(summary.get("average_estimate"))
    high = _parse_estimate_number(summary.get("high_estimate"))
    low = _parse_estimate_number(summary.get("low_estimate"))
    std_dev = _parse_estimate_number(summary.get("standard_deviation"))
    n_estimates = _parse_estimate_number(summary.get("number_of_estimates"))

    ranked_pairs = []
    for est in estimates:
        if est.get("estimate") is None or est.get("rank") is None:
            continue
        ranked_pairs.append((float(est["estimate"]), 1.0 / max(1, int(est["rank"]))))

    as_of_dates = []
    for est in estimates:
        if est.get("as_of"):
            try:
                as_of_dates.append(pd.to_datetime(est["as_of"]))
            except Exception:
                pass
    max_as_of = max(as_of_dates) if as_of_dates else None
    recency_pairs = []
    if max_as_of is not None:
        for est in estimates:
            if est.get("estimate") is None or not est.get("as_of"):
                continue
            try:
                days_old = max(0, (max_as_of - pd.to_datetime(est["as_of"])).days)
                recency_pairs.append((float(est["estimate"]), 1.0 / (1.0 + days_old)))
            except Exception:
                continue

    expected_change = (median - prior) if median is not None and prior is not None else None
    return {
        "summary": {
            "release_date": str(summary.get("release_date")) if summary.get("release_date") is not None else None,
            "time": str(summary.get("time")) if summary.get("time") is not None else None,
            "event": str(summary.get("event")) if summary.get("event") is not None else None,
            "period": str(summary.get("period")) if summary.get("period") is not None else None,
            "prior": prior,
            "median": median,
            "average": average,
            "high": high,
            "low": low,
            "std_dev": std_dev,
            "n_estimates": int(n_estimates) if n_estimates is not None else len(estimate_values),
            "expected_change": expected_change,
            "prior_percentile": _percentile_rank(estimate_values, prior),
            "rank_weighted_consensus": _weighted_average(ranked_pairs),
            "recency_weighted_consensus": _weighted_average(recency_pairs),
        },
        "estimates": estimates,
    }


def _interpolate_numeric(xs: List[float], ys: List[float], x: float) -> Optional[float]:
    if not xs or not ys or len(xs) != len(ys):
        return None
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if xs[i] >= x:
            x0, x1 = xs[i - 1], xs[i]
            y0, y1 = ys[i - 1], ys[i]
            if x1 == x0:
                return y1
            w = (x - x0) / (x1 - x0)
            return y0 + (y1 - y0) * w
    return None


def _median_sorted(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    vals = sorted(vals)
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def _linear_fit(xs: List[float], ys: List[float]) -> Dict:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return {"n": len(pairs), "slope": None, "intercept": None, "corr": None, "r2": None}
    x_vals = [p[0] for p in pairs]
    y_vals = [p[1] for p in pairs]
    x_mean = sum(x_vals) / len(x_vals)
    y_mean = sum(y_vals) / len(y_vals)
    sxx = sum((x - x_mean) ** 2 for x in x_vals)
    syy = sum((y - y_mean) ** 2 for y in y_vals)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
    slope = sxy / sxx if sxx else None
    intercept = y_mean - slope * x_mean if slope is not None else None
    corr = sxy / ((sxx * syy) ** 0.5) if sxx > 0 and syy > 0 else None
    return {
        "n": len(pairs),
        "slope": slope,
        "intercept": intercept,
        "corr": corr,
        "r2": corr * corr if corr is not None else None,
    }


def _aggregate_impulse_paths(records: List[Dict], step_min: int = 30) -> List[Dict]:
    if not records:
        return []
    max_min = max((pt["t_min"] for r in records for pt in r.get("path", [])), default=0)
    grid = list(range(0, int(max_min) + 1, step_min))
    if grid and grid[-1] < max_min:
        grid.append(int(max_min))
    out = []
    for t in grid:
        vals = []
        for rec in records:
            pts = rec.get("path", [])
            xs = [p["t_min"] for p in pts]
            ys = [p["bps"] for p in pts]
            v = _interpolate_numeric(xs, ys, t)
            if v is not None:
                vals.append(v)
        vals_sorted = sorted(vals)
        if not vals_sorted:
            continue
        out.append(
            {
                "t_min": t,
                "mean": sum(vals_sorted) / len(vals_sorted),
                "median": _median_sorted(vals_sorted),
                "p25": percentile(vals_sorted, 25),
                "p75": percentile(vals_sorted, 75),
                "count": len(vals_sorted),
            }
        )
    return out


def build_impulse_history(event_type: str, start_day: int = -2, start_hhmm: str = "15:00") -> Dict:
    event_type = (event_type or "NFP").upper()
    cached = load_cached_dashboard_data(event_type)
    if cached is None:
        raw = load_event_data(event_type)
        cached = build_dashboard_data(raw, event_type=event_type)
    events = [e for e in cached.get("events", []) if e.get("date")]
    events.sort(key=lambda e: e.get("date", ""))

    try:
        start_h, start_m = [int(x) for x in (start_hhmm or "15:00").split(":", 1)]
    except Exception:
        start_h, start_m = 15, 0

    from scripts.l1_event_store import load_event_meta, load_price_table, slice_by_ts_range

    records = []
    prev_actual = None
    for ev in events:
        actual = _safe_float(ev.get("nfp_actual") or ev.get("actual"))
        consensus = _safe_float(ev.get("nfp_cons") or ev.get("consensus"))
        event_date = ev.get("date")
        if prev_actual is None or consensus is None or not event_date:
            prev_actual = actual if actual is not None else prev_actual
            continue
        expected_change = consensus - prev_actual
        try:
            meta = load_event_meta(event_type, event_date)
            event_dt = datetime.fromisoformat(str(meta.get("event_ts_et")))
            if event_dt.tzinfo is None:
                from zoneinfo import ZoneInfo
                event_dt = event_dt.replace(tzinfo=ZoneInfo("America/New_York"))
            start_dt = (event_dt + timedelta(days=start_day)).replace(
                hour=start_h, minute=start_m, second=0, microsecond=0
            )
            start_ns = int(start_dt.timestamp() * 1_000_000_000)
            end_ns = int(event_dt.timestamp() * 1_000_000_000)
            table = load_price_table(event_type, event_date, "1m")
            sliced = slice_by_ts_range(table, start_ns, end_ns)
            if sliced.num_rows < 2:
                prev_actual = actual if actual is not None else prev_actual
                continue
            cols = {name: sliced.column(name).to_pylist() for name in sliced.schema.names}
            ts_ns = cols.get("ts_et_ns") or []
            closes = cols.get("close") or cols.get("close_dec") or []
            base = _safe_float(closes[0]) if closes else None
            if base is None or base <= 0:
                prev_actual = actual if actual is not None else prev_actual
                continue
            path = []
            for ts_val, close in zip(ts_ns, closes):
                px = _safe_float(close)
                if px is None:
                    continue
                t_min = (int(ts_val) - start_ns) / 60_000_000_000
                path.append({"t_min": round(t_min, 2), "bps": window_move_bps(base, px)})
            if len(path) < 2:
                prev_actual = actual if actual is not None else prev_actual
                continue
            drift_bps = path[-1]["bps"] - path[0]["bps"]
            records.append(
                {
                    "date": event_date,
                    "actual": actual,
                    "prior": prev_actual,
                    "consensus": consensus,
                    "expected_change": expected_change,
                    "cons_std": _safe_float(ev.get("cons_std")),
                    "drift_bps": drift_bps,
                    "path": path,
                }
            )
        except Exception:
            pass
        prev_actual = actual if actual is not None else prev_actual

    fit = _linear_fit(
        [r["expected_change"] for r in records],
        [r["drift_bps"] for r in records],
    )
    drift_vals = sorted(r["drift_bps"] for r in records if r.get("drift_bps") is not None)
    return {
        "event_type": event_type,
        "start_day": start_day,
        "start_hhmm": f"{start_h:02d}:{start_m:02d}",
        "records": records,
        "aggregate_path": _aggregate_impulse_paths(records),
        "fit": fit,
        "stats": {
            "n": len(records),
            "mean_drift": (sum(drift_vals) / len(drift_vals)) if drift_vals else None,
            "median_drift": _median_sorted(drift_vals),
            "rally_rate": (sum(1 for v in drift_vals if v > 0) / len(drift_vals)) if drift_vals else None,
            "selloff_rate": (sum(1 for v in drift_vals if v < 0) / len(drift_vals)) if drift_vals else None,
        },
    }


def _impulse_percentile_rank(sorted_values: List[float], value: Optional[float]) -> Optional[float]:
    if value is None or not sorted_values:
        return None
    import bisect

    return (bisect.bisect_right(sorted_values, value) / len(sorted_values)) * 100.0


def _summarize_metric(values: List[float]) -> Dict:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"count": 0, "mean": None, "median": None}
    return {
        "count": len(vals),
        "mean": sum(vals) / len(vals),
        "median": _median_sorted(vals),
    }


def _bucket_index(percentile_value: Optional[float], bucket_width: float) -> Optional[int]:
    if percentile_value is None or bucket_width <= 0:
        return None
    idx = int(min(99.999, max(0.0, percentile_value)) // bucket_width)
    return max(0, idx)


def _window_metrics_from_table(table, start_ns: int, end_ns: int) -> Optional[Dict]:
    from scripts.l1_event_store import slice_by_ts_range

    sliced = slice_by_ts_range(table, start_ns, end_ns)
    if sliced.num_rows < 2:
        return None
    cols = {name: sliced.column(name).to_pylist() for name in sliced.schema.names}
    opens = cols.get("open") or cols.get("open_dec") or []
    highs = cols.get("high") or cols.get("high_dec") or []
    lows = cols.get("low") or cols.get("low_dec") or []
    closes = cols.get("close") or cols.get("close_dec") or []
    base = _safe_float(opens[0] if opens else closes[0] if closes else None)
    last = _safe_float(closes[-1] if closes else None)
    if base is None or last is None:
        return None
    high_vals = [_safe_float(v) for v in highs]
    low_vals = [_safe_float(v) for v in lows]
    high_vals = [v for v in high_vals if v is not None]
    low_vals = [v for v in low_vals if v is not None]
    if not high_vals or not low_vals:
        return None
    max_extent_points = max(abs(max(high_vals) - base), abs(base - min(low_vals)))
    return {
        "signed_bps": window_move_bps(base, last),
        "max_bps": decimal_to_bps(max_extent_points),
        "count": sliced.num_rows,
    }


def build_surprise_bucket_stats(
    event_type: str,
    pre_start_day: int = -2,
    pre_start_hhmm: str = "15:00",
    bucket_width: float = 20.0,
) -> Dict:
    event_type = (event_type or "NFP").upper()
    bucket_width = max(1.0, min(100.0, float(bucket_width or 20.0)))
    cached = load_cached_dashboard_data(event_type)
    if cached is None:
        raw = load_event_data(event_type)
        cached = build_dashboard_data(raw, event_type=event_type)
    events = [e for e in cached.get("events", []) if e.get("date")]
    events.sort(key=lambda e: e.get("date", ""))

    # First pass: standardized surprises and prior reconstructed as lag-1 actual.
    records_base = []
    prev_actual = None
    for ev in events:
        actual = _safe_float(ev.get("nfp_actual") or ev.get("actual"))
        consensus = _safe_float(ev.get("nfp_cons") or ev.get("consensus"))
        cons_std = _safe_float(ev.get("cons_std"))
        event_date = ev.get("date")
        if prev_actual is None or actual is None or consensus is None or cons_std is None or cons_std <= 0 or not event_date:
            if actual is not None:
                prev_actual = actual
            continue
        std_surprise = (actual - consensus) / cons_std
        records_base.append(
            {
                "date": event_date,
                "actual": actual,
                "prior": prev_actual,
                "consensus": consensus,
                "cons_std": cons_std,
                "std_surprise": std_surprise,
            }
        )
        prev_actual = actual

    std_surprises = sorted(r["std_surprise"] for r in records_base if r.get("std_surprise") is not None)
    try:
        start_h, start_m = [int(x) for x in (pre_start_hhmm or "15:00").split(":", 1)]
    except Exception:
        start_h, start_m = 15, 0
    pre_start_day = max(-4, min(-1, int(pre_start_day)))

    from scripts.l1_event_store import load_event_meta, load_price_table

    records = []
    for rec in records_base:
        try:
            meta = load_event_meta(event_type, rec["date"])
            event_dt = datetime.fromisoformat(str(meta.get("event_ts_et")))
            if event_dt.tzinfo is None:
                from zoneinfo import ZoneInfo

                event_dt = event_dt.replace(tzinfo=ZoneInfo("America/New_York"))
            table = load_price_table(event_type, rec["date"], "1m")
            pre_start = (event_dt + timedelta(days=pre_start_day)).replace(
                hour=start_h, minute=start_m, second=0, microsecond=0
            )
            post_2h_end = event_dt + timedelta(hours=2)
            post_eod_end = event_dt.replace(hour=17, minute=0, second=0, microsecond=0)
            windows = {
                "pre": _window_metrics_from_table(
                    table,
                    int(pre_start.timestamp() * 1_000_000_000),
                    int(event_dt.timestamp() * 1_000_000_000),
                ),
                "post_2h": _window_metrics_from_table(
                    table,
                    int(event_dt.timestamp() * 1_000_000_000),
                    int(post_2h_end.timestamp() * 1_000_000_000),
                ),
                "post_eod": _window_metrics_from_table(
                    table,
                    int(event_dt.timestamp() * 1_000_000_000),
                    int(post_eod_end.timestamp() * 1_000_000_000),
                ),
            }
            if not any(windows.values()):
                continue
            pct_val = _impulse_percentile_rank(std_surprises, rec["std_surprise"])
            idx = _bucket_index(pct_val, bucket_width)
            if idx is None:
                continue
            bucket_min = idx * bucket_width
            bucket_max = min(100.0, bucket_min + bucket_width)
            records.append(
                {
                    **rec,
                    "surprise_pct": pct_val,
                    "bucket_idx": idx,
                    "bucket_min": bucket_min,
                    "bucket_max": bucket_max,
                    "windows": windows,
                }
            )
        except Exception:
            continue

    buckets = {}
    for rec in records:
        idx = rec["bucket_idx"]
        if idx not in buckets:
            buckets[idx] = {
                "bucket_idx": idx,
                "bucket_min": rec["bucket_min"],
                "bucket_max": rec["bucket_max"],
                "events": [],
            }
        buckets[idx]["events"].append(rec)

    bucket_rows = []
    for idx in sorted(buckets):
        bucket = buckets[idx]
        row = {
            "bucket_idx": idx,
            "bucket_min": bucket["bucket_min"],
            "bucket_max": bucket["bucket_max"],
            "count": len(bucket["events"]),
        }
        for name in ("pre", "post_2h", "post_eod"):
            signed = [
                e["windows"][name]["signed_bps"]
                for e in bucket["events"]
                if e.get("windows", {}).get(name)
            ]
            maxes = [
                e["windows"][name]["max_bps"]
                for e in bucket["events"]
                if e.get("windows", {}).get(name)
            ]
            row[name] = {
                "signed": _summarize_metric(signed),
                "max": _summarize_metric(maxes),
            }
        bucket_rows.append(row)

    return {
        "event_type": event_type,
        "pre_start_day": pre_start_day,
        "pre_start_hhmm": f"{start_h:02d}:{start_m:02d}",
        "bucket_width": bucket_width,
        "std_surprises": std_surprises,
        "records": records,
        "buckets": bucket_rows,
    }


def _telegram_config() -> Tuple[str, List[int]]:
    """Load Telegram bot token and allowed chat ids from bot config."""
    try:
        from scripts.telegram_bot_config import BOT_TOKEN, ALLOWED_CHAT_IDS
    except Exception:
        return ("", [])
    token = str(BOT_TOKEN or "").strip()
    chat_ids = []
    for cid in (ALLOWED_CHAT_IDS or []):
        try:
            chat_ids.append(int(cid))
        except Exception:
            continue
    return (token, chat_ids)


def _telegram_api_post_json(bot_token: str, method: str, payload: Dict) -> Dict:
    """Call Telegram Bot API endpoint using JSON body."""
    if not bot_token:
        raise RuntimeError("Telegram bot token not configured")
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram API {method} failed")
    return data


def _multipart_formdata(fields: Dict[str, str], file_field: str, filename: str, file_bytes: bytes, mime_type: str = "image/png") -> Tuple[bytes, str]:
    """Build multipart/form-data payload bytes."""
    boundary = f"----CursorFormBoundary{uuid.uuid4().hex}"
    parts: List[bytes] = []
    for key, value in (fields or {}).items():
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        parts.append(str(value).encode("utf-8"))
        parts.append(b"\r\n")
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return (b"".join(parts), f"multipart/form-data; boundary={boundary}")


def _telegram_send_photo(bot_token: str, chat_id: int, image_bytes: bytes, caption: str = "") -> Dict:
    """Send image bytes to Telegram chat via sendPhoto."""
    if not bot_token:
        raise RuntimeError("Telegram bot token not configured")
    if not image_bytes:
        raise RuntimeError("Image payload is empty")
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:1024]
    body, content_type = _multipart_formdata(fields, "photo", "dashboard.png", image_bytes, "image/png")
    req = urllib.request.Request(url=url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "ignore")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or "Telegram sendPhoto failed")
    return data


def _percentile_rank(sorted_values: List[float], value: float) -> float:
    if not sorted_values:
        return 50.0
    import bisect

    idx = bisect.bisect_right(sorted_values, value)
    return (idx / len(sorted_values)) * 100.0


def summarize_values(values: List[float]) -> Dict:
    if not values:
        return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
    values_sorted = sorted(values)
    n = len(values_sorted)
    mid = n // 2
    median = values_sorted[mid] if n % 2 == 1 else (values_sorted[mid - 1] + values_sorted[mid]) / 2.0
    return {
        "count": n,
        "mean": sum(values_sorted) / n,
        "median": median,
        "min": values_sorted[0],
        "max": values_sorted[-1],
    }


def _percentile_from_sorted(values_sorted: List[float], p: float) -> Optional[float]:
    if not values_sorted:
        return None
    if p <= 0:
        return values_sorted[0]
    if p >= 100:
        return values_sorted[-1]
    rank = (len(values_sorted) - 1) * (p / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(values_sorted) - 1)
    frac = rank - lo
    return values_sorted[lo] * (1.0 - frac) + values_sorted[hi] * frac


def compute_distro_stats(values: List[float]) -> Dict:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "p0": None,
            "p1": None,
            "p5": None,
            "p25": None,
            "p50": None,
            "p75": None,
            "p95": None,
            "p99": None,
            "p100": None,
            "p_rally": None,
            "p_selloff": None,
            "expected_rally": None,
            "expected_selloff": None,
            "pct_zero": None,
        }
    vals = sorted(float(v) for v in values)
    n = len(vals)
    mean = sum(vals) / n
    median = _percentile_from_sorted(vals, 50.0)
    pos = [v for v in vals if v > 0]
    neg = [v for v in vals if v < 0]
    p_rally = len(pos) / n
    p_selloff = len(neg) / n
    expected_rally = p_rally * ((sum(pos) / len(pos)) if pos else 0.0)
    expected_selloff = p_selloff * ((sum(neg) / len(neg)) if neg else 0.0)
    pct_zero = _percentile_rank(vals, 0.0)
    return {
        "count": n,
        "mean": mean,
        "median": median,
        "p0": _percentile_from_sorted(vals, 0.0),
        "p1": _percentile_from_sorted(vals, 1.0),
        "p5": _percentile_from_sorted(vals, 5.0),
        "p25": _percentile_from_sorted(vals, 25.0),
        "p50": _percentile_from_sorted(vals, 50.0),
        "p75": _percentile_from_sorted(vals, 75.0),
        "p95": _percentile_from_sorted(vals, 95.0),
        "p99": _percentile_from_sorted(vals, 99.0),
        "p100": _percentile_from_sorted(vals, 100.0),
        "p_rally": p_rally,
        "p_selloff": p_selloff,
        "expected_rally": expected_rally,
        "expected_selloff": expected_selloff,
        "pct_zero": pct_zero,
    }


def build_distro_stats_lines(stats: Dict) -> List[str]:
    if not stats or not stats.get("count"):
        return ["N = 0"]
    return [
        f"N = {stats['count']}",
        f"Mean = {stats['mean']:.2f} bps",
        f"Median = {stats['median']:.2f} bps",
        f"P1/P5/P25 = {stats['p1']:.2f}/{stats['p5']:.2f}/{stats['p25']:.2f} bps",
        f"P50/P75 = {stats['p50']:.2f}/{stats['p75']:.2f} bps",
        f"P95/P99 = {stats['p95']:.2f}/{stats['p99']:.2f} bps",
        f"Min/Max = {stats['p0']:.2f}/{stats['p100']:.2f} bps",
        f"Exp Rally = {stats['expected_rally']:.2f} bps (P={stats['p_rally']:.0%})",
        f"Exp Selloff = {stats['expected_selloff']:.2f} bps (P={stats['p_selloff']:.0%})",
        f"0 bps %ile = {stats['pct_zero']:.1f}",
    ]


def build_last_two_markers(rows: List[Dict]) -> List[Dict]:
    valid = [r for r in rows if r.get("timestamp") and r.get("value") is not None]
    if not valid:
        return []
    valid.sort(key=lambda r: str(r["timestamp"]))
    out = []
    for row in valid[-2:]:
        ts = str(row["timestamp"])[:10]
        val = float(row["value"])
        out.append({"value": val, "label": f"{ts} {val:+.2f} bps"})
    return out


def _parse_range_label_to_actual(label: str, component: str) -> Optional[float]:
    import re

    if not label:
        return None
    txt = label.lower().replace(",", "").strip()
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", txt)]
    if component in {"uer", "cpi"}:
        # Remove year-like numbers in labels such as "January 2026 unemployment rate".
        filtered = [n for n in nums if n <= 20]
        if filtered:
            nums = filtered
    if not nums:
        return None

    if component == "nfp":
        open_step = 25.0
    else:
        open_step = 0.1

    if len(nums) >= 2:
        return (nums[0] + nums[1]) / 2.0

    x = nums[0]
    if any(k in txt for k in ("or fewer", "or less", "below", "under", "<")):
        return x - open_step
    if any(k in txt for k in ("or more", "above", "over", "at least", "+", ">")):
        return x + open_step
    return x


def _is_cpi_monthly_label(label: str) -> bool:
    txt = str(label or "").lower()
    return "monthly inflation" in txt


def _is_cpi_annual_label(label: str) -> bool:
    txt = str(label or "").lower()
    return "annual inflation" in txt


def resolve_polymarket_markets_from_db(kind: str) -> List[Dict]:
    if not POLYMARKET_DB_PATH.exists():
        return []
    keywords = PM_KIND_KEYWORDS.get(kind, ())
    conn = sqlite3.connect(str(POLYMARKET_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT condition_id, market_slug, title, yes_token_id, no_token_id, active
            FROM tracked_markets
            WHERE active = 1
            """
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    if not rows:
        return []

    scored = []
    for row in rows:
        text = f"{(row['title'] or '')} {(row['market_slug'] or '')}".lower()
        score = sum(1 for kw in keywords if kw in text)
        if score >= max(1, len(keywords) - 1):
            scored.append((score, dict(row)))
    scored.sort(key=lambda t: (t[0], len((t[1].get("title") or ""))), reverse=True)
    return [t[1] for t in scored]


def _try_fetch_market_metadata(condition_id: str, query_text: str) -> Dict[str, str]:
    from src.servers.polymarket.search_markets import call_polymarket_search_api, parse_public_search_response

    try:
        resp = call_polymarket_search_api(query_text or condition_id, limit_per_type=15)
        markets = parse_public_search_response(resp, query_text or condition_id)
    except Exception:
        return {}

    for m in markets:
        if str(m.get("market_id") or "") != str(condition_id):
            continue
        token_ids = m.get("clob_token_ids") or []
        outcomes = m.get("outcomes") or []
        mapping = {}
        for idx, tid in enumerate(token_ids):
            mapping[str(tid)] = str(outcomes[idx]) if idx < len(outcomes) else f"Outcome {idx + 1}"
        return mapping
    return {}


def get_latest_mid_by_token_ids(token_ids: List[str]) -> List[Dict]:
    if not POLYMARKET_DB_PATH.exists() or not token_ids:
        return []
    conn = sqlite3.connect(str(POLYMARKET_DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join(["?"] * len(token_ids))
        rows = conn.execute(
            f"""
            SELECT token_id, observed_ts, mid
            FROM market_prob_timeseries
            WHERE token_id IN ({placeholders})
              AND mid IS NOT NULL
            ORDER BY observed_ts DESC
            """,
            token_ids,
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    latest = {}
    for row in rows:
        tid = str(row["token_id"])
        if tid in latest:
            continue
        latest[tid] = {
            "token_id": tid,
            "mid": _safe_float(row["mid"]),
            "observed_ts": row["observed_ts"],
        }
    return list(latest.values())


def get_polymarket_outcomes_with_latest_mid(kind: str) -> List[Dict]:
    markets = resolve_polymarket_markets_from_db(kind)
    if not markets:
        return []

    token_ids = []
    for m in markets:
        yes = m.get("yes_token_id")
        if yes:
            token_ids.append(str(yes))
    latest_rows = get_latest_mid_by_token_ids(token_ids)
    latest_by_id = {str(r["token_id"]): r for r in latest_rows}

    out = []
    for m in markets:
        token_id = str(m.get("yes_token_id") or "")
        if not token_id:
            continue
        cid = str(m.get("condition_id") or "")
        row = latest_by_id.get(token_id)
        # In this workflow each condition is itself one range bucket market.
        # Use market title as bucket label (not binary Yes/No).
        label = m.get("title") or m.get("market_slug") or token_id
        out.append(
            {
                "token_id": token_id,
                "condition_id": cid,
                "market_slug": m.get("market_slug"),
                "label": label,
                "mid": row.get("mid") if row else None,
                "observed_ts": row.get("observed_ts") if row else None,
            }
        )

    def _sort_key(item: Dict):
        scenario_component = kind if kind in SCENARIO_COMPONENTS else "uer"
        val = _parse_range_label_to_actual(item.get("label") or "", scenario_component)
        return float("inf") if val is None else val

    out.sort(key=_sort_key)
    return out


def _load_component_surprise_series(component: str) -> List[float]:
    event_names = SCENARIO_COMPONENT_EVENT_NAMES.get(component, ("NFP",))
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in event_names)
        rows = conn.execute(
            f"""
            SELECT actual, consensus, consensus_std_dev
            FROM bbg_data
            WHERE event_name IN ({placeholders})
              AND timestamp >= '2022-01-01'
              AND actual IS NOT NULL
              AND consensus IS NOT NULL
              AND consensus_std_dev IS NOT NULL
            """,
            event_names,
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    out = []
    for r in rows:
        a = _safe_float(r["actual"])
        c = _safe_float(r["consensus"])
        s = _safe_float(r["consensus_std_dev"])
        if a is None or c is None or s is None or s <= 0:
            continue
        out.append((a - c) / s)
    return sorted(out)


def _fetch_ty_rows_between(start_ts: str, end_ts: str) -> List[Dict]:
    result = run_query(
        template="custom",
        columns=["timestamp", "high", "low", "close"],
        params={
            "conditions": (
                f"symbol = 'TY' AND "
                f"timestamp >= '{start_ts}' AND "
                f"timestamp <= '{end_ts}' AND "
                f"close >= {TY_PRICE_MIN} AND close <= {TY_PRICE_MAX}"
            )
        },
        limit=10000,
    )
    rows = result.get("data") or result.get("rows") or []
    out = []
    for row in rows:
        ts = row.get("timestamp")
        close = _safe_float(row.get("close"))
        high = _safe_float(row.get("high")) or close
        low = _safe_float(row.get("low")) or close
        if not ts or close is None:
            continue
        out.append({"timestamp": str(ts), "close": close, "high": high, "low": low})
    out.sort(key=lambda r: r["timestamp"])
    return out


def _compute_pre_post_from_reference(event_ts: datetime) -> Tuple[Optional[float], Optional[float]]:
    prev_day = (event_ts - timedelta(days=1)).strftime("%Y-%m-%d")
    event_day = event_ts.strftime("%Y-%m-%d")
    rows = _fetch_ty_rows_between(f"{prev_day} 15:00:00", f"{event_day} 15:00:00")
    if not rows:
        return (None, None)

    open_px = rows[0]["close"]
    event_iso_space = event_ts.strftime("%Y-%m-%d %H:%M:%S")
    pre_high = open_px
    pre_low = open_px
    post_high = open_px
    post_low = open_px
    seen_post = False
    for row in rows:
        ts = row["timestamp"]
        # Pre-event window is strictly up to 8:29:59 (exclude 8:30 print).
        if ts < event_iso_space:
            pre_high = max(pre_high, row["high"])
            pre_low = min(pre_low, row["low"])
        # Post-event window starts at 8:30:00.
        if ts >= event_iso_space:
            seen_post = True
            post_high = max(post_high, row["high"])
            post_low = min(post_low, row["low"])

    pre_be = max(abs(pre_high - open_px), abs(open_px - pre_low))
    pre_bps = window_move_bps(open_px, open_px + pre_be)
    if not seen_post:
        return (pre_bps, None)
    post_be = max(abs(post_high - open_px), abs(open_px - post_low))
    post_bps = window_move_bps(open_px, open_px + post_be)
    return (pre_bps, post_bps)


def _component_event_metrics(component: str) -> List[Dict]:
    # Fast path: reuse precomputed unified dashboard cache (contains per-event
    # surprise + pre/post B/E multipliers and fwd).
    cached = load_cached_dashboard_data("NFP")
    if component in {"nfp", "uer"} and cached and isinstance(cached.get("events"), list):
        out = []
        for ev in cached.get("events", []):
            date = ev.get("date")
            if not date or date < "2022-01-01":
                continue
            fwd = _safe_float(ev.get("fwd")) or 0.0
            std = _safe_float(ev.get("standardized_surprise")) if component == "nfp" else _safe_float(ev.get("uer_std_surprise"))
            if std is None:
                continue
            pre_mult = _safe_float(ev.get("pre_be"))
            post_mult = _safe_float(ev.get("post_be"))
            pre_bps = pre_mult * fwd if pre_mult is not None and fwd > 0 else None
            post_bps = post_mult * fwd if post_mult is not None and fwd > 0 else None
            out.append(
                {
                    "timestamp": f"{date}T08:30:00",
                    "std_surprise": std,
                    "pre_bps": pre_bps,
                    "post_bps": post_bps,
                }
            )
        if out:
            return out

    # Fallback path (slower): derive from bbg_data + TY windows.
    event_names = SCENARIO_COMPONENT_EVENT_NAMES.get(component, ("NFP",))
    if not ECONOMIC_EVENTS_DB.exists():
        return []
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in event_names)
        rows = conn.execute(
            f"""
            SELECT timestamp, actual, consensus, consensus_std_dev
            FROM bbg_data
            WHERE event_name IN ({placeholders})
              AND timestamp >= '2022-01-01'
              AND actual IS NOT NULL
              AND consensus IS NOT NULL
              AND consensus_std_dev IS NOT NULL
            ORDER BY timestamp ASC
            """,
            event_names,
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    metrics = []
    for r in rows:
        ts_str = str(r["timestamp"])
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "").replace("+00:00", ""))
        except Exception:
            continue
        a = _safe_float(r["actual"])
        c = _safe_float(r["consensus"])
        s = _safe_float(r["consensus_std_dev"])
        if a is None or c is None or s is None or s <= 0:
            continue
        std_surprise = (a - c) / s
        pre_bps, post_bps = _compute_pre_post_from_reference(ts)
        metrics.append(
            {
                "timestamp": ts_str,
                "std_surprise": std_surprise,
                "pre_bps": pre_bps,
                "post_bps": post_bps,
            }
        )
    return metrics


def _bucket_bounds_from_percentile(p: float, width: float = 10.0) -> Tuple[float, float]:
    half = width / 2.0
    return (max(0.0, p - half), min(100.0, p + half))


def _bucket_be_stats(component: str, bucket_min: float, bucket_max: float) -> Tuple[Optional[float], Optional[float]]:
    hist_series = _load_component_surprise_series(component)
    metrics = _component_event_metrics(component)
    if not hist_series or not metrics:
        return (None, None)
    pre_vals = []
    post_vals = []
    for m in metrics:
        p = _percentile_rank(hist_series, m["std_surprise"])
        if bucket_min <= p <= bucket_max:
            if m["pre_bps"] is not None:
                pre_vals.append(m["pre_bps"])
            if m["post_bps"] is not None:
                post_vals.append(m["post_bps"])
    pre_mean = (sum(pre_vals) / len(pre_vals)) if pre_vals else None
    post_mean = (sum(post_vals) / len(post_vals)) if post_vals else None
    return (pre_mean, post_mean)


def get_latest_component_defaults() -> Dict:
    defaults = {
        "nfp_consensus": 150.0,
        "nfp_std": 40.0,
        "uer_consensus": 4.0,
        "uer_std": 0.1,
        "cpi_consensus": 0.3,
        "cpi_std": 0.05,
    }
    if not ECONOMIC_EVENTS_DB.exists():
        return defaults
    conn = sqlite3.connect(str(ECONOMIC_EVENTS_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT event_name, consensus, consensus_std_dev, timestamp
            FROM bbg_data
            WHERE event_name IN ('NFP', 'UER', 'CPI', 'Core CPI MoM', 'Core Inflation Rate MoM')
              AND consensus IS NOT NULL
            ORDER BY timestamp DESC
            """
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    seen = set()
    for r in rows:
        ev = str(r["event_name"] or "").upper()
        if ev in seen:
            continue
        consensus = _safe_float(r["consensus"])
        cons_std = _safe_float(r["consensus_std_dev"])
        if consensus is None:
            continue
        if ev == "NFP":
            defaults["nfp_consensus"] = consensus
            if cons_std and cons_std > 0:
                defaults["nfp_std"] = cons_std
            seen.add(ev)
        elif ev == "UER":
            defaults["uer_consensus"] = consensus
            if cons_std and cons_std > 0:
                defaults["uer_std"] = cons_std
            seen.add(ev)
        elif ev in {"CPI", "CORE CPI MOM", "CORE INFLATION RATE MOM"}:
            defaults["cpi_consensus"] = consensus
            if cons_std and cons_std > 0:
                defaults["cpi_std"] = cons_std
            seen.add("CPI")
        if len(seen) == 3:
            break
    return defaults


def build_scenario_table(component: str, consensus: float, cons_std: float) -> Tuple[List[Dict], Dict]:
    if component == "uer":
        # UER is stored in percentage points (e.g. 4.3), not decimal (0.043).
        # Accept decimal-style inputs and normalize.
        if consensus <= 1.0:
            consensus *= 100.0
        if cons_std <= 0.02:
            cons_std *= 100.0

    market_kind = component if component in SCENARIO_COMPONENTS else "uer"
    outcomes = get_polymarket_outcomes_with_latest_mid(market_kind)
    if component == "cpi":
        # CPI scenario table is for headline MoM only.
        outcomes = [o for o in outcomes if _is_cpi_monthly_label(o.get("label"))]
    hist_series = _load_component_surprise_series(component)
    rows = []
    expected_pre = 0.0
    expected_post = 0.0
    weight_sum = 0.0
    for out in outcomes:
        label = out.get("label") or out.get("token_id")
        odds = _safe_float(out.get("mid"))
        if odds is None:
            continue
        representative_actual = _parse_range_label_to_actual(label, component)
        if representative_actual is None:
            continue
        std_surprise = (representative_actual - consensus) / cons_std
        pctl = _percentile_rank(hist_series, std_surprise)
        bucket_min, bucket_max = _bucket_bounds_from_percentile(pctl)
        pre_mean, post_mean = _bucket_be_stats(component, bucket_min, bucket_max)
        rows.append(
            {
                "token_id": out.get("token_id"),
                "actual_range_label": label,
                "actual_representative": representative_actual,
                "polymarket_odds": odds,
                "surprise_percentile": pctl,
                "bucket_min": bucket_min,
                "bucket_max": bucket_max,
                "pre_event_be_bps": pre_mean,
                "post_event_be_bps": post_mean,
            }
        )
        if pre_mean is not None:
            expected_pre += odds * pre_mean
        if post_mean is not None:
            expected_post += odds * post_mean
        weight_sum += odds

    expected = {
        "pre_event_be_bps": (expected_pre / weight_sum) if weight_sum > 0 else None,
        "post_event_be_bps": (expected_post / weight_sum) if weight_sum > 0 else None,
    }
    return rows, expected


def _window_be_for_event(timestamp_iso: str, start_h: float, end_h: float) -> Optional[float]:
    """Compute end-to-end return (last close vs first close) in bps for the
    given window around an event.  Result can be negative (selloff)."""
    try:
        event_ts = datetime.fromisoformat(str(timestamp_iso).replace("Z", "").replace("+00:00", ""))
    except Exception:
        return None

    window_start = event_ts + timedelta(hours=start_h)
    window_end = event_ts + timedelta(hours=end_h)
    if window_end < window_start:
        window_start, window_end = window_end, window_start

    rows = _fetch_ty_rows_between(
        window_start.strftime("%Y-%m-%d %H:%M:%S"),
        window_end.strftime("%Y-%m-%d %H:%M:%S"),
    )
    if not rows:
        return None
    open_px = rows[0]["close"]
    close_px = rows[-1]["close"]
    # Signed return: positive = rally, negative = selloff
    return window_move_bps(open_px, close_px)


def get_bucket_window_distribution(component: str, bucket_min: float, bucket_max: float, start_h: float, end_h: float) -> List[Dict]:
    hist_series = _load_component_surprise_series(component)
    metrics = _component_event_metrics(component)
    if not hist_series or not metrics:
        return []
    out: List[Dict] = []
    for m in metrics:
        p = _percentile_rank(hist_series, m["std_surprise"])
        if not (bucket_min <= p <= bucket_max):
            continue
        be = _window_be_for_event(m["timestamp"], start_h, end_h)
        if be is not None:
            ts = m["timestamp"]
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            out.append({"timestamp": ts, "value": be})
    return out


def _parse_ts_maybe(ts_val: str) -> Optional[datetime]:
    if ts_val is None:
        return None
    txt = str(ts_val).strip()
    if not txt:
        return None
    txt = txt.replace("Z", "").replace("+00:00", "")
    try:
        return datetime.fromisoformat(txt)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(txt[:19], fmt)
        except Exception:
            continue
    return None


def _window_path_for_event(timestamp_iso: str, start_h: float, end_h: float) -> Optional[Dict]:
    event_ts = _parse_ts_maybe(timestamp_iso)
    if event_ts is None:
        return None

    window_start = event_ts + timedelta(hours=start_h)
    window_end = event_ts + timedelta(hours=end_h)
    if window_end < window_start:
        window_start, window_end = window_end, window_start

    rows = _fetch_ty_rows_between(
        window_start.strftime("%Y-%m-%d %H:%M:%S"),
        window_end.strftime("%Y-%m-%d %H:%M:%S"),
    )
    if not rows:
        return None
    base = _safe_float(rows[0].get("close"))
    first_ts = _parse_ts_maybe(rows[0].get("timestamp"))
    if base is None or first_ts is None:
        return None

    pts = []
    for r in rows:
        px = _safe_float(r.get("close"))
        ts = _parse_ts_maybe(r.get("timestamp"))
        if px is None or ts is None:
            continue
        t_min = (ts - first_ts).total_seconds() / 60.0
        pts.append(
            {
                "ts": ts.isoformat(),
                "t_min": t_min,
                "bps": window_move_bps(base, px),
            }
        )
    if not pts:
        return None
    return {
        "date": event_ts.strftime("%Y-%m-%d"),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "points": pts,
    }


def _last_finite_from_events(events: List[Dict], keys: List[str]) -> Optional[float]:
    """Return the most recent finite value for any key."""
    for ev in reversed(events or []):
        if not isinstance(ev, dict):
            continue
        for key in keys:
            val = _safe_float(ev.get(key))
            if val is not None:
                return val
    return None


def _recommend_bucket_width_from_range(
    value_range: Optional[float],
    bucket_min: float,
    bucket_max: float,
    target_bucket_count: int = 5,
) -> Optional[float]:
    """
    Recommend a bucket width to render ~target buckets.

    The current bucket renderer includes the upper boundary bucket, so divide
    by (target-1) to keep the visible count close to target.
    """
    rng = _safe_float(value_range)
    if rng is None or rng <= 0:
        return None
    denom = max(1, int(target_bucket_count) - 1)
    width = rng / float(denom)
    return max(bucket_min, min(bucket_max, width))


def _percentile_value(sorted_vals: List[float], pct: float) -> float:
    """Linear-interpolated percentile from a sorted list."""
    if not sorted_vals:
        return 0.0
    pct = max(0.0, min(100.0, pct))
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac)


def _parse_outlier_rule(rule: Optional[str]) -> tuple[float, float]:
    """Parse an outlier rule like 'p5_p95' into (pct_min, pct_max). Defaults to (5, 95)."""
    try:
        parts = str(rule or "").lower().replace("p", "").split("_")
        if len(parts) == 2:
            lo = float(parts[0])
            hi = float(parts[1])
            if 0 <= lo < hi <= 100:
                return lo, hi
    except (ValueError, TypeError):
        pass
    return 5.0, 95.0


def filter_events_to_regime(events: List[Dict], event_type: str = "NFP") -> List[Dict]:
    """Restrict events to the current regime: date >= configured start year and
    cons_std within the configured percentile band (drops non-comparable outliers
    such as the 2020 COVID dispersion spike)."""
    if not events:
        return events
    try:
        from config.unified_dashboard_event_config import load_unified_dashboard_event_config
        cfg = load_unified_dashboard_event_config()
    except Exception:
        cfg = {}

    start_year = cfg.get("default_start_year")
    filtered = list(events)
    if start_year:
        cutoff = f"{int(start_year):04d}-01-01"
        filtered = [e for e in filtered if str(e.get("date") or "") >= cutoff]

    pct_min, pct_max = _parse_outlier_rule(cfg.get("outlier_rule_default"))
    cons_values = sorted(
        e.get("cons_std") for e in filtered if e.get("cons_std") is not None
    )
    if cons_values:
        lower = _percentile_value(cons_values, pct_min)
        upper = _percentile_value(cons_values, pct_max)
        filtered = [
            e
            for e in filtered
            if e.get("cons_std") is None or lower <= e["cons_std"] <= upper
        ]
    return filtered


def build_dashboard_data(
    events: List[Dict],
    user_cons_std: Optional[float] = None,
    user_consensus: Optional[float] = None,
    user_fwd_be: Optional[float] = None,
    user_actual: Optional[float] = None,
    event_type: str = "NFP",
    apply_regime_filter: bool = True,
) -> Dict:
    if apply_regime_filter:
        events = filter_events_to_regime(events, event_type)
    full_events = []
    event_cfg = get_event_config((event_type or "NFP").upper())
    event_defaults = get_event_defaults(event_type)
    event_time_hhmm = str(event_cfg.get("release_time") or "08:30")
    for event in events or []:
        date = event.get("date")
        if not date:
            continue
        fwd = _safe_float(event.get("fwd")) or _safe_float(event.get("fwd_be")) or 6.5
        prices_pre = fetch_preevent_prices(date) or []
        prices_post = fetch_postevent_prices(date) or []
        pre_be = None
        post_be = None
        if prices_pre:
            prev_day = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            # Pre-event B/E must be 3:00pm -> 8:29am.
            out = calculate_be_for_endtime(prices_pre, prev_day, date, 8, 29, fwd)
            pre_be = out[0] if out else None
        if prices_post:
            post_be = calculate_postevent_be(prices_post, fwd)
        full = dict(event)
        full["pre_be"] = pre_be
        full["post_be"] = post_be
        # Pre-event tab expects be_by_time/bps_by_time keyed at event time.
        if pre_be is not None:
            full["be_by_time"] = {event_time_hhmm: pre_be}
            full["bps_by_time"] = {event_time_hhmm: (pre_be * fwd) if fwd else None}
        else:
            full.setdefault("be_by_time", {})
            full.setdefault("bps_by_time", {})
        full_events.append(full)

    # Ensure percentile is populated for all event types when standardized
    # surprise is present in the source rows.
    std_vals = []
    for e in full_events:
        sval = _safe_float(e.get("standardized_surprise"))
        if sval is not None:
            std_vals.append(sval)
    std_vals.sort()
    if std_vals:
        for e in full_events:
            if e.get("surp_pct") is not None:
                continue
            std_val = _safe_float(e.get("standardized_surprise"))
            if std_val is None:
                continue
            e["surp_pct"] = calculate_surprise_percentile(std_val, std_vals)

    cons_std_stats = compute_cons_std_stats(full_events)
    max_std = _safe_float(cons_std_stats.get("max"))
    min_std = _safe_float(cons_std_stats.get("min"))
    cons_std_range = (max_std - min_std) if (max_std is not None and min_std is not None) else None
    bucket_width_default = _recommend_bucket_width_from_range(
        cons_std_range,
        bucket_min=float(event_defaults.get("bucket_min") or 0.001),
        bucket_max=float(event_defaults.get("bucket_max") or 1000.0),
        target_bucket_count=5,
    )

    inferred_cons_std = _safe_float(cons_std_stats.get("median"))
    if inferred_cons_std is None or inferred_cons_std <= 0:
        inferred_cons_std = _safe_float(event_defaults.get("cons_std_default")) or 25.0

    inferred_consensus = _last_finite_from_events(full_events, ["nfp_cons", "consensus", "uer_cons"])
    inferred_actual = _last_finite_from_events(full_events, ["nfp_actual", "actual", "uer_actual"])
    if inferred_actual is None:
        inferred_actual = inferred_consensus

    resolved_user_cons_std = _safe_float(user_cons_std)
    if resolved_user_cons_std is None or resolved_user_cons_std <= 0:
        resolved_user_cons_std = inferred_cons_std

    resolved_user_consensus = _safe_float(user_consensus)
    if resolved_user_consensus is None:
        resolved_user_consensus = inferred_consensus

    resolved_user_actual = _safe_float(user_actual)
    if resolved_user_actual is None:
        resolved_user_actual = inferred_actual

    ui_defaults = {
        "user_cons_std": resolved_user_cons_std,
        "user_consensus": resolved_user_consensus,
        "user_actual": resolved_user_actual,
        "cons_std_step": _safe_float(event_defaults.get("cons_std_step")) or 0.1,
        "cons_std_min": _safe_float(event_defaults.get("cons_std_min")) or 0.01,
        "cons_std_max": _safe_float(event_defaults.get("cons_std_max")) or 1000.0,
        "bucket_width_default": bucket_width_default or (_safe_float(event_defaults.get("bucket_width_default")) or 1.0),
        "bucket_step": _safe_float(event_defaults.get("bucket_step")) or 0.1,
        "bucket_min": _safe_float(event_defaults.get("bucket_min")) or 0.001,
        "bucket_max": _safe_float(event_defaults.get("bucket_max")) or 1000.0,
        "bucket_precision": int(event_defaults.get("bucket_precision") or 0),
    }

    return {
        "event_type": event_type,
        "events": full_events,
        "cons_std_stats": cons_std_stats,
        "all_std_surprises": extract_std_surprises(full_events),
        "user_cons_std": resolved_user_cons_std,
        "user_consensus": resolved_user_consensus,
        "user_fwd_be": user_fwd_be,
        "user_actual": resolved_user_actual,
        "ui_defaults": ui_defaults,
    }


def generate_html_dashboard(
    dashboard_data: Dict,
    output_path: Path,
    show_live: bool = True,
    embed_data: bool = True,
    embed_plotly: bool = False,
    preevent_only: bool = False,
) -> str:
    event_type = (dashboard_data.get("event_type") or "NFP").upper()
    event_defaults = get_event_defaults(event_type)
    ui_defaults = dashboard_data.get("ui_defaults", {}) if isinstance(dashboard_data.get("ui_defaults"), dict) else {}
    hist_events = dashboard_data.get("events", []) if isinstance(dashboard_data.get("events"), list) else []
    cons_stats = dashboard_data.get("cons_std_stats", {}) if isinstance(dashboard_data.get("cons_std_stats"), dict) else {}
    hist_cons_std_median = _safe_float(cons_stats.get("median"))
    hist_consensus = _last_finite_from_events(hist_events, ["nfp_cons", "consensus", "uer_cons"])
    hist_actual = _last_finite_from_events(hist_events, ["nfp_actual", "actual", "uer_actual"])

    user_cons_std_numeric = (
        _safe_float(dashboard_data.get("user_cons_std"))
        or _safe_float(ui_defaults.get("user_cons_std"))
        or hist_cons_std_median
        or _safe_float(event_defaults.get("cons_std_default"))
        or 25.0
    )
    user_consensus_numeric = (
        _safe_float(dashboard_data.get("user_consensus"))
        or _safe_float(ui_defaults.get("user_consensus"))
        or hist_consensus
        or 0.0
    )
    user_actual_numeric = (
        _safe_float(dashboard_data.get("user_actual"))
        or _safe_float(ui_defaults.get("user_actual"))
        or hist_actual
        or user_consensus_numeric
    )
    std_surprises = sorted(
        [float(v) for v in (dashboard_data.get("all_std_surprises") or []) if _safe_float(v) is not None]
    )
    user_std_surprise = None
    if user_cons_std_numeric and user_cons_std_numeric > 0:
        user_std_surprise = (user_actual_numeric - user_consensus_numeric) / user_cons_std_numeric
    default_surprise_percentile = (
        _percentile_rank(std_surprises, user_std_surprise)
        if user_std_surprise is not None and std_surprises
        else 50.0
    )
    default_post_bucket_width = 20.0
    user_fwd_numeric = _safe_float(dashboard_data.get("user_fwd_be")) or 6.5
    cons_std_step = _safe_float(ui_defaults.get("cons_std_step")) or _safe_float(event_defaults.get("cons_std_step")) or 0.1
    cons_std_min = _safe_float(ui_defaults.get("cons_std_min")) or _safe_float(event_defaults.get("cons_std_min")) or 0.01
    cons_std_max = _safe_float(ui_defaults.get("cons_std_max")) or _safe_float(event_defaults.get("cons_std_max")) or 1000.0
    bucket_width_default = _safe_float(ui_defaults.get("bucket_width_default"))
    if bucket_width_default is None:
        max_std = _safe_float(cons_stats.get("max"))
        min_std = _safe_float(cons_stats.get("min"))
        cons_range = (max_std - min_std) if (max_std is not None and min_std is not None) else None
        bucket_width_default = _recommend_bucket_width_from_range(
            cons_range,
            bucket_min=_safe_float(event_defaults.get("bucket_min")) or 0.001,
            bucket_max=_safe_float(event_defaults.get("bucket_max")) or 1000.0,
            target_bucket_count=5,
        )
    bucket_width_default = bucket_width_default or _safe_float(event_defaults.get("bucket_width_default")) or 1.0
    bucket_step = _safe_float(ui_defaults.get("bucket_step")) or _safe_float(event_defaults.get("bucket_step")) or 0.1
    bucket_min = _safe_float(ui_defaults.get("bucket_min")) or _safe_float(event_defaults.get("bucket_min")) or 0.001
    bucket_max = _safe_float(ui_defaults.get("bucket_max")) or _safe_float(event_defaults.get("bucket_max")) or 1000.0
    bucket_precision = int(ui_defaults.get("bucket_precision") or event_defaults.get("bucket_precision") or 0)
    event_cfg = get_event_config(event_type)
    event_time = str(event_cfg.get("release_time") or "08:30")
    post_end_time = str(event_cfg.get("post_event_end_time") or "15:00")
    try:
        _eh, _em = event_time.split(":", 1)
        event_time_hours = int(_eh) + (int(_em) / 60.0)
    except Exception:
        event_time_hours = 8.5

    pre_html = preevent_tab.generate_preevent_tab_html(
        {
            "event_short_name": event_type,
            "show_live": show_live,
            "user_fwd_be": user_fwd_numeric,
            "user_cons_std": user_cons_std_numeric,
            "cons_std_step": cons_std_step,
            "cons_std_min": cons_std_min,
            "cons_std_max": cons_std_max,
            "bucket_width_default": bucket_width_default,
            "bucket_step": bucket_step,
            "bucket_min": bucket_min,
            "bucket_max": bucket_max,
            "event_time": event_time,
            "event_time_hours": event_time_hours,
        }
    )
    post_html = postevent_tab.generate_postevent_tab_html(
        {
            "user_fwd_be": user_fwd_numeric,
            "default_surprise_percentile": default_surprise_percentile,
            "default_post_bucket_width": default_post_bucket_width,
        }
    )
    adhoc_html = adhoc_tab.generate_adhoc_tab_html({"event_short_name": event_type})
    weekmatch_html = weekmatch_tab.generate_weekmatch_tab_html({})
    impulse_html = impulse_tab.generate_impulse_tab_html({"event_type": event_type})
    pm_defaults = get_latest_component_defaults()
    pm_html = polymarket_scenarios_tab.generate_polymarket_scenarios_tab_html(
        {
            "event_type": event_type,
            "default_nfp_consensus": pm_defaults["nfp_consensus"],
            "default_nfp_std": pm_defaults["nfp_std"],
            "default_uer_consensus": pm_defaults["uer_consensus"],
            "default_uer_std": pm_defaults["uer_std"],
            "default_cpi_consensus": pm_defaults["cpi_consensus"],
            "default_cpi_std": pm_defaults["cpi_std"],
        }
    )

    events_json = json.dumps(dashboard_data.get("events", []))
    cfg_json = json.dumps(
        {
            "event_type": event_type,
            "cons_std_stats": dashboard_data.get("cons_std_stats", {}),
            "bucket_precision": bucket_precision,
            "cons_std_step": cons_std_step,
            "cons_std_min": cons_std_min,
            "cons_std_max": cons_std_max,
            "bucket_width_default": bucket_width_default,
            "bucket_step": bucket_step,
            "bucket_min": bucket_min,
            "bucket_max": bucket_max,
            "user_consensus": user_consensus_numeric,
            "user_actual": user_actual_numeric,
            "default_surprise_percentile": default_surprise_percentile,
            "default_post_bucket_width": default_post_bucket_width,
            "all_std_surprises": dashboard_data.get("all_std_surprises", []),
            "event_preselected": dashboard_data.get("event_preselected", False),
        }
    )
    post_btn = '<button class="tab-btn" data-tab="posteventTab" onclick="switchTab(\'posteventTab\', this)">Post Event</button>'
    adhoc_btn = '<button class="tab-btn" data-tab="adhocTab" onclick="switchTab(\'adhocTab\', this)">Ad Hoc</button>'
    weekmatch_btn = '<button class="tab-btn" data-tab="weekmatchTab" onclick="switchTab(\'weekmatchTab\', this)">Week Match</button>'
    impulse_btn = '<button class="tab-btn" data-tab="impulseTab" onclick="switchTab(\'impulseTab\', this)">Pre-Event Impulse</button>'
    pm_btn = '<button class="tab-btn" data-tab="polymarketScenariosTab" onclick="switchTab(\'polymarketScenariosTab\', this)">Polymarket Scenarios</button>'

    pre_js = preevent_tab.generate_preevent_tab_js(
        {
            "bucket_precision": bucket_precision,
            "event_time": event_time,
            "event_time_hours": event_time_hours,
        }
    )
    post_js = postevent_tab.generate_postevent_tab_js() if not preevent_only else ""
    impulse_js = impulse_tab.generate_impulse_tab_js({"event_type": event_type}) if not preevent_only else ""
    shared_runtime_js = f"""
    (() => {{
      const cfg = window.DASHBOARD_CONFIG || {{}};
      window.events = Array.isArray(window.DASHBOARD_EVENTS) ? window.DASHBOARD_EVENTS : [];
      window.eventType = (cfg.event_type || "{event_type}").toUpperCase();
      window.consStdStats = cfg.cons_std_stats || {{}};
      window.allStdSurprises = Array.isArray(cfg.all_std_surprises) ? cfg.all_std_surprises : [];
      window.userFwdBe = Number({user_fwd_numeric});
      window.userConsStd = Number({user_cons_std_numeric});
      window.userConsensus = Number({user_consensus_numeric});
      window.userActual = Number({user_actual_numeric});
      window.bucketPrecision = Number(cfg.bucket_precision ?? 0);
      globalThis.bucketPrecision = window.bucketPrecision;
      window.headlineComponentKey = 'headline';
      window.uerComponentKey = window.eventType === 'NFP' ? 'uer' : null;
      window.selectedComponents = window.uerComponentKey ? ['headline', 'uer'] : ['headline'];
      window.componentStdDevs = {{
        headline: Number.isFinite(window.userConsStd) ? window.userConsStd : 25,
        uer: 0.1,
      }};
      window.eventComponentMap = {{
        headline: {{ label: window.eventType }},
        uer: {{ label: 'UER' }},
      }};

      window.getFilteredEvents = function() {{
        return Array.isArray(window.events) ? window.events : [];
      }};

      window.percentile = function(sortedValues, p) {{
        if (!Array.isArray(sortedValues) || !sortedValues.length) return null;
        const idx = (Math.max(0, Math.min(100, Number(p))) / 100) * (sortedValues.length - 1);
        const lo = Math.floor(idx);
        const hi = Math.ceil(idx);
        if (lo === hi) return sortedValues[lo];
        const w = idx - lo;
        return sortedValues[lo] * (1 - w) + sortedValues[hi] * w;
      }};

      window.computePercentileOfZero = function(sortedValues) {{
        if (!Array.isArray(sortedValues) || !sortedValues.length) return null;
        let below = 0;
        for (const v of sortedValues) {{
          if (v < 0) below += 1;
        }}
        return (below / sortedValues.length) * 100;
      }};

      window.gaussianKDE = function(values) {{
        const arr = (Array.isArray(values) ? values : []).filter(v => Number.isFinite(v));
        if (arr.length < 2) {{
          return function() {{ return 0; }};
        }}
        const n = arr.length;
        const mean = arr.reduce((a, b) => a + b, 0) / n;
        const variance = arr.reduce((acc, v) => acc + (v - mean) ** 2, 0) / Math.max(1, n - 1);
        const sigma = Math.sqrt(Math.max(variance, 1e-9));
        const h = 1.06 * sigma * Math.pow(n, -0.2);
        const invSqrt2Pi = 1 / Math.sqrt(2 * Math.PI);
        return function(x) {{
          if (!Number.isFinite(x) || h <= 0) return 0;
          let sum = 0;
          for (const v of arr) {{
            const z = (x - v) / h;
            sum += Math.exp(-0.5 * z * z) * invSqrt2Pi;
          }}
          return sum / (n * h);
        }};
      }};

      window.smoothSeries = function(values, windowSize) {{
        const arr = Array.isArray(values) ? values : [];
        const w = Math.max(1, Number(windowSize) || 1);
        if (w <= 1 || arr.length <= 2) return arr.slice();
        const out = [];
        for (let i = 0; i < arr.length; i += 1) {{
          let sum = 0;
          let count = 0;
          for (let j = Math.max(0, i - w + 1); j <= i; j += 1) {{
            const v = arr[j];
            if (Number.isFinite(v)) {{
              sum += v;
              count += 1;
            }}
          }}
          out.push(count ? (sum / count) : arr[i]);
        }}
        return out;
      }};

      window.interpolateAt = function(times, values, target) {{
        if (!Array.isArray(times) || !Array.isArray(values) || !times.length || !values.length) return 0;
        if (target <= times[0]) return values[0];
        if (target >= times[times.length - 1]) return values[values.length - 1];
        for (let i = 0; i < times.length - 1; i += 1) {{
          const t0 = times[i];
          const t1 = times[i + 1];
          if (target >= t0 && target <= t1) {{
            const v0 = values[i];
            const v1 = values[i + 1];
            const ratio = (target - t0) / (t1 - t0 || 1);
            return v0 + ((v1 - v0) * ratio);
          }}
        }}
        return values[values.length - 1];
      }};

      window.decimalToBps = function(priceDelta) {{
        const v = Number(priceDelta);
        if (!Number.isFinite(v)) return NaN;
        // TY convention: 1 point = 16 bps
        return v * 16.0;
      }};

      window.darkLayout = {{
        paper_bgcolor: '#0b1220',
        plot_bgcolor: '#0b1220',
        font: {{ color: '#d1d5db' }},
        xaxis: {{ gridcolor: '#1f2937', zerolinecolor: '#374151' }},
        yaxis: {{ gridcolor: '#1f2937', zerolinecolor: '#374151' }},
        margin: {{ l: 60, r: 20, t: 40, b: 50 }},
      }};

      function toBond32(px) {{
        const v = Number(px);
        if (!Number.isFinite(v)) return '---';
        const whole = Math.floor(v);
        const frac32 = (v - whole) * 32.0;
        const thirtySeconds = Math.floor(frac32 + 1e-9);
        const half = (frac32 - thirtySeconds) >= 0.5;
        const ts = String(Math.max(0, Math.min(31, thirtySeconds))).padStart(2, '0');
        return `${{whole}}-${{ts}}${{half ? '+' : ''}}`;
      }}

      window.ensureEventDetailsLoaded = async function(eventList) {{
        const list = Array.isArray(eventList) ? eventList : [];
        for (const evt of list) {{
          if (!evt || !evt.date) continue;
          if (Array.isArray(evt.prices) && Array.isArray(evt.postevent_prices)) continue;
          try {{
            const params = new URLSearchParams({{
              event_type: window.eventType,
              date: evt.date,
              pre_start_hhmm: document.getElementById('startTimeInput')?.value || '15:00',
              post_end_hhmm: document.getElementById('postEndTimeInput')?.value || '{post_end_time}',
            }});
            const resp = await fetch(`/api/event_details?${{params.toString()}}`).then(r => r.json());
            if (resp && resp.success && resp.event) {{
              Object.assign(evt, resp.event);
            }}
          }} catch (_err) {{
            // Keep going; charts will simply skip missing events.
          }}
        }}
      }};

      window.openCandlestickModal = function(_eventObj) {{
        // Keep generated mode non-blocking; full candlestick modal lives in server-mode template.
      }};

      window.refreshLiveData = async function() {{
        let live = null;
        try {{
          live = await fetch('/api/live').then(r => r.json());
        }} catch (_err) {{
          return;
        }}
        const ty = Number(live?.ty_price);
        const ref = Number(live?.ty_price_3pm ?? live?.price_ref);
        const hi = Number(live?.high_since_3pm ?? live?.high_since_ref);
        const lo = Number(live?.low_since_3pm ?? live?.low_since_ref);
        const be = Number(live?.be_realized_bps);
        const nowTs = live?.timestamp_iso ? new Date(live.timestamp_iso).toLocaleTimeString() : '--:--:--';

        const setText = (id, text) => {{
          const el = document.getElementById(id);
          if (el) el.textContent = text;
        }};

        setText('liveTimestamp', nowTs);
        setText('liveTimestampPost', nowTs);
        setText('livePrice', Number.isFinite(ty) ? ty.toFixed(6) : '---');
        setText('livePricePost', Number.isFinite(ty) ? ty.toFixed(6) : '---');
        setText('livePrice32', Number.isFinite(ty) ? toBond32(ty) : '---');
        setText('livePrice32Post', Number.isFinite(ty) ? toBond32(ty) : '---');
        setText('liveRef', Number.isFinite(ref) ? ref.toFixed(6) : '---');
        setText('liveRefPost', Number.isFinite(ref) ? ref.toFixed(6) : '---');
        setText('liveRefTimestamp', live?.ty_price_3pm_timestamp ? String(live.ty_price_3pm_timestamp).split('T')[1]?.slice(0, 8) || '--:--:--' : '--:--:--');
        setText('liveRefTimestampPost', 'Release Price');
        setText('liveHighLow', (Number.isFinite(hi) && Number.isFinite(lo)) ? `${{toBond32(hi)}} / ${{toBond32(lo)}}` : '--- / ---');
        setText('liveHighLowPost', (Number.isFinite(hi) && Number.isFinite(lo)) ? `${{toBond32(hi)}} / ${{toBond32(lo)}}` : '--- / ---');
        setText('liveBeRealized', Number.isFinite(be) ? `${{be.toFixed(2)}} bps` : '--- bps');
        setText('liveBeRealizedPost', Number.isFinite(be) ? `${{be.toFixed(2)}} bps` : '--- bps');

        const fwdPre = Number(document.getElementById('liveFwdInput')?.value || window.userFwdBe || 0);
        const fwdPost = Number(document.getElementById('liveFwdInputPost')?.value || window.userFwdBe || 0);
        const multPre = Number.isFinite(be) && fwdPre > 0 ? (be / fwdPre) : null;
        const multPost = Number.isFinite(be) && fwdPost > 0 ? (be / fwdPost) : null;
        setText('liveBeMultiplier', multPre != null ? `${{multPre.toFixed(2)}}x` : '---x');
        setText('liveBeMultiplierPost', multPost != null ? `${{multPost.toFixed(2)}}x` : '---x');
        setText('liveBeMultiplierBadgePost', multPost != null ? `LIVE: ${{multPost.toFixed(2)}}x` : 'LIVE: --x');
      }};

      function ensureHiddenInput(id, value) {{
        let el = document.getElementById(id);
        if (!el) {{
          el = document.createElement('input');
          el.type = 'hidden';
          el.id = id;
          document.body.appendChild(el);
        }}
        if (!el.value) el.value = value;
        return el;
      }}

      function ensureComponentInputs() {{
        const container = document.getElementById('componentStdDevContainer');
        if (!container) return;
        if (document.getElementById('consStdInput')) return;
        container.innerHTML = '';
        const headline = document.createElement('div');
        headline.innerHTML = `<label style="display:block; color: var(--text-secondary); font-size:0.8rem;">${{window.eventType}} Std Dev</label>
          <input id="consStdInput" type="number" step="{cons_std_step}" value="{user_cons_std_numeric}" min="{cons_std_min}" max="{cons_std_max}" style="width:110px;">`;
        container.appendChild(headline);
        if (window.uerComponentKey) {{
          const uer = document.createElement('div');
          uer.innerHTML = `<label style="display:block; color: var(--text-secondary); font-size:0.8rem;">UER Std Dev</label>
            <input id="uerStdInput" type="number" step="0.01" value="0.10" style="width:110px;">`;
          container.appendChild(uer);
        }}
      }}

      window.onConfigChange = function() {{
        if (typeof updatePreeventDashboard === 'function') updatePreeventDashboard();
        if (typeof updatePosteventDashboard === 'function') updatePosteventDashboard();
      }};

      window.__initGeneratedDashboard = function() {{
        ensureHiddenInput('eventTimeInput', '{event_time}');
        ensureHiddenInput('startTimeInput', '15:00');
        ensureHiddenInput('postEndTimeInput', '{post_end_time}');
        ensureComponentInputs();
      }};
    }})();
    """

    event_options = [str(x).upper() for x in (get_supported_events() or ["NFP", "CPI"])]
    if event_type not in event_options:
        event_options = [event_type] + event_options
    event_options_html = "".join(
        f'<option value="{ev}"{" selected" if ev == event_type else ""}>{ev}</option>' for ev in event_options
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Live Unified {event_type} Dashboard</title>
  <script src="/plotly.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
  <style>
    :root {{
      --bg-primary: #0b1220;
      --bg-secondary: #111827;
      --bg-tertiary: #1f2937;
      --text-primary: #e5e7eb;
      --text-secondary: #94a3b8;
      --border-color: #334155;
      --accent-blue: #1d9bf0;
      --accent-purple: #8b5cf6;
      --accent-green: #22c55e;
      --accent-orange: #f59e0b;
      --accent-red: #ef4444;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: Inter, Arial, sans-serif;
      background: var(--bg-primary);
      color: var(--text-primary);
      margin: 0;
    }}
    .header {{
      padding: 10px 14px;
      border-bottom: 1px solid #1f2937;
      display: flex;
      gap: 10px;
      align-items: center;
      background: #081022;
    }}
    .header label {{ color: var(--text-secondary); font-size: 0.92rem; }}
    .header select {{
      background: var(--bg-secondary);
      color: var(--text-primary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 5px 8px;
    }}
    .tabs {{
      display: flex;
      gap: 6px;
      padding: 10px 14px;
      border-bottom: 1px solid #1f2937;
      flex-wrap: wrap;
      background: #09152b;
    }}
    .tab-btn {{
      background: #111827;
      color: #d1d5db;
      border: 1px solid #374151;
      border-radius: 6px;
      padding: 6px 10px;
      cursor: pointer;
      font-weight: 600;
    }}
    .tab-btn.active {{ background: #1f2937; color: #fff; }}
    .tab-content {{ display: none; padding: 14px; }}
    .tab-content.active {{ display: block; }}

    /* Pre/Post panel styling */
    #preeventTab .controls,
    #posteventTab .controls {{
      display: grid;
      grid-template-columns: repeat(2, minmax(260px, 1fr));
      gap: 14px;
      margin-bottom: 14px;
    }}
    #preeventTab .control-group,
    #posteventTab .control-group {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 12px;
      min-height: 68px;
    }}
    #preeventTab .control-group label,
    #posteventTab .control-group label {{
      display: block;
      color: var(--text-secondary);
      font-size: 0.82rem;
      margin-bottom: 6px;
      font-weight: 600;
    }}
    #preeventTab input,
    #preeventTab select,
    #posteventTab input,
    #posteventTab select {{
      background: #0f172a;
      color: var(--text-primary);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 6px 8px;
    }}
    .btn {{
      border: 1px solid var(--border-color);
      border-radius: 6px;
      padding: 6px 12px;
      cursor: pointer;
      font-weight: 600;
      background: #0f172a;
      color: var(--text-primary);
    }}
    .btn-primary {{
      background: linear-gradient(90deg, #0f4c81, #1d9bf0);
      border-color: #1d9bf0;
      color: #fff;
    }}
    .btn-secondary {{
      background: #1f2937;
      border-color: #475569;
      color: #e2e8f0;
    }}
    .checkbox-wrapper {{
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--text-primary);
    }}
    .checkbox-wrapper input[type="checkbox"] {{
      width: 14px;
      height: 14px;
      margin: 0;
      accent-color: var(--accent-blue);
    }}

    #preeventTab .charts-container,
    #posteventTab .charts-container {{
      display: grid;
      grid-template-columns: repeat(2, minmax(360px, 1fr));
      gap: 14px;
      margin-top: 10px;
    }}
    #preeventTab .chart-panel,
    #posteventTab .chart-panel {{
      background: var(--bg-secondary);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      padding: 12px;
      min-height: 320px;
    }}
    #preeventTab .chart-panel h2,
    #posteventTab .chart-panel h2 {{
      margin: 0 0 10px;
      font-size: 1.02rem;
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .badge {{
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 0.75rem;
      background: #334155;
      color: #e2e8f0;
      font-weight: 700;
    }}
    .bucket-info {{
      margin-top: 10px;
      padding: 10px;
      border-left: 3px solid var(--accent-blue);
      background: rgba(29, 155, 240, 0.08);
      border-radius: 6px;
      color: var(--text-primary);
      font-size: 0.9rem;
    }}
    .bucket-info .highlight {{ color: #fde047; font-weight: 700; }}
    .event-details {{
      margin-top: 12px;
      border: 1px dashed var(--border-color);
      border-radius: 8px;
      padding: 10px;
      background: #0f172a;
    }}
    .event-details h3 {{ margin: 0 0 8px; font-size: 0.95rem; }}
    .raw-paths-stats {{
      background: #0f172a;
      border: 1px solid #243042;
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 10px;
    }}
    .stats-row {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 8px;
    }}
    .stat-item {{
      background: #111b2f;
      border: 1px solid #243042;
      border-radius: 6px;
      padding: 6px 8px;
    }}
    .stat-label {{ display: block; color: var(--text-secondary); font-size: 0.74rem; }}
    .stat-value {{ font-size: 0.88rem; font-weight: 700; color: var(--text-primary); }}

    .live-data-panel {{
      margin-bottom: 12px;
      background: linear-gradient(180deg, #0f172a, #111827);
      border: 1px solid #243042;
      border-radius: 12px;
      padding: 12px;
    }}
    .live-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      border-bottom: 1px solid #243042;
      padding-bottom: 8px;
      margin-bottom: 10px;
    }}
    .live-indicator {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: #ef4444;
      box-shadow: 0 0 0 4px rgba(239, 68, 68, 0.2);
    }}
    .live-title {{ font-weight: 800; letter-spacing: 0.04em; color: #e2e8f0; }}
    .live-timestamp {{ margin-left: auto; color: var(--text-secondary); font-size: 0.82rem; }}
    .live-stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 8px;
    }}
    .live-stat {{
      background: #101b31;
      border: 1px solid #243042;
      border-radius: 8px;
      padding: 8px;
    }}
    .live-stat.highlight {{ border-color: var(--accent-orange); }}
    .live-label {{ color: var(--text-secondary); font-size: 0.76rem; margin-bottom: 2px; }}
    .live-value {{ font-size: 1.05rem; font-weight: 800; color: #f8fafc; }}
    .live-sublabel {{ color: var(--text-secondary); font-size: 0.76rem; }}
    .live-status {{ margin-top: 9px; color: var(--text-secondary); font-size: 0.85rem; }}

    .distro-preset-btn {{
      background: #111827;
      color: #cbd5e1;
      border: 1px solid #334155;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.78rem;
      cursor: pointer;
    }}
    .distro-preset-btn.active {{
      background: #1d4ed8;
      border-color: #60a5fa;
      color: #fff;
    }}
    .distro-stat-row {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
      font-size: 0.9rem;
    }}

    .card {{ background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 12px; }}
    .data-table td, .data-table th {{ border-bottom: 1px solid #1f2937; padding: 7px; }}
    .tg-share-btn {{
      position: absolute;
      top: 10px;
      right: 10px;
      z-index: 20;
      border: 1px solid #2563eb;
      background: rgba(37, 99, 235, 0.14);
      color: #bfdbfe;
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.01em;
      cursor: pointer;
    }}
    .tg-share-btn:hover {{
      background: rgba(37, 99, 235, 0.24);
      color: #ffffff;
    }}
    .tg-share-btn:disabled {{
      opacity: 0.6;
      cursor: not-allowed;
    }}
    .tg-modal-backdrop {{
      position: fixed;
      inset: 0;
      background: rgba(2, 6, 23, 0.65);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 10000;
    }}
    .tg-modal {{
      width: min(92vw, 420px);
      background: #0f172a;
      border: 1px solid #334155;
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.45);
    }}
    .tg-modal h4 {{
      margin: 0 0 10px;
      font-size: 1rem;
      color: #e2e8f0;
    }}
    .tg-modal select {{
      width: 100%;
      background: #111827;
      color: #e5e7eb;
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 8px 10px;
      margin-bottom: 10px;
    }}
    .tg-modal .tg-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }}

    @media (max-width: 1100px) {{
      #preeventTab .controls,
      #posteventTab .controls,
      #preeventTab .charts-container,
      #posteventTab .charts-container {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <div class="header">
    <label for="eventDropdown">Event</label>
    <select id="eventDropdown">
      {event_options_html}
    </select>
  </div>
  <div class="tabs">
    <button class="tab-btn active" data-tab="preeventTab" onclick="switchTab('preeventTab', this)">Pre Event</button>
    {'' if preevent_only else post_btn}
    {'' if preevent_only else adhoc_btn}
    {'' if preevent_only else weekmatch_btn}
    {'' if preevent_only else impulse_btn}
    {'' if preevent_only else pm_btn}
  </div>
  {pre_html}
  {'' if preevent_only else post_html}
  {'' if preevent_only else adhoc_html}
  {'' if preevent_only else weekmatch_html}
  {'' if preevent_only else impulse_html}
  {'' if preevent_only else pm_html}
  <script>
    window.DASHBOARD_EVENTS = {events_json if embed_data else '[]'};
    window.DASHBOARD_CONFIG = {cfg_json if embed_data else '{}'};
    {shared_runtime_js}
    function switchTab(tabId, btn) {{
      document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
      const tgt = document.getElementById(tabId);
      if (tgt) tgt.classList.add('active');
      if (btn) btn.classList.add('active');
    }}
    {pre_js}
    {post_js}
    {impulse_js}
    window.__captureNodeAsPngBlob = async function(node) {{
      if (!node) throw new Error('No capture target found');
      const plotNode = node.querySelector('.js-plotly-plot');
      if (plotNode && window.Plotly && typeof window.Plotly.toImage === 'function') {{
        const width = Math.max(900, plotNode.clientWidth || 900);
        const height = Math.max(500, plotNode.clientHeight || 500);
        const dataUrl = await window.Plotly.toImage(plotNode, {{
          format: 'png',
          width: width,
          height: height,
          scale: 2,
        }});
        const resp = await fetch(dataUrl);
        return await resp.blob();
      }}
      if (typeof window.html2canvas === 'function') {{
        const canvas = await window.html2canvas(node, {{
          backgroundColor: '#0b1220',
          scale: 2,
          useCORS: true,
          logging: false,
        }});
        const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
        if (!blob) throw new Error('Failed to render image');
        return blob;
      }}
      throw new Error('Capture library unavailable');
    }};

    window.telegramChats = [];
    window.telegramChatsLoaded = false;

    window.loadTelegramChats = async function(force) {{
      if (!force && window.telegramChatsLoaded && Array.isArray(window.telegramChats) && window.telegramChats.length) {{
        return window.telegramChats;
      }}
      const resp = await fetch('/api/telegram/chats');
      const data = await resp.json();
      if (!resp.ok || !data || !data.success) {{
        throw new Error((data && data.error) ? data.error : 'Failed to load Telegram chats');
      }}
      window.telegramChats = Array.isArray(data.chats) ? data.chats : [];
      window.telegramChatsLoaded = true;
      return window.telegramChats;
    }};

    window.openTelegramChatPicker = async function() {{
      let chats = [];
      try {{
        chats = await window.loadTelegramChats(false);
      }} catch (err) {{
        const msg = (err && err.message) ? err.message : 'Could not load Telegram chats';
        alert(msg);
        return null;
      }}
      if (!Array.isArray(chats) || !chats.length) {{
        alert('No Telegram chats are configured for this bot.');
        return null;
      }}
      return await new Promise((resolve) => {{
        const existing = document.getElementById('tgChatPickerBackdrop');
        if (existing) existing.remove();
        const backdrop = document.createElement('div');
        backdrop.id = 'tgChatPickerBackdrop';
        backdrop.className = 'tg-modal-backdrop';
        const modal = document.createElement('div');
        modal.className = 'tg-modal';
        modal.innerHTML = `
          <h4>Send Snapshot via Telegram Bot</h4>
          <select id="tgChatPickerSelect">
            ${{chats.map((c) => `<option value="${{c.chat_id}}">${{c.name}}</option>`).join('')}}
          </select>
          <div class="tg-actions">
            <button type="button" class="btn btn-secondary" id="tgChatCancelBtn">Cancel</button>
            <button type="button" class="btn btn-primary" id="tgChatSendBtn">Send</button>
          </div>
        `;
        backdrop.appendChild(modal);
        document.body.appendChild(backdrop);
        const cleanup = (value) => {{
          backdrop.remove();
          resolve(value);
        }};
        backdrop.addEventListener('click', (ev) => {{
          if (ev.target === backdrop) cleanup(null);
        }});
        modal.querySelector('#tgChatCancelBtn')?.addEventListener('click', () => cleanup(null));
        modal.querySelector('#tgChatSendBtn')?.addEventListener('click', () => {{
          const v = modal.querySelector('#tgChatPickerSelect')?.value;
          cleanup(v ? Number(v) : null);
        }});
      }});
    }};

    window.sendPanelToTelegram = async function(targetSelector, label, triggerButton) {{
      const btn = triggerButton || null;
      if (btn) btn.disabled = true;
      try {{
        const target = document.querySelector(targetSelector);
        if (!target) throw new Error('Target panel not found');
        const blob = await window.__captureNodeAsPngBlob(target);
        const chatId = await window.openTelegramChatPicker();
        if (!chatId) return;
        const form = new FormData();
        form.append('chat_id', String(chatId));
        form.append('caption', String(label || 'Dashboard Snapshot').slice(0, 120));
        form.append('image', blob, 'dashboard_snapshot.png');
        const sendResp = await fetch('/api/telegram/send_snapshot', {{
          method: 'POST',
          body: form,
        }});
        const sendData = await sendResp.json();
        if (!sendResp.ok || !sendData || !sendData.success) {{
          throw new Error((sendData && sendData.error) ? sendData.error : 'Telegram send failed');
        }}
        alert('Snapshot sent to Telegram.');
      }} catch (err) {{
        const msg = (err && err.message) ? err.message : 'Could not capture panel';
        alert('Telegram share failed: ' + msg);
      }} finally {{
        if (btn) btn.disabled = false;
      }}
    }};

    window.attachTelegramShareButtons = function() {{
      const panels = Array.from(document.querySelectorAll('.chart-panel'));
      panels.forEach((panel, idx) => {{
        if (!(panel instanceof HTMLElement)) return;
        if (panel.querySelector('.tg-share-btn')) return;
        if (!panel.id) panel.id = 'tg-share-target-' + idx;
        const cs = window.getComputedStyle(panel);
        if (!cs || cs.position === 'static') panel.style.position = 'relative';
        const heading = panel.querySelector('h1, h2, h3, .pm-title');
        const label = heading && heading.textContent ? heading.textContent.trim().slice(0, 40) : ('panel-' + (idx + 1));
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'tg-share-btn';
        btn.textContent = 'Send to Telegram';
        btn.title = 'Capture this panel and open Telegram share';
        btn.onclick = function(ev) {{
          ev.preventDefault();
          ev.stopPropagation();
          window.sendPanelToTelegram('#' + panel.id, label, btn);
        }};
        panel.appendChild(btn);
      }});
    }};

    document.addEventListener('DOMContentLoaded', async () => {{
      const eventDropdown = document.getElementById('eventDropdown');
      if (eventDropdown) {{
        eventDropdown.addEventListener('change', () => {{
          const next = String(eventDropdown.value || '').trim().toLowerCase();
          if (next) window.location.href = `/${{next}}`;
        }});
      }}
      if (typeof window.__initGeneratedDashboard === 'function') {{
        window.__initGeneratedDashboard();
      }}
      // Always attach share buttons first, even if downstream dashboard updates fail.
      if (typeof window.attachTelegramShareButtons === 'function') {{
        window.attachTelegramShareButtons();
        setTimeout(() => window.attachTelegramShareButtons(), 800);
      }}
      if (typeof window.loadTelegramChats === 'function') {{
        window.loadTelegramChats(false).catch(() => null);
      }}
      if (typeof refreshLiveData === 'function') {{
        try {{
          refreshLiveData();
        }} catch (_err) {{
          // Keep bootstrap resilient.
        }}
      }}
      if (typeof updatePreeventDashboard === 'function') {{
        try {{
          await updatePreeventDashboard();
        }} catch (_err) {{
          // Keep other features usable even if one panel fails.
        }}
      }}
      if ({'false' if preevent_only else 'true'} && typeof updatePosteventDashboard === 'function') {{
        try {{
          await updatePosteventDashboard();
        }} catch (_err) {{
          // Keep other features usable even if one panel fails.
        }}
      }}
    }});
  </script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return str(output_path)


def build_live_unified_dashboard(
    event_type: str = "NFP",
    force_refresh: bool = False,
    output_path: Optional[Path] = None,
) -> Dict:
    event_type = (event_type or "NFP").upper()
    dashboard_data = None
    if not force_refresh:
        try:
            dashboard_data = load_cached_dashboard_data(event_type)
        except Exception:
            dashboard_data = None

    if dashboard_data is None:
        raw = load_event_data(event_type)
        dashboard_data = build_dashboard_data(raw, event_type=event_type)
        try:
            save_dashboard_cache(event_type, dashboard_data)
        except Exception:
            pass

    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"live_unified_{event_type.lower()}_dashboard_{ts}.html"

    html_path = generate_html_dashboard(dashboard_data, output_path=output_path, show_live=True, embed_data=True)
    return {"success": True, "event_type": event_type, "html_path": html_path, "event_count": len(dashboard_data.get("events", []))}

