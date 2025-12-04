"""
Trading Economics WebSocket Client.

Provides tools for real-time event streaming via WebSocket.
Maintains a background connection and buffers events for retrieval.

Optimized for low-latency processing with async DB writes.
"""

import json
import logging
import threading
import time
import queue
import sqlite3
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path

# Try to import websockets (optional dependency)
try:
    import asyncio
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

from src.mcp.discovery import register_tool
from src.servers.tradingeconomics.schema import (
    TE_WEBSOCKET_URL,
    LIVE_EVENT_STREAM_TABLE,
    get_db_path,
    normalize_country,
    normalize_importance,
    _parse_numeric,
    _parse_event_date
)

logger = logging.getLogger(__name__)

# =============================================================================
# Global State
# =============================================================================

# Singleton instance
_WS_CLIENT = None
_CLIENT_LOCK = threading.Lock()

# =============================================================================
# Background DB Writer (Async Batch Inserts)
# =============================================================================

class _DBWriter(threading.Thread):
    """
    Background thread for batched database writes.
    
    Decouples event processing from DB I/O to minimize latency.
    Collects events and writes them in batches for efficiency.
    """
    
    def __init__(self, batch_size: int = 10, batch_timeout: float = 0.1):
        """
        Initialize DB writer.
        
        Args:
            batch_size: Max events per batch insert
            batch_timeout: Max seconds to wait before flushing batch
        """
        super().__init__(daemon=True)
        self.write_queue = queue.Queue()
        self.running = True
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.conn: Optional[sqlite3.Connection] = None
        self.stats = {
            "batches_written": 0,
            "events_written": 0,
            "errors": 0
        }
    
    def enqueue(self, event: Dict[str, Any]) -> None:
        """Add event to write queue (non-blocking)."""
        try:
            self.write_queue.put_nowait(event)
        except queue.Full:
            logger.warning("DB write queue full, dropping event")
            self.stats["errors"] += 1
    
    def run(self) -> None:
        """Main loop: collect batches and write to DB."""
        try:
            self.conn = sqlite3.connect(get_db_path(), check_same_thread=False)
            logger.debug("DBWriter: Persistent connection opened")
            
            while self.running or not self.write_queue.empty():
                batch = self._collect_batch()
                if batch:
                    self._write_batch(batch)
            
        except Exception as e:
            logger.error(f"DBWriter error: {e}")
            self.stats["errors"] += 1
        finally:
            if self.conn:
                self.conn.close()
                logger.debug("DBWriter: Connection closed")
    
    def _collect_batch(self) -> List[Dict[str, Any]]:
        """Collect events up to batch_size or batch_timeout."""
        batch = []
        deadline = time.monotonic() + self.batch_timeout
        
        while len(batch) < self.batch_size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            
            try:
                event = self.write_queue.get(timeout=min(remaining, 0.05))
                batch.append(event)
            except queue.Empty:
                # If not running and queue empty, exit
                if not self.running:
                    break
                continue
        
        return batch
    
    def _write_batch(self, batch: List[Dict[str, Any]]) -> None:
        """Write batch of events to database."""
        if not batch or not self.conn:
            return
        
        try:
            cursor = self.conn.cursor()
            
            # Build SQL for batch insert
            cols = list(batch[0].keys())
            cols_str = ", ".join(cols)
            placeholders = ", ".join(["?" for _ in cols])
            sql = f"INSERT OR IGNORE INTO {LIVE_EVENT_STREAM_TABLE} ({cols_str}) VALUES ({placeholders})"
            
            # Prepare values
            values = [tuple(event[col] for col in cols) for event in batch]
            
            # Execute batch
            cursor.executemany(sql, values)
            self.conn.commit()
            
            self.stats["batches_written"] += 1
            self.stats["events_written"] += len(batch)
            
            logger.debug(f"DBWriter: Wrote batch of {len(batch)} events")
            
        except sqlite3.Error as e:
            logger.error(f"DBWriter batch write error: {e}")
            self.stats["errors"] += 1
    
    def stop(self) -> None:
        """Signal thread to stop (will flush remaining events)."""
        self.running = False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get writer statistics."""
        return {
            **self.stats,
            "queue_size": self.write_queue.qsize()
        }


# =============================================================================
# WebSocket Client Implementation
# =============================================================================

class TEWebSocketClient:
    """
    Manages WebSocket connection to Trading Economics.
    Runs in a background thread to maintain persistence across tool calls.
    
    Optimized for low-latency with:
    - Async DB writes via _DBWriter
    - Minimal logging overhead (DEBUG level for per-message logs)
    - Events available immediately via memory queue
    """
    
    def __init__(self):
        self.is_running = False
        self.is_connected = False
        self.thread = None
        self.loop = None
        self.queue = queue.Queue(maxsize=1000)
        self.subscriptions = {"countries": [], "importance": []}
        self.error = None
        self.verbose = False  # Set to True for detailed per-message logging
        self.db_writer: Optional[_DBWriter] = None
        self.stats = {
            "messages_received": 0,
            "events_processed": 0,
            "errors": 0,
            "start_time": None,
            "last_message_time": None
        }
        
    def start(
        self, 
        countries: List[str] = None, 
        importance: List[str] = None,
        verbose: bool = False
    ):
        """
        Start the WebSocket client in a background thread.
        
        Args:
            countries: List of country codes to filter
            importance: List of importance levels to filter
            verbose: Enable detailed per-message logging (default: False)
        """
        if self.is_running:
            logger.info("WebSocket client already running")
            return

        if not HAS_WEBSOCKETS:
            raise ImportError("The 'websockets' library is required for streaming. Install it with: pip install websockets")

        self.subscriptions["countries"] = [normalize_country(c) for c in (countries or [])]
        self.subscriptions["importance"] = [normalize_importance(i) for i in (importance or [])]
        self.verbose = verbose
        
        self.is_running = True
        self.stats["start_time"] = datetime.now(timezone.utc).isoformat()
        
        # Start DB writer thread
        self.db_writer = _DBWriter(batch_size=10, batch_timeout=0.1)
        self.db_writer.start()
        logger.info("DB writer background thread started")
        
        # Start WebSocket thread
        self.thread = threading.Thread(target=self._thread_runner, daemon=True)
        self.thread.start()
        logger.info("WebSocket background thread started")

    def stop(self):
        """Stop the WebSocket client and DB writer."""
        self.is_running = False
        
        if self.loop:
            # Schedule stop in the async loop
            self.loop.call_soon_threadsafe(self.loop.stop)
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        # Stop DB writer (will flush remaining events)
        if self.db_writer:
            self.db_writer.stop()
            self.db_writer.join(timeout=2.0)
            logger.info(f"DB writer stopped: {self.db_writer.get_stats()}")
            
        self.is_connected = False
        logger.info("WebSocket client stopped")

    def _thread_runner(self):
        """Entry point for background thread."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            self.loop.run_until_complete(self._async_runner())
        except Exception as e:
            self.error = str(e)
            logger.error(f"WebSocket thread error: {e}")
        finally:
            self.loop.close()
            self.is_connected = False
            self.is_running = False

    async def _async_runner(self):
        """Async main loop managing connection and reconnection.
        
        Uses Trading Economics WebSocket protocol:
        - URL: wss://stream.tradingeconomics.com/?client=API_KEY
        - Subscribe: {"topic": "subscribe", "to": "calendar"}
        
        See: https://docs.tradingeconomics.com/economic_calendar/streaming/
        """
        # Load API key from config
        from config.settings import get_api_key
        try:
            api_key = get_api_key("TRADING_ECONOMICS_API_KEY")
            # Log masked key for debugging
            if api_key:
                masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
                logger.info(f"Loaded API key: {masked} (len={len(api_key)})")
            else:
                logger.error("API key is empty!")
                self.error = "API key is empty"
                return
        except Exception as e:
            logger.error(f"Failed to load API key: {e}")
            self.error = f"API key error: {e}"
            return
        
        # Build URL per TE docs
        url = f"{TE_WEBSOCKET_URL}?client={api_key}"
        logger.info(f"WebSocket URL (masked): {TE_WEBSOCKET_URL}?client={masked}")
        
        while self.is_running:
            try:
                logger.info(f"Connecting to {TE_WEBSOCKET_URL}...")
                async with websockets.connect(url) as websocket:
                    self.is_connected = True
                    self.error = None
                    logger.info("WebSocket connected")
                    
                    # Send subscription message
                    sub_msg = {"topic": "subscribe", "to": "calendar"}
                    logger.info(f"Sending subscription: {sub_msg}")
                    await websocket.send(json.dumps(sub_msg))
                    logger.info("Subscribed to 'calendar' channel - waiting for events...")
                    
                    while self.is_running:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                            self._process_message(message)
                        except asyncio.TimeoutError:
                            # Send ping to keep alive
                            try:
                                pong_waiter = await websocket.ping()
                                await asyncio.wait_for(pong_waiter, timeout=10.0)
                            except:
                                break # Reconnect on ping fail
                                
            except Exception as e:
                self.is_connected = False
                self.stats["errors"] += 1
                logger.warning(f"WebSocket connection lost: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    def _process_message(self, message: str):
        """
        Process incoming raw message with minimal latency.
        
        Events are queued immediately for consumers.
        DB writes happen async via _DBWriter.
        """
        try:
            self.stats["messages_received"] += 1
            self.stats["last_message_time"] = datetime.now(timezone.utc).isoformat()
            
            data = json.loads(message)
            
            # Debug logging (only if verbose)
            if self.verbose:
                if isinstance(data, dict):
                    logger.debug(f"WS message received (dict): {json.dumps(data, default=str)}")
                elif isinstance(data, list):
                    logger.debug(f"WS message received (list of {len(data)} items)")
            
            # TE often sends a list of events or a single object
            if isinstance(data, dict):
                events = [data]
            elif isinstance(data, list):
                events = data
            else:
                return

            for event in events:
                # Basic validation - check if it looks like a calendar event
                if not isinstance(event, dict) or "event" not in event:
                    if self.verbose and isinstance(event, dict):
                        logger.debug(f"Skipped (no 'event' key): keys={list(event.keys())[:10]}")
                    continue
                    
                # Apply filters
                country = normalize_country(event.get("country", ""))
                importance = str(event.get("importance", "1"))
                importance_str = normalize_importance(importance)
                event_name = event.get("event", "Unknown")
                
                # Check country subscription
                if self.subscriptions["countries"] and country not in self.subscriptions["countries"]:
                    if self.verbose:
                        logger.debug(f"Filtered by country: {event_name} ({country})")
                    continue
                    
                # Check importance subscription
                if self.subscriptions["importance"] and importance_str not in self.subscriptions["importance"]:
                    if self.verbose:
                        logger.debug(f"Filtered by importance: {event_name} ({importance_str})")
                    continue
                
                # Format event (fast, no I/O)
                formatted = self._format_event(event)
                if formatted:
                    # Queue for consumers IMMEDIATELY (low latency path)
                    if self.queue.full():
                        try:
                            self.queue.get_nowait()  # Drop oldest
                        except queue.Empty:
                            pass
                    self.queue.put(formatted)
                    self.stats["events_processed"] += 1
                    
                    # Log the processed event (INFO level - important for visibility)
                    logger.info(f"📅 {event_name} ({country}) | actual={formatted.get('actual')} | consensus={formatted.get('consensus')}")
                    
                    # Push to DB writer (async, non-blocking)
                    if self.db_writer:
                        self.db_writer.enqueue(formatted)
                    
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def _format_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Format event for storage and consumption.
        
        Fast path - no I/O operations.
        
        Field Mapping (Trading Economics WebSocket API → Our fields):
        - actual → actual (the released value)
        - forecast → consensus (market consensus forecast)  
        - teforecast → forecast (Trading Economics' own forecast)
        - previous → previous (prior period value)
        - calendarId → event_id
        - event → event_name
        - country → country
        - category → category
        - importance → importance (1=low, 2=medium, 3=high)
        - date → event_date
        - ticker → ticker
        - unit → unit
        """
        try:
            event_id = str(event.get("calendarId", event.get("id", "")))
            if not event_id:
                return None
                
            formatted = {
                "event_id": event_id,
                "event_name": event.get("event", "").strip(),
                "country": event.get("country", ""),
                "category": event.get("category", ""),
                "importance": normalize_importance(event.get("importance", "")),
                "event_date": _parse_event_date(event.get("date", "")),
                "actual": _parse_numeric(event.get("actual")),
                "consensus": _parse_numeric(event.get("forecast")),  # Market consensus
                "forecast": _parse_numeric(event.get("teforecast")),  # TE's own forecast
                "previous": _parse_numeric(event.get("previous")),
                "unit": event.get("unit", ""),
                "ticker": event.get("ticker", ""),
                "received_at": datetime.now(timezone.utc).isoformat(),
                "source": "websocket"
            }
            
            return formatted
            
        except Exception as e:
            logger.error(f"Error formatting event: {e}")
            return None

    def get_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent events from the memory queue."""
        results = []
        try:
            count = 0
            while count < limit and not self.queue.empty():
                results.append(self.queue.get_nowait())
                count += 1
        except:
            pass
        return results
    
    def get_full_stats(self) -> Dict[str, Any]:
        """Get combined stats from client and DB writer."""
        stats = {**self.stats}
        if self.db_writer:
            stats["db_writer"] = self.db_writer.get_stats()
        return stats


# =============================================================================
# Tools
# =============================================================================

@register_tool("start_event_stream", "Start real-time WebSocket stream for economic events")
def start_event_stream(
    countries: Optional[List[str]] = None,
    importance: Optional[List[str]] = None,
    verbose: bool = False
) -> Dict[str, Any]:
    """
    Start the WebSocket stream.
    
    Args:
        countries: List of countries to filter (e.g. ["US", "UK"])
        importance: List of importance levels (e.g. ["high", "medium"])
        verbose: Enable detailed per-message logging (default: False)
        
    Returns:
        Status dictionary
    """
    global _WS_CLIENT
    with _CLIENT_LOCK:
        if _WS_CLIENT is None:
            _WS_CLIENT = TEWebSocketClient()
            
        if not _WS_CLIENT.is_running:
            try:
                _WS_CLIENT.start(countries, importance, verbose=verbose)
                return {
                    "success": True,
                    "status": "started",
                    "message": "WebSocket stream started in background",
                    "filters": {
                        "countries": countries,
                        "importance": importance
                    }
                }
            except ImportError as e:
                 return {
                    "success": False,
                    "status": "error",
                    "error": str(e),
                    "message": "Please install websockets library: pip install websockets"
                }
        else:
            return {
                "success": True,
                "status": "already_running",
                "message": "Stream is already running"
            }

@register_tool("stop_event_stream", "Stop the WebSocket stream")
def stop_event_stream() -> Dict[str, Any]:
    """Stop the WebSocket stream."""
    global _WS_CLIENT
    with _CLIENT_LOCK:
        if _WS_CLIENT and _WS_CLIENT.is_running:
            _WS_CLIENT.stop()
            stats = _WS_CLIENT.get_full_stats()
            _WS_CLIENT = None  # Reset singleton
            return {
                "success": True,
                "status": "stopped",
                "final_stats": stats
            }
        return {
            "success": False,
            "status": "not_running",
            "message": "No active stream found"
        }

@register_tool("get_stream_status", "Get status of WebSocket stream")
def get_stream_status() -> Dict[str, Any]:
    """Get current stream status and statistics."""
    global _WS_CLIENT
    if _WS_CLIENT:
        return {
            "is_running": _WS_CLIENT.is_running,
            "is_connected": _WS_CLIENT.is_connected,
            "subscriptions": _WS_CLIENT.subscriptions,
            "statistics": _WS_CLIENT.get_full_stats(),
            "queue_size": _WS_CLIENT.queue.qsize(),
            "error": _WS_CLIENT.error
        }
    return {
        "is_running": False,
        "is_connected": False,
        "message": "Stream not initialized"
    }

@register_tool("get_live_events", "Get buffered live events")
def get_live_events(limit: int = 10) -> Dict[str, Any]:
    """Get recent events from the live buffer."""
    global _WS_CLIENT
    if _WS_CLIENT:
        events = _WS_CLIENT.get_events(limit)
        return {
            "success": True,
            "events": events,
            "count": len(events),
            "stream_status": "running" if _WS_CLIENT.is_running else "stopped"
        }
    return {
        "success": False,
        "events": [],
        "count": 0,
        "message": "Stream not initialized"
    }
